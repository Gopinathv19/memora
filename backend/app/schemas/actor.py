import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.enums import ActorType, ResourceStatus


def _clean_external_id(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("external_id must not be blank")
    return v


class ActorCreate(BaseModel):
    external_id: str = Field(
        min_length=1,
        max_length=255,
        description="The consuming application's own identifier for this actor, "
        "e.g. 'lawyer_123'. Memora does not own the customer's identity system.",
    )
    type: ActorType = ActorType.USER
    name: str | None = Field(default=None, max_length=255)
    status: ResourceStatus = ResourceStatus.ACTIVE

    _ext_ok = field_validator("external_id")(_clean_external_id)


class ActorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    type: ActorType | None = None
    status: ResourceStatus | None = None


class ActorRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    application_id: uuid.UUID
    external_id: str
    type: str
    name: str | None = None
    status: str
    created_at: datetime


class ActorWithCounts(ActorRead):
    application_name: str | None = None
    subject_count: int = 0
