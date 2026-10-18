"""The Graph RAG index job: plan what is out of date, build it, build communities, record the run (plan Step 7).

    plan        compare the graph with Postgres, per source:
                  new        no graph document
                  changed    the graph holds chunk ids Postgres no longer has as active (the file was
                             re-extracted, re-chunked or re-imported): compared by a fingerprint of the ids
                  outdated   built with other versions (schema, extraction prompt, ontology, judge); rebuilt
                             only when `rebuild_outdated` is set, because a new prompt means extracting again
                  orphan     in the graph but no longer in Postgres: removed
    guard       a tenant over its daily cost cap is `blocked`, not run
    build       only the sources that need it (`ingest.build_sources`), reporting a heartbeat as windows finish
    communities `community_build.build_hierarchy` for the subject (summaries are reused where members overlap)
    record      status, plan, stats and cost on the run row; nothing is held in process memory

Runs as a durable job (`graph_rag_index` in `job_service`) or a background task, whichever the deployment
uses. The one-active-run-per-subject rule is the table's index, not code here (`runs.start_run`).
Cost covers the extraction calls only; community summaries and the entity judge are not metered.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import GraphRagIndexRun, RetrievalChunk, Source
from app.graphrag import runs
from app.graphrag.graph_write import chunk_fingerprint, current_signature

log = logging.getLogger(__name__)

KIND = "graph_rag_index"
HEARTBEAT_EVERY_S = 15.0


@dataclass
class Plan:
    to_build: list[dict] = field(default_factory=list)       # {"source_id", "reason"}
    up_to_date: list[str] = field(default_factory=list)
    outdated: list[str] = field(default_factory=list)        # built with other versions, not rebuilt
    orphans: list[str] = field(default_factory=list)         # graph documents whose source is gone
    inactive: list[str] = field(default_factory=list)        # sources still in Postgres with no active chunk left

    def summary(self) -> dict:
        reasons: dict[str, int] = {}
        for item in self.to_build:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        return {"to_build": len(self.to_build), "reasons": reasons, "up_to_date": len(self.up_to_date),
                "outdated_not_rebuilt": len(self.outdated), "orphans": len(self.orphans), "inactive": len(self.inactive),
                "sources_to_build": [i["source_id"] for i in self.to_build]}


def make_plan(
    active: dict[str, str], graph_docs: list[dict], signature: str, *, subject_id: str,
    all_source_ids: set[str], rebuild_outdated: bool = False, requested: list[str] | None = None,
    subject_ids: set[str] | None = None, in_subject: set[str] | None = None,
) -> Plan:
    """Pure. `active`: source id -> the fingerprint of its active chunk ids (only sources that have active chunks).
    `graph_docs`: the tenant graph's Document rows. `all_source_ids`: every source Postgres has for the tenant,
    to tell an orphan from a document that merely belongs to another subject."""
    docs = {d["id"]: d for d in graph_docs}
    plan = Plan()
    wanted = set(requested or [])
    for source_id, fingerprint in sorted(active.items()):
        doc = docs.get(source_id)
        if source_id in wanted:
            plan.to_build.append({"source_id": source_id, "reason": "requested"})
        elif doc is None:
            plan.to_build.append({"source_id": source_id, "reason": "new"})
        elif doc.get("chunk_fingerprint") != fingerprint:
            plan.to_build.append({"source_id": source_id, "reason": "changed"})
        elif doc.get("signature") != signature:
            if rebuild_outdated:
                plan.to_build.append({"source_id": source_id, "reason": "outdated"})
            else:
                plan.outdated.append(source_id)
        else:
            plan.up_to_date.append(source_id)
    # a document of this subject (or one of its sub-folders) whose source Postgres no longer has
    scope_subjects = subject_ids or {subject_id}
    plan.orphans = sorted(
        d["id"] for d in graph_docs if d.get("subject_id") in scope_subjects and d["id"] not in all_source_ids
    )
    if in_subject is not None:
        # the source still exists but none of its chunks is active any more: nothing in the graph is retrievable
        plan.inactive = sorted(d["id"] for d in graph_docs if d["id"] in in_subject and d["id"] not in active)
    return plan


def _scope(db, tenant_id, subject_id):
    """(sources of the subject and its sub-folders, fingerprint of each one's active chunk ids, every source id of the tenant)."""
    from app.services.subject_service import _descendant_ids

    subjects = _descendant_ids(db, subject_id)
    in_subject = {str(s) for s in db.execute(select(Source.id).where(Source.tenant_id == tenant_id, Source.subject_id.in_(subjects))).scalars()}
    all_ids = {str(s) for s in db.execute(select(Source.id).where(Source.tenant_id == tenant_id)).scalars()}
    rows = db.execute(
        select(RetrievalChunk.source_id, RetrievalChunk.id).where(
            RetrievalChunk.tenant_id == tenant_id, RetrievalChunk.is_active.is_(True),
            RetrievalChunk.source_id.in_([uuid.UUID(s) for s in in_subject] or [uuid.uuid4()]),
        )
    ).all()
    by_source: dict[str, list[str]] = {}
    for source_id, chunk_id in rows:
        by_source.setdefault(str(source_id), []).append(str(chunk_id))
    return in_subject, {s: chunk_fingerprint(ids) for s, ids in by_source.items()}, all_ids, {str(s) for s in subjects}


def _cost(report) -> float:
    from app.graphrag.pipelines import llm_cost

    settings = get_settings()
    return round(sum(llm_cost(settings.llm_graph_model, u.prompt_tokens, u.completion_tokens) for u in report.usage), 6)


def run_index(
    run_id: uuid.UUID, *, store=None, extractor=None, embed=None, dimension: int | None = None, judge=None,
    client=None, signature: str | None = None,
) -> dict:
    """Execute one run. Never raises: the outcome is on the run row. Dependencies are injectable for tests."""
    run = runs.claim(run_id)
    if run is None:
        return {"skipped": "the run is not queued (already running, finished, or gone)"}
    try:
        return _execute(run, store, extractor, embed, dimension, judge, client, signature)
    except runs.LeaseLost as exc:      # another worker owns the subject now: stop quietly, touch nothing more
        log.warning("%s", exc)
        return {"abandoned": str(exc)}
    except Exception as exc:  # noqa: BLE001 - recorded on the row
        log.exception("graph rag index run %s failed", run_id)
        runs.finish(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return {"failed": str(exc)}


def _execute(run: GraphRagIndexRun, store, extractor, embed, dimension, judge, client, signature) -> dict:
    from app.graphrag import community_build, embedder, ingest
    from app.graphrag.extract import WindowExtractor
    from app.graphrag.resolve import LLMPairJudge
    from app.graphrag.store import get_rag_store
    from app.graph.ontology import get_ontology
    from app.llm.client import get_llm_client
    from app.services import embedding_service

    settings = get_settings()
    tenant_id, subject_id = run.tenant_id, run.subject_id
    store = store or get_rag_store()
    if store is None:
        runs.finish(run.id, "failed", error="FalkorDB is not configured")
        return {"failed": "FalkorDB is not configured"}
    options = run.options or {}
    signature = signature or current_signature(get_ontology())

    with SessionLocal() as db:
        cap = runs.cost_cap_status(db, tenant_id)
        in_subject, active, all_ids, subject_ids = _scope(db, tenant_id, subject_id)
        cfg = embedding_service.prepare_query_strategy(db).config
    if cap["blocked"]:
        runs.finish(run.id, "blocked", error=f"Daily index cost cap reached: ${cap['spent']} of ${cap['cap']}", stats={"cost_cap": cap})
        return {"blocked": cap}

    graph_docs = store.documents_state(tenant_id)
    plan = make_plan(active, graph_docs, signature, subject_id=str(subject_id), all_source_ids=all_ids,
                     rebuild_outdated=bool(options.get("rebuild_outdated")), requested=run.source_ids,
                     subject_ids=subject_ids, in_subject=in_subject)
    runs.set_plan(run.id, plan.summary())

    for gone in plan.orphans + plan.inactive:         # the delete hook also clears atomic facts, merges, stale communities
        store.base.delete_source(tenant_id, gone)

    lease_lost: list[bool] = []
    stats: dict = {"orphans_removed": len(plan.orphans), "inactive_removed": len(plan.inactive)}
    report = None
    if plan.to_build:
        client = client or get_llm_client()
        last = [0.0]

        def beat(done: int, total: int) -> None:
            if time.monotonic() - last[0] >= HEARTBEAT_EVERY_S:
                last[0] = time.monotonic()
                if not runs.heartbeat(run.id, {"windows_done": done, "windows_total": total}):
                    lease_lost.append(True)       # noticed between stages: extraction cannot be stopped mid-flight

        report = ingest.build_sources(
            SessionLocal, store, tenant_id, [uuid.UUID(i["source_id"]) for i in plan.to_build],
            extractor=extractor or WindowExtractor(client, settings, get_ontology()),
            embed=embed or (lambda texts: embedder.embed_documents(cfg, texts)),
            dimension=dimension if dimension is not None else cfg.dimension,
            judge=judge if judge is not None else LLMPairJudge(client, settings),
            concurrency=settings.graphrag_build_concurrency, build_id=str(run.id), on_progress=beat, signature=signature,
        )
        stats["build"] = {
            "windows": report.windows, "failed_windows": report.failed_windows, "gleaned_windows": report.gleaned_windows,
            "written": dict(report.written), "resolution": dict(report.resolution), "embedded": dict(report.embedded),
            "errors": report.errors[:5], "timings_s": report.timings_s,
        }
    if lease_lost or not runs.heartbeat(run.id, stats):
        raise runs.LeaseLost(f"run {run.id} lost its lease during the build")

    in_graph = {d["id"] for d in store.documents_state(tenant_id)}
    scope_sources = sorted(in_subject & in_graph)
    if scope_sources and (plan.to_build or plan.orphans or options.get("communities")):
        embed_fn = embed or (lambda texts: embedder.embed_documents(cfg, texts))
        stats["communities"] = dict(community_build.build_hierarchy(
            store, tenant_id, subject_id, scope_sources, client=client or get_llm_client(), settings=settings, embed=embed_fn,
        ))
    cost = _cost(report) if report else 0.0
    failed_all = report is not None and report.windows > 0 and report.failed_windows == report.windows
    runs.finish(run.id, "failed" if failed_all else "done", stats=stats, cost_usd=cost,
                error="every window failed" if failed_all else None)
    return {"plan": plan.summary(), "stats": stats, "cost_usd": cost}


def dispatch(db, background, run_id: uuid.UUID) -> None:
    """Hand a queued run to the configured runner: a durable job, or a background task."""
    from app.services import job_service

    if job_service.is_durable():
        job_service.enqueue(db, job_service.KIND_GRAPH_RAG_INDEX, {"run_id": str(run_id)})
    else:
        background.add_task(run_index, run_id)
