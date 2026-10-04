"""Merge the knowledge-graph and retrieval-pipeline migration branches

Revision ID: cd75bae887c4
Revises: b6d4e8f1a2c3, d4e5f6a7b8c9
Create Date: 2026-10-04 06:06:18.601242+00:00

Both branches were cut from f4c1d2e3b5a6 and developed in parallel:

* graph:     a9d3e5f7c2b1 (extraction_usage.price)
             -> b6d4e8f1a2c3 (source_extractions.content, source_graph_builds,
                              extraction_usage.graph_build_id)
* retrieval: a1b2c3d4e5f6 (source_extractions.readings)
             -> b2c3d4e5f6a7 (document_units, semantic_blocks, retrieval_chunks)
             -> d4e5f6a7b8c9 (pgvector, embedding_models, embedding_strategies,
                              chunk_embeddings + HNSW index)

The two touch disjoint columns and tables and neither references the other's
tables, so they apply in either order and this revision changes nothing. It
only joins the two heads, so a database at either one upgrades by running the
other branch. Do not re-parent either branch instead: a database already
stamped at one head would then skip the other branch's migrations.
"""
from collections.abc import Sequence


revision: str = 'cd75bae887c4'
down_revision: str | Sequence[str] | None = ('b6d4e8f1a2c3', 'd4e5f6a7b8c9')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
