"""initial memora schema

Creates the full ownership chain in dependency order:

    tenants -> applications -> actors -> subjects -> sources

plus api_credentials hanging off applications. Table order matters here: each
table's foreign keys must already exist when it is created.


Revision ID: 1881c01963c8
Revises: 
Create Date: 2026-09-10 18:54:25.469742+00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '1881c01963c8'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tenants',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('applications',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slug', sa.String(length=128), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'slug', name='uq_applications_tenant_slug')
    )
    op.create_index('ix_applications_tenant_id', 'applications', ['tenant_id'], unique=False)
    op.create_table('actors',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('type', sa.String(length=32), server_default='user', nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'external_id', name='uq_actors_application_external_id')
    )
    op.create_index('ix_actors_application_id', 'actors', ['application_id'], unique=False)
    op.create_index('ix_actors_tenant_id', 'actors', ['tenant_id'], unique=False)
    op.create_table('api_credentials',
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('token_preview', sa.String(length=32), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_credentials_application_id', 'api_credentials', ['application_id'], unique=False)
    op.create_index('ix_api_credentials_token_hash', 'api_credentials', ['token_hash'], unique=True)
    op.create_table('subjects',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['actors.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'external_id', name='uq_subjects_application_external_id')
    )
    op.create_index('ix_subjects_actor_id', 'subjects', ['actor_id'], unique=False)
    op.create_index('ix_subjects_application_id', 'subjects', ['application_id'], unique=False)
    op.create_index('ix_subjects_tenant_id', 'subjects', ['tenant_id'], unique=False)
    op.create_table('sources',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('subject_id', sa.UUID(), nullable=False),
    sa.Column('created_by_actor_id', sa.UUID(), nullable=True),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('mime_type', sa.String(length=255), nullable=True),
    sa.Column('filename', sa.String(length=512), nullable=True),
    sa.Column('storage_uri', sa.Text(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=32), server_default='pending', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by_actor_id'], ['actors.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sources_application_id', 'sources', ['application_id'], unique=False)
    op.create_index('ix_sources_status', 'sources', ['status'], unique=False)
    op.create_index('ix_sources_subject_created', 'sources', ['subject_id', 'created_at'], unique=False)
    op.create_index('ix_sources_subject_id', 'sources', ['subject_id'], unique=False)
    op.create_index('ix_sources_tenant_id', 'sources', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_sources_tenant_id', table_name='sources')
    op.drop_index('ix_sources_subject_id', table_name='sources')
    op.drop_index('ix_sources_subject_created', table_name='sources')
    op.drop_index('ix_sources_status', table_name='sources')
    op.drop_index('ix_sources_application_id', table_name='sources')
    op.drop_table('sources')
    op.drop_index('ix_subjects_tenant_id', table_name='subjects')
    op.drop_index('ix_subjects_application_id', table_name='subjects')
    op.drop_index('ix_subjects_actor_id', table_name='subjects')
    op.drop_table('subjects')
    op.drop_index('ix_api_credentials_token_hash', table_name='api_credentials')
    op.drop_index('ix_api_credentials_application_id', table_name='api_credentials')
    op.drop_table('api_credentials')
    op.drop_index('ix_actors_tenant_id', table_name='actors')
    op.drop_index('ix_actors_application_id', table_name='actors')
    op.drop_table('actors')
    op.drop_index('ix_applications_tenant_id', table_name='applications')
    op.drop_table('applications')
    op.drop_table('tenants')
