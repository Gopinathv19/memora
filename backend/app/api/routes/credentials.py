import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentScope, DbSession
from app.schemas.credential import (
    CredentialCreate,
    CredentialCreated,
    CredentialRead,
    CredentialUpdate,
)
from app.services import credential_service

application_router = APIRouter(
    prefix="/applications/{application_id}/credentials", tags=["credentials"]
)
router = APIRouter(prefix="/credentials", tags=["credentials"])


@application_router.post(
    "", response_model=CredentialCreated, status_code=status.HTTP_201_CREATED
)
def create_credential(
    application_id: uuid.UUID,
    payload: CredentialCreate,
    db: DbSession,
    scope: CurrentScope,
):
    """Issue a credential. This is the only response that carries the raw token."""
    return credential_service.create_credential(db, application_id, payload, scope)


@application_router.get("", response_model=list[CredentialRead])
def list_application_credentials(
    application_id: uuid.UUID, db: DbSession, scope: CurrentScope
):
    return credential_service.list_credentials(db, scope, application_id=application_id)


@router.get("", response_model=list[CredentialRead])
def list_credentials(
    db: DbSession, scope: CurrentScope, application_id: uuid.UUID | None = None
):
    return credential_service.list_credentials(db, scope, application_id=application_id)


@router.patch("/{credential_id}", response_model=CredentialRead)
def update_credential(
    credential_id: uuid.UUID,
    payload: CredentialUpdate,
    db: DbSession,
    scope: CurrentScope,
):
    return credential_service.update_credential(db, credential_id, payload, scope)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_credential(credential_id: uuid.UUID, db: DbSession, scope: CurrentScope):
    credential_service.revoke_credential(db, credential_id, scope)
