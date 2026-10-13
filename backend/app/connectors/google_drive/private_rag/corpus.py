"""Ephemeral corpus construction: Drive PDFs -> in-memory chunks.

This is private mode's only "ingestion". Nothing here touches the database,
the storage backends, or the filesystem: the PDF bytes are downloaded into
memory, parsed from memory, read by the Extraction Agent, and chunked by the
pure chunking layer. The output is a list of `PrivateChunk`s that lives for
the duration of one request and is then garbage-collected -- no source row,
no extraction row, no chunks table, no embeddings table, no temp file.

The existing parse -> read -> chunk code is reused unchanged; the only new
thing is where the bytes come from (Drive, in memory) and where the chunks
go (nowhere).
"""

import io
import logging
import uuid
from dataclasses import dataclass, field

from app.agents.extraction_agent import ExtractionAgent
from app.chunking import build_semantic_blocks, chunk_semantic_block, parse_readings
from app.chunking.structure import assign_section_paths, detect_numbered_headings
from app.connectors.google_drive import drive_client, file_discovery
from app.core.config import Settings, get_settings
from app.processing.processor import DocumentProcessor
from app.storage.local import MAX_UPLOAD_BYTES

log = logging.getLogger(__name__)


@dataclass
class PrivateChunk:
    """One retrieval chunk that exists only for this request."""

    content: str
    embedding_text: str
    token_count: int
    content_type: str
    section_path: list
    page_start: int | None
    page_end: int | None
    # Provenance: which Drive file the chunk came from.
    drive_file_id: str
    file_name: str
    # Filled by the retrieval stage.
    vector: list = field(default_factory=list)
    similarity: float = 0.0
    rerank_score: float | None = None


@dataclass
class FileReport:
    """What happened to one requested Drive file."""

    drive_file_id: str
    file_name: str
    status: str  # processed | skipped | failed
    detail: str | None = None
    pages: int = 0
    chunks: int = 0


@dataclass
class Corpus:
    """The ephemeral retrieval corpus: chunks plus per-file provenance."""

    chunks: list[PrivateChunk]
    files: list[FileReport]
    pages_processed: int


def build_corpus(
    access_token: str,
    drive_file_ids: list[str],
    agent: ExtractionAgent,
    settings: Settings | None = None,
) -> Corpus:
    """Read the requested Drive PDFs into memory and chunk them.

    Hard caps (settings): at most `private_rag_max_pages` pages across the
    whole request. A file that does not fit the remaining budget is skipped
    whole -- a partially-read document would answer half-questions silently.
    One file failing never fails the others; it is reported instead.
    """
    settings = settings or get_settings()
    max_pages = settings.private_rag_max_pages
    # Align the processor's own per-document page cap with the private
    # budget, so a file is either read within the budget or skipped whole.
    processor = DocumentProcessor(
        settings.model_copy(update={"extraction_max_pages": max_pages})
    )

    chunks: list[PrivateChunk] = []
    files: list[FileReport] = []
    pages_used = 0

    for drive_file_id in drive_file_ids:
        report = _process_file(
            access_token,
            drive_file_id,
            agent,
            processor,
            pages_left=max_pages - pages_used,
            chunks=chunks,
        )
        pages_used += report.pages if report.status == "processed" else 0
        files.append(report)

    return Corpus(chunks=chunks, files=files, pages_processed=pages_used)


def _process_file(
    access_token: str,
    drive_file_id: str,
    agent: ExtractionAgent,
    processor: DocumentProcessor,
    *,
    pages_left: int,
    chunks: list[PrivateChunk],
) -> FileReport:
    name = ""
    try:
        # Metadata first: the Drive ACL check happens here -- a file the
        # connected account cannot read is a 404 from Google, reported below.
        file = file_discovery.get_pdf(access_token, drive_file_id)
        name = file.file_name
        if file.size_bytes is not None and file.size_bytes > MAX_UPLOAD_BYTES:
            return FileReport(
                drive_file_id, name, "skipped",
                f"larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
            )

        # Download into memory. The size limit is enforced mid-stream by the
        # client, so an oversized file never lands anywhere in full.
        buffer = io.BytesIO()
        drive_client.download_file(
            access_token, drive_file_id, buffer, max_bytes=MAX_UPLOAD_BYTES
        )
        document = processor.process(buffer.getvalue(), file.mime_type, file.file_name)
        del buffer  # the raw bytes die here; only the parsed units live on

        pages = len(document.units) + document.skipped_units
        if pages > pages_left:
            return FileReport(
                drive_file_id, name, "skipped",
                f"{pages} pages exceed the remaining page budget ({pages_left})",
                pages=pages,
            )

        # The agent requires a source_id; it is ephemeral -- nothing is ever
        # persisted under it.
        output = agent.extract(document, source_id=uuid.uuid4())
        file_chunks = _chunk_readings(
            output.readings, processor.settings, drive_file_id, name
        )
        chunks.extend(file_chunks)
        return FileReport(
            drive_file_id, name, "processed", pages=pages, chunks=len(file_chunks)
        )
    except Exception as exc:  # one bad file must not fail the whole query
        log.warning(
            "private RAG: Drive file %s failed (%s)", drive_file_id, type(exc).__name__
        )
        return FileReport(drive_file_id, name, "failed", _safe_detail(exc))


def _safe_detail(exc: Exception) -> str:
    """A short, type-focused error for the per-file report."""
    message = getattr(exc, "message", None) or str(exc)
    return f"{type(exc).__name__}: {message}"[:200]


def _chunk_readings(
    readings, settings: Settings, drive_file_id: str, file_name: str
) -> list[PrivateChunk]:
    """The pure chunking pipeline, exactly as chunking_service runs it."""
    units = parse_readings([r.__dict__ for r in readings])
    units = detect_numbered_headings(units)
    units = assign_section_paths(units)
    blocks = build_semantic_blocks(units)

    chunks: list[PrivateChunk] = []
    for block in blocks:
        for c in chunk_semantic_block(block, settings):
            chunks.append(
                PrivateChunk(
                    content=c.content,
                    embedding_text=c.embedding_text,
                    token_count=c.token_count,
                    content_type=c.content_type,
                    section_path=c.section_path,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    drive_file_id=drive_file_id,
                    file_name=file_name,
                )
            )
    return chunks
