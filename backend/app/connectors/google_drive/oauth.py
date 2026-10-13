"""Google Drive OAuth 2.0: authorization URL, state, token exchange, storage.

This is a *separate* OAuth client from the console's Google sign-in. Sign-in
verifies a Google-issued ID token (no secret); the Drive connector runs the
authorization-code flow: it needs GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI,
and asks for the read-only Drive scope.

Security rules enforced here:
* The `state` parameter is a signed, expiring, single-use value bound to the
  console user's session -- a forged or replayed state is rejected.
* Tokens are exchanged server-side; no token ever reaches the browser.
* Tokens at rest are Fernet-encrypted with a key derived from API_SECRET.
* Nothing here logs a token or a state value.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ValidationError

log = logging.getLogger(__name__)

# Least privilege: read Drive files, change nothing.
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# The Drive API's own "who am I" -- readable with the drive.readonly scope,
# unlike the userinfo endpoint (which needs openid/profile/email).
DRIVE_ABOUT_ENDPOINT = "https://www.googleapis.com/drive/v3/about"

STATE_TTL_SECONDS = 600  # 10 minutes to finish the consent dance
TOKEN_HTTP_TIMEOUT = 15.0


def drive_client_id() -> str:
    """The Drive connector's OAuth client id (falls back to the sign-in one)."""
    s = get_settings()
    return s.google_drive_client_id or s.google_client_id


def is_configured() -> bool:
    s = get_settings()
    return bool(
        drive_client_id() and s.google_client_secret and s.google_redirect_uri
    )


def require_configured() -> None:
    if not is_configured():
        raise ValidationError(
            "Google Drive connector is not configured: set GOOGLE_CLIENT_SECRET "
            "and GOOGLE_REDIRECT_URI in backend/.env"
        )


# --- State -----------------------------------------------------------------


def _state_secret() -> bytes:
    """The HMAC key for state values: API_SECRET, domain-separated."""
    return hashlib.sha256(
        b"memora-gdrive-state\x00" + get_settings().api_secret.encode()
    ).digest()


def mint_state(user_id: uuid.UUID) -> str:
    """A signed state: `payload.signature`, payload = user_id + nonce + expiry."""
    nonce = secrets.token_urlsafe(16)
    expires = int(time.time()) + STATE_TTL_SECONDS
    payload = f"{user_id}:{nonce}:{expires}"
    sig = hmac.new(_state_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_state(state: str, user_id: uuid.UUID) -> None:
    """Check the signature, the expiry and the binding to this user."""
    payload, _, sig = state.rpartition(".")
    expected = hmac.new(_state_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise AuthenticationError("Invalid OAuth state")
    uid, _, rest = payload.partition(":")
    nonce, _, expires = rest.partition(":")
    if uid != str(user_id):
        raise AuthenticationError("OAuth state does not belong to this session")
    try:
        if int(expires) < time.time():
            raise AuthenticationError("OAuth state has expired")
    except ValueError as exc:
        raise AuthenticationError("Invalid OAuth state") from exc
    if not nonce:
        raise AuthenticationError("Invalid OAuth state")


# --- Token encryption --------------------------------------------------------


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(
        hashlib.sha256(
            b"memora-gdrive-tokens\x00" + get_settings().api_secret.encode()
        ).digest()
    )
    return Fernet(key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise AuthenticationError("Stored Drive token could not be decrypted") from exc


# --- Flow ---------------------------------------------------------------------


def authorization_url(state: str) -> str:
    """The consent screen URL. `access_type=offline` gets a refresh token."""
    s = get_settings()
    params = {
        "client_id": drive_client_id(),
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",  # refresh token for V2 sync
        "include_granted_scopes": "true",
        "prompt": "consent",  # always return a refresh_token
        "state": state,
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return f"{AUTH_ENDPOINT}?{query}"


@dataclass(frozen=True)
class DriveTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    granted_scopes: str


@dataclass(frozen=True)
class DriveAccount:
    google_user_id: str
    email: str


def exchange_code(code: str) -> DriveTokens:
    """Swap the authorization code for tokens, server-side."""
    require_configured()
    s = get_settings()
    try:
        with httpx.Client(timeout=TOKEN_HTTP_TIMEOUT) as client:
            response = client.post(
                TOKEN_ENDPOINT,
                data={
                "client_id": drive_client_id(),
                "client_secret": s.google_client_secret,
                "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": s.google_redirect_uri,
                },
            )
    except httpx.HTTPError as exc:
        log.warning("Drive OAuth token exchange failed to reach Google")
        raise AuthenticationError("Could not reach Google to connect Drive") from exc
    if response.status_code != 200:
        # The body can echo parts of the code; never include it in the error.
        raise AuthenticationError("Google rejected the Drive authorization code")
    return _tokens_from_response(response.json())


def refresh_access_token(refresh_token: str) -> DriveTokens:
    """Get a fresh access token with the stored refresh token."""
    require_configured()
    s = get_settings()
    try:
        with httpx.Client(timeout=TOKEN_HTTP_TIMEOUT) as client:
            response = client.post(
                TOKEN_ENDPOINT,
                data={
                    "client_id": drive_client_id(),
                    "client_secret": s.google_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
    except httpx.HTTPError as exc:
        raise AuthenticationError("Could not reach Google to refresh Drive access") from exc
    if response.status_code != 200:
        raise AuthenticationError("Drive access could not be refreshed; reconnect Google Drive")
    data = response.json()
    expires_at = _expiry(data)
    return DriveTokens(
        access_token=str(data["access_token"]),
        # A refresh response does not repeat the refresh token.
        refresh_token=refresh_token,
        expires_at=expires_at,
        granted_scopes=str(data.get("scope", DRIVE_SCOPE)),
    )


def fetch_account(access_token: str) -> DriveAccount:
    """Who the granted Drive account belongs to, for the connection row.

    Read from the Drive API's /about endpoint rather than userinfo: the
    connector only holds the drive.readonly scope, which /about accepts and
    userinfo does not. The Drive `permissionId` is the stable account id.
    """
    try:
        with httpx.Client(timeout=TOKEN_HTTP_TIMEOUT) as client:
            response = client.get(
                DRIVE_ABOUT_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": "user(permissionId,displayName,emailAddress)"},
            )
    except httpx.HTTPError as exc:
        raise AuthenticationError("Could not read the Drive account") from exc
    if response.status_code != 200:
        raise AuthenticationError("Could not read the Drive account")
    user: dict[str, Any] = response.json().get("user", {})
    return DriveAccount(
        google_user_id=str(user.get("permissionId", "")),
        email=str(user.get("emailAddress", "unknown")),
    )


def _expiry(data: dict[str, Any]) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)))


def _tokens_from_response(data: dict[str, Any]) -> DriveTokens:
    if "access_token" not in data or "refresh_token" not in data:
        raise AuthenticationError("Google did not return a refresh token; retry the connection")
    return DriveTokens(
        access_token=str(data["access_token"]),
        refresh_token=str(data["refresh_token"]),
        expires_at=_expiry(data),
        granted_scopes=str(data.get("scope", DRIVE_SCOPE)),
    )