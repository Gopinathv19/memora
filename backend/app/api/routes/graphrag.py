"""Graph RAG: query, compare, index, explore, benchmark.

    POST /graph-rag/query        one question through the graph (or hybrid) pipeline
    POST /compare/query          the same question through several pipelines, side by side
    POST /compare/stream         the same, as server-sent events as each pipeline finishes
    GET  /compare/runs           recent runs (what the dashboard plots)
    POST /graph-rag/expand       one hop out from entities (graph explorer)
    GET  /subjects/{id}/graph-rag/status   what is indexed for a subject
    POST /subjects/{id}/graph-rag/index    embed entities and facts, build communities
    POST/GET /compare/benchmarks[...]      benchmark the pipelines over a question set
"""

import json
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentScope, DbSession, GraphRagStoreDep
from app.core.errors import NotFoundError
from app.db.models import BenchmarkRun, RagRun, Source
from app.graphrag import autoindex, benchmark, pipelines
from app.graphrag.compare import compare as run_compare, stream_compare
from app.graphrag.params import Pipeline
from app.graphrag.store import GraphRagStore
from app.schemas.graphrag import (
    BenchmarkDetail,
    BenchmarkRead,
    BenchmarkRequest,
    CompareRequest,
    ExpandRequest,
    GraphRagQueryRequest,
    IndexRequest,
    RagRunRead,
)
from app.services import embedding_service, graph_service
from app.services.source_service import get_source

router = APIRouter(tags=["graph-rag"])

def _require(store: GraphRagStore | None) -> GraphRagStore:
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The knowledge graph is not configured (FALKORDB_URL is not set)",
        )
    return store


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _reachable(fn):
    """FalkorDB being down is a 503, not a 500."""
    from redis.exceptions import RedisError

    try:
        return fn()
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The graph database is unreachable: {exc}",
        ) from exc


@router.post("/graph-rag/query")
def graph_rag_query(
    payload: GraphRagQueryRequest, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep
):
    """Answer a question from the knowledge graph with the chosen technique."""
    rag = _require(store)
    subject, _ = graph_service.subject_source_ids(db, payload.subject_id, scope, True)
    result = _reachable(
        lambda: pipelines.run_graph(
            db, scope, rag, payload.query, payload.subject_id, payload.params.to_params(),
            hybrid=payload.pipeline == Pipeline.HYBRID,
        )
    )
    pipelines.record_run(db, result, tenant_id=subject.tenant_id, subject_id=subject.id)
    return result.to_dict()


@router.post("/compare/query")
def compare_query(
    payload: CompareRequest, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep
):
    rag = _require(store)
    subject, source_ids = graph_service.subject_source_ids(db, payload.subject_id, scope, True)
    tenant_id, subject_id = subject.tenant_id, subject.id
    db.close()  # the pipelines open their own sessions
    return _reachable(
        lambda: run_compare(
            scope, rag, payload.query, subject_id, tenant_id,
            payload.params.to_params(), list(dict.fromkeys(payload.pipelines)),
            source_ids=source_ids,
        )
    )


@router.post("/compare/stream")
def compare_stream(
    payload: CompareRequest, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep
):
    """Server-sent events: `start`, then one `result` per pipeline as it finishes,
    then `done` with the evidence overlap."""
    rag = _require(store)
    subject, source_ids = graph_service.subject_source_ids(db, payload.subject_id, scope, True)
    tenant_id, subject_id = subject.tenant_id, subject.id
    db.close()
    events = stream_compare(
        scope, rag, payload.query, subject_id, tenant_id, payload.params.to_params(),
        list(dict.fromkeys(payload.pipelines)), source_ids,
    )

    def body():
        for event, data in events:
            yield _sse(event, data)

    return StreamingResponse(
        body(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/compare/runs", response_model=list[RagRunRead])
def list_runs(
    db: DbSession, scope: CurrentScope,
    subject_id: uuid.UUID | None = None, limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(RagRun).where(scope.tenant_predicate(RagRun.tenant_id))
    if subject_id is not None:
        stmt = stmt.where(RagRun.subject_id == subject_id)
    return list(db.execute(stmt.order_by(RagRun.created_at.desc()).limit(limit)).scalars())


@router.post("/graph-rag/expand")
def expand(payload: ExpandRequest, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep):
    """One hop out from some entities: the nodes and edges to add to a plot."""
    rag = _require(store)
    subject, source_ids = graph_service.subject_source_ids(db, payload.subject_id, scope, True)

    def go():
        rows = rag.neighbourhood(subject.tenant_id, payload.entity_ids, source_ids, payload.limit)
        ids = sorted({r[k] for r in rows for k in ("source_entity_id", "target_entity_id")})
        info = rag.entities_info(subject.tenant_id, ids)
        degrees = rag.entity_degrees(subject.tenant_id, ids, source_ids)
        return {
            "nodes": [
                {"id": i, "name": info[i]["name"], "type": info[i]["entity_type"],
                 "degree": degrees.get(i, 0)}
                for i in ids if i in info
            ],
            "edges": [
                {"source": r["source_entity_id"], "target": r["target_entity_id"],
                 "relation": r["relation"], "confidence": r.get("confidence"),
                 "description": r.get("description"), "chunk_ids": r.get("chunk_ids") or []}
                for r in rows
            ],
        }

    return _reachable(go)


# --- inspection -----------------------------------------------------------------------------


@router.get("/sources/{source_id}/graph/inspect")
def inspect_source_graph(
    source_id: uuid.UUID, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep
):
    """One source's own graph, with the passages it was read from.

    For checking how a build turned text into entities and facts: every passage,
    the entities each one produced, and every fact with the passages that support
    it. Only what this source contributed; entities other sources also mention are
    shown from this source's side only.
    """
    rag = _require(store)
    source = get_source(db, source_id, scope)
    data = _reachable(lambda: rag.inspect_source(source.tenant_id, str(source.id)))
    return {"source_id": str(source.id), "filename": source.filename, **data}


# --- index ----------------------------------------------------------------------------------


@router.get("/subjects/{subject_id}/graph-rag/status")
def index_status(subject_id: uuid.UUID, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep):
    rag = _require(store)
    subject, source_ids = graph_service.subject_source_ids(db, subject_id, scope, True)
    coverage = _reachable(lambda: rag.stats(subject.tenant_id, source_ids))
    in_graph = set(_reachable(lambda: rag.base.document_ids(subject.tenant_id)))
    missing = [
        {"id": str(s.id), "filename": s.filename, "extracted": s.status == "completed"}
        for s in db.execute(
            select(Source)
            .where(Source.id.in_([uuid.UUID(x) for x in source_ids if x not in in_graph]))
            .order_by(Source.filename)
        ).scalars()
    ] if source_ids else []
    return {
        "subject_id": str(subject.id),
        "sources": len(source_ids),
        # Files Graph RAG cannot see: it only knows what has a knowledge graph.
        "files_without_graph": missing,
        "coverage": coverage,
        "ready": coverage["entities"] > 0 and coverage["entities_embedded"] >= coverage["entities"],
        "build": autoindex.BUILDS.get(str(subject.id)),
    }


@router.post("/subjects/{subject_id}/graph-rag/index", status_code=status.HTTP_202_ACCEPTED)
def build_index(
    subject_id: uuid.UUID, payload: IndexRequest, db: DbSession, scope: CurrentScope,
    store: GraphRagStoreDep, background: BackgroundTasks,
):
    """Embed the subject's entities and facts and build its communities.

    Idempotent and incremental (only what is new is embedded). Returns at once;
    poll `GET .../status` for progress.
    """
    rag = _require(store)
    subject, source_ids = graph_service.subject_source_ids(db, subject_id, scope, True)
    if not autoindex.claim(subject.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "An index build is already running")
    cfg = embedding_service.prepare_query_strategy(db).config
    tenant_id = subject.tenant_id

    def work():
        autoindex.run_index(rag, tenant_id, subject.id, source_ids, cfg, communities=payload.communities)

    background.add_task(work)
    return {"status": "running"}


# --- benchmarks -----------------------------------------------------------------------------


@router.post("/compare/benchmarks", response_model=BenchmarkRead, status_code=status.HTTP_202_ACCEPTED)
def start_benchmark(
    payload: BenchmarkRequest, db: DbSession, scope: CurrentScope, store: GraphRagStoreDep
):
    rag = _require(store)
    subject, _ = graph_service.subject_source_ids(db, payload.subject_id, scope, True)
    questions = [q.model_dump() for q in payload.questions]
    config = {
        "techniques": [str(t) for t in payload.techniques], "include_hybrid": payload.include_hybrid,
        "judge": payload.judge, "params": payload.params.model_dump(mode="json"),
    }
    run = benchmark.create_run(
        db, tenant_id=subject.tenant_id, subject_id=subject.id, label=payload.label,
        questions=questions, config=config, build_cost=autoindex.BUILDS.get(str(subject.id)) or {},
    )
    benchmark.start_background(
        benchmark_id=run.id, scope=scope, rag_store=rag, subject_id=subject.id,
        tenant_id=subject.tenant_id, questions=questions, techniques=list(payload.techniques),
        include_hybrid=payload.include_hybrid, do_judge=payload.judge,
        params=payload.params.to_params(),
    )
    return run


@router.get("/compare/benchmarks", response_model=list[BenchmarkRead])
def list_benchmarks(db: DbSession, scope: CurrentScope, limit: int = Query(default=20, ge=1, le=100)):
    stmt = select(BenchmarkRun).where(scope.tenant_predicate(BenchmarkRun.tenant_id))
    return list(db.execute(stmt.order_by(BenchmarkRun.created_at.desc()).limit(limit)).scalars())


@router.get("/compare/benchmarks/{benchmark_id}", response_model=BenchmarkDetail)
def get_benchmark(benchmark_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    run = db.execute(
        select(BenchmarkRun).where(
            BenchmarkRun.id == benchmark_id, scope.tenant_predicate(BenchmarkRun.tenant_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Benchmark not found")
    return run
