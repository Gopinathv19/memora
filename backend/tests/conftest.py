"""Test fixtures.

These tests run against a real PostgreSQL database, not SQLite: the schema uses
`gen_random_uuid()`, native UUID columns and ON DELETE behaviour that SQLite
cannot represent, so testing against it would verify something other than what
ships. Point TEST_DATABASE_URL at any throwaway PostgreSQL (a local container is
fine) and the suite creates the schema through Alembic.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

DEFAULT_TEST_DB = "postgresql+psycopg://memora:memora@localhost:55432/memora"


@pytest.fixture(scope="session", autouse=True)
def _environment():
    os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB))
    os.environ.setdefault("API_SECRET", "test-secret")
    os.environ.setdefault("STORAGE_DIR", "./var/test-storage")
    os.environ.pop("ADMIN_API_KEY", None)


@pytest.fixture(scope="session")
def client(_environment) -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_tables(_environment):
    """Truncate every table between tests so each one starts from empty."""
    from app.db.database import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE sources, subjects, actors, api_credentials, "
                "applications, tenants CASCADE"
            )
        )
    yield
