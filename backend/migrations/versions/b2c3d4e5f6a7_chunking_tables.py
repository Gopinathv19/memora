"""Chunking: document_units, semantic_blocks, retrieval_chunks

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-25 21:10:00.000000+00:00

The chunking stage (docs/chunking.md). Three derived tables hanging off
`source_extractions`, all cascading from it. Ownership columns are
denormalized from the source for single-predicate scope checks.

No embedding vector or HNSW/tsvector indexes in this migration -- those
arrive with the retrieval phase. `embedding_text` and `token_count` are
produced now because they are part of chunking.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> list[sa.Column]:
    return [
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('subject_id', sa.UUID(), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
    ]


def _scope_fks() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    ]


def upgrade() -> None:
    # --- document_units -------------------------------------------------------
    op.create_table(
        'document_units',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('extraction_id', sa.UUID(), nullable=False),
        *_scope_columns(),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('heading_level', sa.Integer(), nullable=True),
        sa.Column('section_path', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.ForeignKeyConstraint(['extraction_id'], ['source_extractions.id'], ondelete='CASCADE'),
        *_scope_fks(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_document_units_extraction_id', 'document_units', ['extraction_id'])
    op.create_index('ix_document_units_tenant_id', 'document_units', ['tenant_id'])
    op.create_index('ix_document_units_application_id', 'document_units', ['application_id'])

    # --- semantic_blocks ------------------------------------------------------
    op.create_table(
        'semantic_blocks',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('extraction_id', sa.UUID(), nullable=False),
        *_scope_columns(),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('section_path', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('title', sa.String(length=512), nullable=True),
        sa.Column('unit_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('page_start', sa.Integer(), nullable=True),
        sa.Column('page_end', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['extraction_id'], ['source_extractions.id'], ondelete='CASCADE'),
        *_scope_fks(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_semantic_blocks_extraction_id', 'semantic_blocks', ['extraction_id'])
    op.create_index('ix_semantic_blocks_tenant_id', 'semantic_blocks', ['tenant_id'])
    op.create_index('ix_semantic_blocks_application_id', 'semantic_blocks', ['application_id'])

    # --- retrieval_chunks -----------------------------------------------------
    op.create_table(
        'retrieval_chunks',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('semantic_block_id', sa.UUID(), nullable=False),
        sa.Column('extraction_id', sa.UUID(), nullable=False),
        sa.Column('source_version', sa.Integer(), nullable=False),
        *_scope_columns(),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding_text', sa.Text(), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=False),
        sa.Column('content_type', sa.String(length=32), nullable=False),
        sa.Column('page_start', sa.Integer(), nullable=True),
        sa.Column('page_end', sa.Integer(), nullable=True),
        sa.Column('document_unit_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('section_path', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.ForeignKeyConstraint(['semantic_block_id'], ['semantic_blocks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['extraction_id'], ['source_extractions.id'], ondelete='CASCADE'),
        *_scope_fks(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_retrieval_chunks_block_id', 'retrieval_chunks', ['semantic_block_id'])
    op.create_index('ix_retrieval_chunks_extraction_id', 'retrieval_chunks', ['extraction_id'])
    op.create_index(
        'ix_retrieval_chunks_scope_active', 'retrieval_chunks',
        ['tenant_id', 'application_id', 'is_active'],
    )


def downgrade() -> None:
    op.drop_index('ix_retrieval_chunks_scope_active', table_name='retrieval_chunks')
    op.drop_index('ix_retrieval_chunks_extraction_id', table_name='retrieval_chunks')
    op.drop_index('ix_retrieval_chunks_block_id', table_name='retrieval_chunks')
    op.drop_table('retrieval_chunks')

    op.drop_index('ix_semantic_blocks_application_id', table_name='semantic_blocks')
    op.drop_index('ix_semantic_blocks_tenant_id', table_name='semantic_blocks')
    op.drop_index('ix_semantic_blocks_extraction_id', table_name='semantic_blocks')
    op.drop_table('semantic_blocks')

    op.drop_index('ix_document_units_application_id', table_name='document_units')
    op.drop_index('ix_document_units_tenant_id', table_name='document_units')
    op.drop_index('ix_document_units_extraction_id', table_name='document_units')
    op.drop_table('document_units')
