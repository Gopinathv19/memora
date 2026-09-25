"""Graph retrieval: a question, or a subject, -> a bounded, provenance-scoped subgraph.

Deterministic, with no LLM and no generated Cypher:

    question -> seed entities (exact key / alias match, then full-text search)
             -> breadth-first expansion, 1..max_hops, through fact edges whose
                source is in scope, bounded by max_entities / max_relationships
             -> entities + relationships + the chunks that support them

Scope is a list of source ids -- the sources of a subject (and its folders),
resolved and permission-checked in Postgres by the caller. Only facts from
those sources are ever returned, even though entity nodes are shared across
the tenant.
"""

import re

from redis.exceptions import ResponseError

from app.graph import store as graph_store
from app.graph.normalization import canonical_key
from app.graph.store import FalkorGraphStore
from app.schemas.graph import (
    GraphChunkResult,
    GraphEntityResult,
    GraphRelationshipResult,
    GraphRetrievalResult,
)

SEED_LIMIT = 5
MAX_CHUNKS = 20
MAX_ENTITY_CHUNKS = 20

_STOPWORDS = frozenset(
    """a an and are as at be been by can could did do does for from had has have how i in
    into is it its me my of on or our so than that the their them then there these they this
    to us was we were what when where which who whom whose why will with would you your
    about any all also between connected related tell show list give find much many
    please other others""".split()
)


def _tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[^\W_]+", query.casefold()) if len(t) > 1]


def query_keys(query: str) -> list[str]:
    """Candidate canonical keys: every 1-4 word phrase of the question."""
    words = _tokens(query)
    keys: list[str] = []
    for n in range(4, 0, -1):
        for i in range(len(words) - n + 1):
            phrase = words[i : i + n]
            if n == 1 and phrase[0] in _STOPWORDS:
                continue
            if phrase[0] in _STOPWORDS or phrase[-1] in _STOPWORDS:
                continue
            for key in {canonical_key(" ".join(phrase)), canonical_key(" ".join(phrase), "COMPANY")}:
                if key and key not in keys:
                    keys.append(key)
    return keys


def fulltext_query(query: str) -> str:
    """A safe full-text query: plain word tokens OR-ed together."""
    words = [t for t in _tokens(query) if t not in _STOPWORDS]
    return "|".join(dict.fromkeys(words))


def _merge_facts(rows: list[dict]) -> list[GraphRelationshipResult]:
    """Per-source fact edges -> one relationship with all its supporting chunks."""
    merged: dict[tuple, GraphRelationshipResult] = {}
    for row in rows:
        key = (row["source_entity_id"], row["relation"], row["target_entity_id"])
        fact = merged.get(key)
        if fact is None:
            fact = merged[key] = GraphRelationshipResult(
                source_entity_id=row["source_entity_id"],
                source_name=row["source_name"],
                relation=row["relation"],
                target_entity_id=row["target_entity_id"],
                target_name=row["target_name"],
                description=row.get("description"),
                confidence=row.get("confidence"),
                raw_relation=row.get("raw_relation"),
            )
        if row["source_id"] not in fact.source_ids:
            fact.source_ids.append(row["source_id"])
        for chunk in row.get("chunk_ids") or []:
            if chunk not in fact.source_chunk_ids:
                fact.source_chunk_ids.append(chunk)
        if not fact.description and row.get("description"):
            fact.description = row["description"]
        if row.get("confidence") is not None:
            fact.confidence = max(fact.confidence or 0.0, row["confidence"])
    return list(merged.values())


class GraphRetrievalService:
    def __init__(self, store: FalkorGraphStore):
        self.store = store

    def _entities(
        self, tenant_id, ids: list[str], source_ids: list[str], mentions: dict[str, int] | None = None
    ) -> list[GraphEntityResult]:
        rows = {r["id"]: r for r in self.store.entity_details(tenant_id, ids, source_ids)}
        results = []
        for eid in ids:
            row = rows.get(eid)
            if row is None:
                continue
            descriptions = [d for d in row.get("descriptions") or [] if d]
            chunk_ids = list(row.get("chunk_ids") or [])
            results.append(
                GraphEntityResult(
                    entity_id=eid,
                    name=row["name"],
                    entity_type=row["entity_type"],
                    description=descriptions[0] if descriptions else None,
                    aliases=list(row.get("aliases") or []),
                    mention_count=(mentions or {}).get(eid, len(chunk_ids)),
                    source_ids=list(row.get("source_ids") or []),
                    source_chunk_ids=chunk_ids[:MAX_ENTITY_CHUNKS],
                )
            )
        return results

    def seed_entities(self, tenant_id, query: str, source_ids: list[str], limit: int) -> list[str]:
        seeds: list[str] = []
        for row in self.store.find_entities_by_keys(tenant_id, query_keys(query), source_ids, limit):
            if row["id"] not in seeds:
                seeds.append(row["id"])
        text = fulltext_query(query)
        if text and len(seeds) < limit:
            try:
                rows = self.store.search_entities(tenant_id, text, source_ids, limit)
            except ResponseError:  # a query the full-text syntax rejects
                rows = []
            for row in rows:
                if row["id"] not in seeds:
                    seeds.append(row["id"])
        return seeds[:limit]

    def retrieve(
        self,
        *,
        tenant_id,
        source_ids: list[str],
        query: str,
        max_hops: int = 2,
        max_entities: int = 20,
        max_relationships: int = 50,
    ) -> GraphRetrievalResult:
        if not source_ids:
            return GraphRetrievalResult()
        seeds = self.seed_entities(
            tenant_id, query, source_ids, min(SEED_LIMIT, max_entities)
        )
        if not seeds:
            return GraphRetrievalResult()

        visited: list[str] = list(seeds)
        rows: list[dict] = []
        seen_edges: set[tuple] = set()
        frontier = list(seeds)
        for _ in range(max(1, max_hops)):
            budget = max_relationships - len(rows)
            if not frontier or budget <= 0:
                break
            next_frontier: list[str] = []
            for row in self.store.facts_touching(tenant_id, frontier, source_ids, budget):
                edge = (row["source_entity_id"], row["relation"], row["target_entity_id"], row["source_id"])
                if edge in seen_edges:
                    continue
                new = [
                    e for e in (row["source_entity_id"], row["target_entity_id"])
                    if e not in visited and e not in next_frontier
                ]
                if len(visited) + len(next_frontier) + len(new) > max_entities:
                    continue  # would exceed the entity budget
                seen_edges.add(edge)
                rows.append(row)
                next_frontier.extend(new)
            visited.extend(next_frontier)
            frontier = next_frontier

        relationships = _merge_facts(rows)
        entities = self._entities(tenant_id, visited, source_ids)

        chunk_ids: list[str] = []
        for group in [r.source_chunk_ids for r in relationships] + [
            e.source_chunk_ids for e in entities if e.entity_id in seeds
        ]:
            for chunk in group:
                if chunk not in chunk_ids:
                    chunk_ids.append(chunk)
        chunk_ids = chunk_ids[:MAX_CHUNKS]
        by_id = {c["id"]: c for c in self.store.chunks(tenant_id, chunk_ids, source_ids)}
        chunks = [
            GraphChunkResult(
                chunk_id=c["id"],
                source_id=c["source_id"],
                index=c["index"],
                page_start=c.get("page_start"),
                page_end=c.get("page_end"),
                char_start=c.get("char_start"),
                char_end=c.get("char_end"),
                text=c["text"],
            )
            for c in (by_id.get(cid) for cid in chunk_ids)
            if c is not None
        ]
        return GraphRetrievalResult(
            seed_entity_ids=seeds,
            entities=entities,
            relationships=relationships,
            source_chunk_ids=[c.chunk_id for c in chunks],
            chunks=chunks,
        )

    def view(
        self,
        *,
        tenant_id,
        source_ids: list[str],
        max_entities: int = 200,
        max_relationships: int = 500,
    ) -> GraphRetrievalResult:
        """The subject's graph for plotting: its most-mentioned entities and
        the facts between them. No chunk text."""
        if not source_ids:
            return GraphRetrievalResult()
        top = self.store.top_entities(tenant_id, source_ids, max_entities)
        ids = [row["id"] for row in top]
        mentions = {row["id"]: int(row["mentions"]) for row in top}
        relationships = _merge_facts(
            self.store.facts_among(tenant_id, ids, source_ids, max_relationships)
        )
        chunk_ids: list[str] = []
        for fact in relationships:
            for chunk in fact.source_chunk_ids:
                if chunk not in chunk_ids:
                    chunk_ids.append(chunk)
        return GraphRetrievalResult(
            entities=self._entities(tenant_id, ids, source_ids, mentions),
            relationships=relationships,
            source_chunk_ids=chunk_ids,
        )


def get_graph_retrieval() -> GraphRetrievalService | None:
    """FastAPI dependency: None when FalkorDB is not configured. Tests override it."""
    store = graph_store.get_graph_store()
    return GraphRetrievalService(store) if store is not None else None
