import time
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, event, exc
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every model and by Alembic's autogenerate."""


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    # Neon closes idle connections, so a connection that sat unused gets a
    # liveness check (below). `pool_pre_ping` would pay a network round trip
    # on *every* checkout instead, which is most of a request against a remote
    # database.
    pool_pre_ping=False,
    pool_recycle=1800,  # idle ones are pinged on checkout, so no need to churn them
    pool_size=10,
    max_overflow=10,
    pool_use_lifo=True,  # reuse the warmest connection; idle ones age out
    future=True,
)

# A connection used this recently is trusted without a ping.
_PING_AFTER_IDLE_SECONDS = 30.0


@event.listens_for(engine, "checkin")
def _stamp_last_used(dbapi_connection, connection_record) -> None:
    connection_record.info["last_used"] = time.monotonic()


@event.listens_for(engine, "checkout")
def _ping_if_idle(dbapi_connection, connection_record, connection_proxy) -> None:
    last_used = connection_record.info.get("last_used")
    if last_used is None or time.monotonic() - last_used < _PING_AFTER_IDLE_SECONDS:
        return
    try:
        dbapi_connection.execute("SELECT 1")
        dbapi_connection.rollback()  # leave no transaction open on the connection
    except Exception as err:
        # Makes the pool discard this connection and open a fresh one.
        raise exc.DisconnectionError() from err


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Read-only requests run without a transaction: no BEGIN before the first
# statement and no ROLLBACK when the connection is returned. Each of those is a
# network round trip, and against a remote database they cost more than the
# queries themselves. Writes keep the normal transactional engine.
_autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
ReadSessionLocal = sessionmaker(
    bind=_autocommit_engine, autoflush=False, expire_on_commit=False
)


# GET routes that write (the Drive OAuth callback saves tokens; the file list
# refreshes an expired one) keep a real transaction.
_TRANSACTIONAL_GET_PREFIXES = ("/api/v1/gdrive",)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    read_only = request.method in ("GET", "HEAD") and not request.url.path.startswith(
        _TRANSACTIONAL_GET_PREFIXES
    )
    db = (ReadSessionLocal if read_only else SessionLocal)()
    try:
        yield db
    finally:
        db.close()
