"""The knowledge graph through the API: build, status, cost, view, query,
scope, and keeping the graph in step with deletes and moves.

Real route -> service -> extraction pipeline -> graph pipeline -> FalkorDB and
PostgreSQL; only the two model providers are faked (`fake_llm`, `graph`).
TestClient runs background tasks before returning, so a build has finished by
the time the triggering request comes back.
"""

import io

import pytest

from tests.graph_fakes import padded

TEXT = "Dell provides servers to Microsoft Corporation.\n\nMicrosoft Corp. uses Azure."


def _setup(client, name="Acme"):
    tenant_id = client.post("/api/v1/tenants", json={"name": name}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "App", "slug": "app"}
    ).json()["id"]
    subject_id = client.post(
        f"/api/v1/applications/{application_id}/subjects", json={"external_id": "case-1"}
    ).json()["id"]
    return tenant_id, application_id, subject_id


def _upload(client, subject_id, text=TEXT, headers=None, **form):
    return client.post(
        f"/api/v1/subjects/{subject_id}/sources/upload",
        files={"file": ("notes.txt", io.BytesIO(text.encode()), "text/plain")},
        data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()},
        headers=headers or {},
    )


def _built(client, subject_id, text=TEXT, **headers):
    response = _upload(client, subject_id, text, extract=True, build_graph=True, **headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _facts(result):
    return sorted((r["source_name"], r["relation"], r["target_name"]) for r in result["relationships"])


def test_upload_with_build_graph_extracts_then_builds(client, fake_llm, graph):
    tenant_id, _, subject_id = _setup(client)
    source_id = _built(client, subject_id)

    extraction = client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()
    assert extraction["status"] == "completed"
    # The extraction's own usage lists only its own calls, not the graph's.
    assert [u["role"] for u in extraction["usage"]] == ["extract"]

    build = client.get(f"/api/v1/sources/{source_id}/graph/builds/latest").json()
    assert build["status"] == "completed", build
    assert build["extraction_id"] == extraction["id"] and build["extraction_version"] == 1
    assert (build["chunk_count"], build["entity_count"], build["relationship_count"]) == (1, 3, 2)
    assert build["triggered_by_kind"] == "user"
    assert [u["role"] for u in build["usage"]] == ["graph"]
    assert build["prompt_tokens"] == 500 and build["cost_usd"] == 0.0
    assert build["stats"]["entities_created"] == 3

    from app.db.database import SessionLocal
    from app.db.models import SourceExtraction

    with SessionLocal() as db:
        stored = db.get(SourceExtraction, extraction["id"]).content
    assert stored.startswith("<!-- document 1") and "Dell provides servers" in stored

    report = client.get("/api/v1/usage/extractions").json()
    assert {g["label"] for g in report["by_model"]} == {"extract", "graph"}


def test_building_later_and_the_rules_around_it(client, fake_llm, graph):
    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id).json()["id"]

    no_extraction = client.post(f"/api/v1/sources/{source_id}/graph", json={})
    assert no_extraction.status_code == 422 and "extract it first" in no_extraction.json()["detail"]

    client.post(f"/api/v1/sources/{source_id}/extractions", json={})
    started = client.post(f"/api/v1/sources/{source_id}/graph")
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "processing"  # as returned, before the task ran
    builds = client.get(f"/api/v1/sources/{source_id}/graph/builds").json()
    assert [b["status"] for b in builds] == ["completed"]

    retry = client.post(f"/api/v1/sources/{source_id}/graph", json={"retry_failed": True})
    assert retry.status_code == 422  # the latest build is not partial


def test_a_second_build_while_one_runs_is_rejected(client, fake_llm, graph):
    from app.db.database import SessionLocal
    from app.db.models import SourceGraphBuild

    _, _, subject_id = _setup(client)
    source_id = _built(client, subject_id)
    with SessionLocal() as db:
        db.query(SourceGraphBuild).filter_by(source_id=source_id).one().status = "processing"
        db.commit()
    response = client.post(f"/api/v1/sources/{source_id}/graph")
    assert response.status_code == 409


def test_extractions_from_before_the_graph_need_a_re_extract(client, fake_llm, graph):
    from app.db.database import SessionLocal
    from app.db.models import SourceExtraction

    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id, extract=True).json()["id"]
    with SessionLocal() as db:
        db.query(SourceExtraction).filter_by(source_id=source_id).one().content = None
        db.commit()
    response = client.post(f"/api/v1/sources/{source_id}/graph")
    assert response.status_code == 422 and "re-extract" in response.json()["detail"]


def test_without_falkordb_graph_endpoints_answer_503(client, fake_llm):
    from app.graph.ingestion import get_graph_ingestion
    from app.graph.retrieval import get_graph_retrieval
    from app.main import app

    app.dependency_overrides[get_graph_ingestion] = lambda: None
    app.dependency_overrides[get_graph_retrieval] = lambda: None
    try:
        _, _, subject_id = _setup(client)
        # Asking for a graph at upload must not break the upload or extraction.
        source_id = _upload(client, subject_id, extract=True, build_graph=True).json()["id"]
        assert client.get(f"/api/v1/sources/{source_id}").json()["status"] == "completed"
        assert client.post(f"/api/v1/sources/{source_id}/graph").status_code == 503
        assert client.get(f"/api/v1/subjects/{subject_id}/graph").status_code == 503
        # Deleting still works; there is simply no graph to clean.
        assert client.delete(f"/api/v1/sources/{source_id}").status_code == 204
    finally:
        app.dependency_overrides.pop(get_graph_ingestion, None)
        app.dependency_overrides.pop(get_graph_retrieval, None)


def test_subject_view_and_query_cover_subfolders(client, fake_llm, graph):
    _, application_id, root_id = _setup(client)
    folder_id = client.post(
        f"/api/v1/applications/{application_id}/subjects",
        json={"external_id": "invoices", "parent_subject_id": root_id},
    ).json()["id"]
    _built(client, folder_id)
    _built(client, root_id, "Bob lives in Chennai.")

    view = client.get(f"/api/v1/subjects/{root_id}/graph").json()
    assert sorted(e["name"] for e in view["entities"]) == [
        "Azure", "Bob", "Chennai", "Dell", "Microsoft Corporation",
    ]
    only_root = client.get(f"/api/v1/subjects/{root_id}/graph?include_subfolders=false").json()
    assert sorted(e["name"] for e in only_root["entities"]) == ["Bob", "Chennai"]
    assert _facts(client.get(f"/api/v1/subjects/{folder_id}/graph").json()) == [
        ("Dell", "PROVIDES_TO", "Microsoft Corporation"),
        ("Microsoft Corporation", "USES", "Azure"),
    ]

    answer = client.post(
        f"/api/v1/subjects/{root_id}/graph/query",
        json={"query": "What cloud does Dell's customer use?", "max_hops": 2},
    )
    assert answer.status_code == 200, answer.text
    result = answer.json()
    assert _facts(result) == [
        ("Dell", "PROVIDES_TO", "Microsoft Corporation"),
        ("Microsoft Corporation", "USES", "Azure"),
    ]
    assert len(result["source_chunk_ids"]) == 1
    assert "Dell provides servers" in result["chunks"][0]["text"]

    bad = client.post(f"/api/v1/subjects/{root_id}/graph/query", json={"query": "  ", "max_hops": 9})
    assert bad.status_code == 422


def test_credentials_only_reach_their_own_application(client, fake_llm, graph):
    _, app_a, subject_a = _setup(client, "Tenant A")
    _, app_b, subject_b = _setup(client, "Tenant B")
    token_a = client.post(f"/api/v1/applications/{app_a}/credentials", json={"name": "a"}).json()["token"]
    token_b = client.post(f"/api/v1/applications/{app_b}/credentials", json={"name": "b"}).json()["token"]
    as_a = {"Authorization": f"Bearer {token_a}"}
    as_b = {"Authorization": f"Bearer {token_b}"}

    source_a = _built(client, subject_a, headers=as_a)
    build = client.get(f"/api/v1/sources/{source_a}/graph/builds/latest", headers=as_a).json()
    assert build["triggered_by_kind"] == "credential"

    assert client.get(f"/api/v1/subjects/{subject_a}/graph", headers=as_a).status_code == 200
    for method, path in [
        ("get", f"/api/v1/subjects/{subject_a}/graph"),
        ("post", f"/api/v1/subjects/{subject_a}/graph/query"),
        ("post", f"/api/v1/sources/{source_a}/graph"),
        ("get", f"/api/v1/sources/{source_a}/graph/builds"),
    ]:
        kwargs = {"json": {"query": "Dell"}} if path.endswith("query") else {}
        response = getattr(client, method)(path, headers=as_b, **kwargs)
        assert response.status_code == 404, (path, response.text)
    # Tenant B's own subject has an empty graph, never tenant A's facts.
    assert client.get(f"/api/v1/subjects/{subject_b}/graph", headers=as_b).json()["entities"] == []


def test_deleting_sources_and_subjects_clears_their_graph(client, fake_llm, graph):
    tenant_id, application_id, subject_id = _setup(client)
    kept = _built(client, subject_id, "Microsoft Corp. uses Azure.")
    doomed = _built(client, subject_id, "Azure is owned by Microsoft.\n\nBob lives in Chennai.")
    assert sorted(graph.store.document_ids(tenant_id)) == sorted([kept, doomed])

    assert client.delete(f"/api/v1/sources/{doomed}").status_code == 204
    assert graph.store.document_ids(tenant_id) == [kept]
    view = client.get(f"/api/v1/subjects/{subject_id}/graph").json()
    assert sorted(e["name"] for e in view["entities"]) == ["Azure", "Microsoft Corp."]

    assert client.delete(f"/api/v1/subjects/{subject_id}").status_code == 204
    assert graph.store.document_ids(tenant_id) == []
    assert graph.store._read(tenant_id, "MATCH (n) RETURN count(n) AS n")[0]["n"] == 0


def test_moving_a_file_moves_its_graph_view(client, fake_llm, graph):
    tenant_id, application_id, subject_id = _setup(client)
    other_id = client.post(
        f"/api/v1/applications/{application_id}/subjects", json={"external_id": "case-2"}
    ).json()["id"]
    source_id = _built(client, subject_id)
    moved = client.post(f"/api/v1/sources/{source_id}/move", json={"target_subject_id": other_id})
    assert moved.status_code == 200
    assert client.get(f"/api/v1/subjects/{subject_id}/graph").json()["entities"] == []
    assert len(client.get(f"/api/v1/subjects/{other_id}/graph").json()["entities"]) == 3
    subject = graph.store._read(tenant_id, "MATCH (d:Document) RETURN d.subject_id AS s")
    assert subject == [{"s": other_id}]


def test_partial_builds_retry_only_the_failed_chunks(client, fake_llm, graph, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "graph_chunk_chars", 200)
    monkeypatch.setattr(get_settings(), "graph_chunk_overlap", 0)
    _, _, subject_id = _setup(client)
    text = "\n\n".join([
        padded("Alice works at Acme Corporation."),
        padded("Acme Corp. uses Azure. FAIL-ME"),
    ])
    graph.llm.fail_when = {"FAIL-ME"}
    source_id = _built(client, subject_id, text)
    first = client.get(f"/api/v1/sources/{source_id}/graph/builds/latest").json()
    assert first["status"] == "partial"
    assert first["failed_chunk_count"] == 1 and first["failed_chunks"][0]["index"] == 1
    assert sorted(u["status"] for u in first["usage"]) == ["failed", "ok"]

    # Settings changing between builds must not shift the chunks a retry reads.
    monkeypatch.setattr(get_settings(), "graph_chunk_chars", 3000)
    graph.llm.fail_when = set()
    calls = len(graph.llm.calls)
    retry = client.post(f"/api/v1/sources/{source_id}/graph", json={"retry_failed": True})
    assert retry.status_code == 202, retry.text
    assert len(graph.llm.calls) - calls == 1
    latest = client.get(f"/api/v1/sources/{source_id}/graph/builds/latest").json()
    assert latest["status"] == "completed"
    assert latest["retry_of_id"] == first["id"] and latest["chunk_chars"] == 200
    assert _facts(client.get(f"/api/v1/subjects/{subject_id}/graph").json()) == [
        ("Acme Corporation", "USES", "Azure"),
        ("Alice", "WORKS_FOR", "Acme Corporation"),
    ]


def test_interrupted_builds_fail_and_orphans_are_swept(client, fake_llm, graph):
    import uuid

    from app.db.database import SessionLocal
    from app.db.models import SourceGraphBuild
    from app.services.graph_service import fail_interrupted_builds, sweep_orphaned_documents

    tenant_id, _, subject_id = _setup(client)
    source_id = _built(client, subject_id)
    with SessionLocal() as db:
        db.query(SourceGraphBuild).filter_by(source_id=source_id).one().status = "processing"
        db.commit()
        assert fail_interrupted_builds(db) == 1
    build = client.get(f"/api/v1/sources/{source_id}/graph/builds/latest").json()
    assert build["status"] == "failed" and "restarted" in build["error"]

    # A document whose source Postgres no longer has (a cleanup that missed).
    ghost = str(uuid.uuid4())
    graph.store.upsert_document(tenant_id, {
        "id": ghost, "application_id": "x", "subject_id": "x", "filename": None,
        "extraction_id": "x", "extraction_version": 1, "build_id": "x",
    })
    with SessionLocal() as db:
        assert sweep_orphaned_documents(db) == 1
    assert graph.store.document_ids(tenant_id) == [source_id]


@pytest.mark.parametrize("field", ["retry_failed", "actor_id"])
def test_build_request_is_validated(client, fake_llm, graph, field):
    _, _, subject_id = _setup(client)
    source_id = _built(client, subject_id)
    value = "not-a-uuid" if field == "actor_id" else "maybe"
    assert client.post(f"/api/v1/sources/{source_id}/graph", json={field: value}).status_code == 422


def test_an_unreachable_graph_never_blocks_uploads_and_answers_503(client, fake_llm, monkeypatch):
    """FalkorDB configured but down (or rejecting the login): nothing connects
    until the graph is actually used, and graph reads say so with a 503."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.core.config import get_settings
    from app.graph import store as store_module
    from app.graph.extraction import GraphExtractor
    from app.graph.ingestion import GraphIngestionService, get_graph_ingestion
    from app.graph.ontology import get_ontology
    from app.graph.retrieval import GraphRetrievalService, get_graph_retrieval
    from app.graph.store import FalkorGraphStore
    from app.main import app
    from tests.graph_fakes import FakeGraphLLM

    attempts = []

    def connect():
        attempts.append(1)
        raise RedisConnectionError("Timeout connecting to server")

    down = FalkorGraphStore(connect=connect, prefix="memora_test_down")
    ingestion = GraphIngestionService(
        down, GraphExtractor(FakeGraphLLM(), get_settings(), get_ontology()), get_settings()
    )
    monkeypatch.setattr(store_module, "get_graph_store", lambda: down)
    app.dependency_overrides[get_graph_ingestion] = lambda: ingestion
    app.dependency_overrides[get_graph_retrieval] = lambda: GraphRetrievalService(down)
    try:
        _, _, subject_id = _setup(client)
        uploaded = _upload(client, subject_id, extract=True)
        assert uploaded.status_code == 201
        assert attempts == []  # the upload never touched FalkorDB
        source_id = uploaded.json()["id"]

        assert client.post(f"/api/v1/sources/{source_id}/graph").status_code == 202
        build = client.get(f"/api/v1/sources/{source_id}/graph/builds/latest").json()
        assert build["status"] == "failed" and "Timeout connecting" in build["error"]

        view = client.get(f"/api/v1/subjects/{subject_id}/graph")
        assert view.status_code == 503 and "not reachable" in view.json()["detail"]
        # Deleting still works; the graph cleanup failure is only logged.
        assert client.delete(f"/api/v1/sources/{source_id}").status_code == 204
    finally:
        app.dependency_overrides.pop(get_graph_ingestion, None)
        app.dependency_overrides.pop(get_graph_retrieval, None)
