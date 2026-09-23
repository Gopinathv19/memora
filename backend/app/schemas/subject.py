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
        "'case-ABC-456' or 'customer_123'. Unique among siblings: no other "
        "root (or child of the same parent) in this application may share it.",
    )
    parent_subject_id: uuid.UUID | None = Field(
        default=None,
        description="The subject this one lives inside, when it is a folder. "
        "Omitted for a root workspace. Must belong to the same application.",
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
    """Rename, move, or change lifecycle fields.

    `external_id` renames the subject and must stay unique among its siblings;
    `parent_subject_id` moves it, and cannot be the subject itself or any of
    its descendants (that would create a cycle).
    """

    external_id: str | None = Field(default=None, min_length=1, max_length=255)
    parent_subject_id: uuid.UUID | None = None
    status: ResourceStatus | None = None
    actor_id: uuid.UUID | None = None

    @field_validator("external_id")
    @classmethod
    def _ext_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("external_id must not be blank")
        return v


class SubjectRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    application_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    parent_subject_id: uuid.UUID | None = None
    external_id: str
    status: str
    created_at: datetime


class SubjectPathEntry(ORMModel):
    """One hop of the ancestor chain, nearest first (immediate parent, then
    its parent, and so on up to the root)."""

    id: uuid.UUID
    external_id: str


class SubjectWithCounts(SubjectRead):
    application_name: str | None = None
    tenant_name: str | None = None
    actor_external_id: str | None = None
    source_count: int = 0
    child_count: int = 0
    path: list[SubjectPathEntry] = []
