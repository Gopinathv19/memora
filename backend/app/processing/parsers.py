"""One reader per file type. Local and free: no model is called here.

Each reader turns a file into `DocumentUnit`s and decides each unit's route.
PDFs are triaged page by page (see triage.py). DOCX/PPTX/XLSX store their text
and table cells exactly in their XML, so those are read directly and only
their embedded pictures need a model.
"""

import io
from pathlib import PurePath

from app.core.config import Settings
from app.processing.document import (
    DocumentUnit,
    ImageBlob,
    ProcessedDocument,
    UnsupportedDocumentError,
)
from app.processing.triage import PageSignals, classify, junk_ratio, route_for
from app.schemas.enums import ExtractionMode, ExtractionRoute, PageDifficulty

_MIME_FORMATS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/json": "text",
    "application/xml": "text",
    "application/csv": "text",
}
_EXT_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".csv": "text",
    ".tsv": "text",
    ".json": "text",
    ".xml": "text",
    ".html": "text",
    ".htm": "text",
    ".log": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
}
# Legacy binary Office formats need a converter we do not ship.
_LEGACY = {".doc", ".ppt", ".xls"}

MAX_SHEET_ROWS = 1000
MAX_SHEET_COLS = 50
MIN_IMAGE_POINTS = 40  # ignore icons and rules smaller than this on a PDF page
MAX_IMAGE_SIDE = 2048


def detect_format(mime: str | None, filename: str | None) -> str:
    """Pick a reader. The extension wins over a generic/missing MIME type."""
    ext = PurePath(filename or "").suffix.lower()
    if ext in _LEGACY:
        raise UnsupportedDocumentError(
            f"Legacy {ext} files are not supported; save it as {ext}x and upload again"
        )
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in _MIME_FORMATS:
        return _MIME_FORMATS[mime]
    if ext in _EXT_FORMATS:
        return _EXT_FORMATS[ext]
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("text/"):
        return "text"
    raise UnsupportedDocumentError(
        f"Unsupported document type ({mime or 'unknown'}, {ext or 'no extension'})"
    )


def markdown_table(rows: list[list[str]]) -> str:
    rows = [[_cell(c) for c in row] for row in rows if any(_cell(c) for c in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def normalize_image(data: bytes, label: str) -> ImageBlob:
    """Re-encode to PNG/JPEG within the models' size limits."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedDocumentError(
            "Image format not readable (HEIC and similar need converting to JPEG/PNG)"
        ) from exc
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return ImageBlob(out.getvalue(), "image/png", label)


class _ImageBudget:
    """Caps how many pictures one document may send to the vision model."""

    def __init__(self, limit: int, warnings: list[str]):
        self.left = limit
        self.dropped = 0
        self.warnings = warnings

    def take(self, blobs: list[ImageBlob]) -> list[ImageBlob]:
        kept = blobs[: max(self.left, 0)]
        self.left -= len(kept)
        self.dropped += len(blobs) - len(kept)
        return kept

    def finish(self) -> None:
        if self.dropped:
            self.warnings.append(
                f"{self.dropped} image(s) not sent to the vision model "
                "(EXTRACTION_MAX_IMAGES reached)"
            )


# --- PDF ------------------------------------------------------------------------


def _render(pdf, index: int, long_side: int):
    page = pdf[index]
    width, height = page.get_size()
    scale = long_side / max(width, height, 1)
    return page.render(scale=scale).to_pil(), scale


def _png(img) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def parse_pdf(data: bytes, settings: Settings, mode: ExtractionMode) -> ProcessedDocument:
    import pdfplumber
    import pypdfium2 as pdfium

    warnings: list[str] = []
    units: list[DocumentUnit] = []
    budget = _ImageBudget(settings.extraction_max_images, warnings)
    try:
        plumber = pdfplumber.open(io.BytesIO(data))
        rendered = pdfium.PdfDocument(data)
    except Exception as exc:  # pdfminer/pdfium raise a zoo of types
        raise UnsupportedDocumentError(f"PDF could not be opened: {exc}") from exc

    try:
        total = len(plumber.pages)
        limit = min(total, settings.extraction_max_pages)
        for i in range(limit):
            page = plumber.pages[i]
            text = page.extract_text() or ""
            area = max(float(page.width) * float(page.height), 1.0)
            boxes = [
                im
                for im in page.images
                if (im["x1"] - im["x0"]) >= MIN_IMAGE_POINTS
                and (im["bottom"] - im["top"]) >= MIN_IMAGE_POINTS
            ]
            covered = sum((im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) for im in boxes)
            try:
                tables = len(page.find_tables())
            except Exception:
                tables = 0
            signals = PageSignals(
                text_chars=len(text.strip()),
                image_count=len(boxes),
                image_ratio=min(covered / area, 1.0),
                table_count=tables,
                ruled_lines=len(page.lines) + len(page.rects),
                junk_ratio=junk_ratio(text),
            )
            difficulty = (
                PageDifficulty.HARD
                if mode == ExtractionMode.DEEP
                else classify(signals, settings)
            )
            unit = DocumentUnit(
                index=i + 1,
                kind="page",
                text=text,
                difficulty=difficulty,
                route=route_for(difficulty),
                signals=signals.as_dict(),
            )
            if unit.route != ExtractionRoute.TEXT:
                img, scale = _render(rendered, i, settings.extraction_render_long_side)
                if unit.route == ExtractionRoute.LAYOUT:
                    unit.page_image = ImageBlob(_png(img), "image/png", f"page {i + 1}")
                else:
                    # Crop each picture out of the rendered page: no need to
                    # decode the PDF's own image streams and their filters.
                    crops = [
                        ImageBlob(
                            _png(
                                img.crop(
                                    (
                                        int(im["x0"] * scale),
                                        int(im["top"] * scale),
                                        int(im["x1"] * scale),
                                        int(im["bottom"] * scale),
                                    )
                                )
                            ),
                            "image/png",
                            f"picture on page {i + 1}",
                        )
                        for im in boxes
                    ]
                    unit.images = budget.take(crops)
            units.append(unit)
        skipped = total - limit
        if skipped:
            warnings.append(
                f"Only the first {limit} of {total} pages were read (EXTRACTION_MAX_PAGES)"
            )
    finally:
        plumber.close()
        rendered.close()
    budget.finish()
    return ProcessedDocument("pdf", units, skipped_units=skipped, warnings=warnings)


# --- Office formats ---------------------------------------------------------------


def parse_docx(data: bytes, settings: Settings) -> ProcessedDocument:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise UnsupportedDocumentError(f"DOCX could not be opened: {exc}") from exc

    warnings: list[str] = []
    blocks: list[str] = []
    # Walk the body in order so tables stay where they were in the text.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name or "").lower() if paragraph.style else ""
            if style.startswith("heading"):
                level = "".join(c for c in style if c.isdigit()) or "1"
                text = "#" * min(int(level), 6) + " " + text
            blocks.append(text)
        elif tag == "tbl":
            table = Table(child, document)
            blocks.append(markdown_table([[c.text for c in r.cells] for r in table.rows]))

    pictures = [
        ImageBlob(rel.target_part.blob, rel.target_part.content_type, "embedded picture")
        for rel in document.part.rels.values()
        if "image" in rel.reltype
    ]
    budget = _ImageBudget(settings.extraction_max_images, warnings)
    images = budget.take(_readable(pictures))
    budget.finish()
    unit = DocumentUnit(
        index=1,
        kind="document",
        text="\n\n".join(b for b in blocks if b),
        difficulty=PageDifficulty.MEDIUM if images else PageDifficulty.EASY,
        route=ExtractionRoute.VISION if images else ExtractionRoute.TEXT,
        images=images,
    )
    return ProcessedDocument("docx", [unit], warnings=warnings)


def _readable(blobs: list[ImageBlob]) -> list[ImageBlob]:
    """Normalize embedded pictures, skipping ones Pillow cannot decode (EMF/WMF)."""
    kept = []
    for blob in blobs:
        try:
            kept.append(normalize_image(blob.data, blob.label))
        except UnsupportedDocumentError:
            continue
    return kept


def parse_pptx(data: bytes, settings: Settings) -> ProcessedDocument:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise UnsupportedDocumentError(f"PPTX could not be opened: {exc}") from exc

    warnings: list[str] = []
    budget = _ImageBudget(settings.extraction_max_images, warnings)
    units: list[DocumentUnit] = []

    def walk(shapes, blocks: list[str], pictures: list[ImageBlob], number: int) -> None:
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(shape.shapes, blocks, pictures, number)
            elif getattr(shape, "has_table", False) and shape.has_table:
                blocks.append(
                    markdown_table([[c.text for c in r.cells] for r in shape.table.rows])
                )
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                pictures.append(
                    ImageBlob(shape.image.blob, shape.image.content_type, f"picture on slide {number}")
                )
            elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    blocks.append(text)

    slides = list(deck.slides)
    limit = min(len(slides), settings.extraction_max_pages)
    for number, slide in enumerate(slides[:limit], start=1):
        blocks: list[str] = []
        pictures: list[ImageBlob] = []
        walk(slide.shapes, blocks, pictures, number)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                blocks.append(f"Speaker notes: {notes}")
        images = budget.take(_readable(pictures))
        units.append(
            DocumentUnit(
                index=number,
                kind="slide",
                text="\n\n".join(blocks),
                difficulty=PageDifficulty.MEDIUM if images else PageDifficulty.EASY,
                route=ExtractionRoute.VISION if images else ExtractionRoute.TEXT,
                images=images,
            )
        )
    skipped = len(slides) - limit
    if skipped:
        warnings.append(f"Only the first {limit} of {len(slides)} slides were read")
    budget.finish()
    return ProcessedDocument("pptx", units, skipped_units=skipped, warnings=warnings)


def parse_xlsx(data: bytes, settings: Settings) -> ProcessedDocument:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise UnsupportedDocumentError(f"XLSX could not be opened: {exc}") from exc

    warnings: list[str] = []
    units: list[DocumentUnit] = []
    try:
        for number, sheet in enumerate(book.worksheets, start=1):
            rows: list[list[str]] = []
            truncated = False
            for row in sheet.iter_rows(values_only=True):
                if len(rows) >= MAX_SHEET_ROWS:
                    truncated = True
                    break
                rows.append([_cell(v) for v in row[:MAX_SHEET_COLS]])
            table = markdown_table(rows)
            if truncated:
                warnings.append(
                    f"Sheet '{sheet.title}' truncated to its first {MAX_SHEET_ROWS} rows"
                )
            units.append(
                DocumentUnit(
                    index=number,
                    kind="sheet",
                    text=f"## Sheet: {sheet.title}\n\n{table}" if table else "",
                )
            )
    finally:
        book.close()
    return ProcessedDocument("xlsx", units, warnings=warnings)


# --- Images and text ------------------------------------------------------------


def parse_image(data: bytes) -> ProcessedDocument:
    blob = normalize_image(data, "uploaded image")
    unit = DocumentUnit(
        index=1,
        kind="image",
        difficulty=PageDifficulty.HARD,
        route=ExtractionRoute.VISION,
        images=[blob],
    )
    return ProcessedDocument("image", [unit])


def parse_text(data: bytes) -> ProcessedDocument:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    return ProcessedDocument("text", [DocumentUnit(index=1, kind="document", text=text)])
