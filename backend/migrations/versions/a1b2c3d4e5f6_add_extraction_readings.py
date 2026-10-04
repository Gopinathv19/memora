"""Add source_extractions.readings (the per-unit document representation)

Revision ID: a1b2c3d4e5f6
Revises: f4c1d2e3b5a6
Create Date: 2026-09-25 21:00:00.000000+00:00

The chunking stage (docs/chunking.md) derives DocumentUnits, SemanticBlocks
and RetrievalChunks from the per-page Markdown the extraction models produced.
Until now that text existed only as local variables inside `extract()` and was
discarded after the single extract call. This migration adds a `readings`
JSONB column to `source_extractions` so the document representation is
persisted per version, and chunks can be regenerated without re-extracting.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'f4c1d2e3b5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'source_extractions',
        sa.Column(
            'readings',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('source_extractions', 'readings')
