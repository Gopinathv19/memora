"""Parse extraction readings into ordered DocumentUnit elements.

The readings are the per-unit Markdown the extraction models produced, each
carrying a page number, kind, route, model and status. The agent joins them
into a merged document with provenance markers like
``<!-- page 3 · hard · layout -->``. This module consumes those markers as
provenance stamps and strips them from the content, then splits the Markdown
into fine-grained elements: headings, paragraphs, lists, tables, figures and
key/value pairs.

Page boundaries are NOT semantic boundaries (strategy RULE 3): we parse the
merged document, not per-page, so a section spanning pages stays together.

The layout model (Nemotron-Parse) sometimes emits LaTeX fragments (tables as
``\\begin{tabular}…\\end{tabular}``, escapes like ``\\&``) and unicode bullets
(``•``) that plain Markdown parsers miss. Those are normalized here so the
rest of the chunking pipeline sees clean Markdown.
"""

import re
from dataclasses import dataclass, field

# Matches the provenance marker the extraction agent inserts:
#   <!-- page 3 · hard · layout -->
#   <!-- slide 2 · medium · vision -->
#   <!-- document 1 · easy · text -->
_MARKER = re.compile(
    r"<!--\s*(\w+)\s+(\d+)\s*·\s*\w+\s*·\s*\w+\s*-->"
)

# Markdown heading: # Title, ## Subtitle, etc.
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# A Markdown table: a line starting with | that is followed by a separator row.
_TABLE_ROW = re.compile(r"^\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|\s*$")

# A LaTeX tabular block: \begin{tabular}{lll} ... \end{tabular}
_LATEX_TABULAR_START = re.compile(r"^\s*\\begin\{tabular\}")
_LATEX_TABULAR_END = re.compile(r"^\s*\\end\{tabular\}")

# A figure description the layout/vision prompts produce:
#   [Figure: ...]
# or
#   [Image — label: ...]
# Some layout output also emits a bare caption line like "Figure 1" or
# "Fig. 2: caption text" without brackets. Those are figures too.
_FIGURE = re.compile(r"^\[(Figure|Image)[^\]]*\]", re.IGNORECASE)
_FIGURE_BARE = re.compile(r"^(Figure|Fig\.?)\s*\d+\b", re.IGNORECASE)

# A key/value pair: "Key: value" on a single line. Deliberately tight: the key
# is short (≤ 30 chars), has no commas and no sentence punctuation, so ordinary
# prose containing a colon ("A good test document mixes the structures: real
# users produce…") is not mistaken for a form field.
_KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9 _/'-]{0,29}):\s*(.+)$")

# A list item: "- item", "* item", "1. item", and unicode bullets
# ("• item", "◦ item", "▪ item", "‣ item") that layout models emit.
_LIST_ITEM = re.compile(r"^\s*([-*•◦▪‣]|\d+\.)\s+")

# Numbered headings ("1. Section 1", "1.1 Details") used by the structure layer.
_NUMBERED_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


@dataclass
class ParsedUnit:
    """A fine-grained element parsed from the readings.

    The chunking layer's DocumentUnit (strategy §3.1). Not to be confused with
    the extraction layer's ReadingUnit.
    """

    kind: str  # heading | text | list | table | figure | key_value
    content: str
    page: int | None = None
    heading_level: int | None = None
    section_path: list[str] = field(default_factory=list)


def parse_readings(readings: list[dict]) -> list[ParsedUnit]:
    """Turn persisted readings into ordered ParsedUnit elements.

    Each reading is a dict with keys: page, kind, difficulty, route, model,
    status, text, note (as produced by UnitReading.__dict__). The text of each
    reading is Markdown with an optional provenance marker. We join all
    readings into one document (preserving order), track the current page from
    the markers, and split into elements.
    """
    if not readings:
        return []

    # Build the merged document, tracking which page each line belongs to.
    lines_with_pages: list[tuple[str, int | None]] = []
    for r in readings:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        page = r.get("page")
        for line in text.split("\n"):
            lines_with_pages.append((line, page))

    normalized = _normalize_latex(lines_with_pages)
    return _split_into_elements(normalized)


# --- LaTeX normalization --------------------------------------------------------

# Escape / fragment fixes applied to every line. Ordered longest-first.
_LATEX_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\(", "("),
    (r"\)", ")"),
    (r"\&", "&"),
    (r"\%", "%"),
    (r"\$", "$"),
    (r"\#", "#"),
    (r"\_", "_"),
    (r"\cdot", "·"),
    (r"\ldots", "…"),
    (r"\dots", "…"),
]


def _strip_latex_escapes(line: str) -> str:
    """Remove common LaTeX escapes a transcription model leaks into text."""
    for pattern, replacement in _LATEX_REPLACEMENTS:
        line = line.replace(pattern, replacement)
    # En/em-dashes written as "--" / "---" in LaTeX.
    line = re.sub(r"(?<!\w)---(?!\w)", "—", line)
    line = re.sub(r"(?<!\w)--(?!\w)", "–", line)
    return line


def _latex_tabular_to_markdown(block_lines: list[str]) -> str:
    """Convert a \\begin{tabular}…\\end{tabular} block to a Markdown table.

    Rows are separated by ``\\\\``, cells by ``&``. The alignment argument of
    the environment (e.g. ``{ll|r}``) is ignored; all columns are left-aligned
    Markdown.
    """
    rows: list[list[str]] = []
    for line in block_lines:
        stripped = line.strip()
        if _LATEX_TABULAR_START.match(stripped) or _LATEX_TABULAR_END.match(stripped):
            continue
        if not stripped or stripped == r"\hline" or stripped == r"\toprule" or stripped == r"\midrule" or stripped == r"\bottomrule":
            continue
        # Split the line into row chunks at the LaTeX row separator "\\".
        for chunk in re.split(r"\\\\", stripped):
            cells = [c.strip() for c in chunk.split("&")]
            cells = [c for c in cells if c != ""] or cells
            if any(cells):
                rows.append(cells)

    if not rows:
        return ""

    # Column count = the widest row.
    width = max(len(r) for r in rows)
    # Pad short rows.
    rows = [r + [""] * (width - len(r)) for r in rows]

    # First row is the header; make sure cells are non-empty strings.
    header = [c if c else f"col{i+1}" for i, c in enumerate(rows[0])]
    sep = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _normalize_latex(
    lines_with_pages: list[tuple[str, int | None]],
) -> list[tuple[str, int | None]]:
    """Normalize LaTeX artifacts in the merged document.

    - Converts ``\\begin{tabular}…\\end{tabular}`` blocks into Markdown tables
      (so they are parsed as `table` units, and the table-splitting logic in
      the chunker can run).
    - Strips common LaTeX escapes (``\\&``, ``\\(``, ``\\cdot``, ...) from
      remaining text so they don't pollute embeddings.
    """
    result: list[tuple[str, int | None]] = []
    i = 0
    n = len(lines_with_pages)
    while i < n:
        line, page = lines_with_pages[i]
        if _LATEX_TABULAR_START.match(line):
            # Collect the whole block, then convert it to a Markdown table.
            block: list[tuple[str, int | None]] = []
            while i < n and not _LATEX_TABULAR_END.match(lines_with_pages[i][0]):
                block.append(lines_with_pages[i])
                i += 1
            if i < n:  # consume the \end{tabular} line
                block.append(lines_with_pages[i])
                i += 1
            md = _latex_tabular_to_markdown([l for l, _ in block])
            if md:
                pages = [p for _, p in block if p is not None]
                block_page = pages[-1] if pages else page
                for md_line in md.split("\n"):
                    result.append((md_line, block_page))
            continue
        result.append((_strip_latex_escapes(line), page))
        i += 1
    return result


def _split_into_elements(
    lines_with_pages: list[tuple[str, int | None]],
) -> list[ParsedUnit]:
    """Walk the merged document line by line, grouping into elements."""
    units: list[ParsedUnit] = []
    current_page: int | None = None
    i = 0
    n = len(lines_with_pages)

    while i < n:
        line, page = lines_with_pages[i]
        stripped = line.strip()

        # Update the current page from the content (not markers; those are
        # already consumed -- the readings don't carry markers, but the
        # extraction agent's merged text does if we ever parse that directly).
        if page is not None:
            current_page = page

        # Skip blank lines (they separate elements but aren't elements).
        if not stripped:
            i += 1
            continue

        # Heading
        m = _HEADING.match(stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            units.append(ParsedUnit(
                kind="heading",
                content=title,
                page=current_page,
                heading_level=level,
            ))
            i += 1
            continue

        # Figure / image description (bracketed or a bare "Figure 1" line).
        if _FIGURE.match(stripped) or (
            _FIGURE_BARE.match(stripped) and len(stripped) < 200
        ):
            units.append(ParsedUnit(
                kind="figure",
                content=stripped,
                page=current_page,
            ))
            i += 1
            continue

        # Table: consecutive |...| rows, starting with a header and separator.
        if _TABLE_ROW.match(stripped) and i + 1 < n and _TABLE_SEP.match(lines_with_pages[i + 1][0].strip()):
            table_lines: list[str] = []
            while i < n:
                tline, tpage = lines_with_pages[i]
                tsline = tline.strip()
                if not _TABLE_ROW.match(tsline):
                    break
                if tpage is not None:
                    current_page = tpage
                table_lines.append(tsline)
                i += 1
            units.append(ParsedUnit(
                kind="table",
                content="\n".join(table_lines),
                page=current_page,
            ))
            continue

        # List: consecutive list items.
        if _LIST_ITEM.match(stripped):
            list_lines: list[str] = []
            while i < n:
                lline, lpage = lines_with_pages[i]
                lsline = lline.strip()
                if not lsline:
                    # Allow one blank line within a list, but break on two.
                    if i + 1 < n and _LIST_ITEM.match(lines_with_pages[i + 1][0].strip()):
                        i += 1
                        continue
                    break
                if not _LIST_ITEM.match(lsline) and not lline.startswith("  "):
                    break
                if lpage is not None:
                    current_page = lpage
                list_lines.append(lline)
                i += 1
            units.append(ParsedUnit(
                kind="list",
                content="\n".join(list_lines).strip(),
                page=current_page,
            ))
            continue

        # Key/value pair: a single line "Key: value" (not part of a table/list).
        m = _KEY_VALUE.match(stripped)
        if m and not stripped.startswith("|"):
            key = m.group(1)
            # Reject prose with a colon: keys are short, comma-free and
            # sentence-punctuation-free. Real form fields ("Name: Alice",
            # "Date: 2026-01-01") pass; prose ("...the structures: real users
            # produce...") does not.
            if (
                len(key) <= 30
                and "," not in key
                and ";" not in key
                and not re.search(r"[.!?]$", key)
            ):
                # Group consecutive key/value pairs into one element.
                kv_lines: list[str] = []
                while i < n:
                    kline, kpage = lines_with_pages[i]
                    ksline = kline.strip()
                    if not ksline:
                        # Allow one blank line; break on two.
                        if i + 1 < n and _KEY_VALUE.match(lines_with_pages[i + 1][0].strip()):
                            i += 1
                            continue
                        break
                    if not _KEY_VALUE.match(ksline):
                        break
                    if kpage is not None:
                        current_page = kpage
                    kv_lines.append(ksline)
                    i += 1
                units.append(ParsedUnit(
                    kind="key_value",
                    content="\n".join(kv_lines),
                    page=current_page,
                ))
                continue

        # Paragraph: collect consecutive non-empty, non-special lines.
        para_lines: list[str] = []
        while i < n:
            pline, ppage = lines_with_pages[i]
            psline = pline.strip()
            if not psline:
                break
            if (
                _HEADING.match(psline)
                or _FIGURE.match(psline)
                or (_FIGURE_BARE.match(psline) and len(psline) < 200)
                or _TABLE_ROW.match(psline)
                or _LIST_ITEM.match(psline)
                or (
                    _KEY_VALUE.match(psline)
                    and len(_KEY_VALUE.match(psline).group(1)) <= 30
                    and "," not in _KEY_VALUE.match(psline).group(1)
                    and ";" not in _KEY_VALUE.match(psline).group(1)
                )
            ):
                break
            if ppage is not None:
                current_page = ppage
            para_lines.append(psline)
            i += 1
        if para_lines:
            units.append(ParsedUnit(
                kind="text",
                content=" ".join(para_lines),
                page=current_page,
            ))

    return units
