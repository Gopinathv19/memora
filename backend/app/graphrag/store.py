"""FalkorDB access for Graph RAG: the Cypher the five techniques run.

This wraps the existing `FalkorGraphStore` (same per-tenant graph, same
connection, same timeout) and adds what only the new service needs:

* **Vector indexes** on `Entity.embedding`, `Fact.embedding` and
  `Community.embedding` (HNSW, cosine).
* **`Fact` nodes.** A fact edge's type is dynamic (WORKS_FOR, OWNS, ...), and
  FalkorDB's relationship vector index needs one fixed type, so each fact edge
  is reified once as `(:Fact {text, embedding, source_id, chunk_ids})` linked
  `-[:SUBJ]->` and `-[:OBJ]->` to its entities. One index then serves every
  relation type, and a question about a *relationship* ("who supplies whom")
  can be matched against fact text directly.
* **`Community` nodes** holding an LLM summary and embedding, linked
  `-[:HAS_MEMBER]->` to the entities the community detection found.
* **Uniqueness constraints** on the ids every MERGE relies on.

Every read takes the in-scope source ids, exactly like the base store, so the
scope check happens inside the query, not after it. Vector search is the one
exception FalkorDB forces on us: the index cannot be filtered, so it is
over-fetched (`OVERFETCH`) and filtered by scope in the same Cypher statement.
Scores from `db.idx.vector.*` are cosine *distances*; this module converts them
to similarities (1 - distance) so higher is always better.
"""

import hashlib
import logging
import threading
from typing import Any

from redis.exceptions import ResponseError

from app.graph.store import _FACT_FIELDS, FalkorGraphStore

log = logging.getLogger(__name__)

OVERFETCH = 6

_RANGE_INDEXES = (
    "CREATE INDEX FOR (f:Fact) ON (f.id)",
    "CREATE INDEX FOR (f:Fact) ON (f.source_id)",
    "CREATE INDEX FOR (c:Community) ON (c.id)",
    "CREATE INDEX FOR (c:Community) ON (c.subject_id)",
)

# (label, property) pairs that must be unique. FalkorDB builds the constraint
# asynchronously (status PENDING) and refuses it while duplicates exist, which
# is reported, not fatal: a graph that predates the constraint keeps working.
_UNIQUE = (("Entity", "id"), ("Chunk", "id"), ("Document", "id"), ("Fact", "id"), ("Community", "id"))


def fact_id(source_id: str, source_entity: str, relation: str, target_entity: str) -> str:
    raw = f"{source_id}|{source_entity}|{relation}|{target_entity}"
    return hashlib.sha1(raw.encode()).hexdigest()


def fact_text(source_name: str, relation: str, target_name: str, description: str | None) -> str:
    base = f"{source_name} {relation.replace('_', ' ').lower()} {target_name}"
    return f"{base}. {description}" if description else base


class GraphRagStore:
    def __init__(self, base: FalkorGraphStore):
        self.base = base
        self._ready: dict[str, int] = {}
        self._lock = threading.Lock()

    # -- setup ---------------------------------------------------------------------------

    def ensure(self, tenant_id, dimension: int) -> None:
        """Create the vector/range indexes and constraints once per graph and process."""
        name = self.base.graph_name(tenant_id)
        if self._ready.get(name) == dimension:
            return
        with self._lock:
            if self._ready.get(name) == dimension:
                return
            self.base._graph(tenant_id)  # base indexes first
            statements = list(_RANGE_INDEXES)
            for label in ("Entity", "Fact", "Community"):
                statements.append(
                    f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
                    f"OPTIONS {{dimension: {int(dimension)}, similarityFunction: 'cosine'}}"
                )
            graph = self.base.db.select_graph(name)
            for statement in statements:
                try:
                    graph.query(statement)
                except ResponseError as exc:
                    if "already" not in str(exc).lower():
                        raise
            for label, prop in _UNIQUE:
                try:
                    self.base.db.connection.execute_command(
                        "GRAPH.CONSTRAINT", "CREATE", name, "UNIQUE", "NODE", label,
                        "PROPERTIES", 1, prop,
                    )
                except ResponseError as exc:
                    text = str(exc).lower()
                    if "already" not in text:
                        log.warning("constraint %s.%s not created on %s: %s", label, prop, name, exc)
            self._ready[name] = dimension

    def explain(self, tenant_id, cypher: str) -> list[str]:
        """`GRAPH.EXPLAIN`: the plan, to check a query uses an index."""
        name = self.base.graph_name(tenant_id)
        plan = self.base.db.connection.execute_command("GRAPH.EXPLAIN", name, cypher)
        return [p.decode() if isinstance(p, bytes) else str(p) for p in plan]

    def _read(self, tenant_id, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        return self.base._read(tenant_id, cypher, params)

    def _write(self, tenant_id, cypher: str, params: dict | None = None):
        return self.base._query(tenant_id, cypher, params)

    # -- embeddings: entities ------------------------------------------------------------

    def entities_missing_embedding(self, tenant_id, source_ids: list[str], limit: int) -> list[dict]:
        if not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (c:Chunk)-[m:MENTIONS]->(e:Entity) "
            "WHERE c.source_id IN $sids AND e.embedding IS NULL "
            "WITH e, collect(m.description) AS descs "
            "RETURN e.id AS id, e.display_name AS name, e.entity_type AS entity_type, "
            "e.aliases AS aliases, descs LIMIT $limit",
            {"sids": source_ids, "limit": limit},
        )

    def set_entity_embeddings(self, tenant_id, rows: list[dict]) -> None:
        if not rows:
            return
        self._write(
            tenant_id,
            "UNWIND $rows AS row MATCH (e:Entity {id: row.id}) "
            "SET e.embedding = vecf32(row.embedding), e.embedded_text = row.text",
            {"rows": rows},
        )

    # -- embeddings: facts ---------------------------------------------------------------

    def scope_facts(self, tenant_id, source_ids: list[str]) -> list[dict]:
        """Every entity-to-entity fact edge the scope's sources contributed."""
        if not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (a:Entity)-[r]->(b:Entity) WHERE r.source_id IN $sids "
            f"RETURN {_FACT_FIELDS}",
            {"sids": source_ids},
        )

    def existing_fact_ids(self, tenant_id, source_ids: list[str]) -> set[str]:
        if not source_ids:
            return set()
        rows = self._read(
            tenant_id,
            "MATCH (f:Fact) WHERE f.source_id IN $sids RETURN f.id AS id",
            {"sids": source_ids},
        )
        return {r["id"] for r in rows}

    def upsert_facts(self, tenant_id, rows: list[dict]) -> None:
        if not rows:
            return
        self._write(
            tenant_id,
            "UNWIND $rows AS row "
            "MATCH (a:Entity {id: row.a}) MATCH (b:Entity {id: row.b}) "
            "MERGE (f:Fact {id: row.id}) "
            "SET f.source_id = row.source_id, f.relation = row.relation, f.text = row.text, "
            "f.chunk_ids = row.chunk_ids, f.confidence = row.confidence, "
            "f.a_id = row.a, f.b_id = row.b, f.embedding = vecf32(row.embedding) "
            "MERGE (f)-[:SUBJ]->(a) MERGE (f)-[:OBJ]->(b)",
            {"rows": rows},
        )

    def prune_orphan_facts(self, tenant_id) -> int:
        """Delete `Fact` nodes whose source `Document` is gone (a deleted source).

        The base store's `delete_source` knows nothing about facts, and a deleted
        source is no longer in any subject's scope, so no scoped build would ever
        see these. Safe tenant-wide: it only removes facts with no document.
        """
        result = self._write(
            tenant_id,
            "MATCH (f:Fact) OPTIONAL MATCH (d:Document {id: f.source_id}) "
            "WITH f, d WHERE d IS NULL DETACH DELETE f",
        )
        return int(result.nodes_deleted)

    def delete_facts(self, tenant_id, ids: list[str]) -> int:
        if not ids:
            return 0
        result = self._write(
            tenant_id, "MATCH (f:Fact) WHERE f.id IN $ids DETACH DELETE f", {"ids": ids}
        )
        return int(result.nodes_deleted)

    # -- vector search (similarity = 1 - cosine distance) --------------------------------

    def _vector(self, tenant_id, cypher: str, params: dict) -> list[dict]:
        try:
            return self._read(tenant_id, cypher, params)
        except ResponseError as exc:  # no index yet: nothing was ever embedded
            if "index" in str(exc).lower():
                return []
            raise

    def vector_entities(
        self, tenant_id, vector: list[float], source_ids: list[str], k: int
    ) -> list[dict]:
        if not source_ids:
            return []
        return self._vector(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Entity', 'embedding', $k, vecf32($q)) "
            "YIELD node, score "
            "MATCH (c:Chunk)-[:MENTIONS]->(node) WHERE c.source_id IN $sids "
            "WITH node, score, count(DISTINCT c) AS mentions "
            "RETURN node.id AS id, 1.0 - score AS similarity, mentions "
            "ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "k": k * OVERFETCH, "sids": source_ids, "limit": k},
        )

    def vector_facts(
        self, tenant_id, vector: list[float], source_ids: list[str], k: int
    ) -> list[dict]:
        if not source_ids:
            return []
        return self._vector(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Fact', 'embedding', $k, vecf32($q)) "
            "YIELD node, score WHERE node.source_id IN $sids "
            "MATCH (node)-[:SUBJ]->(a:Entity) MATCH (node)-[:OBJ]->(b:Entity) "
            "RETURN node.id AS id, node.relation AS relation, node.text AS text, "
            "node.chunk_ids AS chunk_ids, node.confidence AS confidence, "
            "node.source_id AS source_id, a.id AS a_id, a.display_name AS a_name, "
            "b.id AS b_id, b.display_name AS b_name, 1.0 - score AS similarity "
            "ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "k": k * OVERFETCH, "sids": source_ids, "limit": k},
        )

    def vector_communities(
        self, tenant_id, vector: list[float], subject_ids: list[str], k: int
    ) -> list[dict]:
        if not subject_ids:
            return []
        return self._vector(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Community', 'embedding', $k, vecf32($q)) "
            "YIELD node, score WHERE node.subject_id IN $subjects "
            "RETURN node.id AS id, node.title AS title, node.summary AS summary, "
            "node.size AS size, 1.0 - score AS similarity "
            "ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "k": k * OVERFETCH, "subjects": subject_ids, "limit": k},
        )

    # -- spreading activation ------------------------------------------------------------

    def activation(
        self,
        tenant_id,
        seeds: list[dict],
        source_ids: list[str],
        *,
        max_hops: int,
        decay: float,
        min_confidence: float,
        relation_weights: dict[str, float],
        hub_damping: bool,
        limit: int,
    ) -> list[dict]:
        """Hop-decayed activation, scored in one Cypher statement.

        S(v) = sum over paths from a seed of  seed_weight * prod(decay * w_type * confidence)
        divided by (1 + ln(1 + degree(v))) when hub damping is on. `hop` is the
        shortest path length that reached v. Paths use only edges from in-scope
        sources, so scope is enforced during traversal, not after it.

        Paths run through entities only: `MENTIONS` edges also carry a
        `source_id`, and without the `x:Entity` check two entities that merely
        share a text unit would count as connected through it, doubling hop
        counts and inflating scores.
        """
        if not seeds or not source_ids:
            return []
        hops = max(1, min(int(max_hops), 4))  # a literal: Cypher ranges take no parameter
        return self._read(
            tenant_id,
            "UNWIND $seeds AS sr MATCH (s:Entity {id: sr.id}) "
            f"MATCH p=(s)-[*1..{hops}]-(n:Entity) "
            "WHERE n.id <> s.id AND all(x IN nodes(p) WHERE x:Entity) "
            "AND all(r IN relationships(p) WHERE r.source_id IN $sids "
            "AND coalesce(r.confidence, 0.6) >= $minconf) "
            "WITH n, sr.w * reduce(sc = 1.0, r IN relationships(p) | "
            "sc * $decay * coalesce(r.confidence, 0.6) * coalesce($weights[type(r)], 1.0)) AS ps, "
            "length(p) AS h "
            "WITH n, sum(ps) AS raw, min(h) AS hop "
            "OPTIONAL MATCH (n)-[d]-(:Entity) WHERE d.source_id IN $sids "
            "WITH n, raw, hop, count(d) AS deg "
            "RETURN n.id AS id, n.display_name AS name, n.entity_type AS entity_type, "
            "raw AS raw, hop AS hop, deg AS degree, "
            "raw / CASE WHEN $damp THEN 1.0 + log(1.0 + deg) ELSE 1.0 END AS score "
            "ORDER BY score DESC LIMIT $limit",
            {
                "seeds": seeds, "sids": source_ids, "decay": decay,
                "minconf": min_confidence, "weights": relation_weights,
                "damp": hub_damping, "limit": limit,
            },
        )

    def entity_chunks(self, tenant_id, entity_ids: list[str], source_ids: list[str]) -> list[dict]:
        """(entity, chunk) pairs: which graph text units mention which entities."""
        if not entity_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) "
            "WHERE e.id IN $ids AND c.source_id IN $sids "
            "RETURN e.id AS entity_id, c.id AS chunk_id",
            {"ids": entity_ids, "sids": source_ids},
        )

    def entities_info(self, tenant_id, entity_ids: list[str]) -> dict[str, dict]:
        if not entity_ids:
            return {}
        rows = self._read(
            tenant_id,
            "MATCH (e:Entity) WHERE e.id IN $ids "
            "RETURN e.id AS id, e.display_name AS name, e.entity_type AS entity_type",
            {"ids": entity_ids},
        )
        return {r["id"]: r for r in rows}

    def entity_degrees(self, tenant_id, entity_ids: list[str], source_ids: list[str]) -> dict[str, int]:
        if not entity_ids or not source_ids:
            return {}
        rows = self._read(
            tenant_id,
            "MATCH (n:Entity) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[d]-(:Entity) WHERE d.source_id IN $sids "
            "RETURN n.id AS id, count(d) AS degree",
            {"ids": entity_ids, "sids": source_ids},
        )
        return {r["id"]: int(r["degree"]) for r in rows}

    # -- paths ---------------------------------------------------------------------------

    def shortest_paths(
        self, tenant_id, a: str, b: str, source_ids: list[str], max_len: int, limit: int
    ) -> list[dict]:
        """All shortest paths between two entities, edges restricted to the scope.

        FalkorDB resolves `allShortestPaths` before it applies WHERE, so a
        shortest path that crosses an out-of-scope edge is dropped rather than
        replaced by a longer in-scope one. Scoped graphs that contain only the
        subject's sources are unaffected.
        """
        if not source_ids:
            return []
        hops = max(1, min(int(max_len), 5))
        # Only fact edges: a path may not shortcut through a shared text unit
        # (`MENTIONS`). The names come from the ontology whitelist, never a client.
        types = "|".join(sorted(self.base.ontology.relation_types))
        return self._read(
            tenant_id,
            "MATCH (a:Entity {id: $a}), (b:Entity {id: $b}) WITH a, b "
            f"MATCH p=allShortestPaths((a)-[:{types}*..{hops}]-(b)) "
            "WHERE all(r IN relationships(p) WHERE r.source_id IN $sids) "
            "RETURN [n IN nodes(p) | n.id] AS node_ids, "
            "[n IN nodes(p) | n.display_name] AS node_names, "
            "[r IN relationships(p) | type(r)] AS relations, "
            "[r IN relationships(p) | startNode(r).id] AS starts, "
            "[r IN relationships(p) | r.chunk_ids] AS chunk_ids, "
            "[r IN relationships(p) | r.description] AS descriptions, "
            "[r IN relationships(p) | coalesce(r.confidence, 0.6)] AS confidences, "
            "[r IN relationships(p) | r.source_id] AS source_ids "
            "LIMIT $limit",
            {"a": a, "b": b, "sids": source_ids, "limit": limit},
        )

    # -- communities ---------------------------------------------------------------------

    def label_propagation(self, tenant_id) -> list[dict]:
        """`algo.labelPropagation` over the tenant's entity graph (community id per node)."""
        return self._read(
            tenant_id,
            "CALL algo.labelPropagation({nodeLabels: ['Entity']}) "
            "YIELD node, communityId RETURN node.id AS id, communityId AS community",
        )

    def scope_entity_ids(self, tenant_id, source_ids: list[str]) -> list[str]:
        if not source_ids:
            return []
        rows = self._read(
            tenant_id,
            "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.source_id IN $sids "
            "RETURN DISTINCT e.id AS id",
            {"sids": source_ids},
        )
        return [r["id"] for r in rows]

    def facts_among(self, tenant_id, entity_ids: list[str], source_ids: list[str], limit: int):
        return self.base.facts_among(tenant_id, entity_ids, source_ids, limit)

    def replace_communities(self, tenant_id, subject_id: str, rows: list[dict]) -> None:
        """Drop the subject's communities and write the new ones."""
        self._write(
            tenant_id,
            "MATCH (c:Community {subject_id: $sid}) DETACH DELETE c",
            {"sid": subject_id},
        )
        if not rows:
            return
        self._write(
            tenant_id,
            "UNWIND $rows AS row "
            "MERGE (c:Community {id: row.id}) "
            "SET c.subject_id = $sid, c.title = row.title, c.summary = row.summary, "
            "c.size = row.size, c.embedding = vecf32(row.embedding) "
            "WITH c, row UNWIND row.members AS mid "
            "MATCH (e:Entity {id: mid}) MERGE (c)-[:HAS_MEMBER]->(e)",
            {"rows": rows, "sid": subject_id},
        )

    def community_members(self, tenant_id, community_ids: list[str]) -> list[dict]:
        if not community_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (c:Community)-[:HAS_MEMBER]->(e:Entity) WHERE c.id IN $ids "
            "RETURN c.id AS community_id, e.id AS id, e.display_name AS name, "
            "e.entity_type AS entity_type",
            {"ids": community_ids},
        )

    # -- inspection ----------------------------------------------------------------------

    def inspect_source(self, tenant_id, source_id: str) -> dict:
        """Everything one source put into the graph, for checking an extraction.

        Passages (the text units the model read, in order), which entities each
        passage mentions, and the facts the source contributed. Entities are shared
        across a tenant's sources, so only mentions made from *this* source's
        passages, and facts whose `source_id` is this source, are returned.
        """
        chunks = self._read(
            tenant_id,
            "MATCH (c:Chunk {source_id: $sid}) "
            "RETURN c.id AS id, c.index AS index, c.page_start AS page_start, "
            "c.page_end AS page_end, c.char_start AS char_start, c.char_end AS char_end, "
            "c.text AS text ORDER BY c.index",
            {"sid": source_id},
        )
        mentions = self._read(
            tenant_id,
            "MATCH (c:Chunk {source_id: $sid})-[m:MENTIONS]->(e:Entity) "
            "RETURN c.id AS chunk_id, e.id AS entity_id, e.display_name AS name, "
            "e.entity_type AS entity_type, e.aliases AS aliases, m.description AS description",
            {"sid": source_id},
        )
        facts = self._read(
            tenant_id,
            "MATCH (a:Entity)-[r]->(b:Entity) WHERE r.source_id = $sid "
            f"RETURN {_FACT_FIELDS}",
            {"sid": source_id},
        )
        entities: dict[str, dict] = {}
        per_chunk: dict[str, list[str]] = {}
        for m in mentions:
            e = entities.setdefault(
                m["entity_id"],
                {"id": m["entity_id"], "name": m["name"], "type": m["entity_type"],
                 "aliases": list(m.get("aliases") or []), "descriptions": [], "chunk_ids": []},
            )
            if m["chunk_id"] not in e["chunk_ids"]:
                e["chunk_ids"].append(m["chunk_id"])
            if m.get("description") and m["description"] not in e["descriptions"]:
                e["descriptions"].append(m["description"])
            per_chunk.setdefault(m["chunk_id"], []).append(m["entity_id"])
        relationships = [
            {"source": f["source_entity_id"], "source_name": f["source_name"],
             "relation": f["relation"], "target": f["target_entity_id"],
             "target_name": f["target_name"], "description": f.get("description"),
             "confidence": f.get("confidence"), "raw_relation": f.get("raw_relation"),
             "chunk_ids": list(f.get("chunk_ids") or [])}
            for f in facts
        ]
        return {
            "chunks": [
                {**c, "entity_ids": sorted(set(per_chunk.get(c["id"], [])))} for c in chunks
            ],
            "entities": list(entities.values()),
            "relationships": relationships,
        }

    # -- visualization -------------------------------------------------------------------

    def neighbourhood(
        self, tenant_id, entity_ids: list[str], source_ids: list[str], limit: int
    ) -> list[dict]:
        """One hop out from `entity_ids`, either direction, scope only."""
        return self.base.facts_touching(tenant_id, entity_ids, source_ids, limit)

    def stats(self, tenant_id, source_ids: list[str]) -> dict[str, int]:
        """What is indexed for this scope: how much of it Graph RAG can use."""
        out = {"entities": 0, "entities_embedded": 0, "facts": 0, "communities": 0}
        if not source_ids:
            return out
        try:
            row = self._read(
                tenant_id,
                "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.source_id IN $sids "
                "WITH DISTINCT e RETURN count(e) AS entities, "
                "sum(CASE WHEN e.embedding IS NULL THEN 0 ELSE 1 END) AS embedded",
                {"sids": source_ids},
            )
            if row:
                out["entities"] = int(row[0]["entities"] or 0)
                out["entities_embedded"] = int(row[0]["embedded"] or 0)
            out["facts"] = int(
                self._read(
                    tenant_id,
                    "MATCH (f:Fact) WHERE f.source_id IN $sids RETURN count(f) AS n",
                    {"sids": source_ids},
                )[0]["n"]
            )
            out["communities"] = int(
                self._read(tenant_id, "MATCH (c:Community) RETURN count(c) AS n")[0]["n"]
            )
        except ResponseError as exc:
            log.warning("graphrag stats failed: %s", exc)
        return out


__all__ = ["GraphRagStore", "fact_id", "fact_text"]


def get_rag_store() -> "GraphRagStore | None":
    """FastAPI dependency: None when FalkorDB is not configured. Tests override it."""
    from app.graph import store as graph_store

    base = graph_store.get_graph_store()
    if base is None:
        return None
    return _rag_store_for(base)


_rag_stores: dict[int, "GraphRagStore"] = {}


def _rag_store_for(base: FalkorGraphStore) -> "GraphRagStore":
    # One wrapper per base store, so the "indexes ensured" cache survives requests.
    store = _rag_stores.get(id(base))
    if store is None:
        store = _rag_stores[id(base)] = GraphRagStore(base)
    return store
