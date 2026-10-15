"""Graph RAG index builds that run in the background, and their progress.

Two things start one: the "Build graph index" button
(`POST /subjects/{id}/graph-rag/index`) and a "process fully" upload, which chains
the index build after the knowledge-graph build
(`graph_service.build_after_extraction(..., index_graph_rag=True)`). Both go
through `run_index`, so they report progress the same way: `BUILDS`, per subject,
read by `GET /subjects/{id}/graph-rag/status`. It is per process (the index itself
lives in FalkorDB, so a restart forgets only the summary of the last build).
"""

import logging
import threading
import uuid

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Source
from app.graphrag import build
from app.graphrag.store import GraphRagStore, get_rag_store

log = logging.getLogger(__name__)

BUILDS: dict[str, dict] = {}
_lock = threading.Lock()


def claim(subject_id) -> bool:
    """Mark a subject's index build as running; False if one already is."""
    key = str(subject_id)
    with _lock:
        if BUILDS.get(key, {}).get("status") == "running":
            return False
        BUILDS[key] = {"status": "running"}
        return True


def run_index(
    rag: GraphRagStore, tenant_id, subject_id, source_ids: list[str], cfg, *, communities: bool = True
) -> None:
    """Build the index and record the outcome. The caller must have `claim`ed the
    subject. Never raises."""
    from app.graphrag.pipelines import llm_cost

    key = str(subject_id)
    try:
        stats = build.build_index(rag, tenant_id, subject_id, source_ids, cfg, communities=communities)
        stats["llm_cost_usd"] = round(
            sum(llm_cost(u["model"], u["prompt"], u["completion"]) for u in stats["llm_usage"]), 6
        )
        BUILDS[key] = {"status": "done", **stats}
    except Exception as exc:  # noqa: BLE001 - recorded, never raised into a background task
        log.exception("graph-rag index build failed for subject %s", subject_id)
        BUILDS[key] = {"status": "failed", "error": str(exc)[:500]}


def index_subject_for_source(source_id: uuid.UUID) -> None:
    """Index the subject a source belongs to, after that source's graph was built.

    Idempotent and incremental like every index build: only what is new is
    embedded. Covers the subject's folders too, like the Graph RAG pages do, and
    only sources that exist in the graph (a source whose build failed or that was
    never built has nothing to index). Never raises.
    """
    from app.services import embedding_service
    from app.services.subject_service import _descendant_ids

    rag = get_rag_store()
    if rag is None:
        log.warning("graph-rag index requested but FalkorDB is not configured; skipped")
        return
    try:
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return
            subject_id, tenant_id = source.subject_id, source.tenant_id
            subjects = _descendant_ids(db, subject_id)
            ids = [
                str(s)
                for s in db.execute(select(Source.id).where(Source.subject_id.in_(subjects))).scalars()
            ]
            cfg = embedding_service.prepare_query_strategy(db).config
        in_graph = set(rag.base.document_ids(tenant_id))
        scope = [s for s in ids if s in in_graph]
        if not scope:
            log.info("graph-rag index for subject %s skipped: no source of it is in the graph", subject_id)
            return
    except Exception:  # noqa: BLE001
        log.exception("could not prepare the graph-rag index for source %s", source_id)
        return
    if not claim(subject_id):
        log.info("graph-rag index for subject %s is already running; skipped", subject_id)
        return
    run_index(rag, tenant_id, subject_id, scope, cfg)
