import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.extraction import ExtractionUsageRead

MAX_QUERY_CHARS = 1000


# --- Builds -------------------------------------------------------------------------


class GraphBuildRequest(BaseModel):
    """Build a source's knowledge graph from its latest extraction."""

    retry_failed: bool = Field(
        default=False,
        description="Re-read only the chunks the latest (partial) build could "
        "not, against the same extraction version, instead of rebuilding.",
    )
    actor_id: uuid.UUID | None = Field(
        default=None,
        description="The consuming application's end user this build is for. "
        "Recorded for cost attribution; it grants nothing.",
    )


class FailedChunk(BaseModel):
    index: int
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    error: str


class GraphBuildSummary(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    tenant_id: uuid.UUID
    application_id: uuid.UUID
    extraction_id: uuid.UUID
    extraction_version: int
    retry_of_id: uuid.UUID | None = None
    status: str
    provider: str
    model: str
    chunk_chars: int
    chunk_overlap: int
    chunk_count: int = 0
    failed_chunk_count: int = 0
    entity_count: int = 0
    relationship_count: int = 0
    failed_chunks: list[FailedChunk] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    triggered_by_kind: str
    triggered_by_user_id: uuid.UUID | None = None
    triggered_by_credential_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    created_at: datetime
    finished_at: datetime | None = None


class GraphBuildRead(GraphBuildSummary):
    """A build with every model call it made (role `graph` in the ledger)."""

    usage: list[ExtractionUsageRead] = Field(default_factory=list)


# --- Retrieval --------------------------------------------------------------------------


class GraphQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    max_hops: int = Field(default=2, ge=1, le=3)
    max_entities: int = Field(default=20, ge=1, le=100)
    max_relationships: int = Field(default=50, ge=1, le=200)
    include_subfolders: bool = True

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v


class GraphEntityResult(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    mention_count: int = 0
    source_ids: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)


class GraphRelationshipResult(BaseModel):
    source_entity_id: str
    source_name: str
    relation: str
    target_entity_id: str
    target_name: str
    description: str | None = None
    confidence: float | None = None
    raw_relation: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)


class GraphChunkResult(BaseModel):
    chunk_id: str
    source_id: str
    index: int
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    text: str


class GraphRetrievalResult(BaseModel):
    """Structured graph evidence. Not an LLM prompt: the context builder that
    combines it with vector results decides how to present it."""

    seed_entity_ids: list[str] = Field(default_factory=list)
    entities: list[GraphEntityResult] = Field(default_factory=list)
    relationships: list[GraphRelationshipResult] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    chunks: list[GraphChunkResult] = Field(default_factory=list)
