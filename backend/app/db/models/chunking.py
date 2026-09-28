"""Chunking tables: document_units, semantic_blocks, retrieval_chunks.

Derived artifacts of an extraction's readings (docs/chunking.md). All three
cascade from `source_extractions`, so deleting a source removes its chunks with
it. Ownership columns are denormalized from the source, following the same
convention as `sources` and `source_extractions`: every read is one indexed
scope predicate.

No embedding vector or HNSW/tsvector indexes yet -- those arrive with the
retrieval phase in a later migration. `embedding_text` and `token_count` are
produced now because they are part of chunking.
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.mixins import TimestampCreated, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.extraction import SourceExtraction


class DocumentUnit(UUIDPrimaryKey, TimestampCreated, Base):
    """A fine-grained element parsed from the readings: a heading, paragraph,
    list, table, figure or key/value pair.

    Not to be confused with the extraction layer's `ReadingUnit` (a page/slide/
    sheet). This is the chunking layer's element -- what the strategy §3.1
    calls a DocumentUnit. See docs/chunking.md §2.
    """

    __tablename__ = "document_units"
    __table_args__ = (
        Index("ix_document_units_extraction_id", "extraction_id"),
        Index("ix_document_units_tenant_id", "tenant_id"),
        Index("ix_document_units_application_id", "application_id"),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    extraction: Mapped["SourceExtraction"] = relationship(back_populates="document_units")


class SemanticBlock(UUIDPrimaryKey, TimestampCreated, Base):
    """A grouping of DocumentUnits that belong to the same logical section.

    The parent context for retrieval chunks (strategy §3.2). Its `content`
    holds the block's full text so parent-level context expansion later needs
    no re-assembly.
    """

    __tablename__ = "semantic_blocks"
    __table_args__ = (
        Index("ix_semantic_blocks_extraction_id", "extraction_id"),
        Index("ix_semantic_blocks_tenant_id", "tenant_id"),
        Index("ix_semantic_blocks_application_id", "application_id"),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    unit_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    extraction: Mapped["SourceExtraction"] = relationship(back_populates="semantic_blocks")
    chunks: Mapped[list["RetrievalChunk"]] = relationship(
        back_populates="semantic_block",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RetrievalChunk.chunk_index",
    )


class RetrievalChunk(UUIDPrimaryKey, TimestampCreated, Base):
    """A retrieval-sized piece of a Semantic Block, with section context and
    provenance. The unit that will be embedded and searched (strategy §3.3).

    `embedding_text` is the representation that will be embedded (section
    context prefix + content); `token_count` is counted on it. The vector
    itself is added in a later migration with the retrieval phase.
    """

    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        Index("ix_retrieval_chunks_block_id", "semantic_block_id"),
        Index("ix_retrieval_chunks_extraction_id", "extraction_id"),
        Index("ix_retrieval_chunks_scope_active", "tenant_id", "application_id", "is_active"),
    )

    semantic_block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("semantic_blocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_unit_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    section_path: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    semantic_block: Mapped["SemanticBlock"] = relationship(back_populates="chunks")
    extraction: Mapped["SourceExtraction"] = relationship(back_populates="retrieval_chunks")
    embeddings: Mapped[list["ChunkEmbedding"]] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
