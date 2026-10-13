"""extraction reading + document caches

Revision ID: c8d5f2a6b3e1
Revises: b7e2f9a4c1d8
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c8d5f2a6b3e1"
down_revision: str | None = "b7e2f9a4c1d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reading_cache_entries",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "document_cache_entries",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("readings", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("document_cache_entries")
    op.drop_table("reading_cache_entries")