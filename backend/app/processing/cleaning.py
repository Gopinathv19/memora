"""Clean a unit's transcribed text before it is persisted or chunked.

Model readings and local PDF text carry junk: `<unk>` tokens from a tokenizer,
U+FFFD replacement characters, `(cid:N)` glyphs from unmapped fonts, control
and zero-width characters, and -- worst of all -- loops, where a model repeats
a line or block until it runs out of tokens. `clean_reading()` removes the junk
and collapses the repetition, and reports how much it had to remove so the
caller can decide whether the reading is trustworthy at all.

Everything here is local and free: no model calls.
"""

import re
import unicodedata
import zlib
from dataclasses import dataclass, field

# --- junk patterns ---------------------------------------------------------------

# Tokenizer artifacts and unmapped glyphs.
_UNK = re.compile(r"<unk>|<pad>|</s>|<s>|<\|[^|>\s]{1,20}\|>", re.IGNORECASE)
_CID = re.compile(r"\(cid:\d+\)")
# Control characters except tab/newline/CR, plus zero-width and bidi marks.
_CONTROL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    "\u200b\u200c\u200e\u200f\u202a-\u202e\u2060\ufeff"
    "]"
)


@dataclass
class CleanReport:
    """What `clean_reading()` found and did. Kept alongside the reading."""

    damage_ratio: float = 0.0  # removed chars / original chars
    compression_ratio: float = 1.0  # zlib compressed / original; low => loop
    loop_detected: bool = False
    removed_chars: int = 0
    collapsed_lines: int = 0
    collapsed_blocks: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "damage_ratio": round(self.damage_ratio, 4),
            "compression_ratio": round(self.compression_ratio, 4),
            "loop_detected": self.loop_detected,
            "removed_chars": self.removed_chars,
            "collapsed_lines": self.collapsed_lines,
            "collapsed_blocks": self.collapsed_blocks,
            "notes": list(self.notes),
        }


def compression_ratio(text: str) -> float:
    """zlib compressed size / original size. Abnormally low means a loop."""
    if not text:
        return 1.0
    data = text.encode("utf-8")
    return len(zlib.compress(data, 6)) / max(len(data), 1)


def _strip_junk(text: str, report: CleanReport) -> str:
    original = len(text)
    text = _UNK.sub(" ", text)
    text = _CID.sub(" ", text)
    text = _CONTROL.sub("", text)
    text = text.replace("\ufffd", " ")
    text = unicodedata.normalize("NFC", text)
    report.removed_chars = max(original - len(text.strip()), 0)
    report.damage_ratio = report.removed_chars / max(original, 1)
    return text


def _collapse_duplicate_lines(text: str, report: CleanReport) -> str:
    """Drop a line that repeats the one immediately before it."""
    seen_prev: str | None = None
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and stripped == seen_prev:
            report.collapsed_lines += 1
            continue
        out.append(line)
        seen_prev = stripped
    return "\n".join(out)


_EMPHASIS = re.compile(r"[*_`#>\s]+")


def _phrase_key(line: str) -> str:
    """A line's text without Markdown emphasis, whitespace or case."""
    return _EMPHASIS.sub(" ", line).strip().casefold()


def _collapse_repeated_phrases(
    text: str, report: CleanReport, min_chars: int = 15, min_repeats: int = 3
) -> str:
    """Keep one copy of a short line that recurs `min_repeats`+ times in a unit.

    Handles loops that blank lines, Markdown emphasis or a truncated final copy
    defeat in the adjacent-line and block passes. Table rows, identifier lines
    and short lines (labels, 'Yes') are left alone.
    """
    lines = text.split("\n")

    def eligible(line: str) -> bool:
        stripped = line.strip()
        return (
            len(_phrase_key(line)) >= min_chars
            and not stripped.startswith("|")
            and not _is_identifier(line)
        )

    counts: dict[str, int] = {}
    for line in lines:
        if eligible(line):
            key = _phrase_key(line)
            counts[key] = counts.get(key, 0) + 1
    repeated = {k for k, c in counts.items() if c >= min_repeats}
    if not repeated:
        return text

    def echo_of(key: str) -> str | None:
        """The repeated phrase this line only echoes: the phrase itself, or the
        phrase followed by more copies or a truncated copy of it."""
        for phrase in repeated:
            if key.startswith(phrase):
                rest = key[len(phrase):].strip()
                if not rest or phrase.startswith(rest) or rest.startswith(phrase):
                    return phrase
        return None

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        phrase = echo_of(_phrase_key(line)) if eligible(line) else None
        if phrase is not None:
            if phrase in seen:
                report.collapsed_lines += 1
                continue
            seen.add(phrase)
        out.append(line)
    return "\n".join(out)


def _collapse_repeated_blocks(text: str, report: CleanReport, min_block: int = 1) -> str:
    """Collapse any block of lines repeated 3+ times in a row to one copy."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        # Find the largest block (blank lines allowed inside it) whose next
        # two copies follow immediately.
        best = 0
        for size in range(min_block, max(1, len(lines) - i) + 1):
            block = lines[i : i + size]
            if not block or not block[0].strip():
                break  # a block must start with content
            if (
                lines[i + size : i + 2 * size] == block
                and lines[i + 2 * size : i + 3 * size] == block
            ):
                best = size
        if best:
            out.extend(lines[i : i + best])
            report.collapsed_blocks += 1
            i += best
            # Skip every full copy that follows...
            while i + best <= len(lines) and lines[i : i + best] == out[-best:]:
                i += best
            # ...and a trailing partial copy (the loop was cut off mid-block).
            remaining = lines[i:]
            if remaining and out[-best:][: len(remaining)] == remaining:
                i = len(lines)
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def clean_reading(
    text: str,
    *,
    loop_compression_min: float = 0.10,
    loop_min_chars: int = 200,
) -> tuple[str, CleanReport]:
    """Sanitize one reading. Returns (clean_text, report).

    `loop_compression_min`: below this zlib ratio (on texts longer than
    `loop_min_chars`) the reading is flagged as a likely model loop.
    """
    report = CleanReport()
    if not text or not text.strip():
        return "", report
    cleaned = _strip_junk(text, report)
    cleaned = _collapse_duplicate_lines(cleaned, report)
    cleaned = _collapse_repeated_phrases(cleaned, report)
    cleaned = _collapse_repeated_blocks(cleaned, report)
    # Tidy whitespace without touching content.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    report.compression_ratio = compression_ratio(cleaned)
    if len(cleaned) >= loop_min_chars and report.compression_ratio < loop_compression_min:
        report.loop_detected = True
        report.notes.append("likely model loop: abnormally low compression ratio")
    return cleaned, report


# --- headers / footers -------------------------------------------------------------

_MASK_DIGITS = re.compile(r"\d")
# A long number (8+ digits, optionally grouped by spaces or hyphens): an
# identifier such as an Aadhaar or account number. Masked, every such number
# looks alike, so a per-page ID at the page edge would pass for a footer.
_LONG_NUMBER = re.compile(r"\d(?:[ -]?\d){7,}")
# Enrolment-style numbers grouped with '/' or '.': 2726/10022/47657.
_GROUPED_NUMBER = re.compile(r"\d{3,}(?:[/.]\d{3,}){2,}")


def _line_key(line: str) -> str:
    """A line's identity with digits masked: 'Page 12' == 'Page 87'."""
    return _MASK_DIGITS.sub("#", line.strip().lower())


def _is_identifier(line: str) -> bool:
    """A line carrying a long number is content, never a header/footer."""
    return bool(_LONG_NUMBER.search(line) or _GROUPED_NUMBER.search(line))


def strip_headers_footers(
    texts: list[str],
    *,
    repeat_ratio: float = 0.5,
    min_pages: int = 3,
) -> tuple[list[str], dict[str, str]]:
    """Remove lines that repeat as a header/footer across many pages.

    A line (digits masked) appearing at the top or bottom of more than
    `repeat_ratio` of pages (and at least `min_pages` pages) is a running
    header/footer: drop it from every page and return one copy of each as
    document-level metadata. A line carrying a long number (an ID) is never
    treated as a header/footer: its masked form would match every page.
    """
    n = len(texts)
    if n < min_pages:
        return texts, {}
    threshold = max(min_pages, int(n * repeat_ratio + 0.5))
    counts: dict[str, int] = {}
    for text in texts:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        edges = set(
            _line_key(ln)
            for ln in (lines[:2] + lines[-2:] if lines else [])
            if not _is_identifier(ln)
        )
        for key in edges:
            counts[key] = counts.get(key, 0) + 1
    repeated = {k for k, c in counts.items() if c >= threshold}
    if not repeated:
        return texts, {}
    kept_meta: dict[str, str] = {}
    out: list[str] = []
    for text in texts:
        lines = text.split("\n")
        body = [
            ln for ln in lines if not (ln.strip() and _line_key(ln) in repeated)
        ]
        out.append("\n".join(body).strip())
        for ln in lines:
            if ln.strip() and _line_key(ln) in repeated and _line_key(ln) not in kept_meta:
                kept_meta[_line_key(ln)] = ln.strip()
    return out, kept_meta