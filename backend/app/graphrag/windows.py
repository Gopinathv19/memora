"""Extraction windows: the unit the unified graph builder reads (plan Step 1).

The graph is built from the same `SemanticBlock` / `RetrievalChunk` rows retrieval
uses, so every fact points at a real retrieval chunk and no text-overlap bridge is
needed. Blocks are too small to extract from one by one (on a measured corpus the
median block was 60 tokens, 79% were under 100): a call would see a heading and a
sentence. So consecutive blocks of one document are *packed* into windows of about
`budget` tokens, and a block bigger than `max_tokens` is split at chunk boundaries.

A window is made of the *active chunks'* text, not the block's: that is exactly
what retrieval can cite, so an entity found in a window can always be linked to a
chunk. It never spans documents, so no entity is credited to the wrong source.

The packing (`pack_windows`) and mention linking (`chunks_mentioning`) are pure.
`load_windows` is the only function that touches the database, read-only.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from app.chunking.representation import count_tokens

DEFAULT_BUDGET = 1000       # target tokens per window (today's graph unit is ~750)
DEFAULT_MIN = 400           # close early at a top-level section change above this
DEFAULT_MAX = 1800          # a block above this is split at chunk boundaries


@dataclass
class ChunkRef:
    id: str
    index: int
    text: str
    tokens: int
    page_start: int | None = None
    page_end: int | None = None
    content_type: str = "text"


@dataclass
class Segment:
    """One block (or a chunk-aligned part of an oversized block) inside a window."""

    block_id: str
    section_path: list[str]
    title: str | None
    chunks: list[ChunkRef]

    @property
    def tokens(self) -> int:
        return sum(c.tokens for c in self.chunks)


@dataclass
class Window:
    id: str
    source_id: str
    extraction_id: str
    source_version: int
    filename: str | None
    segments: list[Segment] = field(default_factory=list)
    subject_id: str | None = None
    application_id: str | None = None

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.segments)

    @property
    def chunks(self) -> list[ChunkRef]:
        return [c for s in self.segments for c in s.chunks]

    @property
    def chunk_ids(self) -> list[str]:
        return [c.id for c in self.chunks]

    @property
    def pages(self) -> str | None:
        pages = [p for c in self.chunks for p in (c.page_start, c.page_end) if p is not None]
        if not pages:
            return None
        lo, hi = min(pages), max(pages)
        return f"page {lo}" if lo == hi else f"pages {lo}-{hi}"

    def render(self) -> str:
        """The text the extractor reads: each segment under its section path."""
        parts = []
        for s in self.segments:
            path = " > ".join(s.section_path) if s.section_path else (s.title or "")
            body = "\n\n".join(c.text for c in s.chunks)
            parts.append(f"[Section: {path}]\n{body}" if path else body)
        return "\n\n".join(parts)


# --- packing (pure) --------------------------------------------------------------------


def _split_block(block: dict, max_tokens: int, budget: int) -> list[Segment]:
    """A block as one segment, or chunk-aligned segments of at most `budget` tokens
    when its total exceeds `max_tokens`. A single chunk is never cut."""
    chunks: list[ChunkRef] = sorted(block["chunks"], key=lambda c: c.index)
    base = dict(block_id=block["id"], section_path=list(block.get("section_path") or []),
                title=block.get("title"))
    if sum(c.tokens for c in chunks) <= max_tokens:
        return [Segment(chunks=chunks, **base)]
    out: list[Segment] = []
    current: list[ChunkRef] = []
    used = 0
    for c in chunks:
        if current and used + c.tokens > budget:
            out.append(Segment(chunks=current, **base))
            current, used = [], 0
        current.append(c)
        used += c.tokens
    if current:
        out.append(Segment(chunks=current, **base))
    return out


def pack_windows(
    blocks: list[dict], *, source_id: str, extraction_id: str, source_version: int,
    filename: str | None = None, budget: int = DEFAULT_BUDGET, min_tokens: int = DEFAULT_MIN,
    max_tokens: int = DEFAULT_MAX, subject_id: str | None = None, application_id: str | None = None,
) -> list[Window]:
    """Pack one document's blocks (already in reading order) into windows.

    `blocks`: dicts with `id`, `section_path`, `title`, `chunks` (list of `ChunkRef`,
    the *active* chunks only). Blocks without chunks are skipped.
    A window closes when the next segment would exceed `budget`, or earlier at a
    change of top-level section once it holds at least `min_tokens`.
    """
    segments = [s for b in blocks if b["chunks"] for s in _split_block(b, max_tokens, budget)]
    windows: list[Window] = []
    current: Window | None = None
    for seg in segments:
        if current is not None:
            top_changed = (
                bool(seg.section_path) and bool(current.segments[-1].section_path)
                and seg.section_path[0] != current.segments[-1].section_path[0]
            )
            if current.tokens + seg.tokens > budget or (top_changed and current.tokens >= min_tokens):
                windows.append(current)
                current = None
        if current is None:
            current = Window(
                id=f"{extraction_id}:{len(windows)}", source_id=source_id,
                extraction_id=extraction_id, source_version=source_version, filename=filename,
                subject_id=subject_id, application_id=application_id,
            )
        current.segments.append(seg)
    if current is not None:
        windows.append(current)
    return windows


# --- linking entities to chunks (pure) -------------------------------------------------

_WS = re.compile(r"\s+")
_MARKUP = str.maketrans({"*": None, "_": None, "`": None})  # markdown emphasis / code marks


def _norm(text: str) -> str:
    return _WS.sub(" ", text.translate(_MARKUP)).casefold()


def chunks_mentioning(window: Window, surface_forms: list[str]) -> list[str]:
    """Ids of the window's chunks whose text contains any of `surface_forms`.

    Matching is case-insensitive on word boundaries, so "IBM" does not match inside
    "ribbmo". An empty result means the model named something no single chunk
    states (a name spread over two chunks, or inferred): the caller counts those
    as unlinked rather than guessing a chunk.
    """
    forms = [_norm(f) for f in surface_forms if f and f.strip()]
    if not forms:
        return []
    patterns = [re.compile(r"(?<!\w)" + re.escape(f) + r"(?!\w)") for f in forms]
    return [
        c.id for c in window.chunks
        if any(p.search(_norm(c.text)) for p in patterns)
    ]


# --- database (read-only) --------------------------------------------------------------


def load_windows(
    db, tenant_id: uuid.UUID, source_ids: list[uuid.UUID] | None = None, *,
    budget: int = DEFAULT_BUDGET, min_tokens: int = DEFAULT_MIN, max_tokens: int = DEFAULT_MAX,
) -> list[Window]:
    """Windows for every source with active chunks, in reading order. Read-only."""
    from sqlalchemy import select

    from app.db.models import RetrievalChunk, SemanticBlock, Source

    stmt = (
        select(RetrievalChunk, SemanticBlock, Source.filename)
        .join(SemanticBlock, SemanticBlock.id == RetrievalChunk.semantic_block_id)
        .join(Source, Source.id == RetrievalChunk.source_id)
        .where(RetrievalChunk.tenant_id == tenant_id, RetrievalChunk.is_active.is_(True))
        .order_by(RetrievalChunk.source_id, RetrievalChunk.extraction_id,
                  SemanticBlock.position, RetrievalChunk.chunk_index)
    )
    if source_ids:
        stmt = stmt.where(RetrievalChunk.source_id.in_(source_ids))

    # (source, extraction) -> block id -> block dict, preserving reading order
    docs: dict[tuple[str, str], dict] = {}
    for chunk, block, filename in db.execute(stmt).all():
        key = (str(chunk.source_id), str(chunk.extraction_id))
        doc = docs.setdefault(key, {
            "version": chunk.source_version, "filename": filename, "blocks": {},
            "subject_id": str(chunk.subject_id), "application_id": str(chunk.application_id),
        })
        b = doc["blocks"].setdefault(str(block.id), {
            "id": str(block.id), "section_path": block.section_path or [],
            "title": block.title, "chunks": [],
        })
        b["chunks"].append(ChunkRef(
            id=str(chunk.id), index=chunk.chunk_index, text=chunk.content,
            tokens=count_tokens(chunk.content), page_start=chunk.page_start,
            page_end=chunk.page_end, content_type=chunk.content_type,
        ))
    windows: list[Window] = []
    for (source_id, extraction_id), doc in docs.items():
        windows += pack_windows(
            list(doc["blocks"].values()), source_id=source_id, extraction_id=extraction_id,
            source_version=doc["version"], filename=doc["filename"], budget=budget,
            min_tokens=min_tokens, max_tokens=max_tokens,
            subject_id=doc["subject_id"], application_id=doc["application_id"],
        )
    return windows
