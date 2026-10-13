"""Pydantic contracts for the Google Drive connector."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DriveConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    drive_account_email: str
    granted_scopes: str
    status: str
    created_at: datetime


class DriveFileRead(BaseModel):
    drive_file_id: str
    file_name: str
    mime_type: str
    modified_time: str
    size_bytes: int | None


class DriveImportRequest(BaseModel):
    drive_file_id: str
    subject_id: uuid.UUID
    extract: bool = True
    build_graph: bool = False
    reimport: bool = False


class DriveImportResponse(BaseModel):
    source_id: uuid.UUID
    drive_file_id: str
    file_name: str
    size_bytes: int
    already_imported: bool


class DriveStatus(BaseModel):
    configured: bool
    connected: bool
    connection: DriveConnectionRead | None = None


# --- Private mode (zero-retention Q&A; connectors/google_drive/private_rag) ---


class PrivateQueryRequest(BaseModel):
    query: str
    drive_file_ids: list[str]


class PrivateFileReport(BaseModel):
    """What happened to one requested Drive file."""

    drive_file_id: str
    file_name: str
    status: str  # processed | skipped | failed
    detail: str | None = None
    pages: int = 0
    chunks: int = 0


class PrivateQueryEvidence(BaseModel):
    """One chunk the answer was grounded in, with its Drive provenance."""

    drive_file_id: str
    file_name: str
    content: str
    section_path: list[str] = []
    page_start: int | None = None
    page_end: int | None = None
    similarity: float = 0.0
    rerank_score: float | None = None


class PrivateQueryUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class PrivateQueryResponse(BaseModel):
    answer: str
    files: list[PrivateFileReport]
    evidence: list[PrivateQueryEvidence]
    usage: PrivateQueryUsage
