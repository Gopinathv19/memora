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


@pytest.fixture(scope="session", autouse=True)
def _console_session(client, _environment):
    """One console user, signed in once, for the whole session.

    Console authentication landed after this suite was written, and every
    management endpoint now requires a session. TestClient persists cookies,
    so a single signup (or login, on a database that already holds the user)
    authenticates every request the tests make without headers. The users
    tables are deliberately not truncated between tests, so the session
    survives; requests that carry a `memora_` bearer token still resolve as
    credentials, because the bearer wins over the cookie.
    """
    credentials = {"email": "console@example.com", "password": "test-password-123"}
    response = client.post(
        "/api/v1/auth/signup", json={**credentials, "name": "Console Test"}
    )
    if response.status_code == 409:  # A previous run already created the user.
        response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code in (200, 201), response.text


@pytest.fixture(autouse=True)
def clean_tables(_environment):
    """Truncate every table between tests so each one starts from empty."""
    from app.db.database import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE extraction_usage, source_extractions, sources, "
                "subjects, actors, api_credentials, applications, tenants CASCADE"
            )
        )
    yield


@pytest.fixture
def fake_llm(client):
    """Run the real Document Processor and Extraction Agent over a fake LLM.

    Everything from the upload to the stored result is the production path;
    only the provider call is replaced, so no test spends credits.
    """
    from app.agents.extraction_agent import NemotronExtractionAgent, get_extraction_agent
    from app.core.config import get_settings
    from app.main import app
    from tests.extraction_fakes import FakeLLMClient

    fake = FakeLLMClient()
    app.dependency_overrides[get_extraction_agent] = lambda: NemotronExtractionAgent(
        fake, get_settings()
    )
    yield fake
    app.dependency_overrides.pop(get_extraction_agent, None)
