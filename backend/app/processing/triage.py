"""Decide how hard a PDF page is to read, from cheap local signals.

Local text extraction is reliable for plain digital text and unreliable for
tables, pictures and scans. The signals below are only used to *notice* those,
never to read them: a noticed table goes to the layout model whole. When a
check is borderline the page moves up a level -- a wasted model call costs
little, a missed hard page loses data.
"""

import re
from dataclasses import asdict, dataclass

from app.core.config import Settings
from app.schemas.enums import ExtractionRoute, PageDifficulty

# pdfminer writes "(cid:123)" for glyphs whose font has no Unicode mapping.
_CID = re.compile(r"\(cid:\d+\)")


@dataclass
class PageSignals:
    text_chars: int
    image_count: int
    image_ratio: float
    table_count: int
    ruled_lines: int
    junk_ratio: float

    def as_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in asdict(self).items()}


def junk_ratio(text: str) -> float:
    """Share of the text that is unmapped glyphs, replacement or control chars."""
    if not text:
        return 0.0
    junk = sum(len(m) for m in _CID.findall(text))
    stripped = _CID.sub("", text)
    junk += sum(
        1 for ch in stripped if ch == "�" or (ord(ch) < 32 and ch not in "\n\r\t")
    )
    return junk / max(len(text), 1)


def classify(signals: PageSignals, settings: Settings) -> PageDifficulty:
    blank = (
        signals.text_chars == 0 and signals.image_count == 0 and signals.ruled_lines == 0
    )
    if blank:
        return PageDifficulty.EASY
    if signals.junk_ratio > settings.triage_max_junk_ratio:
        return PageDifficulty.HARD  # broken font/encoding: the text is not trustworthy
    if signals.table_count > 0 or signals.ruled_lines >= settings.triage_min_ruled_lines:
        return PageDifficulty.HARD  # a table or grid
    if signals.image_ratio >= settings.triage_image_ratio:
        return PageDifficulty.HARD  # a scan, a chart, a picture-heavy page
    if signals.text_chars < settings.triage_min_text_chars and (
        signals.image_count > 0 or signals.text_chars == 0
    ):
        return PageDifficulty.HARD  # little real text: probably scanned or drawn
    if signals.image_count > settings.triage_max_medium_images:
        return PageDifficulty.HARD
    if signals.image_count > 0:
        return PageDifficulty.MEDIUM
    return PageDifficulty.EASY


def route_for(difficulty: PageDifficulty) -> ExtractionRoute:
    return {
        PageDifficulty.EASY: ExtractionRoute.TEXT,
        PageDifficulty.MEDIUM: ExtractionRoute.VISION,
        PageDifficulty.HARD: ExtractionRoute.LAYOUT,
    }[difficulty]
