"""Shared request dependencies.

The important one is `get_scope`. Every route takes it, and no route performs
token validation itself -- that logic exists once, here.

Two kinds of caller reach the same endpoints:

* A **consuming application**, sending `Authorization: Bearer memora_...`. Its
  scope is derived entirely from the credential row, so it can only ever see its
  own tenant and application no matter what ids it puts in the URL.
* A **console user**, carrying the session cookie issued by the auth routes. Its
  scope covers every tenant that user owns, and everything underneath them.

The `memora_` prefix is what separates the two. A bearer value carrying it is
always a machine credential and is never tried as a session; anything else is
tried as a session and never as a credential. Without that split, a leaked API
token and a leaked session cookie would be interchangeable.

Deriving scope from the credential or the session rather than from request
parameters is what makes "never trust tenant/application/subject ids supplied by
the client" true by construction rather than by remembering to check.
"""

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.agents.extraction_agent import ExtractionAgent, get_extraction_agent
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.security import is_api_token, session_user_id
from app.db.database import get_db
from app.db.models.users import Users
from app.services.auth_services import get_user, owned_tenant_ids
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


def _session_token(request: Request, bearer: str | None) -> str | None:
    """The console session, from the cookie or a non-credential bearer value.

    The cookie is the normal path. The bearer fallback exists for clients that
    cannot hold cookies, such as a script or a mobile shell.
    """
    cookie = request.cookies.get(get_settings().auth_cookie_name)
    if cookie:
        return cookie
    if bearer is not None and not is_api_token(bearer):
        return bearer
    return None


def _user_from_session(db: Session, token: str) -> Users:
    user = get_user(db, session_user_id(token))
    if user is None:
        # Validly signed token, but the account behind it is gone.
        raise AuthenticationError("User no longer exists")
    return user


def get_current_user(
    db: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Users:
    """Require a logged-in console user. Backs `GET /api/v1/auth/me`."""
    token = _session_token(request, _extract_bearer(authorization))
    if token is None:
        raise AuthenticationError("Not authenticated")
    return _user_from_session(db, token)


def get_scope(
    db: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Scope:
    """Resolve the ownership scope this request may act within."""
    bearer = _extract_bearer(authorization)

    if bearer is not None and is_api_token(bearer):
        return authenticate_token(db, bearer).scope

    token = _session_token(request, bearer)
    if token is None:
        raise AuthenticationError("Not authenticated")

    user = _user_from_session(db, token)
    return Scope.for_users(user.id, tenants_ids=owned_tenant_ids(db, user.id))


def get_auth_context(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Require a real API credential and expose credential/application/tenant.

    Used by routes that are meaningless without a credential, such as
    `GET /api/v1/whoami`. A console session is rejected here on purpose: there is
    no credential behind it to report.
    """
    token = _extract_bearer(authorization)
    if token is None or not is_api_token(token):
        raise AuthenticationError(
            "An Authorization: Bearer <token> header is required"
        )
    return authenticate_token(db, token)


CurrentScope = Annotated[Scope, Depends(get_scope)]
CurrentUser = Annotated[Users, Depends(get_current_user)]
CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]
Storage = Annotated[StorageBackend, Depends(get_storage)]
ExtractionAgentDep = Annotated[ExtractionAgent, Depends(get_extraction_agent)]
