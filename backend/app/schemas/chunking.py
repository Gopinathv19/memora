"""Pydantic schemas for chunking read responses."""

import uuid
from datetime import datetime

from app.schemas.common import ORMModel


class ChunkRead(ORMModel):
    """A retrieval chunk, as returned by GET /sources/{id}/chunks."""

    id: uuid.UUID
    semantic_block_id: uuid.UUID
    extraction_id: uuid.UUID
    source_version: int
    chunk_index: int
    content: str
    embedding_text: str
    token_count: int
    content_type: str
    page_start: int | None = None
    page_end: int | None = None
    section_path: list[str] = []
    is_active: bool
    created_at: datetime


class RechunkResponse(ORMModel):
    """Response for POST /sources/{id}/rechunk."""

    extraction_id: uuid.UUID
    source_id: uuid.UUID
    version: int
    chunk_count: int
