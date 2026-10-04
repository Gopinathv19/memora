"""Embedding subsystem: embedding_models, embedding_strategies, chunk_embeddings

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-26 17:15:00.000000+00:00

The embedding stage (docs/embeddings.md). Three tables:

* embedding_models      -- an actual model at a provider (data, not code)
* embedding_strategies  -- how Memora uses a model (input, templates, metric)
* chunk_embeddings      -- one embedding per (chunk, strategy, input version)

The vector column is `vector(2048)` -- the exact dimension of the default
NVIDIA Nemotron 3 Embed 1B strategy -- so a dimension mismatch fails at the
database boundary as well as at the service boundary.

Vector index: HNSW with cosine distance. With L2-normalized vectors, cosine
distance and inner-product ranking are equivalent; cosine is kept as the
explicit, strategy-declared metric. Parameters m=16 and ef_construction=64
are pgvector's sensible defaults; they are configurable through settings for
future tuning without a code change.

One wrinkle: HNSW over `vector` caps at 2000 dimensions, and the default
model emits 2048. The index therefore covers the column cast to `halfvec`
(pgvector's documented approach for dims > 2000): HNSW over halfvec allows
up to 4000 dimensions, and half precision costs nothing measurable on
L2-normalized vectors ranked by cosine. Retrieval queries use the same cast:

    ORDER BY embedding::halfvec(2048) <=> query::halfvec(2048)

The migration seeds the default model + strategy. No fake embeddings.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'b2c3d4e5f6a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# HNSW parameters (documented here; tunable via settings in later migrations).
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64


def upgrade() -> None:
    # --- pgvector extension ---------------------------------------------------
    # CREATE EXTENSION is idempotent; Neon supports pgvector.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- embedding_models -----------------------------------------------------
    op.create_table(
        'embedding_models',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('model_name', sa.String(length=128), nullable=False),
        sa.Column('model_identifier', sa.String(length=256), nullable=False),
        sa.Column('model_version', sa.String(length=64), nullable=True),
        sa.Column('embedding_type', sa.String(length=32), server_default='dense', nullable=False),
        sa.Column('dimension', sa.Integer(), nullable=False),
        sa.Column('max_input_tokens', sa.Integer(), nullable=True),
        sa.Column('normalization', sa.String(length=32), server_default='l2', nullable=False),
        sa.Column('similarity_metric', sa.String(length=32), server_default='cosine', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'model_identifier', name='uq_embedding_models_provider_identifier'),
    )
    op.create_index('ix_embedding_models_identifier', 'embedding_models', ['model_identifier'])

    # --- embedding_strategies -------------------------------------------------
    op.create_table(
        'embedding_strategies',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('model_id', sa.UUID(), nullable=False),
        sa.Column('input_type', sa.String(length=64), server_default='embedding_text', nullable=False),
        sa.Column('document_template', sa.String(length=512), server_default='{input}', nullable=False),
        sa.Column('query_template', sa.String(length=512), server_default='{input}', nullable=False),
        sa.Column('normalization', sa.String(length=32), server_default='l2', nullable=False),
        sa.Column('similarity_metric', sa.String(length=32), server_default='cosine', nullable=False),
        sa.Column('dimension', sa.Integer(), nullable=False),
        sa.Column('configuration_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_embedding_strategies_name'),
        sa.ForeignKeyConstraint(['model_id'], ['embedding_models.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_embedding_strategies_model_id', 'embedding_strategies', ['model_id'])

    # --- chunk_embeddings -----------------------------------------------------
    op.create_table(
        'chunk_embeddings',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('chunk_id', sa.UUID(), nullable=False),
        sa.Column('strategy_id', sa.UUID(), nullable=False),
        # vector(2048): the dimension of the default strategy's model. Typed
        # exactly, so a wrong-dimension vector is rejected by PostgreSQL.
        sa.Column('embedding', postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column('embedding_status', sa.String(length=32), server_default='pending', nullable=False),
        sa.Column('input_hash', sa.String(length=64), nullable=False),
        sa.Column('model_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('embedded_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chunk_id', 'strategy_id', 'input_hash', name='uq_chunk_embeddings_chunk_strategy_input'),
        sa.ForeignKeyConstraint(['chunk_id'], ['retrieval_chunks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['strategy_id'], ['embedding_strategies.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_chunk_embeddings_chunk_id', 'chunk_embeddings', ['chunk_id'])
    op.create_index('ix_chunk_embeddings_strategy_id', 'chunk_embeddings', ['strategy_id'])
    op.create_index('ix_chunk_embeddings_status', 'chunk_embeddings', ['embedding_status'])
    op.create_index('ix_chunk_embeddings_created_at', 'chunk_embeddings', ['created_at'])

    # The vector column: typed, not a generic array. Done with raw DDL because
    # the migration is the single source of schema truth.
    op.execute("ALTER TABLE chunk_embeddings ALTER COLUMN embedding TYPE vector(2048)")

    # --- HNSW vector index ------------------------------------------------------
    # Distance metric: cosine, matching the strategy's similarity_metric.
    # Index type: HNSW. Parameters: m=16, ef_construction=64 (pgvector
    # defaults; tunable via settings in a later migration).
    #
    # The column is cast to halfvec: HNSW over `vector` is capped at 2000
    # dimensions by PostgreSQL, and the default model emits 2048. halfvec
    # allows up to 4000; retrieval queries must use the same cast.
    op.execute(
        "CREATE INDEX ix_chunk_embeddings_embedding_hnsw "
        "ON chunk_embeddings USING hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops) "
        f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"
    )

    # --- Seed the default model + strategy --------------------------------------
    # The default strategy is data, seeded here so a fresh deployment has a
    # working embedding configuration without manual setup. No fake embeddings.
    op.execute(
        sa.text(
            """
            INSERT INTO embedding_models (
                provider, model_name, model_identifier, model_version,
                embedding_type, dimension, max_input_tokens,
                normalization, similarity_metric, is_active
            )
            VALUES (
                'nvidia', 'Nemotron 3 Embed 1B', 'nvidia/nemotron-3-embed-1b', 'v1',
                'dense', 2048, 8192,
                'l2', 'cosine', true
            )
            ON CONFLICT (provider, model_identifier) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO embedding_strategies (
                name, description, model_id, input_type,
                document_template, query_template,
                normalization, similarity_metric, dimension,
                configuration_json, is_active
            )
            SELECT
                'Memora Dense v1',
                'Default dense strategy: NVIDIA Nemotron 3 Embed 1B over retrieval_chunks.embedding_text, L2-normalized, cosine similarity.',
                m.id,
                'embedding_text',
                '{input}',
                '{input}',
                'l2',
                'cosine',
                m.dimension,
                '{}'::jsonb,
                true
            FROM embedding_models m
            WHERE m.model_identifier = 'nvidia/nemotron-3-embed-1b'
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_embedding_hnsw")
    op.drop_table('chunk_embeddings')
    op.drop_index('ix_embedding_strategies_model_id', table_name='embedding_strategies')
    op.drop_table('embedding_strategies')
    op.drop_index('ix_embedding_models_identifier', table_name='embedding_models')
    op.drop_table('embedding_models')
    # The pgvector extension itself is not dropped: other things may depend on
    # it, and CREATE EXTENSION IF NOT EXISTS makes re-upgrading safe.
