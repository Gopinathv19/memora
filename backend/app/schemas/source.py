import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.enums import SourceStatus, SourceType


class SourceCreate(BaseModel):
    """Register a source from metadata alone.

    Note what the client may *not* send: tenant_id, application_id or
    subject_id. Those are derived from the subject in the URL path, so a client
    cannot attach a source to a tenant it does not own by supplying its own ids.
    """

    type: SourceType = SourceType.FILE
    mime_type: str | None = Field(default=None, max_length=255)
    filename: str | None = Field(default=None, max_length=512)
    storage_uri: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    status: SourceStatus = SourceStatus.PENDING
    created_by_actor_id: uuid.UUID | None = None


class SourceUpdate(BaseModel):
    status: SourceStatus | None = None
    filename: str | None = Field(default=None, max_length=512)
    mime_type: str | None = Field(default=None, max_length=255)
    storage_uri: str | None = None


class SourceRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    application_id: uuid.UUID
    subject_id: uuid.UUID
    created_by_actor_id: uuid.UUID | None = None
    type: str
    mime_type: str | None = None
    filename: str | None = None
    storage_uri: str | None = None
    size_bytes: int | None = None
    status: str
    created_at: datetime


class SourceDetail(SourceRead):
    """Source with the human-readable names of everything that owns it."""

    tenant_name: str | None = None
    application_name: str | None = None
    subject_external_id: str | None = None
    actor_external_id: str | None = None
