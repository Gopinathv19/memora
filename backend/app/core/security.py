"""API-credential token , console passwords , session handling. """

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC , datetime , timedelta
from typing import Any

import bcrypt
import jwt


from app.core.config import get_settings
from app.core.errors import AuthenticationError



# Characters of the raw token kept in cleartext so the console can show the
# user *which* credential a row is without ever revealing the usable secret.
PREVIEW_LENGTH = 12
JWT_ALOGORITHM = "HS256"

# handling the api token logics

def generate_token() -> str:
    """Create a new raw API token. Shown to the user exactly once."""
    settings = get_settings()
    return f"{settings.token_prefix}{secrets.token_urlsafe(32)}"


def hash_token(raw_token: str) -> str:
    """Derive the stored, non-reversible representation of a raw token."""
    settings = get_settings()
    return hmac.new(
        settings.api_secret.encode("utf-8"),
        raw_token.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def token_preview(raw_token: str) -> str:
    """A safe, display-only fragment of the token, e.g. `memora_a1b2c3...`."""
    return f"{raw_token[:PREVIEW_LENGTH]}..."


def is_api_token(value:str)->bool:
    return value.startswith(get_settings().token_prefix)


# handling the console

def hash_password(password:str)-> str:
    psw=bcrypt.hashpw(password.encode("utf-8"),bcrypt.gensalt()).decode("utf-8")
    return psw


def verify_password(password:str,hashed_password:str)->bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"),hashed_password.encode("utf-8"))

    except:
        return False


def create_session_token(user_id:uuid.UUID,email:str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)

    payload = {
        "sub": str(user_id),
        "email":email,
        "iss": settings.auth_jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.auth_token_ttl_minutes))
        .timestamp()),
    }

    return jwt.encode(payload,settings.auth_jwt_secret,algorithm=JWT_ALOGORITHM)


def decode_session_token(token:str) -> dict [str,Any]:
    settings = get_settings()

    try:
        return jwt.decode(
            token,
            settings.auth_jwt_secret,
            algorithms=[JWT_ALOGORITHM],
            issuer=settings.auth_jwt_issuer,
            options={"require":["exp","iat","sub","iss"]},
        )

    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid Token") from exc

def session_user_id(token:str) -> uuid.UUID:
    payload = decode_session_token(token=token)

    try:
        user_id = uuid.UUID(payload["sub"])
        return user_id
    except (KeyError,ValueError) as exc:
        raise AuthenticationError("Invalid Expression") from exc


    