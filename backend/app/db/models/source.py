import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.actor import Actor
    from app.db.models.application import Application
    from app.db.models.extraction import SourceExtraction
    from app.db.models.subject import Subject
    from app.db.models.tenant import Tenant


class Source(UUIDPrimaryKey, TimestampCreated, Base):
    """A registered resource inside a subject: a PDF, an image, a URL, a chat.

    `tenant_id` and `application_id` are denormalized copies of the subject's
    owners. They are redundant by design: every read filters on the full
    ownership chain, and carrying the columns here means that check is one
    indexed predicate instead of a three-table join. The service layer is the
    only thing that writes them, and it always copies them from the parent
    subject rather than from client input.

    Extraction runs hang below a Source (`SourceExtraction`, versioned), and
    so do knowledge-graph builds (`SourceGraphBuild`); the graph itself lives
    in FalkorDB, keyed by the source id.
    """

    __tablename__ = "sources"
    __table_args__ = (
        Index("ix_sources_tenant_id", "tenant_id"),
        Index("ix_sources_application_id", "application_id"),
        Index("ix_sources_subject_id", "subject_id"),
        Index("ix_sources_status", "status"),
        # Serves the console's default "newest first, within this subject" list.
        Index("ix_sources_subject_created", "subject_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Audit only -- which actor registered this source. Distinct from ownership,
    # which the tenant/application/subject chain above already establishes.
    created_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="SET NULL"),
        nullable=True,
    )

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )

    tenant: Mapped["Tenant"] = relationship()
    application: Mapped["Application"] = relationship()
    subject: Mapped["Subject"] = relationship(back_populates="sources")
    created_by_actor: Mapped["Actor | None"] = relationship()
    extractions: Mapped[list["SourceExtraction"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SourceExtraction.version",
    )
