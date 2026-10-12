"""add lexical search to retrieval chunks

Revision ID: e9f3a7c1d5b8
Revises: cd75bae887c4
Create Date: 2026-12-10 16:12:00.000000+00:00

Hybrid retrieval (docs/embedding-retrieval.md): a lexical (tsvector) branch
next to the dense (HNSW) branch, fused with RRF.

* search_text  -- the text the tsvector is generated from (embedding_text +
                  content), maintained by the chunking service. `content`
                  itself stays unchanged: it is the generation/citation text.
* search_vector -- a GENERATED ALWAYS tsvector column, so it can never drift
                  from search_text. 'simple' (not 'english') because
                  enterprise documents contain product names, error codes,
                  identifiers and multilingual content; stemming would damage
                  exact technical-token matching.
* GIN index on search_vector for the @@ matches.
* btree index on the common filter columns so the lexical scan can prefilter
  by tenant/subject/source before ranking.
"""

from collections.abc import Sequence

from alembic import op

revision: str = 'e9f3a7c1d5b8'
down_revision: str | None = 'cd75bae887c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE retrieval_chunks
        ADD COLUMN IF NOT EXISTS search_text TEXT;
    """)

    # Backfill existing rows from the same parts the chunking service uses
    # (embedding_text + content). Stopword stripping happens in application
    # code (app.core.stopwords) for new rows; for the backfill the raw text
    # is good enough -- stopwords add noise but never block a match, and
    # re-chunking a source regenerates its search_text stopword-free.
    # Covers every row (no WHERE), so no chunk is left with a NULL search_text
    # and an empty, never-matching tsvector.
    op.execute("""
        UPDATE retrieval_chunks
        SET search_text = concat_ws(
            ' ',
            embedding_text,
            content
        );
    """)

    op.execute("""
        ALTER TABLE retrieval_chunks
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple', coalesce(search_text, ''))
        ) STORED;
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS retrieval_chunks_search_vector_idx
        ON retrieval_chunks
        USING GIN (search_vector);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS retrieval_chunks_lexical_filters_idx
        ON retrieval_chunks (
            tenant_id,
            subject_id,
            source_id,
            is_active
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS retrieval_chunks_lexical_filters_idx;
    """)

    op.execute("""
        DROP INDEX IF EXISTS retrieval_chunks_search_vector_idx;
    """)

    op.execute("""
        ALTER TABLE retrieval_chunks
        DROP COLUMN IF EXISTS search_vector;
    """)

    op.execute("""
        ALTER TABLE retrieval_chunks
        DROP COLUMN IF EXISTS search_text;
    """)