from dataclasses import dataclass, field
from typing import Callable

from app.schemas.enums import ExtractionRoute, PageDifficulty


class UnsupportedDocumentError(Exception):
    """The file is of a type the Document Processor cannot read."""


@dataclass
class ImageBlob:
    data: bytes
    mime: str
    label: str = "image"


@dataclass
class ReadingUnit:
    """One independently-read piece of a document.

    A PDF page, a slide, a spreadsheet sheet, a standalone image, or the whole
    body of a DOCX/text file. `route` is decided by triage and tells the agent
    how to read it:

    * TEXT   -- `text` is already reliable; no model call.
    * VISION -- `text` is reliable, but `images` must be described by the
                 vision model (or, for an image file, the image *is* the unit).
    * LAYOUT -- `page_image` is the whole page rendered; the layout model
                transcribes it. `text` is kept as the last-resort fallback.

    Renamed from `DocumentUnit` so the chunking layer can use that name for a
    heading/paragraph/table element (see docs/chunking.md). This is the
    extraction-layer routing unit; the chunking layer's `DocumentUnit` is a
    finer-grained element parsed from the readings this unit produces.
    """

    index: int
    kind: str
    text: str = ""
    difficulty: PageDifficulty = PageDifficulty.EASY
    route: ExtractionRoute = ExtractionRoute.TEXT
    images: list[ImageBlob] = field(default_factory=list)
    page_image: ImageBlob | None = None
    signals: dict = field(default_factory=dict)
    # The pre-cleaning text, kept only for debugging. Optional so every
    # existing construction stays valid.
    raw_text: str | None = None
    # Deferred rendering (Phase 3): when set, the Extraction Agent calls this
    # inside its worker pool so pypdfium2 rendering overlaps with model calls
    # instead of blocking the sequential parse loop. Returns (page_image,
    # images) and assigns them onto the unit.
    render_fn: Callable[[], tuple["ImageBlob | None", list[ImageBlob]]] | None = None
    # Local language/script detection (Phase 4). Optional, defaulted.
    language: str | None = None
    script: str | None = None
    lang_confidence: float = 0.0


@dataclass
class ProcessedDocument:
    """What the Document Processor hands the Extraction Agent. No model output."""

    format: str
    units: list[ReadingUnit]
    skipped_units: int = 0
    warnings: list[str] = field(default_factory=list)
    # Optional cleanup hook (e.g. closing the pdfium document that deferred
    # renders still need). The Extraction Agent calls it after reading.
    finalize: Callable[[], None] | None = None
