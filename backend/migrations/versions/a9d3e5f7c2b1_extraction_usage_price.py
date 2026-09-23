"""extraction_usage.price: the operator rate applied to each model call

Revision ID: a9d3e5f7c2b1
Revises: f4c1d2e3b5a6
Create Date: 2026-09-24 12:00:00.000000+00:00

Costs are now priced from the operator's versioned price list
(backend/pricing.json). Each usage row keeps a snapshot of the rate that was
applied, so a later price change never alters -- or obscures -- a past cost.
NULL means the model had no price when the call was made.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a9d3e5f7c2b1'
down_revision: str | None = 'f4c1d2e3b5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'extraction_usage',
        sa.Column('price', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('extraction_usage', 'price')
