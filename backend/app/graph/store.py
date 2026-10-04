"""FalkorDB: the one module that imports the client or writes Cypher.

**One graph per tenant**, named `{FALKORDB_GRAPH_PREFIX}_tenant_{tenant uuid hex}`
-- built only from Memora's own ids, never from anything a client typed. A
query can only ever touch the graph it is run against, so tenants are
separated physically. Inside a tenant graph:

    (:Document {id: source_id, subject_id, application_id, ...})
        -[:CONTAINS]->
    (:Chunk {id, source_id, index, page_start, page_end, char_start, char_end, text})
        -[:MENTIONS {source_id, description}]->
    (:Entity {id, entity_type, canonical_key, display_name, aliases, alias_keys})

    (:Entity)-[:WORKS_FOR {source_id, chunk_ids, description, confidence}]->(:Entity)

Entities are shared by every source in the tenant; everything that says
something about them (MENTIONS, fact edges) carries the `source_id` it came
from. That provenance is how a subject's view is filtered, and how deleting or
rebuilding one source removes exactly its own facts. There is one fact edge per
(source entity, type, target entity, source), holding all its chunk ids.

Every value is a query parameter. The one thing that cannot be -- a fact's
relationship type -- is checked against the ontology whitelist first.
Every read method takes the tenant *and* the in-scope source ids, so there is
no way to ask this layer an unscoped question.
"""

import logging
import re
import threading
import uuid
from functools import lru_cache
from typing import Any

from falkordb import FalkorDB
from redis.exceptions import ResponseError

from app.core.config import get_settings
from app.graph.ontology import Ontology, get_ontology

log = logging.getLogger(__name__)

_PREFIX = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_TENANT_GRAPH = "_tenant_"

_INDEXES = (
    "CREATE INDEX FOR (e:Entity) ON (e.id)",
    "CREATE INDEX FOR (e:Entity) ON (e.canonical_key)",
    "CREATE INDEX FOR (e:Entity) ON (e.entity_type)",
    "CREATE INDEX FOR (c:Chunk) ON (c.id)",
    "CREATE INDEX FOR (c:Chunk) ON (c.source_id)",
    "CREATE INDEX FOR (d:Document) ON (d.id)",
    "CALL db.idx.fulltext.createNodeIndex('Entity', 'search_text')",
)

_FACT_FIELDS = (
    "a.id AS source_entity_id, a.display_name AS source_name, type(r) AS relation, "
    "b.id AS target_entity_id, b.display_name AS target_name, r.source_id AS source_id, "
    "r.chunk_ids AS chunk_ids, r.description AS description, "
    "r.confidence AS confidence, r.raw_relation AS raw_relation"
)


def _rows(result) -> list[dict[str, Any]]:
    header = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
    return [dict(zip(header, row)) for row in result.result_set]


class FalkorGraphStore:
    def __init__(
        self,
        db: FalkorDB | None = None,
        *,
        connect=None,
        prefix: str = "memora",
        ontology: Ontology | None = None,
        timeout_ms: int | None = None,
    ):
        """Pass a connected `db` (tests), or a `connect` callable that makes
        one. With `connect`, nothing touches the network until the first
        query: the FalkorDB client connects inside its constructor, so an
        unreachable server must not stall requests that never use the graph."""
        if not _PREFIX.fullmatch(prefix):
            raise ValueError("FALKORDB_GRAPH_PREFIX may only contain letters, digits and _")
        if db is None and connect is None:
            raise ValueError("FalkorGraphStore needs a db or a connect callable")
        self._db = db
        self._connect = connect
        self.prefix = prefix
        self.ontology = ontology or get_ontology()
        self.timeout_ms = timeout_ms
        self._indexed: set[str] = set()
        self._lock = threading.Lock()
        self._connect_lock = threading.Lock()

    @property
    def db(self) -> FalkorDB:
        if self._db is None:
            with self._connect_lock:
                if self._db is None:
                    # Raises (e.g. redis TimeoutError) on each use while the
                    # server is unreachable, and connects once it is back.
                    self._db = self._connect()
        return self._db

    @classmethod
    def from_settings(cls, settings=None) -> "FalkorGraphStore":
        settings = settings or get_settings()

        def connect() -> FalkorDB:
            return FalkorDB.from_url(
                settings.falkordb_url,
                socket_timeout=settings.falkordb_timeout_ms / 1000,
                socket_connect_timeout=10,
                health_check_interval=30,
            )

        return cls(
            connect=connect,
            prefix=settings.falkordb_graph_prefix,
            timeout_ms=settings.falkordb_timeout_ms,
        )

    # -- graphs --------------------------------------------------------------------

    def graph_name(self, tenant_id) -> str:
        return f"{self.prefix}{_TENANT_GRAPH}{uuid.UUID(str(tenant_id)).hex}"

    def _graph(self, tenant_id):
        name = self.graph_name(tenant_id)
        graph = self.db.select_graph(name)
        if name not in self._indexed:
            with self._lock:
                if name not in self._indexed:
                    for statement in _INDEXES:
                        try:
                            graph.query(statement)
                        except ResponseError as exc:  # already exists
                            if "already" not in str(exc).lower():
                                raise
                    self._indexed.add(name)
        return graph

    def _query(self, tenant_id, cypher: str, params: dict | None = None):
        return self._graph(tenant_id).query(cypher, params or {}, timeout=self.timeout_ms)

    def _read(self, tenant_id, cypher: str, params: dict | None = None) -> list[dict]:
        graph = self._graph(tenant_id)
        return _rows(graph.ro_query(cypher, params or {}, timeout=self.timeout_ms))

    def tenant_ids(self) -> list[uuid.UUID]:
        """Every tenant that has a graph under this prefix."""
        marker = f"{self.prefix}{_TENANT_GRAPH}"
        found = []
        for name in self.db.list_graphs():
            if name.startswith(marker):
                try:
                    found.append(uuid.UUID(hex=name[len(marker) :]))
                except ValueError:
                    continue
        return found

    def drop_tenant_graph(self, tenant_id) -> None:
        name = self.graph_name(tenant_id)
        try:
            self.db.select_graph(name).delete()
        except ResponseError:
            pass  # no such graph
        self._indexed.discard(name)

    # -- writing -------------------------------------------------------------------

    def upsert_document(self, tenant_id, document: dict) -> None:
        self._query(
            tenant_id,
            "MERGE (d:Document {id: $id}) "
            "SET d.tenant_id = $tenant_id, d.application_id = $application_id, "
            "d.subject_id = $subject_id, d.filename = $filename, "
            "d.extraction_id = $extraction_id, d.extraction_version = $extraction_version, "
            "d.build_id = $build_id",
            {"tenant_id": str(tenant_id), **document},
        )

    def upsert_chunks(self, tenant_id, source_id: str, chunks: list[dict]) -> None:
        if not chunks:
            return
        self._query(
            tenant_id,
            "MATCH (d:Document {id: $source_id}) "
            "UNWIND $rows AS row "
            "MERGE (c:Chunk {id: row.id}) "
            "SET c.source_id = $source_id, c.index = row.index, c.text = row.text, "
            "c.char_start = row.char_start, c.char_end = row.char_end, "
            "c.page_start = row.page_start, c.page_end = row.page_end "
            "MERGE (d)-[:CONTAINS]->(c)",
            {"source_id": source_id, "rows": chunks},
        )

    def load_entities(self, tenant_id, entity_types: list[str]) -> list[dict]:
        """What resolution matches new names against."""
        if not entity_types:
            return []
        return self._read(
            tenant_id,
            "MATCH (e:Entity) WHERE e.entity_type IN $types "
            "RETURN e.id AS id, e.entity_type AS entity_type, "
            "e.canonical_key AS canonical_key, e.display_name AS display_name, "
            "e.alias_keys AS alias_keys",
            {"types": entity_types},
        )

    def upsert_entities(self, tenant_id, entities: list[dict]) -> None:
        if not entities:
            return
        self._query(
            tenant_id,
            "UNWIND $rows AS row "
            "MERGE (e:Entity {id: row.id}) "
            "ON CREATE SET e.tenant_id = $tenant_id, e.entity_type = row.entity_type, "
            "e.canonical_key = row.canonical_key, e.display_name = row.display_name, "
            "e.aliases = [], e.alias_keys = [], e.raw_types = [] "
            "SET e.aliases = e.aliases + [a IN row.aliases WHERE NOT a IN e.aliases] "
            "SET e.alias_keys = e.alias_keys + [k IN row.alias_keys WHERE NOT k IN e.alias_keys] "
            "SET e.raw_types = e.raw_types + [t IN row.raw_types WHERE NOT t IN e.raw_types] "
            "SET e.search_text = reduce(s = e.display_name, a IN e.aliases | s + ' ' + a)",
            {"tenant_id": str(tenant_id), "rows": entities},
        )

    def upsert_mentions(self, tenant_id, source_id: str, mentions: list[dict]) -> None:
        if not mentions:
            return
        self._query(
            tenant_id,
            "UNWIND $rows AS row "
            "MATCH (c:Chunk {id: row.chunk_id}) "
            "MATCH (e:Entity {id: row.entity_id}) "
            "MERGE (c)-[m:MENTIONS]->(e) "
            "SET m.source_id = $source_id, "
            "m.description = coalesce(row.description, m.description)",
            {"source_id": source_id, "rows": mentions},
        )

    def upsert_relationships(
        self, tenant_id, source_id: str, relation: str, facts: list[dict]
    ) -> None:
        """MERGE one fact edge per (a, relation, b, source); chunk ids accumulate."""
        if not facts:
            return
        if not self.ontology.is_relation(relation):
            raise ValueError(f"relation {relation!r} is not in the ontology")
        self._query(
            tenant_id,
            "UNWIND $rows AS row "
            "MATCH (a:Entity {id: row.source}) "
            "MATCH (b:Entity {id: row.target}) "
            f"MERGE (a)-[r:{relation} {{source_id: $source_id}}]->(b) "
            "SET r.chunk_ids = coalesce(r.chunk_ids, []) + "
            "[x IN row.chunk_ids WHERE NOT x IN coalesce(r.chunk_ids, [])] "
            "SET r.description = coalesce(r.description, row.description), "
            "r.raw_relation = coalesce(r.raw_relation, row.raw_relation), "
            "r.build_id = $build_id, "
            "r.confidence = CASE WHEN r.confidence IS NULL OR row.confidence > r.confidence "
            "THEN row.confidence ELSE r.confidence END",
            {"source_id": source_id, "rows": facts, "build_id": facts[0].get("build_id")},
        )

    def delete_source(self, tenant_id, source_id: str) -> dict[str, int]:
        """Remove one source's document, chunks and facts from the tenant graph.

        Entities it mentioned survive while any other source still mentions
        them; the ones it alone supported are removed.
        """
        touched = [
            row["id"]
            for row in self._read(
                tenant_id,
                "MATCH (c:Chunk {source_id: $sid})-[:MENTIONS]->(e:Entity) "
                "RETURN DISTINCT e.id AS id",
                {"sid": source_id},
            )
        ]
        stats = {"relationships": 0, "chunks": 0, "entities": 0}
        if touched:
            result = self._query(
                tenant_id,
                "MATCH (a:Entity)-[r]->(:Entity) WHERE a.id IN $ids AND r.source_id = $sid "
                "DELETE r",
                {"ids": touched, "sid": source_id},
            )
            stats["relationships"] = int(result.relationships_deleted)
        result = self._query(
            tenant_id, "MATCH (c:Chunk {source_id: $sid}) DETACH DELETE c", {"sid": source_id}
        )
        stats["chunks"] = int(result.nodes_deleted)
        self._query(tenant_id, "MATCH (d:Document {id: $sid}) DETACH DELETE d", {"sid": source_id})
        if touched:
            result = self._query(
                tenant_id,
                "MATCH (e:Entity) WHERE e.id IN $ids AND NOT (e)<-[:MENTIONS]-(:Chunk) "
                "DETACH DELETE e",
                {"ids": touched},
            )
            stats["entities"] = int(result.nodes_deleted)
        return stats

    def set_document_subject(self, tenant_id, source_ids: list[str], subject_id: str) -> None:
        self._query(
            tenant_id,
            "MATCH (d:Document) WHERE d.id IN $ids SET d.subject_id = $subject_id",
            {"ids": source_ids, "subject_id": subject_id},
        )

    def document_ids(self, tenant_id) -> list[str]:
        return [
            row["id"]
            for row in self._read(tenant_id, "MATCH (d:Document) RETURN d.id AS id")
        ]

    # -- reading (always scoped to source ids) ------------------------------------------

    def find_entities_by_keys(
        self, tenant_id, keys: list[str], source_ids: list[str], limit: int
    ) -> list[dict]:
        if not keys or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (e:Entity) "
            "WHERE e.canonical_key IN $keys OR any(k IN e.alias_keys WHERE k IN $keys) "
            "MATCH (c:Chunk)-[:MENTIONS]->(e) WHERE c.source_id IN $sids "
            "RETURN e.id AS id, count(DISTINCT c) AS mentions "
            "ORDER BY mentions DESC LIMIT $limit",
            {"keys": keys, "sids": source_ids, "limit": limit},
        )

    def search_entities(
        self, tenant_id, text_query: str, source_ids: list[str], limit: int
    ) -> list[dict]:
        """Full-text search over entity names and aliases, inside the scope."""
        if not text_query or not source_ids:
            return []
        return self._read(
            tenant_id,
            "CALL db.idx.fulltext.queryNodes('Entity', $q) YIELD node, score "
            "MATCH (c:Chunk)-[:MENTIONS]->(node) WHERE c.source_id IN $sids "
            "WITH node, score, count(DISTINCT c) AS mentions "
            "RETURN node.id AS id, score, mentions "
            "ORDER BY score DESC, mentions DESC LIMIT $limit",
            {"q": text_query, "sids": source_ids, "limit": limit},
        )

    def facts_touching(
        self, tenant_id, entity_ids: list[str], source_ids: list[str], limit: int
    ) -> list[dict]:
        """One hop, either direction, from any of `entity_ids`."""
        if not entity_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE (a.id IN $ids OR b.id IN $ids) AND r.source_id IN $sids "
            f"RETURN {_FACT_FIELDS} "
            "ORDER BY coalesce(r.confidence, 0) DESC LIMIT $limit",
            {"ids": entity_ids, "sids": source_ids, "limit": limit},
        )

    def facts_among(
        self, tenant_id, entity_ids: list[str], source_ids: list[str], limit: int
    ) -> list[dict]:
        if not entity_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.id IN $ids AND b.id IN $ids AND r.source_id IN $sids "
            f"RETURN {_FACT_FIELDS} "
            "ORDER BY coalesce(r.confidence, 0) DESC LIMIT $limit",
            {"ids": entity_ids, "sids": source_ids, "limit": limit},
        )

    def top_entities(self, tenant_id, source_ids: list[str], limit: int) -> list[dict]:
        """The most-mentioned entities in the scope: a subject's graph view."""
        if not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.source_id IN $sids "
            "WITH e, count(DISTINCT c) AS mentions "
            "RETURN e.id AS id, mentions "
            "ORDER BY mentions DESC, e.display_name LIMIT $limit",
            {"sids": source_ids, "limit": limit},
        )

    def entity_details(
        self, tenant_id, entity_ids: list[str], source_ids: list[str]
    ) -> list[dict]:
        """Entities with their in-scope chunks and descriptions only."""
        if not entity_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (e:Entity) WHERE e.id IN $ids "
            "MATCH (c:Chunk)-[m:MENTIONS]->(e) WHERE c.source_id IN $sids "
            "RETURN e.id AS id, e.display_name AS name, e.entity_type AS entity_type, "
            "e.aliases AS aliases, collect(DISTINCT c.id) AS chunk_ids, "
            "collect(DISTINCT c.source_id) AS source_ids, "
            "collect(m.description) AS descriptions",
            {"ids": entity_ids, "sids": source_ids},
        )

    def chunks(self, tenant_id, chunk_ids: list[str], source_ids: list[str]) -> list[dict]:
        if not chunk_ids or not source_ids:
            return []
        return self._read(
            tenant_id,
            "MATCH (c:Chunk) WHERE c.id IN $ids AND c.source_id IN $sids "
            "RETURN c.id AS id, c.source_id AS source_id, c.index AS index, "
            "c.page_start AS page_start, c.page_end AS page_end, "
            "c.char_start AS char_start, c.char_end AS char_end, c.text AS text",
            {"ids": chunk_ids, "sids": source_ids},
        )


@lru_cache
def get_graph_store() -> FalkorGraphStore | None:
    """The configured store, or None when `FALKORDB_URL` is not set.

    Callers go through this module (`store.get_graph_store()`), so tests can
    swap in a store connected to a throwaway database.
    """
    settings = get_settings()
    if not settings.falkordb_url:
        return None
    return FalkorGraphStore.from_settings(settings)
