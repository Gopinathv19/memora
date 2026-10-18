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
# The vector index cannot be filtered, so a scoped search asks for more than it needs and
# filters in the same statement. When too few survive the scope (a small subject in a large
# tenant), the window is widened and the search repeated, up to this many index hits.
MAX_FETCH = 2000
FETCH_GROWTH = 4

_RANGE_INDEXES = (
    "CREATE INDEX FOR (f:Fact) ON (f.id)",
    "CREATE INDEX FOR (f:Fact) ON (f.source_id)",
    "CREATE INDEX FOR (c:Community) ON (c.id)",
    "CREATE INDEX FOR (c:Community) ON (c.subject_id)",
    "CREATE INDEX FOR (m:Merge) ON (m.id)",
    "CREATE INDEX FOR (f:Fact) ON (f.kind)",
)

# Lexical lookups: an exact term ("INV-1021") that an embedding blurs. Entity's index is
# created by the base store; this adds the atomic facts' text.
_FULLTEXT_INDEXES = (
    "CALL db.idx.fulltext.createNodeIndex('Fact', 'search_text')",
)

# (label, property) pairs that must be unique. FalkorDB builds the constraint
# asynchronously (status PENDING) and refuses it while duplicates exist, which
# is reported, not fatal: a graph that predates the constraint keeps working.
_UNIQUE = (("Entity", "id"), ("Chunk", "id"), ("Document", "id"), ("Fact", "id"), ("Community", "id"),
           ("Merge", "id"))


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
        if not getattr(base, "_graphrag_cleanup_installed", False):            # once per base store, however many wrappers
            base._graphrag_cleanup_installed = True
            base.before_delete_source.append(self.cleanup_before_delete)
            base.after_delete_source.append(self.cleanup_after_delete)

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
            statements = list(_RANGE_INDEXES) + list(_FULLTEXT_INDEXES)
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
            "MATCH (f:Fact) WHERE f.source_id IN $sids AND coalesce(f.kind, 'edge') = 'edge' "
            "RETURN f.id AS id",
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

    # -- the unified builder's writes ----------------------------------------------------
    #
    # Chunk nodes carry the retrieval chunk's own id, so a fact's evidence is a real
    # `retrieval_chunks` row and no text-overlap bridge is needed. An atomic fact is a
    # `Fact {kind: 'atom'}` linked to the chunks that state it (`SUPPORTED_BY`) and to the
    # entities it is about (`ABOUT`). Edge-facts (`kind` unset, `SUBJ`/`OBJ`) are untouched.

    def upsert_retrieval_chunks(self, tenant_id, source_id: str, rows: list[dict]) -> None:
        """rows: id, index, text, page_start, page_end, extraction_id, source_version, subject_id, section_path."""
        if not rows:
            return
        self._write(
            tenant_id,
            "MATCH (d:Document {id: $sid}) UNWIND $rows AS row "
            "MERGE (c:Chunk {id: row.id}) "
            "SET c.source_id = $sid, c.index = row.index, c.text = row.text, "
            "c.page_start = row.page_start, c.page_end = row.page_end, c.kind = 'retrieval', "
            "c.extraction_id = row.extraction_id, c.source_version = row.source_version, "
            "c.subject_id = row.subject_id, c.section_path = row.section_path "
            "MERGE (d)-[:CONTAINS]->(c)",
            {"sid": source_id, "rows": rows},
        )

    def upsert_atomic_facts(self, tenant_id, source_id: str, rows: list[dict], build_id: str | None = None) -> None:
        """rows: id, text, evidence, qualifiers (JSON string), entity_ids, chunk_ids."""
        if not rows:
            return
        params = {"sid": source_id, "rows": rows, "bid": build_id}
        self._write(
            tenant_id,
            "UNWIND $rows AS row MERGE (f:Fact {id: row.id}) "
            "SET f.kind = 'atom', f.source_id = $sid, f.text = row.text, f.search_text = row.text, "
            "f.evidence = row.evidence, f.qualifiers = row.qualifiers, f.build_id = $bid "
            "WITH f, row UNWIND row.chunk_ids AS cid "
            "MATCH (c:Chunk {id: cid}) MERGE (f)-[:SUPPORTED_BY]->(c)",
            params,
        )
        self._write(
            tenant_id,
            "UNWIND $rows AS row UNWIND row.entity_ids AS eid "
            "MATCH (f:Fact {id: row.id}) MATCH (e:Entity {id: eid}) MERGE (f)-[:ABOUT]->(e)",
            params,
        )

    def delete_atomic_facts(self, tenant_id, source_id: str) -> int:
        result = self._write(
            tenant_id,
            "MATCH (f:Fact) WHERE f.source_id = $sid AND f.kind = 'atom' DETACH DELETE f",
            {"sid": source_id},
        )
        return int(result.nodes_deleted)

    def upsert_merges(self, tenant_id, rows: list[dict]) -> None:
        """rows: id, merged_id, survivor_id, stage, score, note, source_ids. The record of a merge."""
        if not rows:
            return
        self._write(
            tenant_id,
            "UNWIND $rows AS row MERGE (m:Merge {id: row.id}) "
            "SET m.merged_id = row.merged_id, m.survivor_id = row.survivor_id, m.stage = row.stage, "
            "m.score = row.score, m.note = row.note, "
            "m.names = coalesce(m.names, []) + [n IN coalesce(row.names, []) WHERE NOT n IN coalesce(m.names, [])], "
            "m.keys = coalesce(m.keys, []) + [k IN coalesce(row.keys, []) WHERE NOT k IN coalesce(m.keys, [])], "
            "m.source_ids = coalesce(m.source_ids, []) + "
            "[s IN row.source_ids WHERE NOT s IN coalesce(m.source_ids, [])]",
            {"rows": rows},
        )

    def atoms_missing_embedding(self, tenant_id, source_ids: list[str], limit: int) -> list[dict]:
        if not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (f:Fact) WHERE f.kind = 'atom' AND f.source_id IN $sids AND f.embedding IS NULL "
            "RETURN f.id AS id, f.text AS text LIMIT $limit",
            {"sids": source_ids, "limit": limit},
        )

    def set_atom_embeddings(self, tenant_id, rows: list[dict]) -> None:
        if not rows:
            return
        self._write(
            tenant_id,
            "UNWIND $rows AS row MATCH (f:Fact {id: row.id}) SET f.embedding = vecf32(row.embedding)",
            {"rows": rows},
        )

    # -- vector search (similarity = 1 - cosine distance) --------------------------------

    def _vector(self, tenant_id, cypher: str, params: dict) -> list[dict]:
        try:
            return self._read(tenant_id, cypher, params)
        except ResponseError as exc:  # no index yet: nothing was ever embedded
            if "index" in str(exc).lower():
                return []
            raise

    def _vector_scoped(self, tenant_id, cypher: str, params: dict, limit: int) -> list[dict]:
        """A vector query whose `$k` is the index window: widened until `limit` rows survive the scope.

        Stops early when the window already covers more than the index holds, or at `MAX_FETCH`.
        """
        fetch = min(limit * OVERFETCH, MAX_FETCH)
        while True:
            rows = self._vector(tenant_id, cypher, {**params, "k": fetch})
            if len(rows) >= limit or fetch >= MAX_FETCH:
                return rows
            fetch = min(fetch * FETCH_GROWTH, MAX_FETCH)

    def vector_entities(
        self, tenant_id, vector: list[float], source_ids: list[str], k: int
    ) -> list[dict]:
        if not source_ids:
            return []
        return self._vector_scoped(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Entity', 'embedding', $k, vecf32($q)) "
            "YIELD node, score "
            "MATCH (c:Chunk)-[:MENTIONS]->(node) WHERE c.source_id IN $sids "
            "WITH node, score, count(DISTINCT c) AS mentions "
            "RETURN node.id AS id, 1.0 - score AS similarity, mentions "
            "ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "sids": source_ids, "limit": k}, k,
        )

    def vector_facts(
        self, tenant_id, vector: list[float], source_ids: list[str], k: int
    ) -> list[dict]:
        if not source_ids:
            return []
        return self._vector_scoped(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Fact', 'embedding', $k, vecf32($q)) "
            "YIELD node, score WHERE node.source_id IN $sids "
            "MATCH (node)-[:SUBJ]->(a:Entity) MATCH (node)-[:OBJ]->(b:Entity) "
            "RETURN node.id AS id, node.relation AS relation, node.text AS text, "
            "node.chunk_ids AS chunk_ids, node.confidence AS confidence, "
            "node.source_id AS source_id, a.id AS a_id, a.display_name AS a_name, "
            "b.id AS b_id, b.display_name AS b_name, 1.0 - score AS similarity "
            "ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "sids": source_ids, "limit": k}, k,
        )

    def vector_atoms(
        self, tenant_id, vector: list[float], source_ids: list[str], k: int
    ) -> list[dict]:
        """Atomic facts nearest the question, with the chunks that state them (`SUPPORTED_BY`)."""
        if not source_ids:
            return []
        return self._vector_scoped(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Fact', 'embedding', $k, vecf32($q)) "
            "YIELD node, score WHERE node.kind = 'atom' AND node.source_id IN $sids "
            "OPTIONAL MATCH (node)-[:SUPPORTED_BY]->(c:Chunk) "
            "WITH node, score, collect(DISTINCT c.id) AS chunk_ids "
            "OPTIONAL MATCH (node)-[:ABOUT]->(e:Entity) "
            "WITH node, score, chunk_ids, collect(DISTINCT e.id) AS entity_ids "
            "RETURN node.id AS id, node.text AS text, node.evidence AS evidence, "
            "node.qualifiers AS qualifiers, node.source_id AS source_id, chunk_ids, entity_ids, "
            "1.0 - score AS similarity ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "sids": source_ids, "limit": k}, k,
        )

    def vector_communities(
        self, tenant_id, vector: list[float], subject_ids: list[str], k: int
    ) -> list[dict]:
        if not subject_ids:
            return []
        return self._vector_scoped(
            tenant_id,
            "CALL db.idx.vector.queryNodes('Community', 'embedding', $k, vecf32($q)) "
            "YIELD node, score WHERE node.subject_id IN $subjects AND coalesce(node.stale, false) = false "
            "RETURN node.id AS id, node.title AS title, node.summary AS summary, "
            "node.size AS size, 1.0 - score AS similarity "
            "ORDER BY similarity DESC LIMIT $limit",
            {"q": vector, "subjects": subject_ids, "limit": k}, k,
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

    def chunk_entities(self, tenant_id, chunk_ids: list[str], source_ids: list[str]) -> list[dict]:
        """(chunk, entity) pairs for the given chunks, in scope."""
        if not chunk_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.id IN $cids AND c.source_id IN $sids "
            "RETURN c.id AS chunk_id, e.id AS entity_id",
            {"cids": chunk_ids, "sids": source_ids},
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
            "MATCH (c:Community) WHERE c.subject_id = $sid AND coalesce(c.kind, 'label') = 'label' "
            "DETACH DELETE c",
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

    # -- profiles, scope graph and hierarchical communities (plan Step 4) ---------------------

    def entity_profiles(self, tenant_id, entity_ids: list[str], source_ids: list[str], per_entity: int = 3) -> dict[str, dict]:
        """What the *scope's* documents say about each entity, assembled at query time.

        An entity node is shared by every source in the tenant, so nothing stored on it may
        carry document text. Descriptions come from `MENTIONS` edges of in-scope chunks only,
        so another subject's text never reaches this scope. (Names and aliases are tenant-wide:
        they are what links an entity across documents.)
        """
        if not entity_ids or not source_ids:
            return {}
        rows = self._read(
            tenant_id,
            "MATCH (e:Entity) WHERE e.id IN $ids "
            "OPTIONAL MATCH (c:Chunk)-[m:MENTIONS]->(e) WHERE c.source_id IN $sids AND m.description IS NOT NULL "
            "WITH e, collect(DISTINCT m.description) AS descs "
            "RETURN e.id AS id, e.display_name AS name, e.entity_type AS entity_type, "
            "e.aliases AS aliases, descs",
            {"ids": entity_ids, "sids": source_ids},
        )
        out = {}
        for r in rows:
            descs = sorted((d for d in (r.get("descs") or []) if d), key=lambda d: (-len(d), d))[:per_entity]
            out[r["id"]] = {"id": r["id"], "name": r["name"], "type": r["entity_type"],
                            "aliases": list(r.get("aliases") or [])[:5], "descriptions": descs}
        return out

    def atoms_about(self, tenant_id, entity_ids: list[str], source_ids: list[str], limit: int) -> list[dict]:
        """In-scope atomic facts about any of `entity_ids`, with how many of them each involves."""
        if not entity_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.kind = 'atom' AND f.source_id IN $sids AND e.id IN $ids "
            "WITH f, collect(e.id) AS about "
            "RETURN f.id AS id, f.text AS text, f.evidence AS evidence, f.qualifiers AS qualifiers, "
            "f.source_id AS source_id, about, size(about) AS involved "
            "ORDER BY involved DESC, text LIMIT $limit",
            {"ids": entity_ids, "sids": source_ids, "limit": limit},
        )

    def scope_entity_edges(self, tenant_id, source_ids: list[str]) -> list[dict]:
        """Typed entity-to-entity edges the scope's sources contributed, one row per pair."""
        if not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (a:Entity)-[r]->(b:Entity) WHERE r.source_id IN $sids AND a.id <> b.id "
            "RETURN a.id AS a, b.id AS b, count(r) AS n",
            {"sids": source_ids},
        )

    def scope_atom_entities(self, tenant_id, source_ids: list[str]) -> list[list[str]]:
        """For each in-scope atomic fact, the entities it is about (a fact about two entities links them)."""
        if not source_ids:
            return []
        rows = self._read(
            tenant_id,
            "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.kind = 'atom' AND f.source_id IN $sids "
            "RETURN f.id AS f, collect(e.id) AS es",
            {"sids": source_ids},
        )
        return [sorted(set(r["es"])) for r in rows]

    def hier_communities(self, tenant_id, subject_id: str) -> list[dict]:
        return self._read(
            tenant_id,
            "MATCH (c:Community) WHERE c.subject_id = $sid AND c.kind = 'hier' "
            "OPTIONAL MATCH (c)-[:HAS_MEMBER]->(e:Entity) "
            "RETURN c.id AS id, c.level AS level, c.parent_id AS parent_id, c.title AS title, "
            "c.summary AS summary, c.size AS size, c.summary_hash AS summary_hash, "
            "c.embedding AS embedding, coalesce(c.stale, false) AS stale, collect(e.id) AS members",
            {"sid": subject_id},
        )

    def replace_hier_communities(self, tenant_id, subject_id: str, rows: list[dict]) -> None:
        """Replace the subject's hierarchical communities. The old index build's communities stay."""
        self._write(
            tenant_id,
            "MATCH (c:Community) WHERE c.subject_id = $sid AND c.kind = 'hier' DETACH DELETE c",
            {"sid": subject_id},
        )
        if not rows:
            return
        self._write(
            tenant_id,
            "UNWIND $rows AS row MERGE (c:Community {id: row.id}) "
            "SET c.subject_id = $sid, c.kind = 'hier', c.level = row.level, c.parent_id = row.parent_id, "
            "c.size = row.size, c.title = row.title, c.summary = row.summary, c.summary_hash = row.summary_hash "
            "WITH c, row UNWIND row.members AS mid MATCH (e:Entity {id: mid}) MERGE (c)-[:HAS_MEMBER]->(e)",
            {"rows": rows, "sid": subject_id},
        )
        embedded = [{"id": r["id"], "embedding": r["embedding"]} for r in rows if r.get("embedding")]
        if embedded:
            self._write(
                tenant_id,
                "UNWIND $rows AS row MATCH (c:Community {id: row.id}) SET c.embedding = vecf32(row.embedding)",
                {"rows": embedded},
            )

    def set_community_summary(
        self, tenant_id, community_id: str, title: str, summary: str, summary_hash: str, embedding: list[float] | None
    ) -> None:
        self._write(
            tenant_id,
            "MATCH (c:Community {id: $id}) SET c.title = $title, c.summary = $summary, c.summary_hash = $hash",
            {"id": community_id, "title": title, "summary": summary, "hash": summary_hash},
        )
        if embedding:
            self._write(
                tenant_id, "MATCH (c:Community {id: $id}) SET c.embedding = vecf32($e)",
                {"id": community_id, "e": embedding},
            )

    # -- deleting a source: everything the unified build added for it ----------------------------------

    def cleanup_before_delete(self, tenant_id, source_id: str) -> dict[str, int]:
        """Runs before the base store removes a source (registered as its `before_delete_source` hook).

        * communities that contain an entity the source mentions are marked `stale`, so their summaries
          are not reused (the facts they were written from are about to disappear)
        * the source's atomic facts go
        * merges that only this source justified are unwound: the record goes and so do the aliases it
          added to the survivor, unless another merge still explains them
        """
        out = {"stale_communities": 0, "atomic_facts": 0, "merges_unwound": 0}
        marked = self._write(
            tenant_id,
            "MATCH (c:Community)-[:HAS_MEMBER]->(e:Entity)<-[:MENTIONS]-(ch:Chunk {source_id: $sid}) "
            "WHERE c.kind = 'hier' AND coalesce(c.stale, false) = false SET c.stale = true",
            {"sid": source_id},
        )
        out["stale_communities"] = int(marked.properties_set)
        out["atomic_facts"] = self.delete_atomic_facts(tenant_id, source_id)
        out["merges_unwound"] = self.unwind_merges(tenant_id, source_id)
        return out

    def cleanup_after_delete(self, tenant_id, source_id: str) -> None:
        """A community whose every member went with the source is an empty shell still carrying a summary of
        deleted content: remove it. (Communities that keep some members stay, marked stale.)"""
        self._write(
            tenant_id,
            "MATCH (c:Community) WHERE c.kind = 'hier' AND NOT (c)-[:HAS_MEMBER]->(:Entity) DETACH DELETE c",
        )

    def unwind_merges(self, tenant_id, source_id: str) -> int:
        """Drop `source_id` from the evidence of every merge it was in; remove the merges left with no evidence.

        Only merges this source was part of can be unwound: a merge that never had sources (two entities already in
        the graph) is left alone. Removals are combined per survivor before they are written, so two merges into one
        entity do not depend on how the database orders a multi-row update. Returns how many merges were removed.
        """
        touched = [r["id"] for r in self._read(
            tenant_id, "MATCH (m:Merge) WHERE $sid IN m.source_ids RETURN m.id AS id", {"sid": source_id})]
        if not touched:
            return 0
        self._write(
            tenant_id,
            "MATCH (m:Merge) WHERE m.id IN $ids SET m.source_ids = [s IN m.source_ids WHERE s <> $sid]",
            {"ids": touched, "sid": source_id},
        )
        dead = self._read(
            tenant_id,
            "MATCH (m:Merge) WHERE m.id IN $ids AND size(coalesce(m.source_ids, [])) = 0 "
            "RETURN m.id AS id, m.survivor_id AS survivor, coalesce(m.names, []) AS names, coalesce(m.keys, []) AS keys",
            {"ids": touched},
        )
        if not dead:
            return 0
        # what the merges that still have evidence keep explaining, per survivor
        alive = self._read(
            tenant_id,
            "MATCH (m:Merge) WHERE size(coalesce(m.source_ids, [])) > 0 "
            "RETURN m.survivor_id AS survivor, coalesce(m.names, []) AS names, coalesce(m.keys, []) AS keys",
        )
        kept_names: dict[str, set[str]] = {}
        kept_keys: dict[str, set[str]] = {}
        for a in alive:
            kept_names.setdefault(a["survivor"], set()).update(a["names"])
            kept_keys.setdefault(a["survivor"], set()).update(a["keys"])
        gone_names: dict[str, set[str]] = {}
        gone_keys: dict[str, set[str]] = {}
        for d in dead:
            gone_names.setdefault(d["survivor"], set()).update(d["names"])
            gone_keys.setdefault(d["survivor"], set()).update(d["keys"])
        rows = [{"survivor": sv, "names": sorted(gone_names[sv] - kept_names.get(sv, set())),
                 "keys": sorted(gone_keys[sv] - kept_keys.get(sv, set()))} for sv in gone_names]
        self._write(
            tenant_id,
            "UNWIND $rows AS row MATCH (e:Entity {id: row.survivor}) "
            "WITH e, row, [a IN coalesce(e.aliases, []) WHERE NOT a IN row.names] AS kept "
            # the merged name may have become the display name (the commoner or alphabetically first of the two):
            # fall back to a name that a remaining source still uses
            "WITH e, row, kept, CASE WHEN e.display_name IN row.names AND size(kept) > 0 THEN head(kept) "
            "ELSE e.display_name END AS name "
            "SET e.display_name = name, e.aliases = [a IN kept WHERE a <> name], "
            "e.alias_keys = [k IN coalesce(e.alias_keys, []) WHERE NOT k IN row.keys OR k = e.canonical_key], "
            "e.search_text = reduce(s = name, a IN [a IN kept WHERE a <> name] | s + ' ' + a)",
            {"rows": rows},
        )
        dead_ids = sorted({d["id"] for d in dead})
        self._write(tenant_id, "MATCH (m:Merge) WHERE m.id IN $ids DELETE m", {"ids": dead_ids})
        return len(dead_ids)

    def set_document_state(self, tenant_id, source_id: str, signature: str, fingerprint: str) -> None:
        """What built this document. `signature`: the versions (schema, prompts, ontology, judge): a change marks it
        outdated. `fingerprint`: hash of its active chunk ids: a change (new extraction, re-chunk) marks it stale."""
        self._write(
            tenant_id, "MATCH (d:Document {id: $sid}) SET d.signature = $sig, d.chunk_fingerprint = $fp",
            {"sid": source_id, "sig": signature, "fp": fingerprint},
        )

    def documents_state(self, tenant_id) -> list[dict]:
        return self._read(
            tenant_id,
            "MATCH (d:Document) RETURN d.id AS id, d.subject_id AS subject_id, d.extraction_id AS extraction_id, "
            "d.signature AS signature, d.chunk_fingerprint AS chunk_fingerprint",
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
