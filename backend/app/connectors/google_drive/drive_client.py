"""A thin Google Drive API v3 client over httpx.

No Google client library: the connector needs exactly three calls (list,
get metadata, download), and keeping them explicit means the token handling
stays visible and testable. Every call takes the access token as an argument;
nothing caches tokens here.
"""

import logging
from dataclasses import dataclass
from typing import Any, BinaryIO

import httpx

from app.core.errors import AuthenticationError, NotFoundError, ValidationError

log = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/drive/v3/files"
HTTP_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0

PDF_MIME = "application/pdf"


class DriveAPIError(RuntimeError):
    """A Drive API call failed. Never carries the access token."""

    def __init__(self, what: str, status: int | None = None):
        super().__init__(what)
        self.what = what
        self.status = status


@dataclass(frozen=True)
class DriveFile:
    drive_file_id: str
    file_name: str
    mime_type: str
    modified_time: str  # RFC-3339
    size_bytes: int | None
    # True when the file can be downloaded with `alt=media` (not a Google
    # Docs shortcut, which would need an export call).
    downloadable: bool


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _check(response: httpx.Response, what: str) -> dict[str, Any]:
    if response.status_code in (401, 403):
        raise DriveAPIError("Drive refused the access token", response.status_code)
    if response.status_code == 404:
        raise DriveAPIError(f"{what} not found in Drive", 404)
    if response.status_code != 200:
        raise DriveAPIError(f"Drive call failed ({what})", response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise DriveAPIError(f"Drive returned an unreadable body ({what})") from exc


def list_files(
    access_token: str,
    *,
    page_size: int = 100,
    page_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the user's files. Returns (files, next_page_token)."""
    params: dict[str, Any] = {
        "pageSize": min(page_size, 1000),
        "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size, capabilities(canDownload))",
        "orderBy": "modifiedTime desc",
    }
    if page_token:
        params["pageToken"] = page_token
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.get(
                f"{DRIVE_API}/files",
                headers=_headers(access_token),
                params=params,
            )
    except httpx.HTTPError as exc:
        raise DriveAPIError("Could not reach Google Drive") from exc
    data = _check(response, "file list")
    return data.get("files", []), data.get("nextPageToken")


def get_file(access_token: str, drive_file_id: str) -> dict[str, Any]:
    """Metadata for one file, including its capabilities."""
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.get(
                f"{DRIVE_API}/files/{drive_file_id}",
                headers=_headers(access_token),
                params={
                    "fields": "id, name, mimeType, modifiedTime, size, capabilities(canDownload)",
                    "supportsAllDrives": "true",
                },
            )
    except httpx.HTTPError as exc:
        raise DriveAPIError("Could not reach Google Drive") from exc
    return _check(response, f"file {drive_file_id}")


def download_file(
    access_token: str, drive_file_id: str, dest: BinaryIO, *, max_bytes: int
) -> int:
    """Stream a file's bytes into `dest`, enforcing the size limit as it goes.

    The limit is enforced while streaming, not after: a file larger than
    max_bytes is cut off mid-stream and rejected, so a hostile or huge file
    never lands in full on local disk.
    """
    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT) as client:
            with client.stream(
                "GET",
                f"{DRIVE_API}/files/{drive_file_id}",
                headers=_headers(access_token),
                params={"alt": "media", "supportsAllDrives": "true"},
            ) as response:
                if response.status_code in (401, 403):
                    raise DriveAPIError(
                        "Drive refused the access token", response.status_code
                    )
                if response.status_code == 404:
                    raise DriveAPIError("File not found in Drive", 404)
                if response.status_code != 200:
                    raise DriveAPIError(
                        "Drive download failed", response.status_code
                    )
                written = 0
                for chunk in response.iter_bytes(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValidationError(
                            "Drive file exceeds the upload size limit"
                        )
                    dest.write(chunk)
                return written
    except httpx.HTTPError as exc:
        raise DriveAPIError("Could not reach Google Drive") from exc


# --- Domain helpers -----------------------------------------------------------


def to_drive_file(raw: dict[str, Any]) -> DriveFile:
    caps = raw.get("capabilities") or {}
    return DriveFile(
        drive_file_id=str(raw.get("id", "")),
        file_name=str(raw.get("name", "untitled")),
        mime_type=str(raw.get("mimeType", "")),
        modified_time=str(raw.get("modifiedTime", "")),
        size_bytes=int(raw["size"]) if raw.get("size") is not None else None,
        downloadable=bool(caps.get("canDownload", True)),
    )


def drive_error_to_http(exc: DriveAPIError):
    """Translate a Drive failure into the domain error the route should raise."""
    if exc.status == 404:
        return NotFoundError("File not found in Google Drive")
    if exc.status in (401, 403):
        return AuthenticationError("Google Drive rejected the connection; reconnect it")
    return ValidationError(exc.what)