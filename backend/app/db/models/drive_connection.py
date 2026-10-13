import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey


class DriveConnection(UUIDPrimaryKey, TimestampCreated, Base):
    """A console user's connected Google Drive account.

    One row per (user, Google account). The OAuth tokens are stored
    encrypted (see app/connectors/google_drive/oauth.py) -- never in
    plaintext, never in a response, never in a log line. Deleting the row
    disconnects the account: nothing else references it.

    V2 note: a later sync worker reads `drive_account_email` and the stored
    refresh token to diff NEW / MODIFIED / DELETED Drive files against the
    `drive_imports` mapping table.
    """

    __tablename__ = "drive_connections"

    # The console user who granted access. The connector's every read is
    # scoped to this user's tenants.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Google's stable id for the Drive account ("sub" claim).
    google_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_account_email: Mapped[str] = mapped_column(String(512), nullable=False)
    # Encrypted access + refresh tokens (Fernet, key derived from API_SECRET).
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_expires_at: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # ISO-8601
    # Read-only scope actually granted, recorded for least-privilege auditing.
    granted_scopes: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active"
    )