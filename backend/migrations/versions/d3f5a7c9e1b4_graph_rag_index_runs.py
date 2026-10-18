"""Graph RAG index runs: durable state and a one-active-run-per-subject lease

Revision ID: d3f5a7c9e1b4
Revises: 9c3e5a7b1d20
Create Date: 2026-10-17

* graph_rag_index_runs -- one row per index run of a subject (status, plan, stats, cost, heartbeat).
  The partial unique index allows a single queued/running run per subject: the lease.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d3f5a7c9e1b4"
down_revision: str | None = "9c3e5a7b1d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_rag_index_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), nullable=True),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("stats", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_rag_index_runs_subject_created", "graph_rag_index_runs", ["subject_id", "created_at"])
    op.create_index("ix_graph_rag_index_runs_tenant_created", "graph_rag_index_runs", ["tenant_id", "created_at"])
    op.create_index(
        "ux_graph_rag_index_runs_one_active", "graph_rag_index_runs", ["subject_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ux_graph_rag_index_runs_one_active", table_name="graph_rag_index_runs")
    op.drop_index("ix_graph_rag_index_runs_tenant_created", table_name="graph_rag_index_runs")
    op.drop_index("ix_graph_rag_index_runs_subject_created", table_name="graph_rag_index_runs")
    op.drop_table("graph_rag_index_runs")
