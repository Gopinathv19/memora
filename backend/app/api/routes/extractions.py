import uuid

from fastapi import APIRouter, BackgroundTasks, Body, status

from app.api.deps import CurrentScope, DbSession, ExtractionAgentDep, Storage
from app.schemas.extraction import ExtractionRead, ExtractionRequest, ExtractionSummary
from app.services import extraction_service

router = APIRouter(prefix="/sources/{source_id}/extractions", tags=["extractions"])


@router.post("", response_model=ExtractionRead, status_code=status.HTTP_202_ACCEPTED)
def start_extraction(
    source_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    storage: Storage,
    agent: ExtractionAgentDep,
    background: BackgroundTasks,
    payload: ExtractionRequest = Body(default_factory=ExtractionRequest),
):
    """Start the next extraction version: a first run, a retry, or a re-extract.

    Returns immediately with the new version in `processing`; poll
    `GET .../extractions/latest` until it is `completed`, `partial` or `failed`.
    409 if a run for this source is already processing.
    """
    extraction = extraction_service.start_extraction(db, source_id, payload, scope)
    background.add_task(extraction_service.run_extraction, extraction.id, storage, agent)
    return extraction


@router.get("", response_model=list[ExtractionSummary])
def list_extractions(source_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    """Every version of this source's extraction, newest first, without results."""
    return extraction_service.list_extractions(db, source_id, scope)


@router.get("/latest", response_model=ExtractionRead)
def get_latest_extraction(source_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return extraction_service.get_extraction(db, source_id, None, scope)


@router.get("/{version}", response_model=ExtractionRead)
def get_extraction_version(
    source_id: uuid.UUID, version: int, db: DbSession, scope: CurrentScope
):
    return extraction_service.get_extraction(db, source_id, version, scope)
