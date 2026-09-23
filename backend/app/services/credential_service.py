import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import AuthenticationError, NotFoundError
from app.core.security import generate_token, hash_token, token_preview
from app.db.models import ApiCredential, Application, Tenant
from app.schemas.credential import (
    CredentialCreate,
    CredentialCreated,
    CredentialRead,
    CredentialUpdate,
)
from app.schemas.enums import ResourceStatus
from app.services.application_service import get_application
from app.services.scope import Scope


@dataclass(frozen=True)
class AuthContext:
    """Everything a token resolves to. Attached to authenticated requests."""

    credential: ApiCredential
    application: Application
    tenant: Tenant

    @property
    def scope(self) -> Scope:
        return Scope.for_credentials(self.tenant.id, self.application.id)


def create_credential(
    db: Session, application_id: uuid.UUID, payload: CredentialCreate, scope: Scope
) -> CredentialCreated:
    """Issue a new credential and return the raw token exactly once.

    The raw token exists only in this function's locals and in the response.
    Nothing writes it to the database, and no other code path can reconstruct
    it, so this response is genuinely the user's only chance to copy it.
    """
    application = get_application(db, application_id, scope)

    raw_token = generate_token()
    credential = ApiCredential(
        application_id=application.id,
        name=payload.name,
        token_hash=hash_token(raw_token),
        token_preview=token_preview(raw_token),
        status=ResourceStatus.ACTIVE.value,
        expires_at=payload.expires_at,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    return CredentialCreated(
        **CredentialRead.model_validate(credential).model_dump(),
        token=raw_token,
    )


def get_credential(
    db: Session, credential_id: uuid.UUID, scope: Scope
) -> ApiCredential:
    credential = db.get(
        ApiCredential, credential_id, options=[joinedload(ApiCredential.application)]
    )
    if credential is None:
        raise NotFoundError("Credential not found")
    scope.assert_owns(
        "Credential",
        credential.application.tenant_id,
        credential.application_id,
    )
    return credential


def list_credentials(
    db: Session, scope: Scope, application_id: uuid.UUID | None = None
) -> list[ApiCredential]:
    if application_id is not None:
        get_application(db, application_id, scope)

    stmt = (
        select(ApiCredential)
        .join(Application, Application.id == ApiCredential.application_id)
        .where(
            scope.tenant_predicate(Application.tenant_id),
            scope.application_predicate(ApiCredential.application_id),
        )
        .order_by(ApiCredential.created_at.desc())
    )
    if application_id is not None:
        stmt = stmt.where(ApiCredential.application_id == application_id)
    return list(db.execute(stmt).scalars().all())


def update_credential(
    db: Session, credential_id: uuid.UUID, payload: CredentialUpdate, scope: Scope
) -> ApiCredential:
    credential = get_credential(db, credential_id, scope)
    if payload.name is not None:
        credential.name = payload.name
    if payload.status is not None:
        credential.status = payload.status.value
    if payload.expires_at is not None:
        credential.expires_at = payload.expires_at
    db.commit()
    db.refresh(credential)
    return credential


def revoke_credential(db: Session, credential_id: uuid.UUID, scope: Scope) -> None:
    """Delete a credential outright.

    A revoked-but-present row would keep a dead hash in the unique index and
    invite "is this still usable?" ambiguity in the console, so DELETE really
    deletes. Auditing who revoked what belongs in an audit log, not in this
    table.
    """
    credential = get_credential(db, credential_id, scope)
    db.delete(credential)
    db.commit()


def authenticate_token(db: Session, raw_token: str) -> AuthContext:
    """Resolve a raw bearer token to its credential, application and tenant.

    The lookup is by HMAC hash, so the raw token is never compared against
    anything stored. Every failure mode returns the same opaque message: a
    caller must not be able to distinguish "no such token" from "expired" or
    "suspended tenant", since that difference is useful only to an attacker
    probing for live tokens.
    """
    generic = "Invalid or expired API token"

    credential = db.execute(
        select(ApiCredential)
        .where(ApiCredential.token_hash == hash_token(raw_token))
        .options(
            joinedload(ApiCredential.application).joinedload(Application.tenant)
        )
    ).scalar_one_or_none()

    if credential is None:
        raise AuthenticationError(generic)
    if credential.status != ResourceStatus.ACTIVE.value:
        raise AuthenticationError(generic)
    if credential.expires_at is not None and credential.expires_at <= datetime.now(UTC):
        raise AuthenticationError(generic)

    application = credential.application
    if application is None or application.status != ResourceStatus.ACTIVE.value:
        raise AuthenticationError(generic)

    tenant = application.tenant
    if tenant is None or tenant.status != ResourceStatus.ACTIVE.value:
        raise AuthenticationError(generic)

    credential.last_used_at = datetime.now(UTC)
    db.commit()

    return AuthContext(credential=credential, application=application, tenant=tenant)
