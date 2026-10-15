"""Graph RAG run log: the data the Compare dashboard reads.

Until now a question's timings, token usage and evidence lived only in the
HTTP response. `RagRun` stores one row per pipeline execution (vector, graph or
hybrid), so the dashboard can show trends and so a recommendation is computed
from what was measured. `BenchmarkRun` stores one evaluation of the pipelines
over a question set: aggregates and per-question results as JSON.
"""

import uuid

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey


class RagRun(UUIDPrimaryKey, TimestampCreated, Base):
    __tablename__ = "rag_runs"
    __table_args__ = (
        Index("ix_rag_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_rag_runs_compare_id", "compare_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    # Runs made by one Arena question share a compare_id.
    compare_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pipeline: Mapped[str] = mapped_column(String(16), nullable=False)  # vector | graph | hybrid
    technique: Mapped[str | None] = mapped_column(String(24), nullable=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    timings_ms: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    chunk_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    trace: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class BenchmarkRun(UUIDPrimaryKey, TimestampCreated, Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (Index("ix_benchmark_runs_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # {pipeline_key: {metric: value, ...}} and {pipeline_key: {query_type: {...}}}
    aggregates: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    by_type: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # One entry per question with every pipeline's result: powers drill-in.
    results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    build_cost: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
