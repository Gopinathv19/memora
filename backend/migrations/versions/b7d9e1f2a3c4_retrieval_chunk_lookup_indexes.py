"""Retrieval chunk lookup indexes: subject_id and source_id over live chunks

Revision ID: b7d9e1f2a3c4
Revises: cd75bae887c4
Create Date: 2026-10-12 00:00:00.000000+00:00

`retrieval_chunks` had indexes for the block, the extraction and the
(tenant, application, is_active) scope, but none for the two columns the hot
paths filter on:

* every retrieval query from Ping narrows the HNSW candidates to one subject
  (`rc.subject_id = :subject_id`), and
* the per-source embedding status that Ping polls while a source is indexing
  joins `chunk_embeddings` to `retrieval_chunks` by `source_id`.

Both only ever look at active chunks, so the indexes are partial on
`is_active`, which keeps them small as sources are re-extracted. No data
changes; no column changes.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d9e1f2a3c4'
down_revision: str | None = 'cd75bae887c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_retrieval_chunks_subject_active',
        'retrieval_chunks',
        ['subject_id'],
        unique=False,
        postgresql_where=sa.text('is_active'),
    )
    op.create_index(
        'ix_retrieval_chunks_source_active',
        'retrieval_chunks',
        ['source_id'],
        unique=False,
        postgresql_where=sa.text('is_active'),
    )


def downgrade() -> None:
    op.drop_index('ix_retrieval_chunks_source_active', table_name='retrieval_chunks')
    op.drop_index('ix_retrieval_chunks_subject_active', table_name='retrieval_chunks')
