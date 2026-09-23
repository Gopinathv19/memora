from dataclasses import dataclass, field

from app.schemas.enums import ExtractionRoute, PageDifficulty


class UnsupportedDocumentError(Exception):
    """The file is of a type the Document Processor cannot read."""


@dataclass
class ImageBlob:
    data: bytes
    mime: str
    label: str = "image"


@dataclass
class DocumentUnit:
    """One independently-read piece of a document.

    A PDF page, a slide, a spreadsheet sheet, a standalone image, or the whole
    body of a DOCX/text file. `route` is decided by triage and tells the agent
    how to read it:

    * TEXT   -- `text` is already reliable; no model call.
    * VISION -- `text` is reliable, but `images` must be described by the
                vision model (or, for an image file, the image *is* the unit).
    * LAYOUT -- `page_image` is the whole page rendered; the layout model
                transcribes it. `text` is kept as the last-resort fallback.
    """

    index: int
    kind: str
    text: str = ""
    difficulty: PageDifficulty = PageDifficulty.EASY
    route: ExtractionRoute = ExtractionRoute.TEXT
    images: list[ImageBlob] = field(default_factory=list)
    page_image: ImageBlob | None = None
    signals: dict = field(default_factory=dict)


@dataclass
class ProcessedDocument:
    """What the Document Processor hands the Extraction Agent. No model output."""

    format: str
    units: list[DocumentUnit]
    skipped_units: int = 0
    warnings: list[str] = field(default_factory=list)
