"""Extraction through the API: upload flag, versions, retries, scope, cost.

The whole production path runs -- route, service, Document Processor, agent,
database -- with only the model provider faked (the `fake_llm` fixture).
TestClient runs background tasks before returning, so a run has finished by
the time the triggering request comes back.
"""

import io

from tests.extraction_fakes import LOREM, make_pdf, table_page, text_page


def _setup(client, name="Acme"):
    tenant_id = client.post("/api/v1/tenants", json={"name": name}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "App", "slug": "app"}
    ).json()["id"]
    subject_id = client.post(
        f"/api/v1/applications/{application_id}/subjects", json={"external_id": "case-1"}
    ).json()["id"]
    return tenant_id, application_id, subject_id


def _token(client, application_id):
    return client.post(
        f"/api/v1/applications/{application_id}/credentials", json={"name": "ci"}
    ).json()["token"]


def _upload(client, subject_id, data=None, filename="invoice.pdf", headers=None, **form):
    data = data or make_pdf([text_page(LOREM), table_page()])
    return client.post(
        f"/api/v1/subjects/{subject_id}/sources/upload",
        files={"file": (filename, io.BytesIO(data), "application/pdf")},
        data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()},
        headers=headers or {},
    )


def test_upload_without_extract_is_unchanged(client, fake_llm):
    _, _, subject_id = _setup(client)
    response = _upload(client, subject_id)
    assert response.status_code == 201, response.text
    source = response.json()
    assert source["status"] == "pending"
    assert fake_llm.calls == []
    assert client.get(f"/api/v1/sources/{source['id']}/extractions").json() == []


def test_upload_with_extract_runs_version_one(client, fake_llm):
    _, _, subject_id = _setup(client)
    response = _upload(client, subject_id, extract=True)
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]

    latest = client.get(f"/api/v1/sources/{source_id}/extractions/latest")
    assert latest.status_code == 200, latest.text
    run = latest.json()
    assert run["version"] == 1
    assert run["status"] == "completed"
    assert run["mode"] == "standard"
    assert run["provider"] == "build-nvidia"
    assert set(run["models"]) == {"layout", "vision", "extract"}
    result = run["result"]
    assert result["source_id"] == source_id
    assert result["document_type"] == "invoice"
    # Page 1 is plain text (no model), page 2 is a table (layout model).
    assert [(p["page"], p["difficulty"], p["route"]) for p in result["pages"]] == [
        (1, "easy", "text"),
        (2, "hard", "layout"),
    ]
    # One layout call + one extract call, both in the ledger.
    assert sorted(u["role"] for u in run["usage"]) == ["extract", "layout"]
    assert run["prompt_tokens"] == 1300 and run["completion_tokens"] == 250
    assert run["cost_usd"] == 0.0  # build.nvidia.com is free
    assert run["triggered_by_kind"] == "user"
    assert run["triggered_by_user_id"] is not None

    assert client.get(f"/api/v1/sources/{source_id}").json()["status"] == "completed"


def test_reextract_adds_a_version_and_keeps_the_old_one(client, fake_llm):
    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id, extract=True).json()["id"]

    again = client.post(
        f"/api/v1/sources/{source_id}/extractions",
        json={"mode": "deep", "instructions": "  capture the premium table  "},
    )
    assert again.status_code == 202, again.text
    assert again.json()["version"] == 2
    assert again.json()["status"] == "processing"  # the response precedes the run

    versions = client.get(f"/api/v1/sources/{source_id}/extractions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert "result" not in versions[0]  # summaries stay small

    v2 = client.get(f"/api/v1/sources/{source_id}/extractions/2").json()
    assert v2["status"] == "completed"
    assert v2["mode"] == "deep"
    assert v2["instructions"] == "capture the premium table"
    # Deep mode: both pages went to the layout model.
    assert {p["route"] for p in v2["result"]["pages"]} == {"layout"}
    assert "capture the premium table" in fake_llm.calls[-1]["user"]

    v1 = client.get(f"/api/v1/sources/{source_id}/extractions/1").json()
    assert v1["mode"] == "standard" and v1["result"]["pages"][0]["route"] == "text"


def test_extraction_can_start_later_from_the_api(client, fake_llm):
    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id).json()["id"]
    started = client.post(f"/api/v1/sources/{source_id}/extractions")  # no body at all
    assert started.status_code == 202, started.text
    assert client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()["status"] == "completed"


def test_a_second_run_while_one_is_processing_is_rejected(client, fake_llm):
    from app.db.database import SessionLocal
    from app.db.models import SourceExtraction

    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id, extract=True).json()["id"]
    with SessionLocal() as db:
        run = db.query(SourceExtraction).filter_by(source_id=source_id).one()
        run.status = "processing"
        db.commit()

    response = client.post(f"/api/v1/sources/{source_id}/extractions", json={})
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_a_failing_model_fails_the_run_with_a_reason(client, fake_llm):
    from app.core.config import get_settings

    fake_llm.fail_models = {get_settings().llm_extract_model}
    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id, extract=True).json()["id"]

    run = client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()
    assert run["status"] == "failed"
    assert "Extraction model failed" in run["error"]
    assert run["result"] is None
    # The failed call is still in the ledger: it may have been billed.
    assert any(u["status"] == "failed" for u in run["usage"])
    assert client.get(f"/api/v1/sources/{source_id}").json()["status"] == "failed"

    # Retrying is just the next version.
    fake_llm.fail_models = set()
    client.post(f"/api/v1/sources/{source_id}/extractions", json={})
    assert client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()["version"] == 2
    assert client.get(f"/api/v1/sources/{source_id}").json()["status"] == "completed"


def test_unsupported_file_fails_cleanly(client, fake_llm):
    _, _, subject_id = _setup(client)
    response = client.post(
        f"/api/v1/subjects/{subject_id}/sources/upload",
        files={"file": ("archive.zip", io.BytesIO(b"PK\x03\x04"), "application/zip")},
        data={"extract": "true"},
    )
    source_id = response.json()["id"]
    run = client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()
    assert run["status"] == "failed"
    assert "Unsupported document type" in run["error"]
    assert fake_llm.calls == []


def test_metadata_only_source_cannot_be_extracted(client, fake_llm):
    _, _, subject_id = _setup(client)
    source_id = client.post(
        f"/api/v1/subjects/{subject_id}/sources",
        json={"type": "url", "storage_uri": None, "filename": "https://example.com"},
    ).json()["id"]
    response = client.post(f"/api/v1/sources/{source_id}/extractions", json={})
    assert response.status_code == 422
    assert "no stored content" in response.json()["detail"]


def test_missing_version_is_404(client, fake_llm):
    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id).json()["id"]
    assert client.get(f"/api/v1/sources/{source_id}/extractions/latest").status_code == 404
    assert client.get(f"/api/v1/sources/{source_id}/extractions/7").status_code == 404


def test_credential_runs_are_attributed_and_scoped(client, fake_llm):
    _, application_id, subject_id = _setup(client, "Acme")
    token = _token(client, application_id)
    auth = {"Authorization": f"Bearer {token}"}
    actor_id = client.post(
        f"/api/v1/applications/{application_id}/actors",
        json={"external_id": "lawyer_1", "type": "user"},
    ).json()["id"]

    source_id = _upload(
        client, subject_id, headers=auth, extract=True, created_by_actor_id=actor_id
    ).json()["id"]
    run = client.get(f"/api/v1/sources/{source_id}/extractions/latest", headers=auth).json()
    assert run["triggered_by_kind"] == "credential"
    assert run["triggered_by_credential_id"] is not None
    assert run["actor_id"] == actor_id

    # Another tenant's credential cannot see or start runs: 404, never 403.
    _, other_app, _ = _setup(client, "Globex")
    intruder = {"Authorization": f"Bearer {_token(client, other_app)}"}
    assert client.get(
        f"/api/v1/sources/{source_id}/extractions/latest", headers=intruder
    ).status_code == 404
    assert client.post(
        f"/api/v1/sources/{source_id}/extractions", json={}, headers=intruder
    ).status_code == 404

    report = client.get("/api/v1/usage/extractions", headers=auth).json()
    assert report["totals"]["runs"] == 1
    assert report["totals"]["calls"] == 2
    [trigger] = report["by_trigger"]
    assert trigger["key"].startswith("credential:") and trigger["label"] == "ci"
    assert {g["label"] for g in report["by_model"]} == {"layout", "extract"}

    # The intruder's report is empty: usage is scoped like everything else.
    assert client.get("/api/v1/usage/extractions", headers=intruder).json()["totals"]["runs"] == 0


def test_nebius_runs_are_priced_from_the_operator_price_list(
    client, fake_llm, monkeypatch, tmp_path
):
    import json

    from app.core import pricing
    from app.core.config import get_settings

    settings = get_settings()
    price_file = tmp_path / "pricing.json"
    price_file.write_text(json.dumps({"versions": [{
        "effective_from": "2026-01-01",
        "providers": {"nebius": {settings.llm_extract_model: {"input_per_1m": 1.0, "output_per_1m": 2.0}}},
    }]}))
    monkeypatch.setattr(settings, "llm_provider", "nebius")
    monkeypatch.setattr(settings, "pricing_file", str(price_file))
    pricing.get_price_list.cache_clear()
    try:
        _, _, subject_id = _setup(client)
        data = make_pdf([text_page(LOREM), table_page()])
        source_id = _upload(client, subject_id, data=data, extract=True).json()["id"]
        run = client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()
    finally:
        pricing.get_price_list.cache_clear()

    assert run["provider"] == "nebius"
    # 1000 prompt tokens at $1/M + 200 completion tokens at $2/M; the layout
    # model has no price, so it is recorded at $0 with no rate snapshot.
    assert run["cost_usd"] == 0.0014
    calls = {u["role"]: u for u in run["usage"]}
    assert calls["extract"]["price"]["input_per_1m"] == 1.0
    assert calls["extract"]["price"]["effective_from"] == "2026-01-01"
    assert calls["layout"]["cost_usd"] == 0.0 and calls["layout"]["price"] is None


def test_deleting_a_source_removes_its_extractions(client, fake_llm):
    from sqlalchemy import func, select

    from app.db.database import SessionLocal
    from app.db.models import ExtractionUsage, SourceExtraction

    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id, extract=True).json()["id"]
    assert client.delete(f"/api/v1/sources/{source_id}").status_code == 204
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(SourceExtraction)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(ExtractionUsage)).scalar_one() == 0


def test_interrupted_runs_are_failed_at_startup(client, fake_llm):
    from app.db.database import SessionLocal
    from app.db.models import Source, SourceExtraction
    from app.services.extraction_service import fail_interrupted_runs

    _, _, subject_id = _setup(client)
    source_id = _upload(client, subject_id, extract=True).json()["id"]
    with SessionLocal() as db:
        run = db.query(SourceExtraction).filter_by(source_id=source_id).one()
        run.status = "processing"
        db.get(Source, run.source_id).status = "processing"
        db.commit()
        assert fail_interrupted_runs(db) == 1
    run = client.get(f"/api/v1/sources/{source_id}/extractions/latest").json()
    assert run["status"] == "failed" and "restarted" in run["error"]
    assert client.get(f"/api/v1/sources/{source_id}").json()["status"] == "failed"
