"""Chunking endpoints: rechunk and list chunks."""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentScope, DbSession
from app.core.config import get_settings
from app.schemas.chunking import ChunkRead, RechunkResponse
from app.services import chunking_service

router = APIRouter(tags=["chunking"])


@router.post(
    "/sources/{source_id}/rechunk",
    response_model=RechunkResponse,
    status_code=status.HTTP_200_OK,
)
def rechunk_source(
    source_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    version: int | None = Query(default=None, description="Extraction version to chunk; latest if omitted"),
):
    """Regenerate chunks from an extraction's stored readings.

    No re-extraction: the readings are already persisted, so this is free (no
    model calls, no credits). Proves that chunks are derived artifacts
    (strategy RULE 19).
    """
    extraction = chunking_service.chunk_extraction(db, source_id, scope, version=version)
    # Count the chunks we just created.
    chunks = chunking_service.list_chunks(db, source_id, scope, active_only=True)

    # Fresh chunks have no embeddings yet; enqueue them (setting-gated) and
    # let the worker drain. Idempotent: unchanged chunks keep their hash and
    # are skipped, so a re-chunk only re-embeds what actually changed.
    if get_settings().embed_on_chunk:
        try:
            from app.services.embedding_service import enqueue_embeddings, kick_worker

            if enqueue_embeddings(db, scope, source_id=source_id):
                kick_worker()
        except Exception:  # never fail the rechunk over embedding bookkeeping
            import logging

            logging.getLogger(__name__).exception(
                "could not enqueue embeddings after rechunk of %s", source_id
            )

    return RechunkResponse(
        extraction_id=extraction.id,
        source_id=source_id,
        version=extraction.version,
        chunk_count=len(chunks),
    )


@router.get("/sources/{source_id}/chunks", response_model=list[ChunkRead])
def list_chunks(
    source_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    active_only: bool = Query(default=True),
):
    """List chunks for a source, optionally including inactive (older) versions."""
    return chunking_service.list_chunks(db, source_id, scope, active_only=active_only)
