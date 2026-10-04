"""Graph ingestion: one extraction's content -> facts in the tenant graph.

    content -> chunking -> LLM extraction (per chunk, in parallel)
            -> validation -> normalization + resolution -> FalkorDB

Every stage is its own module with its own interface; this one only sequences
them. It touches no Postgres table -- `services/graph_service.py` records the
build, the same split as between the Extraction Agent and its service.

Failure rules:
* A chunk whose model call fails is recorded (index, id, error) and the rest
  carry on; the build ends `partial` and those chunks can be retried alone.
* If *every* chunk fails, nothing is written, so a failed rebuild never wipes
  the graph a source already had.
* A full build replaces the source's facts (one extraction version live per
  source); a retry adds to them.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache

from app.agents.extraction_agent import UsageRecord
from app.core.config import Settings, get_settings
from app.graph.chunking import chunk_content
from app.graph.extraction import ChunkExtraction, GraphExtractor
from app.graph.models import KnownEntity, ResolvedGraph
from app.graph.ontology import Ontology, get_ontology
from app.graph.resolution import resolve_graph
from app.graph import store as graph_store
from app.graph.store import FalkorGraphStore
from app.llm.client import get_llm_client
from app.schemas.enums import GraphBuildStatus

log = logging.getLogger(__name__)


@dataclass
class GraphIngestionResult:
    status: GraphBuildStatus
    chunk_count: int = 0
    failed_chunks: list[dict] = field(default_factory=list)
    entity_count: int = 0
    relationship_count: int = 0
    stats: dict = field(default_factory=dict)
    usage: list[UsageRecord] = field(default_factory=list)
    error: str | None = None


def _failure(unit, error: str) -> dict:
    return {
        "index": unit.index,
        "chunk_id": unit.id,
        "page_start": unit.page_start,
        "page_end": unit.page_end,
        "error": error[:500],
    }


class GraphIngestionService:
    def __init__(
        self,
        store: FalkorGraphStore,
        extractor: GraphExtractor,
        settings: Settings | None = None,
        ontology: Ontology | None = None,
    ):
        self.store = store
        self.extractor = extractor
        self.settings = settings or get_settings()
        self.ontology = ontology or get_ontology()

    def ingest_document(
        self,
        *,
        tenant_id: str,
        application_id: str,
        subject_id: str,
        source_id: str,
        extraction_id: str,
        extraction_version: int,
        build_id: str,
        content: str,
        filename: str | None = None,
        chunk_chars: int | None = None,
        chunk_overlap: int | None = None,
        only_chunks: set[int] | None = None,
    ) -> GraphIngestionResult:
        s = self.settings
        units = chunk_content(
            content,
            source_id=source_id,
            version=extraction_version,
            chunk_chars=chunk_chars or s.graph_chunk_chars,
            overlap=s.graph_chunk_overlap if chunk_overlap is None else chunk_overlap,
        )
        if not units:
            return GraphIngestionResult(
                GraphBuildStatus.FAILED, error="The extraction has no text to build a graph from"
            )
        selected = units if only_chunks is None else [u for u in units if u.index in only_chunks]
        if not selected:
            return GraphIngestionResult(
                GraphBuildStatus.FAILED, error="None of the chunks to retry exist any more"
            )
        limit = max(1, s.graph_max_chunks)
        to_read, over = selected[:limit], selected[limit:]
        failed = [
            _failure(u, f"not read: over the GRAPH_MAX_CHUNKS limit of {limit}") for u in over
        ]

        log.info(
            "graph build %s: source %s, extraction v%s, %d chunk(s) to read (%d over limit)",
            build_id, source_id, extraction_version, len(to_read), len(over),
        )
        with ThreadPoolExecutor(max_workers=max(1, s.graph_concurrency)) as pool:
            readings: list[ChunkExtraction] = list(pool.map(self.extractor.extract, to_read))
        usage = [r.usage for r in readings]
        ok = [r for r in readings if r.ok]
        for r in readings:
            if not r.ok:
                log.warning("graph build %s: chunk %s failed: %s", build_id, r.unit.id, r.error)
                failed.append(_failure(r.unit, r.error or "extraction failed"))
        failed.sort(key=lambda f: f["index"])

        base_stats = {
            "chunks_total": len(units),
            "chunks_processed": len(ok),
            "extraction_failures": len(failed),
        }
        if not ok:
            first = failed[0]["error"] if failed else "no chunk could be read"
            return GraphIngestionResult(
                GraphBuildStatus.FAILED,
                chunk_count=len(selected),
                failed_chunks=failed,
                stats=base_stats,
                usage=usage,
                error=f"Every chunk failed; nothing was written. First error: {first}",
            )

        resolved = self._write(
            tenant_id=tenant_id,
            document={
                "id": source_id,
                "application_id": application_id,
                "subject_id": subject_id,
                "filename": filename,
                "extraction_id": extraction_id,
                "extraction_version": extraction_version,
                "build_id": build_id,
            },
            readings=ok,
            build_id=build_id,
            replace=only_chunks is None,
        )
        stats = {**base_stats, **resolved.stats}
        log.info(
            "graph build %s: %d entities (%d created, %d matched), %d relationships written",
            build_id, len(resolved.entities), stats["entities_created"],
            stats["entities_matched"], len(resolved.relationships),
        )
        return GraphIngestionResult(
            GraphBuildStatus.PARTIAL if failed else GraphBuildStatus.COMPLETED,
            chunk_count=len(selected),
            failed_chunks=failed,
            entity_count=len(resolved.entities),
            relationship_count=len(resolved.relationships),
            stats=stats,
            usage=usage,
        )

    def _write(
        self,
        *,
        tenant_id: str,
        document: dict,
        readings: list[ChunkExtraction],
        build_id: str,
        replace: bool,
    ) -> ResolvedGraph:
        store, source_id = self.store, document["id"]
        if replace:
            removed = store.delete_source(tenant_id, source_id)
            log.info("graph build %s: replaced previous facts %s", build_id, removed)

        store.upsert_document(tenant_id, document)
        store.upsert_chunks(
            tenant_id,
            source_id,
            [
                {
                    "id": r.unit.id,
                    "index": r.unit.index,
                    "text": r.unit.text,
                    "char_start": r.unit.char_start,
                    "char_end": r.unit.char_end,
                    "page_start": r.unit.page_start,
                    "page_end": r.unit.page_end,
                }
                for r in readings
            ],
        )

        types = {self.ontology.map_entity_type(e.type)[0] for r in readings for e in r.extraction.entities}
        for r in readings:
            for rel in r.extraction.relationships:
                types.add(self.ontology.map_entity_type(rel.source_type)[0])
                types.add(self.ontology.map_entity_type(rel.target_type)[0])
        known = [
            KnownEntity(
                id=row["id"],
                entity_type=row["entity_type"],
                canonical_key=row["canonical_key"],
                display_name=row["display_name"],
                alias_keys=list(row.get("alias_keys") or []),
            )
            for row in store.load_entities(tenant_id, sorted(types))
        ]
        resolved = resolve_graph(
            [(r.unit, r.extraction) for r in readings],
            tenant_id=tenant_id,
            known=known,
            ontology=self.ontology,
            fuzzy_threshold=self.settings.graph_fuzzy_threshold,
        )

        store.upsert_entities(
            tenant_id,
            [
                {
                    "id": e.id,
                    "entity_type": e.entity_type,
                    "canonical_key": e.canonical_key,
                    "display_name": e.display_name,
                    "aliases": sorted(e.aliases),
                    "alias_keys": sorted(e.alias_keys),
                    "raw_types": sorted(e.raw_types),
                }
                for e in resolved.entities
            ],
        )
        store.upsert_mentions(
            tenant_id,
            source_id,
            [
                {"chunk_id": m.chunk_id, "entity_id": m.entity_id, "description": m.description}
                for m in resolved.mentions
            ],
        )
        by_relation: dict[str, list[dict]] = {}
        for fact in resolved.relationships:
            by_relation.setdefault(fact.relation, []).append(
                {
                    "source": fact.source_entity_id,
                    "target": fact.target_entity_id,
                    "chunk_ids": fact.chunk_ids,
                    "description": fact.description,
                    "confidence": fact.confidence,
                    "raw_relation": fact.raw_relation,
                    "build_id": build_id,
                }
            )
        for relation, facts in sorted(by_relation.items()):
            store.upsert_relationships(tenant_id, source_id, relation, facts)
        return resolved


@lru_cache
def get_graph_extractor() -> GraphExtractor:
    return GraphExtractor(get_llm_client(), get_settings(), get_ontology())


def get_graph_ingestion() -> GraphIngestionService | None:
    """FastAPI dependency: None when FalkorDB is not configured. Tests override it."""
    store = graph_store.get_graph_store()
    if store is None:
        return None
    return GraphIngestionService(store, get_graph_extractor(), get_settings(), get_ontology())
