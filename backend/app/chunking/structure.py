"""Detect document structure: heading hierarchy → section_path.

Structural-first, semantic-fallback (strategy §7, §8). When headings are
present (Markdown #, ##, ### or numbered "1.", "1.1"), they define the
section hierarchy. When headings are missing, deterministic heuristics are
used: numbered headings, short title-case/caps lines, blank-line clustering,
key/value clustering. An LLM fallback is a setting-gated last resort, not
the default (strategy §8).

Layout transcriptions often emit inconsistent heading levels for the same
logical depth: "Key points" is `###` on one page and `##` on the next. If a
plain (un-numbered) heading is allowed to pop a numbered section heading
("2. Section 2") off the stack, the section context is lost and chunks embed
as "Section: … > Key points". To prevent that, `assign_section_paths` keeps
non-numbered headings nested *under* the most recent numbered section unless
they are clearly at the document's top level.
"""

import re
from app.chunking.units import ParsedUnit

# A numbered heading without Markdown #: "1. Title", "1.1 Subtitle", "2.3.4 Deep"
# Matches "1. Title" (dot after number) and "1.1 Title" (dotted numbering).
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def _is_numbered(title: str) -> bool:
    """True when the heading title starts with a section number ("2. Section 2")."""
    return bool(_NUMBERED_HEADING.match(title.strip()))


def assign_section_paths(units: list[ParsedUnit]) -> list[ParsedUnit]:
    """Walk the units, building a section_path for each from the heading hierarchy.

    Headings define the hierarchy; non-heading units inherit the path of the
    most recent heading chain. Returns the same list of units, each with its
    `section_path` populated.

    The heading stack works by level: a level-N heading pops all headings at
    level >= N, then pushes itself. The section_path is the list of heading
    titles in the stack.

    Non-numbered headings ("Key points", "Conclusion") never pop a numbered
    section heading: if their raw Markdown level would, they are demoted to one
    level deeper than the current stack top. This keeps `section_path` at
    ["…", "2. Section 2", "Key points"] even when the transcription model
    emits `## Key points` after `## 2. Section 2`.
    """
    stack: list[tuple[int, str]] = []  # (level, title)

    for unit in units:
        if unit.kind == "heading" and unit.heading_level is not None:
            level = unit.heading_level
            title = unit.content

            # A non-numbered heading that would pop a numbered heading off the
            # stack is demoted to sit *under* the numbered section instead.
            # Layout models emit inconsistent levels ("## Key points" after
            # "## 2. Section 2"); the number is the stronger signal.
            if stack and not _is_numbered(title) and _is_numbered(stack[-1][1]):
                level = stack[-1][0] + 1

            # Pop headings at this level or deeper.
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            unit.section_path = [title for _, title in stack]
        else:
            unit.section_path = [title for _, title in stack]

    return units


def detect_numbered_headings(units: list[ParsedUnit]) -> list[ParsedUnit]:
    """Promote numbered lines that aren't already headings.

    Some documents (especially from layout-model transcription) have numbered
    headings without Markdown # prefixes: "1. Employee Information",
    "1.1 Personal Details". This detects those and converts them to heading
    units with an inferred level (count the dots + 1).

    A line like "1. Employee Information" is ambiguous: it could be a list
    item or a numbered heading. We treat it as a heading when it is a short
    line (title-like) and not followed by more list items at the same level.
    A line like "1.1 Personal Details" is unambiguously a heading (list items
    don't use dotted numbering).
    """
    result: list[ParsedUnit] = []
    for idx, unit in enumerate(units):
        if unit.kind in ("text", "list"):
            # For list units, check the first line; for text, the whole content.
            first_line = unit.content.split("\n")[0].strip()
            m = _NUMBERED_HEADING.match(first_line)
            if m and len(first_line) < 100:
                # "1.1 Personal Details" is always a heading (dotted numbering).
                # "1. Title" is a heading if it's a single short line (not a
                # multi-item list).
                is_dotted = "." in m.group(1)
                is_single_line = "\n" not in unit.content.strip()
                # A dotted number (1.1) is always a heading. A plain number
                # (1.) is a heading when it's a single short line, whether it
                # was parsed as text or as a one-item list.
                if is_dotted or is_single_line:
                    level = m.group(1).count(".") + 1
                    title = m.group(2).strip()
                    result.append(ParsedUnit(
                        kind="heading",
                        content=title,
                        page=unit.page,
                        heading_level=level,
                    ))
                    continue
        result.append(unit)
    return result
