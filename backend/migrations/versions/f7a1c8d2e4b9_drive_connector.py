"""Google Drive connector: connections and imports

Revision ID: f7a1c8d2e4b9
Revises: cd75bae887c4
Create Date: 2026-10-08

Two tables for the Drive connector (see docs/google-drive.md):

* drive_connections -- a console user's connected Drive account, with
  encrypted OAuth tokens.
* drive_imports -- one row per Drive file imported into the RAG pipeline,
  the V2 sync hook (NEW / MODIFIED / DELETED diffing).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7a1c8d2e4b9"
down_revision: str | None = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drive_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_user_id", sa.String(255), nullable=False),
        sa.Column("drive_account_email", sa.String(512), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token_expires_at", sa.Text(), nullable=False),
        sa.Column("granted_scopes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drive_connections_user_id", "drive_connections", ["user_id"])

    op.create_table(
        "drive_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drive_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drive_file_id", sa.String(255), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("modified_time", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sync_state", sa.String(32), nullable=False, server_default="imported"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drive_imports_connection", "drive_imports", ["connection_id"])
    op.create_index(
        "uq_drive_imports_conn_file",
        "drive_imports",
        ["connection_id", "drive_file_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_drive_imports_conn_file", table_name="drive_imports")
    op.drop_index("ix_drive_imports_connection", table_name="drive_imports")
    op.drop_table("drive_imports")
    op.drop_index("ix_drive_connections_user_id", table_name="drive_connections")
    op.drop_table("drive_connections")