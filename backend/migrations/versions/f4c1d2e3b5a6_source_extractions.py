"""Extraction Agent: source_extractions (versioned runs) and extraction_usage

Revision ID: f4c1d2e3b5a6
Revises: e8b2c4f7a913
Create Date: 2026-09-24 10:00:00.000000+00:00

The first processing stage below Source (docs/extraction-agent.md). Every run
of the Extraction Agent over a source is a new `source_extractions` row with
the next `version`; nothing is overwritten. `extraction_usage` is the cost
ledger: one row per model call, attributed to whoever triggered the run.

`sources` itself is not altered -- its existing `status` column is what the
runs advance. Both new tables cascade from sources, so deleting a source (or
its subject) removes its extraction history with it.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f4c1d2e3b5a6'
down_revision: str | None = 'e8b2c4f7a913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _triggered_by_columns() -> list[sa.Column]:
    return [
        sa.Column('triggered_by_kind', sa.String(length=16), nullable=False),
        sa.Column('triggered_by_user_id', sa.UUID(), nullable=True),
        sa.Column('triggered_by_credential_id', sa.UUID(), nullable=True),
        sa.Column('actor_id', sa.UUID(), nullable=True),
    ]


def _triggered_by_fks() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(['triggered_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['triggered_by_credential_id'], ['api_credentials.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['actor_id'], ['actors.id'], ondelete='SET NULL'),
    ]


def upgrade() -> None:
    op.create_table(
        'source_extractions',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), server_default='processing', nullable=False),
        sa.Column('mode', sa.String(length=16), server_default='standard', nullable=False),
        sa.Column('instructions', sa.Text(), nullable=True),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('models', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Float(), server_default='0', nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        *_triggered_by_columns(),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
        *_triggered_by_fks(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_id', 'version', name='uq_source_extractions_version'),
    )
    op.create_index('ix_source_extractions_source_id', 'source_extractions', ['source_id'])
    op.create_index('ix_source_extractions_tenant_id', 'source_extractions', ['tenant_id'])
    op.create_index('ix_source_extractions_application_id', 'source_extractions', ['application_id'])
    op.create_index('ix_source_extractions_status', 'source_extractions', ['status'])

    op.create_table(
        'extraction_usage',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('extraction_id', sa.UUID(), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Float(), server_default='0', nullable=False),
        sa.Column('latency_ms', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        *_triggered_by_columns(),
        sa.ForeignKeyConstraint(['extraction_id'], ['source_extractions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
        *_triggered_by_fks(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_extraction_usage_extraction_id', 'extraction_usage', ['extraction_id'])
    op.create_index('ix_extraction_usage_tenant_created', 'extraction_usage', ['tenant_id', 'created_at'])
    op.create_index('ix_extraction_usage_application_id', 'extraction_usage', ['application_id'])


def downgrade() -> None:
    op.drop_index('ix_extraction_usage_application_id', table_name='extraction_usage')
    op.drop_index('ix_extraction_usage_tenant_created', table_name='extraction_usage')
    op.drop_index('ix_extraction_usage_extraction_id', table_name='extraction_usage')
    op.drop_table('extraction_usage')
    op.drop_index('ix_source_extractions_status', table_name='source_extractions')
    op.drop_index('ix_source_extractions_application_id', table_name='source_extractions')
    op.drop_index('ix_source_extractions_tenant_id', table_name='source_extractions')
    op.drop_index('ix_source_extractions_source_id', table_name='source_extractions')
    op.drop_table('source_extractions')
