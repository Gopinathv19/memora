import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.source import Source


class TriggeredBy:
    """Who started an extraction run -- the key the cost ledger is grouped by.

    A console user and an API credential are the only two callers Memora has,
    so exactly one of the two ids is set, matching `triggered_by_kind`. The
    actor is the consuming application's own end user, when it says so.
    """

    triggered_by_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    triggered_by_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="SET NULL"),
        nullable=True,
    )


class SourceExtraction(UUIDPrimaryKey, TimestampCreated, TriggeredBy, Base):
    """One extraction run over a source: version 1, 2, 3, ...

    Runs are never overwritten. Re-extracting -- to retry a failure, or because
    the first result missed something -- adds the next version, so what the
    agent said last time stays readable next to what it says now.

    `tenant_id` and `application_id` are copied from the source, for the same
    reason the source copies them from its subject: every read is scoped, and
    the scope check should be one indexed predicate.
    """

    __tablename__ = "source_extractions"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_source_extractions_version"),
        Index("ix_source_extractions_source_id", "source_id"),
        Index("ix_source_extractions_tenant_id", "tenant_id"),
        Index("ix_source_extractions_application_id", "application_id"),
        Index("ix_source_extractions_status", "status"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
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

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="processing"
    )
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="standard"
    )
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # role -> model id actually configured for this run.
    models: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # An ExtractionResult (app/schemas/extraction.py), once the run finishes.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The whole document as the agent read it: every unit's Markdown in reading
    # order, each behind a `<!-- page 3 · hard · layout -->` marker, before any
    # truncation. The input to both the knowledge graph and the vector pipeline.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source: Mapped["Source"] = relationship(back_populates="extractions")
    # Only this run's own calls. Graph builds that read this version record
    # their calls against it too (with `graph_build_id` set); they are reported
    # on the build, not here. Rows are removed by the database's cascade.
    usage: Mapped[list["ExtractionUsage"]] = relationship(
        primaryjoin="and_(SourceExtraction.id == ExtractionUsage.extraction_id, "
        "ExtractionUsage.graph_build_id.is_(None))",
        viewonly=True,
        order_by="ExtractionUsage.created_at",
    )


class ExtractionUsage(UUIDPrimaryKey, TimestampCreated, TriggeredBy, Base):
    """The cost ledger: one row per model call an extraction made.

    Failed calls are recorded too, with whatever tokens the provider reported,
    because a failed call can still be billed.
    """

    __tablename__ = "extraction_usage"
    __table_args__ = (
        Index("ix_extraction_usage_extraction_id", "extraction_id"),
        Index("ix_extraction_usage_tenant_created", "tenant_id", "created_at"),
        Index("ix_extraction_usage_application_id", "application_id"),
        Index("ix_extraction_usage_graph_build_id", "graph_build_id"),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Set when the call belonged to a knowledge-graph build that read the
    # extraction above; NULL for the extraction run's own calls.
    graph_build_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_graph_builds.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
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

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # The page / slide / sheet the call was for (for a graph call, the first
    # page of its chunk); NULL for the final extract call.
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    # The rate from the operator's price list that produced cost_usd, with its
    # effective_from date. NULL means the model was not priced (cost $0).
    price: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    extraction: Mapped["SourceExtraction"] = relationship()
