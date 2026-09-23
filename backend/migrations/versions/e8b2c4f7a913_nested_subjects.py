"""Nested subjects: parent_subject_id and sibling-scoped uniqueness

Revision ID: e8b2c4f7a913
Revises: c3f1a9d47b52
Create Date: 2026-09-23 01:12:00.000000+00:00

A subject can now be a folder inside another subject, to any depth. The tree
is an adjacency list: `parent_subject_id` is NULL for a root and points at the
containing subject otherwise. `ON DELETE CASCADE` on the self-foreign key
means deleting a subject deletes its whole subtree, and the existing
subjects -> sources cascade removes their rows with it.

Uniqueness of `external_id` moves from "unique per application" to "unique
among siblings", so two folders may share a name as long as they have
different parents. PostgreSQL treats NULLs as distinct in unique indexes, so
the rule needs two partial indexes: one for roots, one for children. Every
existing row is a root, so the root index is exactly as strict as the
constraint it replaces and no data migration is needed.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'e8b2c4f7a913'
down_revision: str | None = 'c3f1a9d47b52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'subjects',
        sa.Column('parent_subject_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_subjects_parent_subject_id',
        'subjects',
        'subjects',
        ['parent_subject_id'],
        ['id'],
        ondelete='CASCADE',
    )
    # Serves "list the children of this folder" and the sibling-uniqueness
    # partial index below.
    op.create_index(
        'ix_subjects_parent_subject_id',
        'subjects',
        ['parent_subject_id'],
        unique=False,
    )
    op.drop_constraint(
        'uq_subjects_application_external_id', 'subjects', type_='unique'
    )
    op.create_index(
        'uq_subjects_root_external_id',
        'subjects',
        ['application_id', 'external_id'],
        unique=True,
        postgresql_where=sa.text('parent_subject_id IS NULL'),
    )
    op.create_index(
        'uq_subjects_sibling_external_id',
        'subjects',
        ['application_id', 'parent_subject_id', 'external_id'],
        unique=True,
        postgresql_where=sa.text('parent_subject_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_subjects_sibling_external_id', table_name='subjects')
    op.drop_index('uq_subjects_root_external_id', table_name='subjects')
    op.create_unique_constraint(
        'uq_subjects_application_external_id',
        'subjects',
        ['application_id', 'external_id'],
    )
    op.drop_index('ix_subjects_parent_subject_id', table_name='subjects')
    op.drop_constraint('fk_subjects_parent_subject_id', 'subjects', type_='foreignkey')
    op.drop_column('subjects', 'parent_subject_id')
