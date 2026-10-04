"""Knowledge graph: extraction content, source_graph_builds, usage.graph_build_id

Revision ID: b6d4e8f1a2c3
Revises: a9d3e5f7c2b1
Create Date: 2026-09-25 10:00:00.000000+00:00

The graph stage below extraction (docs/graph-rag.md).

* `source_extractions.content` keeps the merged Markdown the agent read --
  until now it was built and thrown away. Both the graph and the vector
  pipeline read it. Runs from before this revision have NULL and need a
  re-extract before a graph can be built from them.
* `source_graph_builds` records every graph build: which extraction version it
  read, how it went, which chunks failed, what it cost. The graph itself is in
  FalkorDB.
* `extraction_usage.graph_build_id` lets graph model calls share the one cost
  ledger. Their `extraction_id` is the version the build read.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b6d4e8f1a2c3'
down_revision: str | None = 'a9d3e5f7c2b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('source_extractions', sa.Column('content', sa.Text(), nullable=True))

    op.create_table(
        'source_graph_builds',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('extraction_id', sa.UUID(), nullable=False),
        sa.Column('extraction_version', sa.Integer(), nullable=False),
        sa.Column('retry_of_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=32), server_default='processing', nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=255), nullable=False),
        sa.Column('chunk_chars', sa.Integer(), nullable=False),
        sa.Column('chunk_overlap', sa.Integer(), nullable=False),
        sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_chunk_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('entity_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('relationship_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_chunks', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Float(), server_default='0', nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('triggered_by_kind', sa.String(length=16), nullable=False),
        sa.Column('triggered_by_user_id', sa.UUID(), nullable=True),
        sa.Column('triggered_by_credential_id', sa.UUID(), nullable=True),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['extraction_id'], ['source_extractions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['retry_of_id'], ['source_graph_builds.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['triggered_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['triggered_by_credential_id'], ['api_credentials.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['actor_id'], ['actors.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_source_graph_builds_source_id', 'source_graph_builds', ['source_id'])
    op.create_index('ix_source_graph_builds_tenant_id', 'source_graph_builds', ['tenant_id'])
    op.create_index('ix_source_graph_builds_application_id', 'source_graph_builds', ['application_id'])
    op.create_index('ix_source_graph_builds_status', 'source_graph_builds', ['status'])

    op.add_column('extraction_usage', sa.Column('graph_build_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'extraction_usage_graph_build_id_fkey',
        'extraction_usage',
        'source_graph_builds',
        ['graph_build_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_index('ix_extraction_usage_graph_build_id', 'extraction_usage', ['graph_build_id'])


def downgrade() -> None:
    op.drop_index('ix_extraction_usage_graph_build_id', table_name='extraction_usage')
    op.drop_constraint('extraction_usage_graph_build_id_fkey', 'extraction_usage', type_='foreignkey')
    op.drop_column('extraction_usage', 'graph_build_id')
    op.drop_index('ix_source_graph_builds_status', table_name='source_graph_builds')
    op.drop_index('ix_source_graph_builds_application_id', table_name='source_graph_builds')
    op.drop_index('ix_source_graph_builds_tenant_id', table_name='source_graph_builds')
    op.drop_index('ix_source_graph_builds_source_id', table_name='source_graph_builds')
    op.drop_table('source_graph_builds')
    op.drop_column('source_extractions', 'content')
