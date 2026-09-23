import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.enums import ResourceStatus


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = None


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: ResourceStatus | None = None
    expires_at: datetime | None = None


class CredentialRead(ORMModel):
    """The only credential shape ever read back from the database.

    Note what is absent: `token_hash`. It is not in this schema, so no endpoint
    returning a CredentialRead can leak it even by accident.
    """

    id: uuid.UUID
    application_id: uuid.UUID
    name: str
    token_preview: str
    status: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


class CredentialCreated(CredentialRead):
    """Returned exactly once, from the create call, and never again.

    `token` is present here and nowhere else in the API surface, because after
    this response the raw token no longer exists anywhere in Memora.
    """

    token: str
    warning: str = (
        "Store this token securely. It will not be shown again."
    )
