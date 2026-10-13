"""llm json cache

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b2d4f6a8c0e1"
down_revision: str | None = "a1c3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_cache_entries",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_cache_entries")
