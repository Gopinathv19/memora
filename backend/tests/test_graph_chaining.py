"""Upload -> extract -> build graph -> Graph RAG index, as one chain.

Real routes, services, PostgreSQL and FalkorDB; the model providers are faked by
the `fake_llm` and `graph` fixtures, and embeddings by conftest's autouse fake.
TestClient runs background tasks before it returns, so the whole chain has
finished when the upload request comes back.
"""

import pytest

from app.graphrag import autoindex, build
from app.graphrag.store import GraphRagStore
from app.services import graph_service, job_service
from tests.test_graph_api import _setup, _upload


@pytest.fixture
def indexed_calls(monkeypatch):
    """Record `index_subject_for_source` calls instead of running the index build."""
    calls: list = []
    monkeypatch.setattr(autoindex, "index_subject_for_source", lambda source_id: calls.append(str(source_id)))
    return calls


def test_flag_builds_the_graph_then_the_index(client, fake_llm, graph, indexed_calls):
    _, _, subject_id = _setup(client)
    response = _upload(client, subject_id, extract=True, index_graph_rag=True)
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    # the flag implies build_graph: the graph was built even though it was not asked for
    build_row = client.get(f"/api/v1/sources/{source_id}/graph/builds/latest").json()
    assert build_row["status"] == "completed"
    assert indexed_calls == [source_id]


def test_no_flag_means_no_index(client, fake_llm, graph, indexed_calls):
    _, _, subject_id = _setup(client)
    assert _upload(client, subject_id, extract=True, build_graph=True).status_code == 201
    assert indexed_calls == []


def test_a_failed_graph_build_skips_the_index(client, fake_llm, graph, indexed_calls, monkeypatch):
    from app.db.database import SessionLocal
    from app.db.models import SourceGraphBuild

    real = graph_service.run_build

    def failing(build_id, ingestion):
        real(build_id, ingestion)
        with SessionLocal() as db:
            db.get(SourceGraphBuild, build_id).status = "failed"
            db.commit()

    monkeypatch.setattr(graph_service, "run_build", failing)
    _, _, subject_id = _setup(client)
    assert _upload(client, subject_id, extract=True, index_graph_rag=True).status_code == 201
    assert indexed_calls == []


def test_the_flag_travels_in_the_durable_job_payload(monkeypatch):
    sent = []
    monkeypatch.setattr(job_service, "is_durable", lambda: True)
    monkeypatch.setattr(job_service, "enqueue", lambda db, kind, payload: sent.append(payload))
    job_service.dispatch_extraction(
        None, None, "00000000-0000-0000-0000-000000000001",
        storage=None, agent=None, graph=None, build_graph=False, index_graph_rag=True,
    )
    assert sent[0]["index_graph_rag"] is True and sent[0]["build_graph"] is True  # implied
    sent.clear()
    job_service.dispatch_extraction(
        None, None, "00000000-0000-0000-0000-000000000001",
        storage=None, agent=None, graph=None, build_graph=True,
    )
    assert sent[0] == {"extraction_id": "00000000-0000-0000-0000-000000000001",
                       "build_graph": True, "index_graph_rag": False}


def test_index_subject_for_source_builds_and_reports(client, fake_llm, graph, graph_store, monkeypatch):
    """The real chain end to end: the index really lands in FalkorDB and the status shows it."""
    monkeypatch.setattr(autoindex, "get_rag_store", lambda: GraphRagStore(graph_store))
    monkeypatch.setattr(build, "summarize_community", lambda members, facts: (None, None))
    tenant_id, _, subject_id = _setup(client)
    response = _upload(client, subject_id, extract=True, index_graph_rag=True)
    assert response.status_code == 201, response.text

    status = autoindex.BUILDS[subject_id]
    assert status["status"] == "done", status
    assert status["entities_embedded"] == 3 and status["facts_embedded"] == 2
    rag = GraphRagStore(graph_store)
    source_id = response.json()["id"]
    coverage = rag.stats(tenant_id, [source_id])
    assert coverage["entities"] == coverage["entities_embedded"] == 3 and coverage["facts"] == 2

    # a second run (e.g. a second upload to the same subject) only pays for what is new
    again = _upload(client, subject_id, "Dell provides servers to Microsoft Corporation.", extract=True, index_graph_rag=True)
    assert again.status_code == 201
    assert autoindex.BUILDS[subject_id]["status"] == "done"


def test_a_running_index_build_is_not_started_twice():
    assert autoindex.claim("subject-x") is True
    assert autoindex.claim("subject-x") is False
    autoindex.BUILDS.pop("subject-x")


def test_inspect_endpoint_shows_what_a_build_produced(client, fake_llm, graph):
    from tests.test_graph_api import _built

    _, _, subject_id = _setup(client)
    source_id = _built(client, subject_id)
    data = client.get(f"/api/v1/sources/{source_id}/graph/inspect").json()
    assert data["source_id"] == source_id and data["filename"] == "notes.txt"
    assert len(data["chunks"]) == 1 and "Dell provides servers" in data["chunks"][0]["text"]
    assert {e["name"] for e in data["entities"]} == {"Dell", "Microsoft Corporation", "Azure"}
    assert len(data["relationships"]) == 2
    assert set(data["chunks"][0]["entity_ids"]) == {e["id"] for e in data["entities"]}

    missing = client.get("/api/v1/sources/00000000-0000-0000-0000-000000000000/graph/inspect")
    assert missing.status_code == 404


def test_inspect_endpoint_is_503_without_a_graph(client, fake_llm):
    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id).json()["id"]
    assert client.get(f"/api/v1/sources/{source_id}/graph/inspect").status_code == 503


def test_status_lists_the_files_graph_rag_cannot_see(client, fake_llm, graph, graph_store, monkeypatch):
    from app.graphrag import build

    monkeypatch.setattr(autoindex, "get_rag_store", lambda: GraphRagStore(graph_store))
    _, _, subject_id = _setup(client)
    built = _upload(client, subject_id, extract=True, build_graph=True).json()["id"]
    not_built = _upload(client, subject_id, extract=True).json()["id"]  # extracted, no graph
    pending = _upload(client, subject_id).json()["id"]  # not even extracted

    status = client.get(f"/api/v1/subjects/{subject_id}/graph-rag/status").json()
    missing = {f["id"]: f for f in status["files_without_graph"]}
    assert built not in missing
    assert missing[not_built]["extracted"] is True and missing[pending]["extracted"] is False
