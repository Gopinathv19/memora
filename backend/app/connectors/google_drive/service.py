"""Connection management: save, find, refresh and disconnect Drive connections.

A connection belongs to exactly one console user. Every read here filters on
`user_id`, so one user can never touch another's Drive account even inside
the same tenant.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.google_drive import oauth
from app.core.errors import NotFoundError
from app.db.models import DriveConnection


def get_connection(db: Session, connection_id: uuid.UUID, user_id: uuid.UUID) -> DriveConnection:
    connection = db.get(DriveConnection, connection_id)
    if connection is None or connection.user_id != user_id:
        # 404, not 403: never reveal that another user has a connection.
        raise NotFoundError("Drive connection not found")
    return connection


def find_active_connection(db: Session, user_id: uuid.UUID) -> DriveConnection | None:
    return (
        db.execute(
            select(DriveConnection).where(
                DriveConnection.user_id == user_id,
                DriveConnection.status == "active",
            )
        )
        .scalars()
        .first()
    )


def save_connection(
    db: Session,
    *,
    user_id: uuid.UUID,
    tokens: oauth.DriveTokens,
    account: oauth.DriveAccount,
) -> DriveConnection:
    """Create or replace the user's connection for this Google account."""
    existing = (
        db.execute(
            select(DriveConnection).where(
                DriveConnection.user_id == user_id,
                DriveConnection.google_user_id == account.google_user_id,
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        existing.encrypted_access_token = oauth.encrypt_token(tokens.access_token)
        existing.encrypted_refresh_token = oauth.encrypt_token(tokens.refresh_token)
        existing.access_token_expires_at = tokens.expires_at.isoformat()
        existing.granted_scopes = tokens.granted_scopes
        existing.status = "active"
        db.commit()
        db.refresh(existing)
        return existing

    connection = DriveConnection(
        user_id=user_id,
        google_user_id=account.google_user_id,
        drive_account_email=account.email,
        encrypted_access_token=oauth.encrypt_token(tokens.access_token),
        encrypted_refresh_token=oauth.encrypt_token(tokens.refresh_token),
        access_token_expires_at=tokens.expires_at.isoformat(),
        granted_scopes=tokens.granted_scopes,
        status="active",
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def current_access_token(db: Session, connection: DriveConnection) -> str:
    """A valid access token for the connection, refreshing it if expired.

    The refreshed token is persisted so the next request does not refresh
    again. Refresh tokens are long-lived; if the refresh itself fails the
    caller surfaces a "reconnect Drive" error.
    """
    expires_at = datetime.fromisoformat(connection.access_token_expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    # Refresh a minute early, to avoid a race with a slow request.
    if expires_at.timestamp() - 60 > datetime.now(UTC).timestamp():
        return oauth.decrypt_token(connection.encrypted_access_token)

    refresh_token = oauth.decrypt_token(connection.encrypted_refresh_token)
    tokens = oauth.refresh_access_token(refresh_token)
    connection.encrypted_access_token = oauth.encrypt_token(tokens.access_token)
    connection.access_token_expires_at = tokens.expires_at.isoformat()
    db.commit()
    return tokens.access_token


def disconnect(db: Session, connection_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Drop the connection row. The stored tokens die with it."""
    connection = get_connection(db, connection_id, user_id)
    db.delete(connection)
    db.commit()