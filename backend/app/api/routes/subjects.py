import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentScope, DbSession, Storage
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
    application_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    parent_subject_id: uuid.UUID | None = None,
    roots_only: bool = False,
):
    return subject_service.list_subjects(
        db,
        scope,
        application_id=application_id,
        parent_subject_id=parent_subject_id,
        roots_only=roots_only,
    )


@router.get("", response_model=list[SubjectWithCounts])
def list_subjects(
    db: DbSession,
    scope: CurrentScope,
    tenant_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    parent_subject_id: uuid.UUID | None = None,
    roots_only: bool = False,
):
    """List subjects, optionally narrowed to one folder's children.

    `parent_subject_id` lists a folder's children; `roots_only` lists only
    root workspaces; neither given, every subject in scope is returned.
    """
    return subject_service.list_subjects(
        db,
        scope,
        tenant_id=tenant_id,
        application_id=application_id,
        actor_id=actor_id,
        parent_subject_id=parent_subject_id,
        roots_only=roots_only,
    )


@router.get("/{subject_id}", response_model=SubjectWithCounts)
def get_subject(subject_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    return subject_service.get_subject_detail(db, subject_id, scope)


@router.patch("/{subject_id}", response_model=SubjectRead)
def update_subject(
    subject_id: uuid.UUID, payload: SubjectUpdate, db: DbSession, scope: CurrentScope
):
    return subject_service.update_subject(db, subject_id, payload, scope)


@router.delete("/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(
    subject_id: uuid.UUID,
    db: DbSession,
    scope: CurrentScope,
    storage: Storage,
):
    """Delete a subject, its whole folder subtree, and their stored bytes.

    Deliberately explicit: this is a large destructive operation, and the
    confirmation belongs in the caller (the console asks before sending it).
    """
    subject_service.delete_subject(db, subject_id, scope, storage)
