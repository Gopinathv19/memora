"""Embedding service: enqueue, claim, batch, embed, persist, report.

The only module that writes to the embedding tables. The pipeline:

    retrieval_chunks (active, in scope)
            |
    enqueue_embeddings()      create pending chunk_embeddings rows
            |                 (idempotent: existing hash -> skip)
    claim_pending()           SELECT ... FOR UPDATE SKIP LOCKED
            |
    run_embedding_batch()    build inputs -> provider -> validate ->
            |                normalize -> persist -> completed/failed
    chunk_embeddings (pgvector, HNSW-indexed)

Work is asynchronous: API routes enqueue and return; a background worker (a
FastAPI background task, matching the extraction pipeline's pattern) drains
the queue. No new infrastructure -- no Celery, no Redis -- because the
database is already the natural queue for a database-backed artifact.

Status lifecycle: pending -> processing -> completed | failed; failed -> pending
(retry); completed -> stale (input changed). History is never overwritten: a
changed input produces a new row, and the old row is marked stale.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.database import SessionLocal
from app.db.models import (
    ChunkEmbedding,
    EmbeddingModel,
    EmbeddingStrategy,
    RetrievalChunk,
    Source,
)
from app.embeddings.provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    get_embedding_provider,
)
from app.embeddings.strategy import (
    EmbeddingStrategyConfig,
    build_document_input,
    build_query_input,
    compute_input_hash,
    l2_normalize,
    validate_dimension,
)
from app.schemas.embedding import (
    EmbeddingStrategyCreate,
    EmbeddingStrategyRead,
    EmbeddingStrategyUpdate,
)
from app.services.scope import Scope
from app.services.source_service import get_source

log = logging.getLogger(__name__)

# Embedding statuses (kept as strings, like every other status column).
PENDING = "pending"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"
STALE = "stale"


# --- strategy loading -----------------------------------------------------------


def _strategy_config(strategy: EmbeddingStrategy) -> EmbeddingStrategyConfig:
    return EmbeddingStrategyConfig(
        strategy_id=str(strategy.id),
        name=strategy.name,
        model_identifier=strategy.model.model_identifier,
        model_version=strategy.model.model_version,
        dimension=strategy.dimension,
        normalization=strategy.normalization,
        similarity_metric=strategy.similarity_metric,
        document_template=strategy.document_template,
        query_template=strategy.query_template,
        input_type=strategy.input_type,
    )


def get_active_strategy(db: Session) -> EmbeddingStrategy:
    """The single active strategy. Loaded with its model in one query."""
    strategy = db.execute(
        select(EmbeddingStrategy)
        .options(selectinload(EmbeddingStrategy.model))
        .where(EmbeddingStrategy.is_active == True)  # noqa: E712
        .order_by(EmbeddingStrategy.created_at)
        .limit(1)
    ).scalar_one_or_none()
    if strategy is None:
        raise NotFoundError("No active embedding strategy is configured")
    return strategy


def get_strategy(db: Session, strategy_id: uuid.UUID) -> EmbeddingStrategy:
    strategy = db.execute(
        select(EmbeddingStrategy)
        .options(selectinload(EmbeddingStrategy.model))
        .where(EmbeddingStrategy.id == strategy_id)
    ).scalar_one_or_none()
    if strategy is None:
        raise NotFoundError("Embedding strategy not found")
    return strategy


def _strategy_read(s: EmbeddingStrategy) -> EmbeddingStrategyRead:
    """A strategy plus the joined model fields the console displays.

    Built explicitly (not via from_attributes) because `model_provider` and
    friends live on the related model row, not on the strategy row.
    """
    return EmbeddingStrategyRead(
        id=s.id,
        name=s.name,
        description=s.description,
        model_id=s.model_id,
        input_type=s.input_type,
        document_template=s.document_template,
        query_template=s.query_template,
        normalization=s.normalization,
        similarity_metric=s.similarity_metric,
        dimension=s.dimension,
        configuration_json=s.configuration_json or {},
        is_active=s.is_active,
        created_at=s.created_at,
        updated_at=s.updated_at,
        model_provider=s.model.provider,
        model_name=s.model.model_name,
        model_identifier=s.model.model_identifier,
    )


def list_models(db: Session) -> list[EmbeddingModel]:
    """Every registered model. Models are global data, not per-tenant."""
    return list(
        db.execute(select(EmbeddingModel).order_by(EmbeddingModel.created_at)).scalars()
    )


def list_strategies(db: Session) -> list[EmbeddingStrategyRead]:
    return [
        _strategy_read(s)
        for s in db.execute(
            select(EmbeddingStrategy)
            .options(selectinload(EmbeddingStrategy.model))
            .order_by(EmbeddingStrategy.created_at)
        ).scalars()
    ]


def get_strategy_read(db: Session, strategy_id: uuid.UUID) -> EmbeddingStrategyRead:
    return _strategy_read(get_strategy(db, strategy_id))


def create_strategy(
    db: Session, payload: EmbeddingStrategyCreate
) -> EmbeddingStrategyRead:
    """Create a strategy over an existing model.

    Dimension, normalization and similarity metric are inherited from the
    model row, never supplied by the caller: the vector column and the HNSW
    index are typed for them, so a strategy that disagreed would produce
    vectors the index cannot serve. A materially different configuration is
    a new model row, then a new strategy over it.
    """
    model = db.get(EmbeddingModel, payload.model_id)
    if model is None:
        raise NotFoundError("Embedding model not found")
    if not model.is_active:
        raise ValidationError("That embedding model is not active")

    if payload.input_type != "embedding_text":
        raise ValidationError(
            f"Unsupported input_type {payload.input_type!r}; "
            "only 'embedding_text' is supported"
        )
    for field, template in (
        ("document_template", payload.document_template),
        ("query_template", payload.query_template),
    ):
        if "{input}" not in template:
            raise ValidationError(f"{field} must contain the {{input}} placeholder")

    strategy = EmbeddingStrategy(
        name=payload.name,
        description=payload.description,
        model_id=model.id,
        input_type=payload.input_type,
        document_template=payload.document_template,
        query_template=payload.query_template,
        normalization=model.normalization,
        similarity_metric=model.similarity_metric,
        dimension=model.dimension,
        configuration_json=payload.configuration_json or {},
        is_active=True,
    )
    db.add(strategy)
    invalidate_strategy_cache()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            f"An embedding strategy named {payload.name!r} already exists"
        )
    return _strategy_read(get_strategy(db, strategy.id))


def update_strategy(
    db: Session, strategy_id: uuid.UUID, payload: EmbeddingStrategyUpdate
) -> EmbeddingStrategyRead:
    """Update the mutable fields only. Templates, model and dimension are
    immutable by design: changing them would silently invalidate every
    vector the strategy produced. Version by creating a new strategy."""
    strategy = get_strategy(db, strategy_id)
    if payload.description is not None:
        strategy.description = payload.description
    if payload.is_active is not None:
        strategy.is_active = payload.is_active
    db.commit()
    invalidate_strategy_cache()
    return _strategy_read(get_strategy(db, strategy_id))


# --- enqueueing -------------------------------------------------------------------


def _scope_predicates(scope: Scope):
    """The tenant/application predicates every chunk query must carry."""
    return [
        scope.tenant_predicate(RetrievalChunk.tenant_id),
        scope.application_predicate(RetrievalChunk.application_id),
    ]


def enqueue_embeddings(
    db: Session,
    scope: Scope,
    strategy_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    force: bool = False,
) -> int:
    """Create pending chunk_embeddings rows for chunks that lack one.

    Idempotent: a chunk whose (strategy, input_hash) row already exists is
    skipped. `force=True` re-enqueues even completed rows (their status is
    reset to pending; the old vector stays until the new one replaces it).

    Returns the number of rows enqueued.
    """
    strategy = (
        get_strategy(db, strategy_id) if strategy_id else get_active_strategy(db)
    )
    cfg = _strategy_config(strategy)

    predicates = _scope_predicates(scope)
    predicates.append(RetrievalChunk.is_active == True)  # noqa: E712
    if source_id is not None:
        get_source(db, source_id, scope)  # scope check; 404 outside it
        predicates.append(RetrievalChunk.source_id == source_id)
    if subject_id is not None:
        from app.services.subject_service import get_subject

        get_subject(db, subject_id, scope)  # scope check; 404 outside it
        predicates.append(RetrievalChunk.subject_id == subject_id)

    chunks = db.execute(
        select(RetrievalChunk).where(*predicates)
    ).scalars().all()

    # Existing rows for this strategy, indexed two ways: by (chunk_id,
    # input_hash) for the idempotency check, and by chunk_id for stale-marking.
    existing_by_key: dict[tuple[uuid.UUID, str], ChunkEmbedding] = {}
    existing_by_chunk: dict[uuid.UUID, list[ChunkEmbedding]] = {}
    if chunks:
        chunk_ids = [c.id for c in chunks]
        rows = db.execute(
            select(ChunkEmbedding).where(
                ChunkEmbedding.strategy_id == strategy.id,
                ChunkEmbedding.chunk_id.in_(chunk_ids),
            )
        ).scalars().all()
        for row in rows:
            existing_by_key[(row.chunk_id, row.input_hash)] = row
            existing_by_chunk.setdefault(row.chunk_id, []).append(row)

    enqueued = 0
    now = datetime.now(UTC)
    for chunk in chunks:
        rendered = build_document_input(cfg, chunk.embedding_text)
        input_hash = compute_input_hash(cfg, rendered)

        row = existing_by_key.get((chunk.id, input_hash))
        if row is not None:
            if force and row.embedding_status in (COMPLETED, STALE, FAILED):
                row.embedding_status = PENDING
                row.error_message = None
                row.updated_at = now
                enqueued += 1
            continue

        # A different input version exists: mark it stale (history preserved).
        for old in existing_by_chunk.get(chunk.id, []):
            if old.embedding_status == COMPLETED:
                old.embedding_status = STALE
                old.updated_at = now

        db.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                strategy_id=strategy.id,
                embedding=[0.0] * strategy.dimension,  # placeholder until embedded
                embedding_status=PENDING,
                input_hash=input_hash,
                model_metadata={
                    "provider": strategy.model.provider,
                    "model_identifier": strategy.model.model_identifier,
                    "model_version": strategy.model.model_version,
                    "dimension": strategy.dimension,
                    "normalization": strategy.normalization,
                    "similarity_metric": strategy.similarity_metric,
                },
            )
        )
        enqueued += 1

    db.commit()
    log.info(
        "enqueued %d embedding(s) for strategy %s (source=%s subject=%s force=%s)",
        enqueued, strategy.id, source_id, subject_id, force,
    )
    return enqueued


# --- the worker --------------------------------------------------------------------


def claim_pending(
    db: Session, strategy_id: uuid.UUID | None = None, limit: int | None = None
) -> list[ChunkEmbedding]:
    """Claim pending rows for processing. Safe under concurrency.

    SELECT ... FOR UPDATE SKIP LOCKED: two workers never claim the same row.
    The claim and the status flip commit together, so a crashed worker leaves
    rows `processing`, which startup recovery resets to `pending`.
    """
    settings = get_settings()
    claim_size = limit or settings.embedding_worker_claim_size

    predicates = [ChunkEmbedding.embedding_status == PENDING]
    if strategy_id is not None:
        predicates.append(ChunkEmbedding.strategy_id == strategy_id)

    rows = db.execute(
        select(ChunkEmbedding)
        .where(*predicates)
        .order_by(ChunkEmbedding.created_at)
        .limit(claim_size)
        .with_for_update(skip_locked=True)
    ).scalars().all()

    now = datetime.now(UTC)
    for row in rows:
        row.embedding_status = PROCESSING
        row.attempt_count += 1
        row.updated_at = now
    db.commit()
    return rows


def run_embedding_batch(
    db: Session,
    rows: list[ChunkEmbedding],
    provider: EmbeddingProvider | None = None,
) -> dict:
    """Embed one claimed batch. Never raises; failures are recorded per row.

    Steps: load chunks -> build inputs -> batch by settings -> provider ->
    validate dimension -> normalize -> persist. A batch-level failure (network,
    provider down) marks every row failed but keeps attempt_count bounded; a
    per-item failure (dimension mismatch on one vector) never loses the
    successful embeddings around it.
    """
    provider = provider or get_embedding_provider()
    settings = get_settings()
    batch_size = max(1, settings.embedding_batch_size)

    if not rows:
        return {"claimed": 0, "completed": 0, "failed": 0}

    # Load the chunks and strategies these rows point at.
    chunk_ids = {r.chunk_id for r in rows}
    strategy_ids = {r.strategy_id for r in rows}
    chunks = {
        c.id: c
        for c in db.execute(
            select(RetrievalChunk).where(RetrievalChunk.id.in_(chunk_ids))
        ).scalars()
    }
    strategies = {
        s.id: s
        for s in db.execute(
            select(EmbeddingStrategy)
            .options(selectinload(EmbeddingStrategy.model))
            .where(EmbeddingStrategy.id.in_(strategy_ids))
        ).scalars()
    }

    completed = failed = 0
    now = datetime.now(UTC)

    # Group rows by strategy so each batch goes to the right model.
    by_strategy: dict[uuid.UUID, list[ChunkEmbedding]] = {}
    for row in rows:
        by_strategy.setdefault(row.strategy_id, []).append(row)

    # Pass 1 (this thread, touches the ORM): turn rows into provider batches.
    jobs: list[tuple[uuid.UUID, EmbeddingStrategyConfig, list[ChunkEmbedding], list[str]]] = []
    for strategy_id, strategy_rows in by_strategy.items():
        strategy = strategies.get(strategy_id)
        if strategy is None:
            for row in strategy_rows:
                _fail_row(db, row, "strategy no longer exists", now)
            failed += len(strategy_rows)
            continue
        cfg = _strategy_config(strategy)

        for start in range(0, len(strategy_rows), batch_size):
            batch = strategy_rows[start : start + batch_size]
            inputs: list[str] = []
            valid_rows: list[ChunkEmbedding] = []
            for row in batch:
                chunk = chunks.get(row.chunk_id)
                if chunk is None:
                    _fail_row(db, row, "chunk no longer exists", now)
                    failed += 1
                    continue
                inputs.append(build_document_input(cfg, chunk.embedding_text))
                valid_rows.append(row)
            if valid_rows:
                jobs.append((strategy_id, cfg, valid_rows, inputs))

    # Pass 2: the provider calls are network-bound and independent, so they
    # run concurrently (the shared limiter still caps in-flight calls). Only
    # plain data crosses threads; the ORM stays on this thread.
    def embed(job):
        _, cfg, _, inputs = job
        try:
            return provider.embed_documents(cfg.model_identifier, inputs, cfg.dimension)
        except EmbeddingProviderError as exc:
            return exc

    workers = max(1, min(settings.embedding_concurrency, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(embed, jobs))

    # Pass 3 (this thread): record each batch and commit it, so a crash
    # later in the sweep does not lose the batches already embedded.
    for (strategy_id, cfg, valid_rows, _), result in zip(jobs, results):
        if isinstance(result, EmbeddingProviderError):
            # Batch-level failure: every row in this batch is failed, but
            # other batches/strategies still got their chance.
            for row in valid_rows:
                _fail_row(db, row, result.message, now)
            failed += len(valid_rows)
            log.warning(
                "embedding batch failed (strategy=%s batch=%d): %s",
                strategy_id, len(valid_rows), result.message,
            )
            db.commit()
            continue
        vectors, usage = result
        for row, vector in zip(valid_rows, vectors):
            try:
                validate_dimension(vector, cfg.dimension)
                final = (
                    l2_normalize(vector)
                    if cfg.normalization == "l2"
                    else vector
                )
                row.embedding = final
                row.embedding_status = COMPLETED
                row.error_message = None
                row.embedded_at = now
                row.updated_at = now
                completed += 1
            except ValueError as exc:
                _fail_row(db, row, str(exc), now)
                failed += 1
        db.commit()
        log.info(
            "embedded batch: strategy=%s model=%s chunks=%d tokens=%d latency_ms=%d",
            strategy_id, cfg.model_identifier, len(valid_rows),
            usage.total_tokens, usage.latency_ms,
        )

    db.commit()
    return {"claimed": len(rows), "completed": completed, "failed": failed}


def _fail_row(db: Session, row: ChunkEmbedding, message: str, now: datetime) -> None:
    row.embedding_status = FAILED
    row.error_message = message[:2000]
    row.updated_at = now
    # Exceeded max attempts: leave failed; a manual retry resets to pending.
    if row.attempt_count >= get_settings().embedding_max_retries:
        row.error_message = (
            f"{message[:1900]} (gave up after {row.attempt_count} attempts)"
        )


def process_pending(
    db: Session, strategy_id: uuid.UUID | None = None, limit: int | None = None
) -> dict:
    """One worker sweep: claim everything pending, embed it, report."""
    rows = claim_pending(db, strategy_id=strategy_id, limit=limit)
    if not rows:
        return {"claimed": 0, "completed": 0, "failed": 0}
    return run_embedding_batch(db, rows)


def drain_pending(
    strategy_id: uuid.UUID | None = None, max_sweeps: int = 100
) -> None:
    """Sweep the queue until it is empty. Owns its session, never raises.

    Entry point for background execution: FastAPI BackgroundTasks (API
    routes) and the startup sweeper both call this. Failed rows do not
    return to `pending`, so a persistently failing batch cannot loop
    forever -- the next sweep claims nothing and the drain ends.
    """
    db = SessionLocal()
    try:
        for _ in range(max_sweeps):
            sweep_started = time.monotonic()
            result = process_pending(db, strategy_id=strategy_id)
            if result["claimed"] == 0:
                break
            log.info(
                "embedding drain: claimed=%d completed=%d failed=%d elapsed_ms=%d",
                result["claimed"], result["completed"], result["failed"],
                int((time.monotonic() - sweep_started) * 1000),
            )
    except Exception:
        db.rollback()
        log.exception("embedding worker drain failed")
    finally:
        db.close()


_worker_state_lock = threading.Lock()
_worker_running = False


def kick_worker(strategy_id: uuid.UUID | None = None) -> None:
    """Start one background sweep thread if none is already running.

    For code paths with no response cycle to attach a background task to,
    such as auto-embed after an extraction. SKIP LOCKED already makes
    concurrent sweeps safe; the running flag just avoids redundant threads.
    """
    global _worker_running
    with _worker_state_lock:
        if _worker_running:
            return
        _worker_running = True

    def _run() -> None:
        global _worker_running
        try:
            drain_pending(strategy_id)
        finally:
            with _worker_state_lock:
                _worker_running = False

    threading.Thread(target=_run, name="embedding-worker", daemon=True).start()


def retry_failed(db: Session, scope: Scope, strategy_id: uuid.UUID | None = None) -> int:
    """failed -> pending, inside the caller's scope. Returns the count reset."""
    predicates = [
        ChunkEmbedding.embedding_status == FAILED,
        RetrievalChunk.id == ChunkEmbedding.chunk_id,
        *_scope_predicates(scope),
    ]
    result = db.execute(
        update(ChunkEmbedding)
        .where(*predicates)
        .values(embedding_status=PENDING, error_message=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount or 0


def reset_interrupted(db: Session) -> int:
    """At startup: rows still `processing` belonged to a dead worker."""
    rows = db.execute(
        select(ChunkEmbedding).where(ChunkEmbedding.embedding_status == PROCESSING)
    ).scalars().all()
    for row in rows:
        row.embedding_status = PENDING
    db.commit()
    return len(rows)


# --- query embedding (retrieval preparation) ----------------------------------------


_QUERY_CACHE_MAX = 256
_query_cache: "OrderedDict[tuple, list[float]]" = OrderedDict()
_query_cache_lock = threading.Lock()


@dataclass(frozen=True)
class QueryStrategy:
    """The few strategy facts a query needs, as plain data: safe to cache and
    to hand to another thread (an ORM row is neither)."""

    id: uuid.UUID
    name: str
    similarity_metric: str
    config: EmbeddingStrategyConfig


_strategy_cache: dict[uuid.UUID | None, tuple[float, QueryStrategy]] = {}
_strategy_cache_lock = threading.Lock()


def invalidate_strategy_cache() -> None:
    with _strategy_cache_lock:
        _strategy_cache.clear()


def prepare_query_strategy(
    db: Session, strategy_id: uuid.UUID | None = None
) -> QueryStrategy:
    """The strategy a query embeds with, cached briefly in this process.

    Looking it up costs two database round trips on every question, and it
    changes only when someone creates or edits a strategy (which clears the
    cache here; other processes see the change within the TTL).
    """
    ttl = get_settings().strategy_cache_seconds
    now = time.monotonic()
    with _strategy_cache_lock:
        hit = _strategy_cache.get(strategy_id)
        if hit is not None and hit[0] > now:
            return hit[1]
    strategy = get_strategy(db, strategy_id) if strategy_id else get_active_strategy(db)
    resolved = QueryStrategy(
        id=strategy.id,
        name=strategy.name,
        similarity_metric=strategy.similarity_metric,
        config=_strategy_config(strategy),
    )
    if ttl > 0:
        with _strategy_cache_lock:
            _strategy_cache[strategy_id] = (now + ttl, resolved)
    return resolved


def embed_query_with_config(
    cfg: EmbeddingStrategyConfig, query: str
) -> tuple[list[float], int, int]:
    """Embed a query under a strategy config: no database access.

    Returns (vector, total_tokens, latency_ms). Identical (model, template,
    query) inputs are served from a small in-process LRU, so a repeated
    question skips the network call and gets exactly the same vector.
    """
    rendered = build_query_input(cfg, query)
    key = (
        cfg.model_identifier, cfg.model_version, cfg.dimension,
        cfg.normalization, rendered,
    )
    with _query_cache_lock:
        cached = _query_cache.get(key)
        if cached is not None:
            _query_cache.move_to_end(key)
            return list(cached), 0, 0

    provider = get_embedding_provider()
    vector, usage = provider.embed_query(
        cfg.model_identifier, rendered, cfg.dimension
    )
    validate_dimension(vector, cfg.dimension)
    if cfg.normalization == "l2":
        vector = l2_normalize(vector)

    with _query_cache_lock:
        _query_cache[key] = list(vector)
        while len(_query_cache) > _QUERY_CACHE_MAX:
            _query_cache.popitem(last=False)
    return vector, usage.total_tokens, usage.latency_ms


def embed_query(db: Session, query: str, strategy_id: uuid.UUID | None = None) -> dict:
    """Embed a query under a strategy. Retrieval preparation only -- no search.

    Loads the strategy, applies the query template, calls the provider,
    validates the dimension, normalizes, and returns the vector plus the
    strategy context the retrieval layer will need (scope filters, metric).
    """
    strategy = prepare_query_strategy(db, strategy_id)
    cfg = strategy.config
    vector, total_tokens, latency_ms = embed_query_with_config(cfg, query)
    return {
        "vector": vector,
        "dimension": cfg.dimension,
        "strategy_id": str(strategy.id),
        "strategy_name": strategy.name,
        "similarity_metric": strategy.similarity_metric,
        "usage": {"total_tokens": total_tokens, "latency_ms": latency_ms},
    }


# --- reporting ----------------------------------------------------------------------


def embedding_stats(db: Session, scope: Scope) -> dict:
    """Embedding health inside the caller's scope: counts + coverage."""
    predicates = [
        RetrievalChunk.id == ChunkEmbedding.chunk_id,
        *_scope_predicates(scope),
        RetrievalChunk.is_active == True,  # noqa: E712
    ]

    def _count(status: str | None) -> int:
        stmt = select(func.count()).select_from(ChunkEmbedding).join(
            RetrievalChunk, RetrievalChunk.id == ChunkEmbedding.chunk_id
        )
        preds = list(_scope_predicates(scope))
        preds.append(RetrievalChunk.is_active == True)  # noqa: E712
        if status is not None:
            preds.append(ChunkEmbedding.embedding_status == status)
        return int(db.execute(select(func.count()).select_from(ChunkEmbedding).join(RetrievalChunk, RetrievalChunk.id == ChunkEmbedding.chunk_id).where(*preds)).scalar_one())

    total_chunks = int(
        db.execute(
            select(func.count())
            .select_from(RetrievalChunk)
            .where(*_scope_predicates(scope), RetrievalChunk.is_active == True)  # noqa: E712
        ).scalar_one()
    )
    embedded = _count(COMPLETED)
    pending = _count(PENDING)
    processing = _count(PROCESSING)
    failed = _count(FAILED)
    stale = _count(STALE)

    coverage = round(embedded / total_chunks * 100, 1) if total_chunks else 0.0
    return {
        "total_chunks": total_chunks,
        "embedded": embedded,
        "pending": pending,
        "processing": processing,
        "failed": failed,
        "stale": stale,
        "coverage_percent": coverage,
    }


def source_embedding_status(db: Session, source_id: uuid.UUID, scope: Scope) -> dict:
    """Per-source embedding health, for the source detail page."""
    get_source(db, source_id, scope)
    strategy = get_active_strategy(db)

    base = [
        RetrievalChunk.source_id == source_id,
        RetrievalChunk.is_active == True,  # noqa: E712
        *_scope_predicates(scope),
    ]
    total = int(
        db.execute(
            select(func.count()).select_from(RetrievalChunk).where(*base)
        ).scalar_one()
    )

    def _count(status: str) -> int:
        return int(
            db.execute(
                select(func.count())
                .select_from(ChunkEmbedding)
                .join(RetrievalChunk, RetrievalChunk.id == ChunkEmbedding.chunk_id)
                .where(
                    *base,
                    ChunkEmbedding.strategy_id == strategy.id,
                    ChunkEmbedding.embedding_status == status,
                )
            ).scalar_one()
        )

    embedded = _count(COMPLETED)
    return {
        "source_id": str(source_id),
        "strategy_id": str(strategy.id),
        "strategy_name": strategy.name,
        "total_chunks": total,
        "embedded": embedded,
        "pending": _count(PENDING),
        "processing": _count(PROCESSING),
        "failed": _count(FAILED),
        "stale": _count(STALE),
        "coverage_percent": round(embedded / total * 100, 1) if total else 0.0,
    }


def chunk_embedding_debug(db: Session, chunk_id: uuid.UUID, scope: Scope) -> list[dict]:
    """Chunk-level embedding info for the debug view: status, hash, vector head."""
    chunk = db.execute(
        select(RetrievalChunk).where(RetrievalChunk.id == chunk_id)
    ).scalar_one_or_none()
    if chunk is None or not scope.allow_tenant(chunk.tenant_id):
        raise NotFoundError("Chunk not found")

    rows = db.execute(
        select(ChunkEmbedding)
        .options(selectinload(ChunkEmbedding.strategy))
        .where(ChunkEmbedding.chunk_id == chunk_id)
        .order_by(ChunkEmbedding.created_at.desc())
    ).scalars().all()

    out = []
    for row in rows:
        vector = row.embedding or []
        out.append(
            {
                "id": str(row.id),
                "strategy_id": str(row.strategy_id),
                "strategy_name": row.strategy.name,
                "status": row.embedding_status,
                "input_hash": row.input_hash,
                "attempt_count": row.attempt_count,
                "error_message": row.error_message,
                "dimension": len(vector) if vector else row.strategy.dimension,
                "vector_preview": (
                    [round(float(x), 6) for x in vector[:4]]
                    + (["..."] if len(vector) > 8 else [])
                    + ([round(float(x), 6) for x in vector[-2:]] if len(vector) > 8 else [])
                    if vector
                    else []
                ),
                "model_metadata": row.model_metadata,
                "created_at": row.created_at,
                "embedded_at": row.embedded_at,
            }
        )
    return out
