import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentScope, DbSession
from app.schemas.subject import (
    SubjectCreate,
    SubjectRead,
    SubjectUpdate,
    SubjectWithCounts,
)
from app.services import subject_service

application_router = APIRouter(
    prefix="/applications/{application_id}/subjects", tags=["subjects"]
)
router = APIRouter(prefix="/subjects", tags=["subjects"])


@application_router.post(
    "", response_model=SubjectRead, status_code=status.HTTP_201_CREATED
)
def create_subject(
    application_id: uuid.UUID,
    payload: SubjectCreate,
    db: DbSession,
    scope: CurrentScope,
):
    return subject_service.create_subject(db, application_id, payload, scope)


@application_router.get("", response_model=list[SubjectWithCounts])
def list_application_subjects(
    application_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    return subject_service.list_subjects(db, scope, application_id=application_id)


@router.get("", response_model=list[SubjectWithCounts])
def list_subjects(
    db: DbSession,
    scope: CurrentScope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
):
    return subject_service.list_subjects(
        db,
        scope,
        tenant_id=tenant_id,
        application_id=application_id,
        actor_id=actor_id,
    )


@router.get("/{subject_id}", response_model=SubjectWithCounts)
def get_subject(subject_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return subject_service.get_subject_detail(db, subject_id, scope)


@router.patch("/{subject_id}", response_model=SubjectRead)
def update_subject(
    subject_id: uuid.UUID, payload: SubjectUpdate, db: DbSession, scope: CurrentScope
):
    return subject_service.update_subject(db, subject_id, payload, scope)
