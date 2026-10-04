import uuid

from fastapi import APIRouter, BackgroundTasks, Body, Query, status

from app.api.deps import CurrentScope, DbSession, GraphIngestionDep, GraphRetrievalDep
from app.schemas.graph import (
    GraphBuildRead,
    GraphBuildRequest,
    GraphBuildSummary,
    GraphQueryRequest,
    GraphRetrievalResult,
)
from app.services import graph_service

source_router = APIRouter(prefix="/sources/{source_id}/graph", tags=["graph"])
subject_router = APIRouter(prefix="/subjects/{subject_id}/graph", tags=["graph"])


@source_router.post("", response_model=GraphBuildSummary, status_code=status.HTTP_202_ACCEPTED)
def start_graph_build(
    source_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    ingestion: GraphIngestionDep,
    background: BackgroundTasks,
    payload: GraphBuildRequest = Body(default_factory=GraphBuildRequest),
):
    """Build this source's knowledge graph from its latest extraction.

    Replaces the facts the source contributed before. With `retry_failed`,
    re-reads only the chunks the latest `partial` build could not. Returns
    at once with the build `processing`; poll `GET .../graph/builds/latest`.
    409 while a build is running; 503 if the graph is not configured.
    """
    build = graph_service.start_build(db, source_id, payload, scope, ingestion)
    background.add_task(graph_service.run_build, build.id, ingestion)
    return build


@source_router.get("/builds", response_model=list[GraphBuildSummary])
def list_graph_builds(source_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    """Every graph build of this source, newest first."""
    return graph_service.list_builds(db, source_id, scope)


@source_router.get("/builds/latest", response_model=GraphBuildRead)
def get_latest_graph_build(source_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return graph_service.get_latest_build(db, source_id, scope)


@subject_router.get("", response_model=GraphRetrievalResult)
def get_subject_graph(
    subject_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    retrieval: GraphRetrievalDep,
    include_subfolders: bool = True,
    max_entities: int = Query(default=200, ge=1, le=1000),
    max_relationships: int = Query(default=500, ge=1, le=2000),
):
    """The subject's knowledge graph, for plotting: its most-mentioned entities
    and the relationships between them, from its sources only (and, by
    default, those of every folder below it)."""
    return graph_service.subject_graph(
        db,
        subject_id,
        scope,
        retrieval,
        include_subfolders=include_subfolders,
        max_entities=max_entities,
        max_relationships=max_relationships,
    )


@subject_router.post("/query", response_model=GraphRetrievalResult)
def query_subject_graph(
    subject_id: uuid.UUID,
    payload: GraphQueryRequest,
    db: DbSession,
    scope: CurrentScope,
    retrieval: GraphRetrievalDep,
):
    """Graph evidence for a question: the entities it names, their neighbourhood
    up to `max_hops`, and the source chunks behind every fact."""
    return graph_service.query_subject(db, subject_id, payload, scope, retrieval)
