"""Embedding tables: embedding_models, embedding_strategies, chunk_embeddings.

The embedding stage (docs/embeddings.md) sits between chunking and retrieval:

    retrieval_chunks --(embedding_text)--> chunk_embeddings --> pgvector/HNSW

Three tables, mirroring the separation the strategy document mandates:

* `embedding_models` -- an actual model that exists at a provider (NVIDIA
  Nemotron 3 Embed 1B). Not application-specific.
* `embedding_strategies` -- how Memora *uses* a model: which input, which
  templates, normalization, similarity. A model is not a strategy.
* `chunk_embeddings` -- one embedding of one chunk under one strategy, with
  an input hash for idempotency and a status lifecycle.

The vector lives in `chunk_embeddings`, never in `retrieval_chunks`: the chunk
is the canonical content, the embedding is a derived, strategy-scoped
representation of it. The same chunk may carry several embeddings under
different strategies (evaluation-ready), but never two for the same strategy
and input version (unique on chunk_id + strategy_id + input_hash).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.db.database import Base
from app.db.models.mixins import UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.chunking import RetrievalChunk


class EmbeddingModel(UUIDPrimaryKey, Base):
    """An embedding model offered by a provider.

    Example row:
        provider            = "nvidia"
        model_name          = "Nemotron 3 Embed 1B"
        model_identifier    = "nvidia/nemotron-3-embed-1b"
        embedding_type      = "dense"
        dimension           = 2048
        normalization       = "l2"
        similarity_metric   = "cosine"

    Model configuration is data, not code: adding BGE-M3 or a future NVIDIA
    multimodal model is an INSERT, not a schema change.
    """

    __tablename__ = "embedding_models"

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_identifier: Mapped[str] = mapped_column(String(256), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="dense")
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalization: Mapped[str] = mapped_column(String(32), nullable=False, server_default="l2")
    similarity_metric: Mapped[str] = mapped_column(String(32), nullable=False, server_default="cosine")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    strategies: Mapped[list["EmbeddingStrategy"]] = relationship(back_populates="model")

    __table_args__ = (
        UniqueConstraint("provider", "model_identifier", name="uq_embedding_models_provider_identifier"),
        Index("ix_embedding_models_identifier", "model_identifier"),
    )


class EmbeddingStrategy(UUIDPrimaryKey, Base):
    """How Memora uses an embedding model.

    The strategy owns everything that determines the vector representation:
    the input field, the document/query templates, normalization and the
    similarity metric. Two strategies over the same model can coexist (an
    evaluation A/B), and a strategy's configuration is versioned by creating
    a new strategy row, never by mutating an active one in place.

    Default strategy (seeded by the migration):
        name                = "Memora Dense v1"
        model               = nvidia/nemotron-3-embed-1b (2048, dense, l2, cosine)
        input_type          = "embedding_text"  (retrieval_chunks.embedding_text)
        document_template   = "{input}"          (identity for Nemotron)
        query_template      = "{input}"          (identity for Nemotron)
    """

    __tablename__ = "embedding_strategies"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("embedding_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_type: Mapped[str] = mapped_column(String(64), nullable=False, server_default="embedding_text")
    document_template: Mapped[str] = mapped_column(String(512), nullable=False, server_default="{input}")
    query_template: Mapped[str] = mapped_column(String(512), nullable=False, server_default="{input}")
    normalization: Mapped[str] = mapped_column(String(32), nullable=False, server_default="l2")
    similarity_metric: Mapped[str] = mapped_column(String(32), nullable=False, server_default="cosine")
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    model: Mapped["EmbeddingModel"] = relationship(back_populates="strategies")
    embeddings: Mapped[list["ChunkEmbedding"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_embedding_strategies_name"),
        Index("ix_embedding_strategies_model_id", "model_id"),
    )


class ChunkEmbedding(UUIDPrimaryKey, Base):
    """One embedding of one chunk under one strategy.

    The artifact table. `input_hash` makes generation idempotent: re-running
    the worker over the same chunk with the same strategy and unchanged
    `embedding_text` is a no-op; changed content or a changed strategy produces
    a new hash and therefore a new embedding row, while the old one is marked
    `stale` rather than deleted (history is never silently overwritten).

    Status lifecycle:
        pending -> processing -> completed
                 -> failed (retryable: failed -> pending)
        completed -> stale (input or strategy changed)
    """

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        # Idempotency: one embedding per (chunk, strategy, input version).
        UniqueConstraint("chunk_id", "strategy_id", "input_hash", name="uq_chunk_embeddings_chunk_strategy_input"),
        Index("ix_chunk_embeddings_chunk_id", "chunk_id"),
        Index("ix_chunk_embeddings_strategy_id", "strategy_id"),
        Index("ix_chunk_embeddings_status", "embedding_status"),
        Index("ix_chunk_embeddings_created_at", "created_at"),
        # The vector index (HNSW, cosine distance) is created in the migration
        # with pgvector's DDL, not here: SQLAlchemy's pgvector support is not
        # used for DDL so the migration stays the single source of schema truth.
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retrieval_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("embedding_strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    # vector(2048) -- typed by the migration; the pgvector Vector type is used
    # for ORM reads/writes. Dimension is validated against the strategy before
    # insert, so a corrupt vector can never be persisted.
    embedding: Mapped[list] = mapped_column(Vector(2048), nullable=False)
    embedding_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chunk: Mapped["RetrievalChunk"] = relationship(back_populates="embeddings")
    strategy: Mapped["EmbeddingStrategy"] = relationship(back_populates="embeddings")
