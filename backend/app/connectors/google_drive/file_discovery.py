"""File discovery: list the connected Drive account's PDFs.

Only `application/pdf` files are offered for import -- the connector's V1
contract. The filter is applied here, once, so the UI and the import route
can both rely on it.
"""

from app.connectors.google_drive import drive_client
from app.connectors.google_drive.drive_client import DriveFile, PDF_MIME

MAX_FILES = 500  # hard cap on one listing response


def list_pdfs(access_token: str, *, limit: int = 100) -> list[DriveFile]:
    """The account's PDFs, newest first, capped at `limit` (<= MAX_FILES)."""
    limit = max(1, min(limit, MAX_FILES))
    pdfs: list[DriveFile] = []
    page_token: str | None = None
    while len(pdfs) < limit:
        remaining = limit - len(pdfs)
        raw_files, page_token = drive_client.list_files(
            access_token,
            page_size=min(remaining * 2, 100),  # over-fetch: not every file is a PDF
            page_token=page_token,
        )
        for raw in raw_files:
            file = drive_client.to_drive_file(raw)
            if file.mime_type == PDF_MIME and file.downloadable:
                pdfs.append(file)
                if len(pdfs) >= limit:
                    return pdfs
        if not page_token:
            break
    return pdfs


def get_pdf(access_token: str, drive_file_id: str) -> DriveFile:
    """Metadata for one file, verified to be a downloadable PDF."""
    raw = drive_client.get_file(access_token, drive_file_id)
    file = drive_client.to_drive_file(raw)
    if file.mime_type != PDF_MIME:
        raise ValueError("Only PDF files can be imported from Google Drive")
    if not file.downloadable:
        raise ValueError("This Drive file cannot be downloaded")
    return file