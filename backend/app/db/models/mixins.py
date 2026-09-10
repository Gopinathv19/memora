import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKey:
    """Server-side UUID primary key.

    `gen_random_uuid()` ships with PostgreSQL 13+ (and Neon), so no pgcrypto
    extension is required. A Python-side default is also set so objects have an
    id before flush, which keeps service code simple.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        default=uuid.uuid4,
    )


class TimestampCreated:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class StatusColumn:
    """Lifecycle status stored as free-form VARCHAR.

    Kept as a string rather than a PostgreSQL ENUM on purpose: adding a new
    status later is then an application change, not a migration that rewrites
    a type other tables depend on. Allowed values are enforced by the Pydantic
    schemas at the edge.
    """

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active"
    )
