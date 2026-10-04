import uuid
from datetime import datetime

from pydantic import BaseModel, Field, computed_field

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


class SourceMove(BaseModel):
    """Move a source to another subject (folder) in the same application."""

    target_subject_id: uuid.UUID


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

    @computed_field
    @property
    def has_stored_content(self) -> bool:
        """True when Memora stored the bytes itself, so it can extract and
        serve them. Decided by the active storage backend, so clients never
        have to guess from the URI scheme."""
        from app.storage import get_storage

        return bool(self.storage_uri) and get_storage().owns(self.storage_uri)


class SourceDetail(SourceRead):
    """Source with the human-readable names of everything that owns it."""

    tenant_name: str | None = None
    application_name: str | None = None
    subject_external_id: str | None = None
    actor_external_id: str | None = None
