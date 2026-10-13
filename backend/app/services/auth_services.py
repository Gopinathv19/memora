"""Console user accounts: lookup, creation, and provider linking.

Google sign-in is the only way into the console. An account is two rows:
`Users` is the person -- one per email address -- and `Authenticated_User` is
the Google identity that proves it. Accounts created earlier with a password
keep their tenants: the first Google sign-in with the same email links to them.

Nothing here imports FastAPI, so the same functions work from a worker, a CLI or
a test. Failures are raised as domain errors and translated to HTTP once, by the
handler in main.py.
"""

import re
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ValidationError
from app.db.models import Authenticated_User, Tenant, Users
from app.schemas.enums import ResourceStatus

PROVIDER_GOOGLE = "google"

GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class GoogleIdentity:
    provider_user_id: str
    email: str
    email_verified: bool
    name: str = ""
    avatar_url: str = ""


def normalize_email(email: str) -> str:
    """Lowercase and trim, so one person cannot own two accounts by casing."""
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise ValidationError("Enter a valid email address")
    return normalized


# --- Lookups --------------------------------------------------------------


def get_user(db: Session, user_id: uuid.UUID) -> Users | None:
    return db.get(Users, user_id)


def find_user_by_email(db: Session, email: str) -> Users | None:
    return db.execute(select(Users).where(Users.email == email)).scalar_one_or_none()


def find_auth_account(
    db: Session,
    *,
    provider: str,
    provider_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Authenticated_User | None:
    stmt = select(Authenticated_User).where(Authenticated_User.provider == provider)
    if provider_user_id is not None:
        stmt = stmt.where(Authenticated_User.provider_user_id == provider_user_id)
    if user_id is not None:
        stmt = stmt.where(Authenticated_User.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


# A console session resolves to (user exists, tenants owned) on every request,
# which is one database round trip before the route's own query even starts.
# The answer is remembered for a few seconds per user; creating a tenant drops
# it at once, so a new tenant is never invisible to the user who made it.
# Single-process cache: with several workers another worker's copy ages out
# within SCOPE_CACHE_TTL_SECONDS (set it to 0 to disable).
_scope_cache: dict[uuid.UUID, tuple[float, frozenset[uuid.UUID]]] = {}


def cached_tenant_ids(user_id: uuid.UUID) -> frozenset[uuid.UUID] | None:
    entry = _scope_cache.get(user_id)
    if entry is None or entry[0] < time.monotonic():
        _scope_cache.pop(user_id, None)
        return None
    return entry[1]


def remember_tenant_ids(user_id: uuid.UUID, tenant_ids: frozenset[uuid.UUID]) -> None:
    ttl = get_settings().scope_cache_ttl_seconds
    if ttl > 0:
        _scope_cache[user_id] = (time.monotonic() + ttl, tenant_ids)


def invalidate_scope_cache(user_id: uuid.UUID | None = None) -> None:
    if user_id is None:
        _scope_cache.clear()
    else:
        _scope_cache.pop(user_id, None)


def get_user_with_tenant_ids(
    db: Session, user_id: uuid.UUID
) -> tuple[Users | None, frozenset[uuid.UUID]]:
    """The user and every tenant they own, in one round trip.

    Same freshness as `get_user` + `owned_tenant_ids` (nothing is cached across
    requests); it only avoids paying network latency twice per request.
    """
    rows = db.execute(
        select(Users, Tenant.id)
        .outerjoin(Tenant, Tenant.user_owner_id == Users.id)
        .where(Users.id == user_id)
    ).all()
    if not rows:
        return None, frozenset()
    return rows[0][0], frozenset(t for _, t in rows if t is not None)


def owned_tenant_ids(db: Session, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Every tenant this user owns.

    Read fresh on each request rather than cached in the session token: a tenant
    created a moment ago has to be visible immediately, not after the next login.
    """
    rows = db.execute(
        select(Tenant.id).where(Tenant.user_owner_id == user_id)
    ).scalars().all()
    return frozenset(rows)


# --- Creation and linking -------------------------------------------------


def create_user(
    db: Session,
    *,
    email: str,
    email_verified: bool,
    name: str = "",
    avatar_url: str = "",
) -> Users:
    user = Users(
        email=email,
        email_verified=email_verified,
        name=name,
        avatar_url=avatar_url,
        status=ResourceStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    return user


def link_google_account(
    db: Session, *, user: Users, identity: GoogleIdentity
) -> Authenticated_User:
    existing = find_auth_account(
        db, provider=PROVIDER_GOOGLE, provider_user_id=identity.provider_user_id
    )
    if existing is not None:
        return existing

    account = Authenticated_User(
        user_id=user.id,
        provider=PROVIDER_GOOGLE,
        provider_user_id=identity.provider_user_id,
        password_hash=None,
        status=ResourceStatus.ACTIVE.value,
    )
    db.add(account)
    db.flush()
    return account


# --- Sign-up and sign-in --------------------------------------------------


def assert_user_active(user: Users) -> None:
    if user.status != ResourceStatus.ACTIVE.value:
        raise AuthenticationError("This account is not active")


def login_with_google(db: Session, *, id_token: str) -> Users:
    """Sign in with Google, creating or linking the account as needed."""
    identity = verify_google_id_token(id_token)

    account = find_auth_account(
        db, provider=PROVIDER_GOOGLE, provider_user_id=identity.provider_user_id
    )
    if account is not None:
        user = db.get(Users, account.user_id)
        if user is None:
            raise AuthenticationError("Linked Google account no longer exists")
        assert_user_active(user)
        return user

    # First Google sign-in. Attach to the account that already owns this email if
    # there is one (created before password login was removed), so that person
    # keeps their tenants instead of colliding with the unique Users.email.
    user = find_user_by_email(db, identity.email)
    if user is None:
        user = create_user(
            db,
            email=identity.email,
            email_verified=identity.email_verified,
            name=identity.name,
            avatar_url=identity.avatar_url,
        )
    else:
        user.email_verified = True
        if identity.name and not user.name:
            user.name = identity.name
        if identity.avatar_url and not user.avatar_url:
            user.avatar_url = identity.avatar_url

    link_google_account(db, user=user, identity=identity)
    db.commit()
    db.refresh(user)

    assert_user_active(user)
    return user


# --- Google ---------------------------------------------------------------


def verify_google_id_token(id_token: str) -> GoogleIdentity:
    """Ask Google whether this ID token is genuine, and who it is for.

    No client secret is involved: the token is already signed by Google, so this
    only verifies the signature and the audience. The audience check is what
    stops a token minted for some other Google app being replayed here, so it
    is mandatory: without a client id there is no way to sign in at all.
    """
    settings = get_settings()
    if not settings.google_client_id:
        raise AuthenticationError(
            "Google sign-in is not configured: set GOOGLE_CLIENT_ID in backend/.env"
        )

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
    except httpx.HTTPError as exc:
        raise AuthenticationError("Could not reach Google to verify sign-in") from exc

    if response.status_code != 200:
        raise AuthenticationError("Invalid Google identity token")

    data = response.json()

    audience = str(data.get("aud", ""))
    if audience != settings.google_client_id:
        raise AuthenticationError("Google token audience does not match this app")

    provider_user_id = str(data.get("sub", ""))
    email_verified = str(data.get("email_verified", "")).lower() == "true"
    if not provider_user_id or not email_verified:
        raise AuthenticationError("Google account email must be verified")

    return GoogleIdentity(
        provider_user_id=provider_user_id,
        email=normalize_email(str(data.get("email", ""))),
        email_verified=email_verified,
        name=str(data.get("name", "")),
        avatar_url=str(data.get("picture", "")),
    )
