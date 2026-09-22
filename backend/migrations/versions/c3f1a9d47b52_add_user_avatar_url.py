"""Add user avatar url

Revision ID: c3f1a9d47b52
Revises: 5d39b5ba2882
Create Date: 2026-09-22 00:00:00.000000+00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f1a9d47b52'
down_revision: str | None = '5d39b5ba2882'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default so the NOT NULL holds for rows that already exist and for
    # inserts that do not go through SQLAlchemy.
    op.add_column(
        'users',
        sa.Column(
            'avatar_url',
            sa.String(length=1024),
            nullable=False,
            server_default='',
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'avatar_url')
