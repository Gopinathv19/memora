import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.enums import ResourceStatus


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    status: ResourceStatus = ResourceStatus.ACTIVE

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: ResourceStatus | None = None


class TenantRead(ORMModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime


class TenantWithCounts(TenantRead):
    """Tenant plus the aggregate counts the console's list view renders."""

    application_count: int = 0
    subject_count: int = 0
    source_count: int = 0
