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
                "TRUNCATE extraction_usage, source_graph_builds, source_extractions, sources, "
                "subjects, actors, api_credentials, applications, tenants CASCADE"
            )
        )
    yield


@pytest.fixture(autouse=True)
def test_price_list(tmp_path, monkeypatch):
    """Tests price against their own list, not the operator's backend/pricing.json.

    The shipped file changes whenever the operator changes prices (it carries
    made-up build-nvidia rates during development); assertions about cost must
    not change with it. Here build.nvidia.com is free and Nebius is unpriced;
    a test that needs prices writes its own file.
    """
    import json

    from app.core import pricing
    from app.core.config import get_settings

    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"versions": [{
        "effective_from": "2026-01-01",
        "providers": {"build-nvidia": {"*": {"free": True}}},
    }]}))
    monkeypatch.setattr(get_settings(), "pricing_file", str(path))
    pricing.get_price_list.cache_clear()
    yield path
    pricing.get_price_list.cache_clear()


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


# --- knowledge graph ------------------------------------------------------------------
# Graph tests need a real FalkorDB, for the same reason the suite needs a real
# PostgreSQL: the Cypher is what ships. Set TEST_FALKORDB_URL (e.g. a FalkorDB
# Cloud instance: falkors://user:password@host:port). Without it the embedded
# `falkordblite` is used if installed; otherwise graph tests are skipped. Each
# test works under its own random graph prefix and drops its graphs after, so
# a shared instance is never polluted.


@pytest.fixture(autouse=True)
def _no_configured_graph(monkeypatch):
    """Never reach the FalkorDB in backend/.env (e.g. your Cloud instance).

    Without the `graph` fixture the graph is simply off: graph endpoints answer
    503 and delete/move hooks do nothing. `graph` re-points it at a test store.
    """
    from app.graph import store as store_module

    monkeypatch.setattr(store_module, "get_graph_store", lambda: None)


@pytest.fixture(scope="session")
def falkordb_connection(tmp_path_factory):
    url = os.environ.get("TEST_FALKORDB_URL")
    if url:
        from falkordb import FalkorDB

        db = FalkorDB.from_url(url)
        db.list_graphs()  # fail fast on bad credentials
        return db
    try:
        from redislite.falkordb_client import FalkorDB as EmbeddedFalkorDB
    except ImportError:
        return None
    return EmbeddedFalkorDB(str(tmp_path_factory.mktemp("falkordb") / "graph.db"))


@pytest.fixture
def graph_store(falkordb_connection):
    import uuid

    from app.graph.store import FalkorGraphStore

    if falkordb_connection is None:
        pytest.skip("no FalkorDB: set TEST_FALKORDB_URL (or pip install falkordblite)")
    store = FalkorGraphStore(falkordb_connection, prefix=f"memora_test_{uuid.uuid4().hex[:8]}")
    yield store
    for tenant_id in store.tenant_ids():
        store.drop_tenant_graph(tenant_id)


@pytest.fixture
def graph(client, graph_store, monkeypatch):
    """The real graph pipeline over a throwaway FalkorDB and a fake graph model.

    Wires the API (dependency overrides) and the post-commit sync hooks
    (`store.get_graph_store`) to the same store.
    """
    from types import SimpleNamespace

    from app.core.config import get_settings
    from app.graph import store as store_module
    from app.graph.extraction import GraphExtractor
    from app.graph.ingestion import GraphIngestionService, get_graph_ingestion
    from app.graph.ontology import get_ontology
    from app.graph.retrieval import GraphRetrievalService, get_graph_retrieval
    from app.main import app
    from tests.graph_fakes import FakeGraphLLM

    llm = FakeGraphLLM()
    ingestion = GraphIngestionService(
        graph_store, GraphExtractor(llm, get_settings(), get_ontology()), get_settings()
    )
    retrieval = GraphRetrievalService(graph_store)
    monkeypatch.setattr(store_module, "get_graph_store", lambda: graph_store)
    app.dependency_overrides[get_graph_ingestion] = lambda: ingestion
    app.dependency_overrides[get_graph_retrieval] = lambda: retrieval
    yield SimpleNamespace(store=graph_store, llm=llm, ingestion=ingestion, retrieval=retrieval)
    app.dependency_overrides.pop(get_graph_ingestion, None)
    app.dependency_overrides.pop(get_graph_retrieval, None)
