from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every model and by Alembic's autogenerate."""


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,   # Neon closes idle connections; re-check before reuse.
    # pre_ping already catches a connection Neon dropped; recycling every
    # 280 s on top of that forced a fresh TLS + SCRAM handshake several times
    # an hour on a busy process. Half an hour keeps the pool warm.
    pool_recycle=1800,
    # Fail fast when the database is unreachable instead of hanging a request
    # for the OS default (minutes).
    connect_args={"connect_timeout": 10},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
