import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import StatusColumn, TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.application import Application


class ApiCredential(UUIDPrimaryKey, StatusColumn, TimestampCreated, Base):
    """A bearer token an application uses to authenticate to Memora.

    The raw token is never persisted -- only `token_hash`, an HMAC-SHA256 of
    the token keyed by API_SECRET (see app/core/security.py). `token_preview`
    holds a deliberately truncated fragment so the console can identify a row
    without ever handling the usable secret.
    """

    __tablename__ = "api_credentials"
    __table_args__ = (
        Index("ix_api_credentials_application_id", "application_id"),
        # Unique, not merely indexed: two credentials resolving to the same
        # hash would make authentication ambiguous.
        Index("ix_api_credentials_token_hash", "token_hash", unique=True),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    token_preview: Mapped[str] = mapped_column(String(32), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    application: Mapped["Application"] = relationship(back_populates="credentials")
