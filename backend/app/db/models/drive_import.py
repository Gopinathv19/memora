import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey


class DriveImport(UUIDPrimaryKey, TimestampCreated, Base):
    """One Drive file imported into the RAG pipeline, and where it landed.

    This is the V2 sync hook: a later worker lists the Drive account again
    and compares `modified_time` against this row to decide NEW (no row) /
    MODIFIED (Drive newer) / DELETED (row without a Drive file). V1 only
    inserts rows; nothing reads them yet.
    """

    __tablename__ = "drive_imports"
    __table_args__ = (
        Index("ix_drive_imports_connection", "connection_id"),
        Index("uq_drive_imports_conn_file", "connection_id", "drive_file_id",
              unique=True),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drive_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The source row the file became -- the RAG pipeline's own handle.
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    drive_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    modified_time: Mapped[str] = mapped_column(Text, nullable=False)  # RFC-3339
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 'imported' | 're-ingested' (V2) -- V1 only ever writes 'imported'.
    sync_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="imported"
    )