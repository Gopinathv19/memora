"""Group DocumentUnits into SemanticBlocks.

A SemanticBlock groups units that belong to the same logical section (strategy
§3.2, §9). Each leaf section becomes one block. The block carries its section
path, the ids of its units, a page range, and its full text (so parent-level
context expansion later needs no re-assembly).

Not every heading is a retrieval boundary. Numbered section headings
("2. Section 2") start new blocks; short un-numbered subheadings ("Key
points") that fall inside a section with body content are demoted to inline
content so the section stays one coherent retrieval unit. Only when a
subheading's own section has substantial body content (or it begins the
document) does it open a new block.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.chunking.units import ParsedUnit

# A numbered section heading title: "2. Section 2", "1.1 Personal Details".
_NUMBERED_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")

# A subheading shorter than this stays inline within its parent section.
_SUBHEADING_INLINE_MAX = 60


@dataclass
class SemanticBlockData:
    """Intermediate representation of a semantic block, before DB persistence."""

    section_path: list[str]
    title: str | None
    unit_indices: list[int]  # indices into the units list
    page_start: int | None
    page_end: int | None
    content: str
    units: list[ParsedUnit] = field(default_factory=list)


def _is_numbered_heading(unit: ParsedUnit) -> bool:
    return unit.kind == "heading" and bool(_NUMBERED_TITLE.match(unit.content.strip()))


def build_semantic_blocks(units: list[ParsedUnit]) -> list[SemanticBlockData]:
    """Group units into semantic blocks, one per leaf section.

    Numbered section headings (or headings with no current block) start a new
    block. A short un-numbered subheading ("Key points") whose parent block
    already has body content is demoted to an inline `**bold**` line inside
    that block instead of splitting the section into an orphan intro chunk and
    a separate "rest of section" chunk.
    """
    if not units:
        return []

    blocks: list[SemanticBlockData] = []
    current: SemanticBlockData | None = None

    for idx, unit in enumerate(units):
        if unit.kind == "heading":
            # Demote: a short, un-numbered subheading inside a section that
            # already has body content is inline content, not a new block.
            if (
                current is not None
                and not _is_numbered_heading(unit)
                and len(unit.content) <= _SUBHEADING_INLINE_MAX
                and any(u.kind != "heading" for u in current.units)
            ):
                # Append as inline content (bolded) within the current block.
                current.unit_indices.append(idx)
                current.units.append(unit)
                if unit.page is not None:
                    if current.page_start is None or unit.page < current.page_start:
                        current.page_start = unit.page
                    if current.page_end is None or unit.page > current.page_end:
                        current.page_end = unit.page
                current.content = current.content + f"\n\n**{unit.content}**"
                continue

            # Otherwise the heading starts a new block.
            if current is not None:
                blocks.append(current)
            current = SemanticBlockData(
                section_path=list(unit.section_path),
                title=unit.content,
                unit_indices=[idx],
                page_start=unit.page,
                page_end=unit.page,
                content=unit.content,
                units=[unit],
            )
        else:
            if current is None:
                # Preamble before any heading.
                current = SemanticBlockData(
                    section_path=[],
                    title=None,
                    unit_indices=[idx],
                    page_start=unit.page,
                    page_end=unit.page,
                    content="",
                    units=[],
                )
            current.unit_indices.append(idx)
            current.units.append(unit)
            if unit.page is not None:
                if current.page_start is None or unit.page < current.page_start:
                    current.page_start = unit.page
                if current.page_end is None or unit.page > current.page_end:
                    current.page_end = unit.page
            # Build content: join non-heading units with blank lines.
            if current.content and current.content != current.title:
                current.content = current.content + "\n\n" + unit.content
            elif current.title and not current.content:
                current.content = current.title + "\n\n" + unit.content
            else:
                current.content = unit.content

    if current is not None:
        blocks.append(current)

    return merge_heading_only_blocks(blocks)


def merge_heading_only_blocks(
    blocks: list[SemanticBlockData],
) -> list[SemanticBlockData]:
    """Merge consecutive heading-only blocks (no body content) into one.

    A document's table of contents produces a run of heading-only blocks —
    each just a title with no body, often only a few tokens. Left alone, each
    becomes a tiny, useless retrieval chunk. Merging consecutive heading-only
    blocks turns the TOC into one coherent block. A heading-only block that
    precedes a content block (a parent heading with children but no body of
    its own) is absorbed into that content block as a context line. Real sections
    with body content are never merged (strategy RULE 6: no padding).
    """
    if not blocks:
        return blocks

    def _is_heading_only(b: SemanticBlockData) -> bool:
        return bool(b.units) and all(u.kind == "heading" for u in b.units)

    def _merge_into(prev: SemanticBlockData, nxt: SemanticBlockData) -> None:
        prev.unit_indices.extend(nxt.unit_indices)
        prev.units.extend(nxt.units)
        prev.content = prev.content + "\n" + nxt.content
        for key in ("page_start", "page_end"):
            v = getattr(nxt, key)
            if v is not None:
                p = getattr(prev, key)
                if p is None or (key == "page_start" and v < p) or (key == "page_end" and v > p):
                    setattr(prev, key, v)

    # Phase 1: merge consecutive heading-only blocks (TOC grouping).
    merged: list[SemanticBlockData] = []
    for block in blocks:
        if _is_heading_only(block) and merged and _is_heading_only(merged[-1]):
            _merge_into(merged[-1], block)
        else:
            merged.append(block)

    # Phase 2: absorb a lone heading-only block into the following content block
    # (a parent heading whose body is only its child sections).
    result: list[SemanticBlockData] = []
    i = 0
    while i < len(merged):
        if (
            _is_heading_only(merged[i])
            and i + 1 < len(merged)
            and not _is_heading_only(merged[i + 1])
        ):
            _merge_into(merged[i + 1], merged[i])  # heading becomes context
            # Skip the heading-only block; its content is now in the next block.
            i += 1
        else:
            result.append(merged[i])
            i += 1

    return result
