import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.enums import ResourceStatus

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_slug(v: str) -> str:
    v = v.strip().lower()
    if not SLUG_RE.match(v):
        raise ValueError(
            "slug must be lowercase alphanumeric words separated by single "
            "hyphens, e.g. 'legal-case-app'"
        )
    return v


class ApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=128)
    status: ResourceStatus = ResourceStatus.ACTIVE

    _slug_ok = field_validator("slug")(_validate_slug)


class ApplicationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=128)
    status: ResourceStatus | None = None

    @field_validator("slug")
    @classmethod
    def _slug_ok(cls, v: str | None) -> str | None:
        return None if v is None else _validate_slug(v)


class ApplicationRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime


class ApplicationWithCounts(ApplicationRead):
    tenant_name: str | None = None
    credential_count: int = 0
    actor_count: int = 0
    subject_count: int = 0
    source_count: int = 0
