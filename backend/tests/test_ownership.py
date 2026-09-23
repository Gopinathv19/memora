"""Ownership isolation: knowing an id must never be enough."""

import pytest


@pytest.fixture
def two_tenants(client):
    """Two independent tenants, each with an application, subject and source."""

    def build(name: str, slug: str, external_id: str):
        tenant_id = client.post("/api/v1/tenants", json={"name": name}).json()["id"]
        application_id = client.post(
            f"/api/v1/tenants/{tenant_id}/applications",
            json={"name": name, "slug": slug},
        ).json()["id"]
        token = client.post(
            f"/api/v1/applications/{application_id}/credentials",
            json={"name": "key"},
        ).json()["token"]
        subject_id = client.post(
            f"/api/v1/applications/{application_id}/subjects",
            json={"external_id": external_id},
        ).json()["id"]
        source_id = client.post(
            f"/api/v1/subjects/{subject_id}/sources",
            json={"type": "url", "storage_uri": "https://example.com/doc"},
        ).json()["id"]
        return {
            "tenant_id": tenant_id,
            "application_id": application_id,
            "token": token,
            "subject_id": subject_id,
            "source_id": source_id,
            "auth": {"Authorization": f"Bearer {token}"},
        }

    return build("Acme", "acme-app", "case-1"), build("Globex", "globex-app", "case-2")


def test_credential_cannot_read_another_tenants_source(two_tenants, client):
    acme, globex = two_tenants

    # Acme's token, Globex's source id. The id is valid and exists.
    response = client.get(f"/api/v1/sources/{globex['source_id']}", headers=acme["auth"])
    # 404, not 403: a 403 would confirm the id exists in another tenant.
    assert response.status_code == 404

    # The same holds for every other resource in the chain.
    for path in (
        f"/api/v1/subjects/{globex['subject_id']}",
        f"/api/v1/applications/{globex['application_id']}",
        f"/api/v1/tenants/{globex['tenant_id']}",
    ):
        assert client.get(path, headers=acme["auth"]).status_code == 404, path


def test_credential_cannot_write_into_another_tenant(two_tenants, client):
    acme, globex = two_tenants

    # Registering a source into a foreign subject.
    assert (
        client.post(
            f"/api/v1/subjects/{globex['subject_id']}/sources",
            json={"type": "file", "filename": "stolen.pdf"},
            headers=acme["auth"],
        ).status_code
        == 404
    )

    # Creating a subject inside a foreign application.
    assert (
        client.post(
            f"/api/v1/applications/{globex['application_id']}/subjects",
            json={"external_id": "intruder"},
            headers=acme["auth"],
        ).status_code
        == 404
    )

    # Minting a credential for a foreign application.
    assert (
        client.post(
            f"/api/v1/applications/{globex['application_id']}/credentials",
            json={"name": "intruder"},
            headers=acme["auth"],
        ).status_code
        == 404
    )

    # Deleting a foreign source.
    assert (
        client.delete(
            f"/api/v1/sources/{globex['source_id']}", headers=acme["auth"]
        ).status_code
        == 404
    )


def test_listing_is_scoped_to_the_credential(two_tenants, client):
    acme, globex = two_tenants

    # Unscoped console request sees both tenants' data.
    assert len(client.get("/api/v1/sources").json()) == 2
    assert len(client.get("/api/v1/tenants").json()) == 2

    # An application credential sees only its own.
    scoped = client.get("/api/v1/sources", headers=acme["auth"]).json()
    assert len(scoped) == 1
    assert scoped[0]["id"] == acme["source_id"]
    assert len(client.get("/api/v1/tenants", headers=acme["auth"]).json()) == 1

    # A client-supplied tenant_id filter cannot widen that scope.
    widened = client.get(
        "/api/v1/sources", params={"tenant_id": globex["tenant_id"]}, headers=acme["auth"]
    ).json()
    assert widened == []


def test_source_must_belong_to_the_subject_in_the_path(two_tenants, client):
    acme, globex = two_tenants

    # Acme's own source, but addressed through Acme's *other* subject.
    other_subject = client.post(
        f"/api/v1/applications/{acme['application_id']}/subjects",
        json={"external_id": "unrelated"},
    ).json()["id"]

    # The full chain has to agree, even though both rows are Acme's.
    assert (
        client.get(
            f"/api/v1/subjects/{other_subject}/sources/{acme['source_id']}"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/subjects/{acme['subject_id']}/sources/{acme['source_id']}"
        ).status_code
        == 200
    )


def test_client_cannot_choose_a_sources_owners(client):
    """Ownership columns are derived from the subject, never from the body."""
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    other_tenant = client.post("/api/v1/tenants", json={"name": "Other"}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "A", "slug": "a"}
    ).json()["id"]
    subject_id = client.post(
        f"/api/v1/applications/{application_id}/subjects",
        json={"external_id": "w1"},
    ).json()["id"]

    # Extra owner ids in the body are ignored, not honoured.
    source = client.post(
        f"/api/v1/subjects/{subject_id}/sources",
        json={"type": "file", "tenant_id": other_tenant, "application_id": other_tenant},
    ).json()
    assert source["tenant_id"] == tenant_id
    assert source["application_id"] == application_id


def test_cross_application_actor_is_rejected(client):
    """An actor from another application cannot be attached to a subject."""
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    app_a = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "A", "slug": "a"}
    ).json()["id"]
    app_b = client.post(
        f"/api/v1/tenants/{tenant_id}/applications", json={"name": "B", "slug": "b"}
    ).json()["id"]
    actor_in_b = client.post(
        f"/api/v1/applications/{app_b}/actors", json={"external_id": "u1"}
    ).json()["id"]

    response = client.post(
        f"/api/v1/applications/{app_a}/subjects",
        json={"external_id": "w1", "actor_id": actor_in_b},
    )
    assert response.status_code == 422
    assert "actor" in response.json()["detail"].lower()
