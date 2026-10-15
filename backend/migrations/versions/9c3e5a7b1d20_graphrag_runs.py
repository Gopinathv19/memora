"""Graph RAG run log and benchmark runs

Revision ID: 9c3e5a7b1d20
Revises: f7a1c8d2e4b9
Create Date: 2026-10-15

* rag_runs       -- one row per pipeline execution (vector / graph / hybrid):
                    timings, tokens, cost, retrieved chunks and the graph trace.
* benchmark_runs -- one evaluation of the pipelines over a question set.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9c3e5a7b1d20"
down_revision: str | None = "f7a1c8d2e4b9"
branch_labels = None
depends_on = None

_uuid_pk = lambda: sa.Column(  # noqa: E731
    "id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
)
_created = lambda: sa.Column(  # noqa: E731
    "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
)


def upgrade() -> None:
    op.create_table(
        "rag_runs",
        _uuid_pk(),
        _created(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("compare_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pipeline", sa.String(16), nullable=False),
        sa.Column("technique", sa.String(24), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("timings_ms", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("chunk_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("trace", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_runs_tenant_created", "rag_runs", ["tenant_id", "created_at"])
    op.create_index("ix_rag_runs_compare_id", "rag_runs", ["compare_id"])

    op.create_table(
        "benchmark_runs",
        _uuid_pk(),
        _created(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dataset_sha", sa.String(64), nullable=True),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("aggregates", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("by_type", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("results", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("build_cost", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_benchmark_runs_tenant_created", "benchmark_runs", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_runs_tenant_created", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
    op.drop_index("ix_rag_runs_compare_id", table_name="rag_runs")
    op.drop_index("ix_rag_runs_tenant_created", table_name="rag_runs")
    op.drop_table("rag_runs")
