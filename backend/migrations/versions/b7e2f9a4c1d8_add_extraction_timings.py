"""Add source_extractions.timings (per-phase wall-clock run timings)

Revision ID: b7e2f9a4c1d8
Revises: e9f3a7c1d5b8
Create Date: 2026-12-10 23:05:00.000000+00:00

The same document can take a minute in one run and ten in another; the
difference lives in the model calls (retries, rate limits, provider latency),
not in the pipeline. Until now nothing recorded where a run spent its time, so
a slow run could only be explained by re-reading logs. This migration adds a
`timings` JSONB column to `source_extractions` -- parse / read / windows /
metadata / agent / chunk / total, in milliseconds -- written as the run
progresses so even a failed run keeps what it measured.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b7e2f9a4c1d8'
down_revision: str | None = 'e9f3a7c1d5b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'source_extractions',
        sa.Column(
            'timings',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('source_extractions', 'timings')
