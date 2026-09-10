import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.enums import ResourceStatus


class SubjectCreate(BaseModel):
    external_id: str = Field(
        min_length=1,
        max_length=255,
        description="The application's own workspace identifier, e.g. "
        "'case-ABC-456' or 'customer_123'. Unique within the application.",
    )
    actor_id: uuid.UUID | None = Field(
        default=None,
        description="The actor that caused this workspace to be created. "
        "Recorded for audit; it does not restrict who may later access it.",
    )
    status: ResourceStatus = ResourceStatus.ACTIVE

    @field_validator("external_id")
    @classmethod
    def _ext_ok(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("external_id must not be blank")
        return v


class SubjectUpdate(BaseModel):
    status: ResourceStatus | None = None
    actor_id: uuid.UUID | None = None


class SubjectRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    application_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    external_id: str
    status: str
    created_at: datetime


class SubjectWithCounts(SubjectRead):
    application_name: str | None = None
    tenant_name: str | None = None
    actor_external_id: str | None = None
    source_count: int = 0
