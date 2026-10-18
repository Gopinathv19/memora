"""One Graph RAG index run for a subject: planned, run, finished (plan Step 7).

Replaces the in-process `autoindex.BUILDS` dict for the unified build: the state is in Postgres, so it
survives a restart and is visible to every worker. The row is also the *lease*: a partial unique index
allows one `queued`/`running` run per subject, so two workers can never build the same subject at once.
That is enforced by the database in one statement, which also works behind a transaction pooler (a session
level advisory lock would not).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey

ACTIVE_STATUSES = ("queued", "running")


class GraphRagIndexRun(UUIDPrimaryKey, TimestampCreated, Base):
    __tablename__ = "graph_rag_index_runs"
    __table_args__ = (
        Index("ix_graph_rag_index_runs_subject_created", "subject_id", "created_at"),
        Index("ix_graph_rag_index_runs_tenant_created", "tenant_id", "created_at"),
        # one active run per subject
        Index(
            "ux_graph_rag_index_runs_one_active", "subject_id", unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    # queued -> running -> done | failed | blocked (cost cap) | interrupted (the worker died)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    trigger: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    # Sources to rebuild whatever their state; null = whatever the plan finds out of date.
    source_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "host:pid" of the process running it: at startup only a dead process's runs are interrupted, not another
    # worker's live ones.
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
