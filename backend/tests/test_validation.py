"""Validation, uniqueness constraints and error shapes."""

import uuid


def test_unique_slug_within_tenant_but_not_across_tenants(client):
    tenant_a = client.post("/api/v1/tenants", json={"name": "A"}).json()["id"]
    tenant_b = client.post("/api/v1/tenants", json={"name": "B"}).json()["id"]

    payload = {"name": "Legal", "slug": "legal-app"}
    assert client.post(f"/api/v1/tenants/{tenant_a}/applications", json=payload).status_code == 201

    duplicate = client.post(f"/api/v1/tenants/{tenant_a}/applications", json=payload)
    assert duplicate.status_code == 409
    assert "legal-app" in duplicate.json()["detail"]

    # The same slug is fine in a different tenant.
    assert client.post(f"/api/v1/tenants/{tenant_b}/applications", json=payload).status_code == 201


def test_unique_external_id_within_application(client):
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    app_a = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "A", "slug": "a"}
    ).json()["id"]
    app_b = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "B", "slug": "b"}
    ).json()["id"]

    subject = {"external_id": "case-ABC-456"}
    assert client.post(f"/api/v1/applications/{app_a}/subjects", json=subject).status_code == 201
    assert client.post(f"/api/v1/applications/{app_a}/subjects", json=subject).status_code == 409
    # The same workspace identifier in another application is a different workspace.
    assert client.post(f"/api/v1/applications/{app_b}/subjects", json=subject).status_code == 201

    actor = {"external_id": "lawyer_123"}
    assert client.post(f"/api/v1/applications/{app_a}/actors", json=actor).status_code == 201
    assert client.post(f"/api/v1/applications/{app_a}/actors", json=actor).status_code == 409


def test_slug_format_is_enforced(client):
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    for bad in ("Legal App", "legal_app", "-legal", "legal--app", ""):
        response = client.post(
            f"/api/v1/tenants/{tenant_id}/applications",
            json={"name": "A", "slug": bad},
        )
        assert response.status_code == 422, bad

    # Mixed case is normalized rather than rejected.
    created = client.post(
        f"/api/v1/tenants/{tenant_id}/applications",
        json={"name": "A", "slug": "Legal-App"},
    )
    assert created.status_code == 201
    assert created.json()["slug"] == "legal-app"


def test_blank_names_are_rejected(client):
    assert client.post("/api/v1/tenants", json={"name": "   "}).status_code == 422
    assert client.post("/api/v1/tenants", json={}).status_code == 422


def test_unknown_ids_are_404_and_malformed_ids_are_422(client):
    missing = uuid.uuid4()
    assert client.get(f"/api/v1/tenants/{missing}").status_code == 404
    assert client.get(f"/api/v1/sources/{missing}").status_code == 404
    assert client.get(f"/api/v1/subjects/{missing}").status_code == 404
    # Not a UUID at all -- rejected before it reaches the database.
    assert client.get("/api/v1/sources/not-a-uuid").status_code == 422


def test_errors_always_carry_a_detail_field(client):
    """The console relies on `detail` being present on every failure."""
    conflict = client.post("/api/v1/tenants", json={"name": ""})
    assert "detail" in conflict.json()

    not_found = client.get(f"/api/v1/tenants/{uuid.uuid4()}")
    assert not_found.json()["detail"] == "Tenant not found"
    assert not_found.json()["code"] == "not_found"


def test_api_credential_cannot_create_a_tenant(client):
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "A", "slug": "a"}
    ).json()["id"]
    token = client.post(
        f"/api/v1/applications/{application_id}/credentials", json={"name": "k"}
    ).json()["token"]

    response = client.post(
        "/api/v1/tenants",
        json={"name": "Sneaky"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_source_status_transitions(client):
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "A", "slug": "a"}
    ).json()["id"]
    subject_id = client.post(
        f"/api/v1/applications/{application_id}/subjects", json={"external_id": "w"}
    ).json()["id"]
    source_id = client.post(
        f"/api/v1/subjects/{subject_id}/sources", json={"type": "chat"}
    ).json()["id"]

    for status in ("processing", "completed", "failed"):
        updated = client.patch(f"/api/v1/sources/{source_id}", json={"status": status})
        assert updated.status_code == 200
        assert updated.json()["status"] == status

    # An unrecognized status is rejected at the edge.
    assert (
        client.patch(f"/api/v1/sources/{source_id}", json={"status": "banana"}).status_code
        == 422
    )


def test_filtering_sources_by_status(client):
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "A", "slug": "a"}
    ).json()["id"]
    subject_id = client.post(
        f"/api/v1/applications/{application_id}/subjects", json={"external_id": "w"}
    ).json()["id"]

    first = client.post(
        f"/api/v1/subjects/{subject_id}/sources", json={"type": "file"}
    ).json()["id"]
    client.post(f"/api/v1/subjects/{subject_id}/sources", json={"type": "url"})
    client.patch(f"/api/v1/sources/{first}", json={"status": "completed"})

    assert len(client.get("/api/v1/sources").json()) == 2
    completed = client.get("/api/v1/sources", params={"status_filter": "completed"}).json()
    assert len(completed) == 1
    assert completed[0]["id"] == first


def test_deleting_a_tenant_cascade_is_not_exposed(client):
    """There is no tenant DELETE endpoint in the MVP -- deletion is destructive
    across the whole chain and needs a deliberate design, not a convenience."""
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    assert client.delete(f"/api/v1/tenants/{tenant_id}").status_code == 405
