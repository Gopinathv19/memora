"""One reader per file type. Local and free: no model is called here.

Each reader turns a file into `ReadingUnit`s and decides each unit's route.
PDFs are triaged page by page (see triage.py). DOCX/PPTX/XLSX store their text
and table cells exactly in their XML, so those are read directly and only
their embedded pictures need a model.
"""

import hashlib
import io
import threading
from pathlib import PurePath

from app.core.config import Settings
from app.processing.document import (
    ImageBlob,
    ProcessedDocument,
    ReadingUnit,
    UnsupportedDocumentError,
)
from app.processing.langdetect import detect_language, looks_like_gibberish
from app.processing.triage import (
    PageSignals,
    classify,
    junk_ratio,
    route_for,
    unmapped_glyphs,
)
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


def _image_content_hash(blob: ImageBlob) -> str:
    return hashlib.sha256(blob.data).hexdigest()


def _is_decorative(
    blob: ImageBlob,
    settings: Settings,
    seen_hashes: set[str],
) -> bool:
    """True for tiny, extreme-aspect or already-seen images: logos, rules,
    repeated icons/headers. These are skipped before any vision call."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(blob.data))
        width, height = img.size
    except Exception:
        return True  # undecodable: nothing to describe
    if width * height < settings.vision_min_image_pixels:
        return True
    if max(width, height) / max(min(width, height), 1) > settings.vision_max_aspect_ratio:
        return True
    if settings.vision_skip_duplicate_images:
        h = _image_content_hash(blob)
        if h in seen_hashes:
            return True
        seen_hashes.add(h)
    return False


def filter_decorative_images(
    blobs: list[ImageBlob], settings: Settings
) -> tuple[list[ImageBlob], int]:
    """Drop decorative images before the vision model sees them.

    Returns (kept, skipped_count). Only kept images count against the
    document's image budget.
    """
    seen: set[str] = set()
    kept = [b for b in blobs if not _is_decorative(b, settings, seen)]
    return kept, len(blobs) - len(kept)


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


def _looks_indic_or_dense(text: str, signals: PageSignals) -> bool:
    """Heuristic: Devanagari/Tamil codepoints, or a very dense text page."""
    indic = sum(
        1
        for ch in text
        if 0x0900 <= ord(ch) <= 0x097F or 0x0B80 <= ord(ch) <= 0x0BFF
    )
    if indic >= 20:
        return True
    return signals.text_chars > 4000


def _assign_rendered(
    unit: ReadingUnit,
    img,
    scale: float,
    boxes,
    settings: Settings,
    budget: "_ImageBudget",
    warnings: list[str],
) -> None:
    """Turn a rendered page image into the unit's page_image / images."""
    if unit.route == ExtractionRoute.LAYOUT:
        unit.page_image = ImageBlob(_png(img), "image/png", f"page {unit.index}")
    else:
        # Crop each picture out of the rendered page: no need to decode the
        # PDF's own image streams and their filters.
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
                f"picture on page {unit.index}",
            )
            for im in boxes
        ]
        kept, skipped = filter_decorative_images(crops, settings)
        if skipped:
            warnings.append(f"{skipped} decorative image(s) on page {unit.index} skipped")
        unit.images = budget.take(kept)


def _make_deferred_render(
    unit: ReadingUnit,
    rendered,
    i: int,
    long_side: int,
    boxes,
    settings: Settings,
    budget: "_ImageBudget",
    warnings: list[str],
    render_lock: threading.Lock,
):
    """A closure the agent calls in its worker pool to render one page.

    pdfium is not thread-safe on a single document: two workers rendering
    pages of the same `PdfDocument` concurrently abort the process natively
    (a hard crash, not an exception). The lock serializes the pdfium part
    only -- PNG encoding and cropping still overlap across workers.
    """

    def render():
        with render_lock:
            img, scale = _render(rendered, i, long_side)
        _assign_rendered(unit, img, scale, boxes, settings, budget, warnings)
        return unit.page_image, unit.images

    return render


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
    units: list[ReadingUnit] = []
    budget = _ImageBudget(settings.extraction_max_images, warnings)
    try:
        plumber = pdfplumber.open(io.BytesIO(data))
        rendered = pdfium.PdfDocument(data)
    except Exception as exc:  # pdfminer/pdfium raise a zoo of types
        raise UnsupportedDocumentError(f"PDF could not be opened: {exc}") from exc

    deferred = settings.extraction_parallel_render
    # One lock per document: pdfium renders of the same PdfDocument must
    # not overlap (see _make_deferred_render).
    render_lock = threading.Lock()
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
                unmapped_glyphs=unmapped_glyphs(text),
            )
            difficulty = (
                PageDifficulty.HARD
                if mode == ExtractionMode.DEEP
                else classify(signals, settings)
            )
            lang = detect_language(text)
            # Plenty of local text but the language looks wrong (a broken
            # Indic font rendered as Latin gibberish, or an unconfident
            # script mix): the text layer is not trustworthy, promote it.
            if (
                settings.triage_lang_promotion_enabled
                and difficulty != PageDifficulty.HARD
                and signals.text_chars >= settings.triage_min_text_chars
                and (
                    looks_like_gibberish(text)
                    or (
                        lang.script is not None
                        and lang.confidence < settings.triage_lang_min_confidence
                    )
                )
            ):
                difficulty = PageDifficulty.HARD
            unit = ReadingUnit(
                index=i + 1,
                kind="page",
                text=text,
                difficulty=difficulty,
                route=route_for(difficulty),
                signals=signals.as_dict(),
                language=lang.language,
                script=lang.script,
                lang_confidence=lang.confidence,
            )
            if unit.route != ExtractionRoute.TEXT:
                long_side = settings.extraction_render_long_side
                # Optional higher resolution for Indic-script or dense pages.
                # Off by default; current behaviour is preserved.
                if (
                    settings.extraction_render_high_res_enabled
                    and _looks_indic_or_dense(text, signals)
                ):
                    long_side = settings.extraction_render_long_side_indic

                if settings.extraction_parallel_render:
                    # Defer rendering to the agent's worker pool: it overlaps
                    # with model calls instead of blocking this loop.
                    unit.render_fn = _make_deferred_render(
                        unit, rendered, i, long_side, boxes, settings,
                        budget, warnings, render_lock,
                    )
                else:
                    img, scale = _render(rendered, i, long_side)
                    _assign_rendered(unit, img, scale, boxes, settings, budget, warnings)
            units.append(unit)
        skipped = total - limit
        if skipped:
            warnings.append(
                f"Only the first {limit} of {total} pages were read (EXTRACTION_MAX_PAGES)"
            )
    finally:
        plumber.close()
        if not deferred:
            rendered.close()
    budget.finish()
    doc = ProcessedDocument("pdf", units, skipped_units=skipped, warnings=warnings)
    if deferred:
        # Deferred renders still need the pdfium document; close it once the
        # agent has finished reading (it calls finalize()).
        doc.finalize = rendered.close
    return doc


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
    readable = _readable(pictures)
    kept, skipped = filter_decorative_images(readable, settings)
    if skipped:
        warnings.append(f"{skipped} decorative image(s) skipped")
    images = budget.take(kept)
    budget.finish()
    unit = ReadingUnit(
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
    units: list[ReadingUnit] = []

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
        readable = _readable(pictures)
        kept, skipped = filter_decorative_images(readable, settings)
        if skipped:
            warnings.append(f"{skipped} decorative image(s) on slide {number} skipped")
        images = budget.take(kept)
        units.append(
            ReadingUnit(
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
    units: list[ReadingUnit] = []
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
                ReadingUnit(
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
    unit = ReadingUnit(
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
    return ProcessedDocument("text", [ReadingUnit(index=1, kind="document", text=text)])
