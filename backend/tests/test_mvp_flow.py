"""The expected MVP flow, end to end, exactly as specified."""

import io


def test_full_ownership_chain(client):
    # Step 1 -- create the tenant.
    tenant = client.post("/api/v1/tenants", json={"name": "Acme Corporation"})
    assert tenant.status_code == 201, tenant.text
    tenant_id = tenant.json()["id"]
    assert tenant.json()["status"] == "active"

    # Step 2 -- create the application under it.
    application = client.post(
        f"/api/v1/tenants/{tenant_id}/applications",
        json={"name": "ThinkFill", "slug": "thinkfill"},
    )
    assert application.status_code == 201, application.text
    application_id = application.json()["id"]
    assert application.json()["tenant_id"] == tenant_id

    # Step 3 -- issue an API credential; the raw token appears exactly once.
    credential = client.post(
        f"/api/v1/applications/{application_id}/credentials",
        json={"name": "Development"},
    )
    assert credential.status_code == 201, credential.text
    body = credential.json()
    token = body["token"]
    assert token.startswith("memora_")
    assert "token_hash" not in body
    assert body["warning"]

    # Re-reading the credential never returns the token again.
    listed = client.get(f"/api/v1/applications/{application_id}/credentials").json()
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]
    assert listed[0]["token_preview"].endswith("...")

    # The actor layer: who caused the workspace to exist.
    actor = client.post(
        f"/api/v1/applications/{application_id}/actors",
        json={"external_id": "lawyer_123", "type": "user", "name": "John Smith"},
    )
    assert actor.status_code == 201, actor.text
    actor_id = actor.json()["id"]
    assert actor.json()["tenant_id"] == tenant_id

    # Step 4 -- create the subject / workspace, attributed to that actor.
    subject = client.post(
        f"/api/v1/applications/{application_id}/subjects",
        json={"external_id": "customer_123", "actor_id": actor_id},
    )
    assert subject.status_code == 201, subject.text
    subject_id = subject.json()["id"]
    assert subject.json()["actor_id"] == actor_id
    # The internal UUID and the application's own identifier are distinct.
    assert subject.json()["external_id"] == "customer_123"
    assert subject_id != "customer_123"

    # Step 5 -- register a source in that workspace.
    source = client.post(
        f"/api/v1/subjects/{subject_id}/sources",
        json={
            "type": "file",
            "mime_type": "application/pdf",
            "filename": "employee-handbook.pdf",
            "storage_uri": "s3://bucket/path/employee-handbook.pdf",
            "created_by_actor_id": actor_id,
        },
    )
    assert source.status_code == 201, source.text
    record = source.json()

    # The full ownership chain is recorded on the source itself.
    assert record["tenant_id"] == tenant_id
    assert record["application_id"] == application_id
    assert record["subject_id"] == subject_id
    assert record["filename"] == "employee-handbook.pdf"
    assert record["mime_type"] == "application/pdf"
    assert record["storage_uri"] == "s3://bucket/path/employee-handbook.pdf"
    assert record["status"] == "pending"

    # And the detail view resolves the chain to human-readable names.
    detail = client.get(f"/api/v1/sources/{record['id']}").json()
    assert detail["tenant_name"] == "Acme Corporation"
    assert detail["application_name"] == "ThinkFill"
    assert detail["subject_external_id"] == "customer_123"
    assert detail["actor_external_id"] == "lawyer_123"

    # The dashboard reports real counts, not hard-coded ones.
    stats = client.get("/api/v1/stats").json()
    assert stats == {
        "tenants": 1,
        "applications": 1,
        "actors": 1,
        "subjects": 1,
        "sources": 1,
        "active_credentials": 1,
        "sources_by_status": {"pending": 1},
    }


def test_upload_a_file_as_an_application(client):
    """The application-facing ingestion point: post a file with a bearer token."""
    tenant_id = client.post("/api/v1/tenants", json={"name": "Acme Law"}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications",
        json={"name": "LegalCase", "slug": "legal-case"},
    ).json()["id"]
    token = client.post(
        f"/api/v1/applications/{application_id}/credentials",
        json={"name": "Production"},
    ).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    # The credential resolves to its own application and tenant.
    who = client.get("/api/v1/whoami", headers=auth)
    assert who.status_code == 200, who.text
    assert who.json()["tenant_id"] == tenant_id
    assert who.json()["application_id"] == application_id

    subject_id = client.post(
        f"/api/v1/applications/{application_id}/subjects",
        json={"external_id": "case-ABC-456"},
        headers=auth,
    ).json()["id"]

    payload = b"%PDF-1.7 pretend complaint\n"
    upload = client.post(
        f"/api/v1/subjects/{subject_id}/sources/upload",
        files={"file": ("complaint.pdf", io.BytesIO(payload), "application/pdf")},
        headers=auth,
    )
    assert upload.status_code == 201, upload.text
    source = upload.json()
    assert source["filename"] == "complaint.pdf"
    assert source["mime_type"] == "application/pdf"
    assert source["size_bytes"] == len(payload)
    assert source["storage_uri"].startswith("file://")
    assert source["status"] == "pending"

    # The stored bytes come back byte-for-byte.
    content = client.get(f"/api/v1/sources/{source['id']}/content", headers=auth)
    assert content.status_code == 200
    assert content.content == payload

    # Deleting the source removes the stored object too.
    assert client.delete(f"/api/v1/sources/{source['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/sources/{source['id']}", headers=auth).status_code == 404
