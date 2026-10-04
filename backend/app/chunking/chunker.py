"""Split SemanticBlocks into retrieval-sized RetrievalChunks.

Recursive splitting (strategy §11, §12): when a block is too large, split at
subsection → paragraph → sentence → token boundaries. Special rules for tables
(split by logical row groups, repeat headers), forms/key-value (keep coherent),
and figures (don't slice). A short coherent section stays small (RULE 6).

The chunker is pure: it takes a SemanticBlockData and settings, returns a list
of ChunkData. No LLM, no DB.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.chunking.blocks import SemanticBlockData
from app.chunking.representation import build_embedding_text, count_tokens
from app.chunking.units import ParsedUnit
from app.core.config import Settings


@dataclass
class ChunkData:
    """Intermediate representation of a retrieval chunk, before DB persistence."""

    content: str
    embedding_text: str
    token_count: int
    content_type: str  # text | table | figure | mixed | key_value
    page_start: int | None
    page_end: int | None
    unit_indices: list[int]
    section_path: list[str]


# --- Splitting helpers ---------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_TABLE_HEADER = re.compile(r"^\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|\s*$")


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on blank lines."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences. Keeps the punctuation."""
    parts = _SENTENCE_END.split(text.strip())
    return [s.strip() for s in parts if s.strip()]


def _is_table(content: str) -> bool:
    lines = content.strip().split("\n")
    return len(lines) >= 2 and bool(_TABLE_HEADER.match(lines[0])) and bool(_TABLE_SEP.match(lines[1]))


def _table_header_lines(content: str) -> list[str]:
    """The header and separator rows of a Markdown table."""
    lines = content.strip().split("\n")
    if len(lines) >= 2 and _TABLE_HEADER.match(lines[0]) and _TABLE_SEP.match(lines[1]):
        return [lines[0], lines[1]]
    return []


def _table_data_rows(content: str) -> list[str]:
    """The data rows of a Markdown table (excluding header + separator)."""
    lines = content.strip().split("\n")
    if len(lines) >= 2 and _TABLE_HEADER.match(lines[0]) and _TABLE_SEP.match(lines[1]):
        return lines[2:]
    return lines


def _join_with_prefix(section_path: list[str], content: str) -> tuple[str, int]:
    """Build embedding_text and count its tokens."""
    embedding_text = build_embedding_text(section_path, content)
    return embedding_text, count_tokens(embedding_text)


# --- The chunker --------------------------------------------------------------


def chunk_semantic_block(
    block: SemanticBlockData,
    settings: Settings | None = None,
) -> list[ChunkData]:
    """Split a semantic block into retrieval chunks.

    If the block fits within the target, it becomes a single chunk. Otherwise
    it is split recursively: subsection → paragraph → sentence → token, with
    special handling for tables, forms and figures.
    """
    from app.core.config import get_settings
    s = settings or get_settings()

    target_min = s.chunk_target_min
    target_max = s.chunk_target_max
    soft_max = s.chunk_soft_max
    hard_max = s.chunk_hard_max

    if not block.content.strip():
        return []

    # Determine the content type of this block.
    content_type = _classify_block(block.units)

    # If the block fits, return one chunk.
    embedding_text, token_count = _join_with_prefix(block.section_path, block.content)
    if token_count <= soft_max:
        return [ChunkData(
            content=block.content,
            embedding_text=embedding_text,
            token_count=token_count,
            content_type=content_type,
            page_start=block.page_start,
            page_end=block.page_end,
            unit_indices=list(block.unit_indices),
            section_path=list(block.section_path),
        )]

    # Too large: split by content type.
    if content_type == "table":
        return _split_table_block(block, s)
    if content_type == "key_value":
        return _split_key_value_block(block, s)
    if content_type == "figure":
        # Don't slice a figure; keep it whole even if over soft_max.
        return [ChunkData(
            content=block.content,
            embedding_text=embedding_text,
            token_count=token_count,
            content_type="figure",
            page_start=block.page_start,
            page_end=block.page_end,
            unit_indices=list(block.unit_indices),
            section_path=list(block.section_path),
        )]

    # Mixed/text: recursive split.
    return _split_text_block(block, s)


def _classify_block(units: list[ParsedUnit]) -> str:
    """Classify a block's dominant content type."""
    kinds = {u.kind for u in units}
    # If the block is entirely one non-text kind, classify as that.
    if kinds == {"table"}:
        return "table"
    if kinds == {"key_value"}:
        return "key_value"
    if kinds == {"figure"}:
        return "figure"
    if kinds <= {"heading", "table"} and "table" in kinds:
        return "table"
    if kinds <= {"heading", "key_value"} and "key_value" in kinds:
        return "key_value"
    if kinds <= {"heading", "figure"} and "figure" in kinds:
        return "figure"
    return "mixed"


def _split_table_block(block: SemanticBlockData, s: Settings) -> list[ChunkData]:
    """Split a large table by logical row groups, repeating the header."""
    header = _table_header_lines(block.content)
    data_rows = _table_data_rows(block.content)

    if not header or not data_rows:
        # Not a clean table; fall back to text splitting.
        return _split_text_block(block, s)

    chunks: list[ChunkData] = []
    current_rows: list[str] = []
    current_units: list[int] = list(block.unit_indices)

    # Estimate: header tokens + row tokens. We accumulate rows until we hit
    # the target max, then emit a chunk.
    for row in data_rows:
        candidate = "\n".join(header + current_rows + [row])
        _, tc = _join_with_prefix(block.section_path, candidate)
        if current_rows and tc > s.chunk_target_max:
            # Emit the current group with the header repeated.
            content = "\n".join(header + current_rows)
            et, tc2 = _join_with_prefix(block.section_path, content)
            chunks.append(ChunkData(
                content=content,
                embedding_text=et,
                token_count=tc2,
                content_type="table",
                page_start=block.page_start,
                page_end=block.page_end,
                unit_indices=list(current_units),
                section_path=list(block.section_path),
            ))
            current_rows = [row]
        else:
            current_rows.append(row)

    if current_rows:
        content = "\n".join(header + current_rows)
        et, tc = _join_with_prefix(block.section_path, content)
        chunks.append(ChunkData(
            content=content,
            embedding_text=et,
            token_count=tc,
            content_type="table",
            page_start=block.page_start,
            page_end=block.page_end,
            unit_indices=list(current_units),
            section_path=list(block.section_path),
        ))

    return chunks


def _split_key_value_block(block: SemanticBlockData, s: Settings) -> list[ChunkData]:
    """Split key/value fields, keeping related fields together when possible."""
    # Key/value sections are usually small. Only split if genuinely large.
    et, tc = _join_with_prefix(block.section_path, block.content)
    if tc <= s.chunk_hard_max:
        return [ChunkData(
            content=block.content,
            embedding_text=et,
            token_count=tc,
            content_type="key_value",
            page_start=block.page_start,
            page_end=block.page_end,
            unit_indices=list(block.unit_indices),
            section_path=list(block.section_path),
        )]
    # Genuinely large: split by lines, grouping to fit the target.
    lines = block.content.strip().split("\n")
    return _group_lines_into_chunks(lines, block, s, "key_value")


def _split_text_block(block: SemanticBlockData, s: Settings) -> list[ChunkData]:
    """Recursive text split: paragraph → sentence → token."""
    paragraphs = _split_paragraphs(block.content)
    chunks: list[ChunkData] = []
    current_parts: list[str] = []

    for para in paragraphs:
        candidate = "\n\n".join(current_parts + [para])
        _, tc = _join_with_prefix(block.section_path, candidate)
        if current_parts and tc > s.chunk_target_max:
            # Emit current group.
            content = "\n\n".join(current_parts)
            chunks.append(_make_chunk(content, block, s, "text"))
            current_parts = []
            # If this single paragraph is too large, split it further.
            _, ptc = _join_with_prefix(block.section_path, para)
            if ptc > s.chunk_soft_max:
                chunks.extend(_split_paragraph(para, block, s))
            else:
                current_parts = [para]
        else:
            current_parts.append(para)

    if current_parts:
        content = "\n\n".join(current_parts)
        chunks.append(_make_chunk(content, block, s, "text"))

    return chunks or [_make_chunk(block.content, block, s, "text")]


def _split_paragraph(para: str, block: SemanticBlockData, s: Settings) -> list[ChunkData]:
    """Split a too-large paragraph by sentences, then by tokens."""
    sentences = _split_sentences(para)
    chunks: list[ChunkData] = []
    current: list[str] = []

    for sent in sentences:
        candidate = " ".join(current + [sent])
        _, tc = _join_with_prefix(block.section_path, candidate)
        if current and tc > s.chunk_target_max:
            content = " ".join(current)
            chunks.append(_make_chunk(content, block, s, "text"))
            current = []
            # If a single sentence is too large, split by tokens.
            _, stc = _join_with_prefix(block.section_path, sent)
            if stc > s.chunk_hard_max:
                chunks.extend(_split_by_tokens(sent, block, s))
            else:
                current = [sent]
        else:
            current.append(sent)

    if current:
        content = " ".join(current)
        chunks.append(_make_chunk(content, block, s, "text"))

    return chunks


def _split_by_tokens(text: str, block: SemanticBlockData, s: Settings) -> list[ChunkData]:
    """Final safety split: by approximate token count."""
    words = text.split()
    if not words:
        return []
    # Estimate words per chunk from the target.
    words_per_chunk = max(1, s.chunk_target_max * 4)  # ~4 chars/token, ~4 chars/word
    chunks: list[ChunkData] = []
    for i in range(0, len(words), words_per_chunk):
        content = " ".join(words[i : i + words_per_chunk])
        chunks.append(_make_chunk(content, block, s, "text"))
    return chunks


def _group_lines_into_chunks(
    lines: list[str], block: SemanticBlockData, s: Settings, content_type: str
) -> list[ChunkData]:
    """Group lines into chunks that fit the target."""
    chunks: list[ChunkData] = []
    current: list[str] = []

    for line in lines:
        candidate = "\n".join(current + [line])
        _, tc = _join_with_prefix(block.section_path, candidate)
        if current and tc > s.chunk_target_max:
            content = "\n".join(current)
            chunks.append(_make_chunk(content, block, s, content_type))
            current = [line]
        else:
            current.append(line)

    if current:
        content = "\n".join(current)
        chunks.append(_make_chunk(content, block, s, content_type))

    return chunks


def _make_chunk(content: str, block: SemanticBlockData, s: Settings, content_type: str) -> ChunkData:
    et, tc = _join_with_prefix(block.section_path, content)
    return ChunkData(
        content=content,
        embedding_text=et,
        token_count=tc,
        content_type=content_type,
        page_start=block.page_start,
        page_end=block.page_end,
        unit_indices=list(block.unit_indices),
        section_path=list(block.section_path),
    )
