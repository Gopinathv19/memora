import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.enums import (
    ExtractionMode,
    ExtractionRoute,
    ExtractionStatus,
    PageDifficulty,
)

MAX_INSTRUCTIONS_CHARS = 2000


# --- The extracted information ------------------------------------------------
# One generic shape for every kind of document. The model chooses the field
# keys; nothing here is specific to invoices, IDs or contracts. Every value
# carries the page it came from, so it can be traced back to the original file.


class ExtractedField(BaseModel):
    key: str = Field(description="snake_case name of the fact, e.g. 'invoice_number'")
    value: str
    page: int | None = Field(default=None, description="1-based page/slide/sheet")
    confidence: float | None = Field(default=None, ge=0, le=1)


class ExtractedTable(BaseModel):
    title: str | None = None
    page: int | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class PageProvenance(BaseModel):
    """How one unit of the document was read -- the audit trail of routing."""

    page: int
    kind: str = Field(description="page | slide | sheet | image | document")
    difficulty: PageDifficulty
    route: ExtractionRoute
    model: str | None = None
    status: str = Field(description="ok | fallback | failed | skipped")
    note: str | None = None


class ExtractionResult(BaseModel):
    source_id: uuid.UUID
    document_type: str = "unknown"
    title: str | None = None
    language: str | None = None
    summary: str = ""
    fields: list[ExtractedField] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    pages: list[PageProvenance] = Field(default_factory=list)
    status: ExtractionStatus = ExtractionStatus.COMPLETED
    warnings: list[str] = Field(default_factory=list)


# --- Requests -------------------------------------------------------------------


class ExtractionRequest(BaseModel):
    """Start a new extraction version: first run, retry, or re-extract."""

    mode: ExtractionMode = ExtractionMode.STANDARD
    instructions: str | None = Field(
        default=None,
        max_length=MAX_INSTRUCTIONS_CHARS,
        description="Optional hint for the agent, e.g. 'capture the premium "
        "table on page 3'. Guidance only: it cannot change the output format.",
    )
    actor_id: uuid.UUID | None = Field(
        default=None,
        description="The consuming application's end user this run is for. "
        "Recorded for cost attribution; it grants nothing.",
    )

    @field_validator("instructions")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


# --- Responses ------------------------------------------------------------------


class ExtractionSummary(ORMModel):
    """A version without its (potentially large) result."""

    id: uuid.UUID
    source_id: uuid.UUID
    tenant_id: uuid.UUID
    application_id: uuid.UUID
    version: int
    status: str
    mode: str
    instructions: str | None = None
    provider: str
    models: dict[str, str] = Field(default_factory=dict)
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


class ExtractionUsageRead(ORMModel):
    id: uuid.UUID
    provider: str
    model: str
    role: str
    page: int | None = None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    price: dict | None = Field(
        default=None,
        description="The operator's rate applied to this call, with its "
        "effective_from date; null when the model is not priced.",
    )
    latency_ms: int
    status: str
    error: str | None = None
    created_at: datetime


class ExtractionRead(ExtractionSummary):
    result: ExtractionResult | None = None
    usage: list[ExtractionUsageRead] = Field(default_factory=list)


class UsageTotals(BaseModel):
    runs: int = 0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class UsageGroup(UsageTotals):
    key: str
    label: str | None = None


class UsageReport(BaseModel):
    """Extraction usage and cost inside the caller's scope."""

    totals: UsageTotals
    by_application: list[UsageGroup]
    by_model: list[UsageGroup]
    by_trigger: list[UsageGroup]
