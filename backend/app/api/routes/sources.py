import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentScope, DbSession, ExtractionAgentDep, Storage
from app.schemas.enums import ExtractionMode
from app.schemas.extraction import MAX_INSTRUCTIONS_CHARS, ExtractionRequest
from app.schemas.source import (
    SourceCreate,
    SourceDetail,
    SourceMove,
    SourceRead,
    SourceUpdate,
)
from app.services import extraction_service, source_service
from app.storage.local import MAX_UPLOAD_BYTES

subject_router = APIRouter(prefix="/subjects/{subject_id}/sources", tags=["sources"])
router = APIRouter(prefix="/sources", tags=["sources"])


@subject_router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(
    subject_id: uuid.UUID, payload: SourceCreate, db: DbSession, scope: CurrentScope
):
    """Register a source from metadata alone (a URL, a chat, external storage)."""
    return source_service.create_source(db, subject_id, payload, scope)


@subject_router.post(
    "/upload", response_model=SourceRead, status_code=status.HTTP_201_CREATED
)
def upload_source(
    subject_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    storage: Storage,
    agent: ExtractionAgentDep,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    created_by_actor_id: uuid.UUID | None = Form(default=None),
    extract: bool = Form(default=False),
    extract_mode: ExtractionMode = Form(default=ExtractionMode.STANDARD),
    extract_instructions: str | None = Form(
        default=None, max_length=MAX_INSTRUCTIONS_CHARS
    ),
):
    """Register a source by posting the file itself.

    This is the application-facing ingestion point: a consuming application
    sends its bearer token plus a file, and gets back a tracked source row
    already bound to the right tenant, application and subject.

    By default storing the bytes is all that happens and the row lands in
    `pending`. With `extract=true`, extraction version 1 starts in the
    background as soon as the row is committed; the response returns at once
    with the source in `processing`.
    """
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
        )
    source = source_service.create_source_from_upload(
        db,
        subject_id,
        fileobj=file.file,
        filename=file.filename,
        content_type=file.content_type,
        scope=scope,
        storage=storage,
        created_by_actor_id=created_by_actor_id,
    )
    if extract:
        extraction = extraction_service.start_extraction(
            db,
            source.id,
            ExtractionRequest(
                mode=extract_mode,
                instructions=extract_instructions,
                actor_id=created_by_actor_id,
            ),
            scope,
        )
        background.add_task(
            extraction_service.run_extraction, extraction.id, storage, agent
        )
        db.refresh(source)
    return source


@subject_router.get("", response_model=list[SourceDetail])
def list_subject_sources(
    subject_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    status_filter: str | None = None,
):
    return source_service.list_sources(
        db, scope, subject_id=subject_id, status=status_filter
    )


@subject_router.get("/{source_id}", response_model=SourceDetail)
def get_source_in_subject(
    subject_id: uuid.UUID, source_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    """Strict lookup: the subject and source ids must describe one chain."""
    source_service.get_source_in_subject(db, subject_id, source_id, scope)
    return source_service.get_source_detail(db, source_id, scope)


@router.get("", response_model=list[SourceDetail])
def list_sources(
    db: DbSession,
    scope: CurrentScope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    status_filter: str | None = None,
):
    return source_service.list_sources(
        db,
        scope,
        tenant_id=tenant_id,
        application_id=application_id,
        subject_id=subject_id,
        status=status_filter,
    )


@router.get("/{source_id}", response_model=SourceDetail)
def get_source(source_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return source_service.get_source_detail(db, source_id, scope)


@router.get("/{source_id}/content")
def download_source(
    source_id: uuid.UUID, db: DbSession, scope: CurrentScope, storage: Storage
):
    """Stream back the stored bytes, if Memora stored them itself."""
    source = source_service.get_source(db, source_id, scope)
    if not source.storage_uri:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This source has no stored content",
        )
    stream = storage.open(source.storage_uri)
    filename = source.filename or f"{source.id}"
    return StreamingResponse(
        stream,
        media_type=source.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: uuid.UUID, payload: SourceUpdate, db: DbSession, scope: CurrentScope
):
    return source_service.update_source(db, source_id, payload, scope)


@router.post("/{source_id}/move", response_model=SourceRead)
def move_source(
    source_id: uuid.UUID,
    payload: SourceMove,
    db: DbSession,
    scope: CurrentScope,
):
    """Move a file to another subject (folder) in the same application."""
    return source_service.move_source(
        db, source_id, payload.target_subject_id, scope
    )


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: uuid.UUID, db: DbSession, scope: CurrentScope, storage: Storage
):
    source_service.delete_source(db, source_id, scope, storage)
