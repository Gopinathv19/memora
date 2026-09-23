"""Token handling and credential lifecycle."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def application(client):
    tenant_id = client.post("/api/v1/tenants", json={"name": "T"}).json()["id"]
    return client.post(
        f"/api/v1/tenants/{tenant_id}/applications",
        json={"name": "App", "slug": "app"},
    ).json()["id"]


def _issue(client, application_id, **payload):
    payload.setdefault("name", "key")
    return client.post(
        f"/api/v1/applications/{application_id}/credentials", json=payload
    ).json()


def test_raw_token_is_never_persisted(client, application):
    """The database must contain no trace of the raw token."""
    from app.db.database import engine

    token = _issue(client, application)["token"]

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT token_hash, token_preview FROM api_credentials")
        ).all()

    assert len(rows) == 1
    token_hash, preview = rows[0]
    assert token_hash != token
    assert token not in token_hash
    # The stored preview is a fragment only -- far too short to be usable.
    assert len(preview) < len(token)
    assert token.startswith(preview[:-3])

    # And the hash is not reachable through any endpoint.
    listed = client.get(f"/api/v1/applications/{application}/credentials").text
    assert token_hash not in listed
    assert "token_hash" not in listed


def test_token_authenticates_and_records_last_used(client, application):
    credential = _issue(client, application)
    assert credential["last_used_at"] is None

    auth = {"Authorization": f"Bearer {credential['token']}"}
    assert client.get("/api/v1/whoami", headers=auth).status_code == 200

    refreshed = client.get(f"/api/v1/applications/{application}/credentials").json()[0]
    assert refreshed["last_used_at"] is not None


def test_rejected_tokens(client, application):
    # A token that was never issued.
    assert (
        client.get(
            "/api/v1/whoami", headers={"Authorization": "Bearer memora_nonsense"}
        ).status_code
        == 401
    )
    # No token at all, on a route that requires one.
    assert client.get("/api/v1/whoami").status_code == 401
    # A malformed scheme.
    assert (
        client.get("/api/v1/whoami", headers={"Authorization": "Basic abc"}).status_code
        == 401
    )


def test_expired_token_is_rejected(client, application):
    expired = _issue(
        client,
        application,
        expires_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    auth = {"Authorization": f"Bearer {expired['token']}"}
    response = client.get("/api/v1/whoami", headers=auth)
    assert response.status_code == 401
    # The message must not reveal *why* -- expired and unknown look identical.
    assert response.json()["detail"] == "Invalid or expired API token"


def test_suspended_credential_and_application_are_rejected(client, application):
    credential = _issue(client, application)
    auth = {"Authorization": f"Bearer {credential['token']}"}

    client.patch(f"/api/v1/credentials/{credential['id']}", json={"status": "revoked"})
    assert client.get("/api/v1/whoami", headers=auth).status_code == 401

    client.patch(f"/api/v1/credentials/{credential['id']}", json={"status": "active"})
    assert client.get("/api/v1/whoami", headers=auth).status_code == 200

    # Suspending the application invalidates its tokens as well.
    client.patch(f"/api/v1/applications/{application}", json={"status": "suspended"})
    assert client.get("/api/v1/whoami", headers=auth).status_code == 401


def test_revoked_credential_stops_working(client, application):
    credential = _issue(client, application)
    auth = {"Authorization": f"Bearer {credential['token']}"}
    assert client.get("/api/v1/whoami", headers=auth).status_code == 200

    assert client.delete(f"/api/v1/credentials/{credential['id']}").status_code == 204
    assert client.get("/api/v1/whoami", headers=auth).status_code == 401
    assert client.get(f"/api/v1/applications/{application}/credentials").json() == []
