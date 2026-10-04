"""Console authentication endpoints. Google sign-in is the only way in.

The session token never appears in a response body. It goes out as an HttpOnly
cookie, so a cross-site scripting bug on the console cannot read it.
"""

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import create_session_token
from app.db.models import Users
from app.schemas.auth import AuthResponse, GoogleLoginRequest, UserRead
from app.services import auth_services

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, user: Users) -> None:
    settings = get_settings()

    response.set_cookie(
        key=settings.auth_cookie_name,
        value=create_session_token(user.id, user.email),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.auth_token_ttl_minutes * 60,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


def _authenticated(response: Response, user: Users) -> AuthResponse:
    _set_session_cookie(response=response, user=user)
    return AuthResponse(user=UserRead.model_validate(user))


@router.post("/google", response_model=AuthResponse)
def google_login(
    payload: GoogleLoginRequest, response: Response, db: DbSession
) -> AuthResponse:
    """Sign in with Google. The first sign-in creates the account."""
    user = auth_services.login_with_google(db, id_token=payload.id_token)
    return _authenticated(response, user)


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> Users:
    """Who the current session belongs to. The console's login check."""
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    """Drop the cookie.

    There is no server-side session to invalidate: a JWT is valid until it
    expires. Anyone holding a copy of the cookie keeps it working until then,
    which is the trade for not storing sessions.
    """
    _clear_session_cookie(response)
