import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.extraction import ExtractionUsage, TriggeredBy
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey


class SourceGraphBuild(UUIDPrimaryKey, TimestampCreated, TriggeredBy, Base):
    """One knowledge-graph build over a source (docs/graph-rag.md).

    A build reads one extraction version's stored `content`, and the graph then
    holds that version's facts for the source; a later full build replaces
    them. A retry (`retry_of_id` set) re-reads only the chunks the build it
    retries could not, against the same extraction version.

    The graph itself lives in FalkorDB. This row is the Postgres-side record:
    what was built from what, how it went, which chunks failed, what it cost.
    """

    __tablename__ = "source_graph_builds"
    __table_args__ = (
        Index("ix_source_graph_builds_source_id", "source_id"),
        Index("ix_source_graph_builds_tenant_id", "tenant_id"),
        Index("ix_source_graph_builds_application_id", "application_id"),
        Index("ix_source_graph_builds_status", "status"),
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
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    extraction_version: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_graph_builds.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="processing"
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    # The chunking this build used; a retry must chunk identically, or chunk
    # indexes would no longer line up with the ones that failed.
    chunk_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    relationship_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # [{"index": 3, "chunk_id": "...", "page_start": 2, "error": "..."}]
    failed_chunks: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # Counters from the stages (entities_created, entities_matched,
    # relationships_skipped, ...), for observability.
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    usage: Mapped[list[ExtractionUsage]] = relationship(
        primaryjoin="SourceGraphBuild.id == ExtractionUsage.graph_build_id",
        viewonly=True,
        order_by="ExtractionUsage.created_at",
    )
