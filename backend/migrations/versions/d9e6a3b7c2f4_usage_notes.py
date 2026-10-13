"""extraction_usage.notes

Revision ID: d9e6a3b7c2f4
Revises: c8d5f2a6b3e1
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa

revision: str = "d9e6a3b7c2f4"
down_revision: str | None = "c8d5f2a6b3e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_usage",
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_usage", "notes")