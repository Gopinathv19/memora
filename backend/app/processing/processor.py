from app.core.config import Settings, get_settings
from app.processing import parsers
from app.processing.document import ProcessedDocument
from app.schemas.enums import ExtractionMode


class DocumentProcessor:
    """Bytes in, routed `DocumentUnit`s out. Pure and local: no model calls.

    This is the boundary between "what is in the file" and "who reads it". The
    processor decides, per page/slide/sheet, whether local text is good enough
    or a model has to look; the Extraction Agent then does the looking.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def process(
        self,
        data: bytes,
        mime: str | None,
        filename: str | None,
        mode: ExtractionMode = ExtractionMode.STANDARD,
    ) -> ProcessedDocument:
        fmt = parsers.detect_format(mime, filename)
        if fmt == "pdf":
            return parsers.parse_pdf(data, self.settings, mode)
        if fmt == "docx":
            return parsers.parse_docx(data, self.settings)
        if fmt == "pptx":
            return parsers.parse_pptx(data, self.settings)
        if fmt == "xlsx":
            return parsers.parse_xlsx(data, self.settings)
        if fmt == "image":
            return parsers.parse_image(data)
        return parsers.parse_text(data)
