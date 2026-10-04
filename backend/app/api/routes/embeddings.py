"""Embedding endpoints: strategies, enqueueing, worker control, reporting.

The routes are thin, like every other route module: they resolve scope,
call the service, and return. The async pattern matches extractions --
enqueue in-request, drain in a background task -- because embedding is the
same kind of work: slow, model-backed, and safe to poll.

Endpoint map (all under /api/v1):

    GET    /embedding/models                          registered models
    GET    /embedding/strategies                       list strategies
    POST   /embedding/strategies                       create a strategy
    GET    /embedding/strategies/{strategy_id}          one strategy
    PATCH  /embedding/strategies/{strategy_id}          update mutable fields
    POST   /embedding/sources/{source_id}/embed         enqueue + drain
    POST   /embedding/subjects/{subject_id}/embed       enqueue + drain
    POST   /embedding/strategies/{strategy_id}/rebuild   force re-embed all
    POST   /embedding/retry-failed                      failed -> pending + drain
    GET    /embedding/stats                            scope-wide health
    GET    /embedding/sources/{source_id}/status        per-source health
    GET    /embedding/chunks/{chunk_id}/embeddings      chunk-level debug
    POST   /embedding/query                            embed a query string
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, status

from app.api.deps import CurrentScope, DbSession
from app.schemas.embedding import (
    ChunkEmbeddingDebug,
    EmbedRequest,
    EmbeddingEnqueueResponse,
    EmbeddingModelRead,
    EmbeddingStatsResponse,
    EmbeddingStrategyCreate,
    EmbeddingStrategyRead,
    EmbeddingStrategyUpdate,
    QueryEmbeddingRequest,
    QueryEmbeddingResponse,
    RetryFailedResponse,
    SourceEmbeddingStatusResponse,
)
from app.services import embedding_service

router = APIRouter(prefix="/embedding", tags=["embeddings"])


# --- models & strategies -------------------------------------------------------------


@router.get("/models", response_model=list[EmbeddingModelRead])
def list_embedding_models(db: DbSession, scope: CurrentScope):
    """Registered embedding models. Global catalog data, readable in any scope."""
    return embedding_service.list_models(db)


@router.get("/strategies", response_model=list[EmbeddingStrategyRead])
def list_embedding_strategies(db: DbSession, scope: CurrentScope):
    return embedding_service.list_strategies(db)


@router.post(
    "/strategies",
    response_model=EmbeddingStrategyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_embedding_strategy(
    payload: EmbeddingStrategyCreate, db: DbSession, scope: CurrentScope
):
    """Create a strategy over an existing model.

    Dimension/normalization/metric are inherited from the model row: the
    vector column and the HNSW index are typed for them.
    """
    return embedding_service.create_strategy(db, payload)


@router.get("/strategies/{strategy_id}", response_model=EmbeddingStrategyRead)
def get_embedding_strategy(
    strategy_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    return embedding_service.get_strategy_read(db, strategy_id)


@router.patch("/strategies/{strategy_id}", response_model=EmbeddingStrategyRead)
def update_embedding_strategy(
    strategy_id: uuid.UUID,
    payload: EmbeddingStrategyUpdate,
    db: DbSession,
    scope: CurrentScope,
):
    """Update the mutable fields only (description, is_active). Everything
    that determines the vector is immutable: version by creating a new
    strategy, so old embeddings keep their provenance."""
    return embedding_service.update_strategy(db, strategy_id, payload)


# --- enqueueing ----------------------------------------------------------------------


def _enqueue_and_drain(
    db,
    scope,
    background: BackgroundTasks,
    *,
    strategy_id: uuid.UUID | None,
    force: bool,
    source_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
) -> EmbeddingEnqueueResponse:
    """Shared body of the embed endpoints: enqueue, then schedule the drain.

    The enqueue commits inside the request, so the response reports real
    queued work; the drain runs after the response, in the background.
    """
    enqueued = embedding_service.enqueue_embeddings(
        db,
        scope,
        strategy_id=strategy_id,
        source_id=source_id,
        subject_id=subject_id,
        force=force,
    )
    strategy = embedding_service.get_strategy(
        db, strategy_id
    ) if strategy_id else embedding_service.get_active_strategy(db)
    if enqueued:
        background.add_task(embedding_service.drain_pending, strategy_id)
    return EmbeddingEnqueueResponse(
        enqueued=enqueued,
        strategy_id=strategy.id,
        strategy_name=strategy.name,
    )


@router.post(
    "/sources/{source_id}/embed",
    response_model=EmbeddingEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def embed_source(
    source_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    background: BackgroundTasks,
    payload: EmbedRequest | None = None,
):
    """Embed a source's active chunks. Idempotent: chunks that already have
    a current embedding are skipped unless `force` is true."""
    payload = payload or EmbedRequest()
    return _enqueue_and_drain(
        db, scope, background,
        strategy_id=payload.strategy_id,
        force=payload.force,
        source_id=source_id,
    )


@router.post(
    "/subjects/{subject_id}/embed",
    response_model=EmbeddingEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def embed_subject(
    subject_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    background: BackgroundTasks,
    payload: EmbedRequest | None = None,
):
    """Embed every active chunk under a subject (all its sources)."""
    payload = payload or EmbedRequest()
    return _enqueue_and_drain(
        db, scope, background,
        strategy_id=payload.strategy_id,
        force=payload.force,
        subject_id=subject_id,
    )


@router.post(
    "/strategies/{strategy_id}/rebuild",
    response_model=EmbeddingEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rebuild_strategy(
    strategy_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    background: BackgroundTasks,
):
    """Force re-embed everything in scope under a strategy.

    The input hash is unchanged, so this reuses each chunk's existing row
    (reset to pending) rather than duplicating history.
    """
    return _enqueue_and_drain(
        db, scope, background,
        strategy_id=strategy_id,
        force=True,
    )


@router.post(
    "/retry-failed",
    response_model=RetryFailedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_failed_embeddings(
    db: DbSession,
    scope: CurrentScope,
    background: BackgroundTasks,
    payload: EmbedRequest | None = None,
):
    """Reset failed embeddings to pending (in scope) and drain them."""
    payload = payload or EmbedRequest()
    reset = embedding_service.retry_failed(db, scope, strategy_id=payload.strategy_id)
    if reset:
        background.add_task(embedding_service.drain_pending, payload.strategy_id)
    return RetryFailedResponse(reset=reset, strategy_id=payload.strategy_id)


# --- reporting -----------------------------------------------------------------------


@router.get("/stats", response_model=EmbeddingStatsResponse)
def embedding_stats(db: DbSession, scope: CurrentScope):
    """Embedding health inside the caller's scope: counts + coverage."""
    return embedding_service.embedding_stats(db, scope)


@router.get(
    "/sources/{source_id}/status", response_model=SourceEmbeddingStatusResponse
)
def source_embedding_status(
    source_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    """Per-source embedding health, for the source detail page."""
    return embedding_service.source_embedding_status(db, source_id, scope)


@router.get("/chunks/{chunk_id}/embeddings", response_model=list[ChunkEmbeddingDebug])
def chunk_embedding_debug(
    chunk_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    """Every embedding row for one chunk: status, hash, vector head. Debug view."""
    return embedding_service.chunk_embedding_debug(db, chunk_id, scope)


@router.post("/query", response_model=QueryEmbeddingResponse)
def embed_query(
    payload: QueryEmbeddingRequest, db: DbSession, scope: CurrentScope
):
    """Embed a query string under a strategy. Retrieval preparation only --
    no search is performed here; the vector is returned for API consumers."""
    return embedding_service.embed_query(db, payload.query, payload.strategy_id)
