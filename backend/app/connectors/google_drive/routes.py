"""Google Drive connector routes.

All under /api/v1/gdrive, all console-user-only: the connector is a console
feature, not an application-credential one. No route validates a token
itself; the session resolves the user, and the import route additionally
takes the standard `CurrentScope` so the existing ingestion function can
scope-check the target subject exactly like the upload route does.

The OAuth callback is the one route Google redirects to. The `state` check
binds the callback to the session that started the flow, so a stolen
authorization code cannot be replayed by anyone else.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import (
    CurrentScope,
    CurrentUser,
    DbSession,
    ExtractionAgentDep,
    GraphIngestionDep,
    Storage,
)
from app.connectors.google_drive import (
    file_discovery,
    oauth,
    private_rag,
    rag_adapter,
    service,
)
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.schemas.enums import ExtractionMode
from app.schemas.extraction import ExtractionRequest
from app.services import extraction_service, graph_service
from app.schemas.gdrive import (
    DriveConnectionRead,
    DriveFileRead,
    DriveImportRequest,
    DriveImportResponse,
    DriveStatus,
    PrivateFileReport,
    PrivateQueryEvidence,
    PrivateQueryRequest,
    PrivateQueryResponse,
    PrivateQueryUsage,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gdrive", tags=["google-drive"])


def _console_gdrive_url() -> str:
    """The console page for the connector, as an absolute URL.

    The OAuth callback runs on the backend's origin, so a relative redirect
    would resolve against it and 404. The console is a different origin.
    """
    return f"{get_settings().console_base_url.rstrip('/')}/gdrive"


@router.get("/status", response_model=DriveStatus)
def drive_status(db: DbSession, user: CurrentUser):
    """Is the connector configured, and is this user connected?"""
    connection = service.find_active_connection(db, user.id)
    return DriveStatus(
        configured=oauth.is_configured(),
        connected=connection is not None,
        connection=(
            DriveConnectionRead.model_validate(connection) if connection else None
        ),
    )


@router.get("/connect")
def connect(user: CurrentUser):
    """Start the OAuth flow: redirect to Google's consent screen.

    The state is signed, expiring and bound to this user's session; the
    callback verifies it before touching the authorization code.
    """
    oauth.require_configured()
    state = oauth.mint_state(user.id)
    return RedirectResponse(oauth.authorization_url(state))


@router.get("/callback")
def callback(
    db: DbSession,
    user: CurrentUser,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Finish the OAuth flow: verify state, exchange the code, save tokens.

    Tokens are exchanged and stored server-side; nothing token-shaped ever
    reaches the browser. Then back to the console's Drive page.
    """
    if error is not None:
        log.warning("Drive OAuth consent was declined")
        return RedirectResponse(f"{_console_gdrive_url()}?error=consent")
    if not code or not state:
        raise ValidationError("Missing authorization code or state")
    oauth.verify_state(state, user.id)

    tokens = oauth.exchange_code(code)
    account = oauth.fetch_account(tokens.access_token)
    service.save_connection(db, user_id=user.id, tokens=tokens, account=account)
    log.info(
        "user %s connected Google Drive account %s",
        user.id,
        account.google_user_id,
    )
    return RedirectResponse(f"{_console_gdrive_url()}?connected=1")


@router.get("/files", response_model=list[DriveFileRead])
def list_drive_files(db: DbSession, user: CurrentUser, limit: int = 100):
    """The connected account's PDFs, newest first."""
    connection = service.find_active_connection(db, user.id)
    if connection is None:
        raise NotFoundError("Google Drive is not connected")
    access_token = service.current_access_token(db, connection)
    pdfs = file_discovery.list_pdfs(access_token, limit=limit)
    return [
        DriveFileRead(
            drive_file_id=f.drive_file_id,
            file_name=f.file_name,
            mime_type=f.mime_type,
            modified_time=f.modified_time,
            size_bytes=f.size_bytes,
        )
        for f in pdfs
    ]


@router.post("/import", response_model=DriveImportResponse)
def import_drive_file(
    payload: DriveImportRequest,
    db: DbSession,
    user: CurrentUser,
    scope: CurrentScope,
    storage: Storage,
    agent: ExtractionAgentDep,
    graph: GraphIngestionDep,
    background: BackgroundTasks,
):
    """Download a Drive PDF and feed it into the *existing* RAG pipeline.

    The subject is scope-checked inside the existing ingestion function, so
    a user can only import into a subject their own tenants own -- the same
    rule as a direct upload. Extraction (and the graph) run as background
    tasks, exactly the way the upload route triggers them.
    """
    connection = service.find_active_connection(db, user.id)
    if connection is None:
        raise NotFoundError("Google Drive is not connected")

    result = rag_adapter.import_pdf(
        db,
        connection,
        payload.drive_file_id,
        payload.subject_id,
        scope,
        storage,
        extract=payload.extract,
        build_graph=payload.build_graph,
        reimport=payload.reimport,
    )

    # The same background triggers the upload route uses. run_extraction
    # opens its own DB session, so it must run after this request commits.
    if payload.extract and not result.already_imported:
        extraction = extraction_service.start_extraction(
            db,
            result.source_id,
            ExtractionRequest(mode=ExtractionMode.STANDARD),
            scope,
        )
        background.add_task(
            extraction_service.run_extraction, extraction.id, storage, agent
        )
        if payload.build_graph:
            background.add_task(
                graph_service.build_after_extraction, extraction.id, graph
            )

    return DriveImportResponse(
        source_id=result.source_id,
        drive_file_id=result.drive_file_id,
        file_name=result.file_name,
        size_bytes=result.size_bytes,
        already_imported=result.already_imported,
    )


@router.post("/query", response_model=PrivateQueryResponse)
def private_drive_query(
    payload: PrivateQueryRequest,
    db: DbSession,
    user: CurrentUser,
    agent: ExtractionAgentDep,
):
    """Zero-retention Q&A over Drive PDFs ("private mode").

    The files never leave the customer's Drive: they are downloaded into
    memory, read, chunked, embedded and answered from, and every byte is
    discarded when the request ends. Nothing is written to any content table,
    vector index, object store or temp file -- the only rows this route ever
    touches are the user's own Drive connection (read, plus the connector's
    usual token refresh). Use `/import` instead when the document should
    become part of the persistent knowledge base.
    """
    connection = service.find_active_connection(db, user.id)
    if connection is None:
        raise NotFoundError("Google Drive is not connected")

    settings = get_settings()
    query = payload.query.strip()
    if not query:
        raise ValidationError("Query must not be empty")
    if len(query) > 4000:
        raise ValidationError("Query is too long (max 4000 characters)")
    if not payload.drive_file_ids:
        raise ValidationError("Select at least one Drive file")
    if len(payload.drive_file_ids) > settings.private_rag_max_files:
        raise ValidationError(
            f"Private queries are limited to {settings.private_rag_max_files} files"
        )

    access_token = service.current_access_token(db, connection)
    built, result = private_rag.run_private_query(
        access_token, query, payload.drive_file_ids, agent, settings
    )
    log.info(
        "private Drive query: user=%s files=%d pages=%d chunks=%d",
        user.id,
        len(built.files),
        built.pages_processed,
        len(built.chunks),
    )

    return PrivateQueryResponse(
        answer=result["answer"],
        files=[PrivateFileReport(**vars(f)) for f in built.files],
        evidence=[
            PrivateQueryEvidence(
                drive_file_id=c.drive_file_id,
                file_name=c.file_name,
                content=c.content,
                section_path=c.section_path,
                page_start=c.page_start,
                page_end=c.page_end,
                similarity=round(c.similarity, 4),
                rerank_score=(
                    round(c.rerank_score, 4) if c.rerank_score is not None else None
                ),
            )
            for c in result["evidence"]
        ],
        usage=PrivateQueryUsage(**result["usage"]),
    )


@router.get("/imports", response_model=list[DriveImportResponse])
def list_imports(db: DbSession, user: CurrentUser):
    """What this user has imported from Drive so far (the V2 sync baseline)."""
    from sqlalchemy import select

    from app.db.models import DriveImport

    connection = service.find_active_connection(db, user.id)
    if connection is None:
        raise NotFoundError("Google Drive is not connected")
    rows = (
        db.execute(
            select(DriveImport).where(DriveImport.connection_id == connection.id)
        )
        .scalars()
        .all()
    )
    return [
        DriveImportResponse(
            source_id=row.source_id,
            drive_file_id=row.drive_file_id,
            file_name=row.file_name,
            size_bytes=row.size_bytes or 0,
            already_imported=True,
        )
        for row in rows
    ]


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_drive(db: DbSession, user: CurrentUser):
    """Disconnect Drive: the connection row and its stored tokens are deleted."""
    connection = service.find_active_connection(db, user.id)
    if connection is None:
        raise NotFoundError("Google Drive is not connected")
    service.disconnect(db, connection.id, user.id)