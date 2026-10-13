"""The RAG adapter: a Drive PDF becomes a Source through the *existing* pipeline.

This is the whole point of the connector. It does not parse, chunk, embed,
graph or answer anything itself: it downloads the Drive file to a temporary
file and hands it to `source_service.create_source_from_upload` -- the same
function the upload route uses -- then triggers the same background
extraction / graph tasks. Google Drive becomes just another input source.

Metadata (drive_file_id, modified_time, ...) is recorded in `drive_imports`,
the V2 sync hook.
"""

import glob
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.connectors.google_drive import drive_client, file_discovery
from app.core.errors import ValidationError
from app.db.models import DriveConnection, DriveImport
from app.services import source_service
from app.services.scope import Scope
from app.storage import StorageBackend
from app.storage.local import MAX_UPLOAD_BYTES

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportResult:
    source_id: uuid.UUID
    drive_file_id: str
    file_name: str
    size_bytes: int
    already_imported: bool


def import_pdf(
    db: Session,
    connection: DriveConnection,
    drive_file_id: str,
    subject_id: uuid.UUID,
    scope: Scope,
    storage: StorageBackend,
    *,
    extract: bool = True,
    build_graph: bool = False,
    reimport: bool = False,
) -> ImportResult:
    """Download a Drive PDF and feed it into the existing RAG ingestion.

    Steps:
    1. Verify the file is a downloadable PDF and under the size limit.
    2. Stream it to a temporary file (cleaned up in `finally`).
    3. Call the existing `create_source_from_upload` -- scope-checked, bytes
       into the existing StorageBackend, a `sources` row in `pending`.
    4. Optionally start the existing extraction (and graph) background tasks,
       exactly the way the upload route does.
    5. Record the Drive metadata in `drive_imports`.
    """
    # Resolve the connection's current access token (refreshing if stale).
    from app.connectors.google_drive.service import current_access_token

    access_token = current_access_token(db, connection)

    # 1. Validate before any bytes move.
    file = file_discovery.get_pdf(access_token, drive_file_id)
    if file.size_bytes is not None and file.size_bytes > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"Drive file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit"
        )

    # Idempotent by default: one import per (connection, drive file).
    existing = (
        db.query(DriveImport)
        .filter(
            DriveImport.connection_id == connection.id,
            DriveImport.drive_file_id == drive_file_id,
        )
        .one_or_none()
    )
    if existing is not None and not reimport:
        return ImportResult(
            source_id=existing.source_id,
            drive_file_id=drive_file_id,
            file_name=existing.file_name,
            size_bytes=existing.size_bytes or 0,
            already_imported=True,
        )

    # 2. Download to a temp file. Named so the processor can sniff the
    #    extension; deleted in `finally` no matter what happens below.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="gdrive-")
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            drive_client.download_file(
                access_token, drive_file_id, tmp, max_bytes=MAX_UPLOAD_BYTES
            )
        size = os.path.getsize(tmp_path)

        # 3. The existing pipeline, unchanged. The subject is scope-checked
        #    inside create_source_from_upload before a byte is written.
        with open(tmp_path, "rb") as fileobj:
            source = source_service.create_source_from_upload(
                db,
                subject_id,
                fileobj=fileobj,
                filename=file.file_name,
                content_type=file.mime_type,
                scope=scope,
                storage=storage,
            )

        # 4. Extraction / graph are triggered by the *route* as background
        #    tasks (exactly like the upload route), not here: run_extraction
        #    opens its own DB session and must not run inside this request.
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # 5. Record the Drive metadata (the V2 sync hook).
    if existing is not None:
        # A re-import: point at the new source row and bump the state.
        existing.source_id = source.id
        existing.file_name = file.file_name
        existing.modified_time = file.modified_time
        existing.size_bytes = size
        existing.sync_state = "re-ingested"
        db.commit()
    else:
        db.add(
            DriveImport(
                connection_id=connection.id,
                source_id=source.id,
                drive_file_id=drive_file_id,
                file_name=file.file_name,
                mime_type=file.mime_type,
                modified_time=file.modified_time,
                size_bytes=size,
                sync_state="imported",
            )
        )
        db.commit()

    log.info(
        "imported Drive file %s as source %s", drive_file_id, source.id
    )  # ids only, never tokens
    return ImportResult(
        source_id=source.id,
        drive_file_id=drive_file_id,
        file_name=file.file_name,
        size_bytes=size,
        already_imported=False,
    )


def sweep_stale_temp_files(max_age_seconds: float = 6 * 3600) -> int:
    """Delete `gdrive-*` temp files left behind by a crashed import.

    The import's `finally` removes its temp file on any handled path, but a
    SIGKILL or power cut strands one with a full customer PDF in it. Called at
    server startup. Age-gated so a second worker booting on the same host
    cannot delete a file another worker is mid-download on.
    """
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "gdrive-*.pdf")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            pass
    return removed
