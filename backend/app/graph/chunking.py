"""Split an extraction's content into graph text units.

The content is the agent's merged Markdown: one section per page / slide /
sheet, each behind a marker line such as `<!-- page 3 · hard · layout -->`.
Units are built in three steps, all deterministic:

1. **Sections** -- split on the markers, so every block knows its page.
2. **Blocks** -- split each section on blank lines. A Markdown table has no
   blank lines inside it, so it stays one block; only a block longer than a
   whole unit is split further (at line breaks, then whitespace).
3. **Packing** -- blocks are packed in order into units of at most
   `chunk_chars`, and each unit after the first starts with up to
   `overlap` characters from the end of the previous one.

Nothing is dropped: every non-blank character outside the markers lands in at
least one unit. Units may span pages and record `page_start` / `page_end`.
This is independent of the vector pipeline's chunking.
"""

import re
from dataclasses import dataclass

from app.graph.models import GraphTextUnit

MARKER = re.compile(r"^<!--\s*([A-Za-z]+)\s+(\d+)\b[^\n]*?-->[ \t]*$", re.MULTILINE)
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


@dataclass
class _Block:
    start: int
    end: int
    page: int | None


def chunk_id(source_id: str, version: int, index: int) -> str:
    return f"{source_id}:{version}:{index}"


def _sections(content: str) -> list[tuple[int, int, int | None]]:
    sections: list[tuple[int, int, int | None]] = []
    markers = list(MARKER.finditer(content))
    first = markers[0].start() if markers else len(content)
    if content[:first].strip():
        sections.append((0, first, None))
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(content)
        sections.append((marker.end(), end, int(marker.group(2))))
    return sections


def _trimmed(content: str, start: int, end: int, page: int | None) -> _Block | None:
    text = content[start:end]
    stripped = text.strip()
    if not stripped:
        return None
    offset = start + (len(text) - len(text.lstrip()))
    return _Block(offset, offset + len(stripped), page)


def _split_long(content: str, block: _Block, limit: int) -> list[_Block]:
    """Cut a block longer than `limit` at line breaks, then whitespace."""
    pieces: list[_Block] = []
    start = block.start
    while block.end - start > limit:
        window = content[start : start + limit]
        cut = window.rfind("\n")
        if cut <= limit // 4:
            cut = max(window.rfind(" "), window.rfind("\t"))
        if cut <= limit // 4:
            cut = limit  # one enormous token: a hard cut is the only option
        piece = _trimmed(content, start, start + cut, block.page)
        if piece:
            pieces.append(piece)
        start += cut
    tail = _trimmed(content, start, block.end, block.page)
    if tail:
        pieces.append(tail)
    return pieces


def _blocks(content: str, limit: int) -> list[_Block]:
    blocks: list[_Block] = []
    for start, end, page in _sections(content):
        position = start
        for gap in list(_BLANK_LINE.finditer(content, start, end)) + [None]:
            stop = gap.start() if gap else end
            block = _trimmed(content, position, stop, page)
            if block:
                if block.end - block.start > limit:
                    blocks.extend(_split_long(content, block, limit))
                else:
                    blocks.append(block)
            if gap:
                position = gap.end()
    return blocks


def _length(blocks: list[_Block]) -> int:
    if not blocks:
        return 0
    return sum(b.end - b.start for b in blocks) + 2 * (len(blocks) - 1)


def _overlap_tail(content: str, blocks: list[_Block], overlap: int) -> list[_Block]:
    """The end of the previous unit that the next one starts with."""
    if overlap <= 0 or not blocks:
        return []
    tail: list[_Block] = []
    for block in reversed(blocks):
        if _length([block, *tail]) > overlap:
            break
        tail.insert(0, block)
    if tail:
        return tail
    # The last block alone is longer than the overlap: take its end, starting
    # at a word boundary.
    last = blocks[-1]
    start = max(last.start, last.end - overlap)
    space = content.find(" ", start, last.end)
    if space != -1:
        start = space
    piece = _trimmed(content, start, last.end, last.page)
    return [piece] if piece else []


def chunk_content(
    content: str,
    *,
    source_id: str,
    version: int,
    chunk_chars: int = 3000,
    overlap: int = 300,
) -> list[GraphTextUnit]:
    size = max(200, chunk_chars)
    overlap = max(0, min(overlap, size // 2))
    groups: list[list[_Block]] = []
    current: list[_Block] = []
    fresh = False  # does `current` hold anything beyond the carried-over overlap?
    for block in _blocks(content or "", size):
        if fresh and _length([*current, block]) > size:
            groups.append(current)
            current = _overlap_tail(content, current, overlap)
            if _length([*current, block]) > size:
                current = []
        current.append(block)
        fresh = True
    if fresh:
        groups.append(current)

    units = []
    for index, group in enumerate(groups):
        pages = [b.page for b in group if b.page is not None]
        units.append(
            GraphTextUnit(
                id=chunk_id(source_id, version, index),
                source_id=source_id,
                index=index,
                text="\n\n".join(content[b.start : b.end] for b in group),
                char_start=group[0].start,
                char_end=group[-1].end,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
            )
        )
    return units
