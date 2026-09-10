"""Shared request dependencies.

The important one is `get_scope`. Every route takes it, and no route performs
token validation itself -- that logic exists once, here.

Two kinds of caller reach the same endpoints:

* A **consuming application**, sending `Authorization: Bearer memora_...`. Its
  scope is derived entirely from the credential row, so it can only ever see
  its own tenant and application no matter what ids it puts in the URL.
* The **console**, sending no bearer token. It gets an admin scope, optionally
  gated by `X-Admin-Key` when ADMIN_API_KEY is configured.

Deriving scope from the credential rather than from request parameters is what
makes "never trust tenant/application/subject ids supplied by the client" true
by construction rather than by remembering to check.
"""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError
from app.core.security import verify_admin_key
from app.db.database import get_db
from app.services.credential_service import AuthContext, authenticate_token
from app.services.scope import Scope
from app.storage import StorageBackend, get_storage

DbSession = Annotated[Session, Depends(get_db)]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def get_scope(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> Scope:
    """Resolve the ownership scope this request may act within."""
    token = _extract_bearer(authorization)
    if token is not None:
        return authenticate_token(db, token).scope

    if not verify_admin_key(x_admin_key):
        raise AuthenticationError("A valid X-Admin-Key header is required")
    return Scope.admin()


def get_auth_context(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Require a real API credential and expose credential/application/tenant.

    Used by routes that are meaningless without a credential, such as
    `GET /api/v1/whoami`.
    """
    token = _extract_bearer(authorization)
    if token is None:
        raise AuthenticationError(
            "An Authorization: Bearer <token> header is required"
        )
    return authenticate_token(db, token)


CurrentScope = Annotated[Scope, Depends(get_scope)]
CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]
Storage = Annotated[StorageBackend, Depends(get_storage)]
