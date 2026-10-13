"""Persistent caches for the extraction pipeline.

The reading cache backs the in-process LRU (app/agents/reading_cache.py) so
a validated page reading survives restarts, worker changes and eviction:
re-extracting the same document never re-pays for pages already read well.

The document cache short-circuits whole runs: the same file bytes, extracted
under the same pipeline version, mode and instructions, reuse the stored
result instead of calling any model.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.mixins import TimestampCreated


class ReadingCacheEntry(TimestampCreated, Base):
    """One validated model reading, keyed exactly as the in-memory cache:
    sha256(image bytes) + model + prompt version + render settings + prompt.
    """

    __tablename__ = "reading_cache_entries"

    # The hex sha256 the in-memory cache already computes (ReadingCache.key).
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class DocumentCacheEntry(TimestampCreated, Base):
    """One whole extraction run's output, keyed by
    sha256(file bytes) + pipeline version + mode + instructions hash.
    """

    __tablename__ = "document_cache_entries"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # An ExtractionResult (app/schemas/extraction.py) as stored on the run.
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The merged document Markdown the agent read, and the per-unit
    # readings, so a cache hit reproduces a run completely.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    readings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)