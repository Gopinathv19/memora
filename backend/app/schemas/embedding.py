"""Pydantic schemas for the embedding subsystem's read/write models."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import ORMModel


class EmbeddingModelRead(ORMModel):
    """An embedding model, as returned by GET /embedding/models."""

    id: uuid.UUID
    provider: str
    model_name: str
    model_identifier: str
    model_version: str | None = None
    embedding_type: str
    dimension: int
    max_input_tokens: int | None = None
    normalization: str
    similarity_metric: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EmbeddingStrategyRead(ORMModel):
    """A strategy, as returned by the strategy endpoints."""

    id: uuid.UUID
    name: str
    description: str | None = None
    model_id: uuid.UUID
    input_type: str
    document_template: str
    query_template: str
    normalization: str
    similarity_metric: str
    dimension: int
    configuration_json: dict = {}
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Joined from the model row for display.
    model_provider: str | None = None
    model_name: str | None = None
    model_identifier: str | None = None


class EmbeddingStrategyCreate(BaseModel):
    """Create a new strategy. Immutable-after-creation fields only."""

    name: str
    description: str | None = None
    model_id: uuid.UUID
    input_type: str = "embedding_text"
    document_template: str = "{input}"
    query_template: str = "{input}"
    normalization: str = "l2"
    similarity_metric: str = "cosine"
    configuration_json: dict = {}


class EmbeddingStrategyUpdate(BaseModel):
    """Mutable fields only. Model, dimension and templates are immutable:
    changing them would silently invalidate every vector the strategy
    produced. Materially different configuration = a new strategy row.
    """

    description: str | None = None
    is_active: bool | None = None


class EmbedRequest(BaseModel):
    """Body for the embed endpoints. Defaults: the active strategy, no force."""

    strategy_id: uuid.UUID | None = None
    force: bool = False


class EmbeddingEnqueueResponse(BaseModel):
    """What POST .../embed returns: work queued, not work done."""

    enqueued: int
    strategy_id: uuid.UUID
    strategy_name: str


class RetryFailedResponse(BaseModel):
    reset: int
    strategy_id: uuid.UUID | None = None


class EmbeddingStatsResponse(BaseModel):
    total_chunks: int
    embedded: int
    pending: int
    processing: int
    failed: int
    stale: int
    coverage_percent: float


class SourceEmbeddingStatusResponse(BaseModel):
    source_id: str
    strategy_id: str
    strategy_name: str
    total_chunks: int
    embedded: int
    pending: int
    processing: int
    failed: int
    stale: int
    coverage_percent: float


class ChunkEmbeddingDebug(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    id: str
    strategy_id: str
    strategy_name: str
    status: str
    input_hash: str
    attempt_count: int
    error_message: str | None = None
    dimension: int
    vector_preview: list = []
    model_metadata: dict = {}
    created_at: datetime
    embedded_at: datetime | None = None


class QueryEmbeddingRequest(BaseModel):
    query: str
    strategy_id: uuid.UUID | None = None


class QueryEmbeddingResponse(BaseModel):
    """The query vector plus the context retrieval will need. The vector
    itself is returned for API consumers; the console never renders it."""

    dimension: int
    strategy_id: str
    strategy_name: str
    similarity_metric: str
    usage: dict
    vector: list[float]
