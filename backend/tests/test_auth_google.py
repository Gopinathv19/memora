import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.services import auth_services


class _FakeGoogle:
    """Stands in for httpx.Client when calling Google's tokeninfo endpoint."""

    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        return httpx.Response(self.status_code, json=self.payload)


TOKEN_INFO = {
    "aud": "memora-client.apps.googleusercontent.com",
    "sub": "google-user-1",
    "email": "Person@Example.com",
    "email_verified": "true",
    "name": "Person",
    "picture": "https://example.com/p.png",
}


@pytest.fixture
def client_id(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", TOKEN_INFO["aud"])


def test_missing_client_id_refuses_sign_in(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    monkeypatch.setattr(auth_services.httpx, "Client", _FakeGoogle(TOKEN_INFO))
    with pytest.raises(AuthenticationError, match="GOOGLE_CLIENT_ID"):
        auth_services.verify_google_id_token("token")


def test_token_for_another_app_is_rejected(monkeypatch, client_id):
    other = {**TOKEN_INFO, "aud": "someone-else.apps.googleusercontent.com"}
    monkeypatch.setattr(auth_services.httpx, "Client", _FakeGoogle(other))
    with pytest.raises(AuthenticationError, match="audience"):
        auth_services.verify_google_id_token("token")


def test_unverified_email_is_rejected(monkeypatch, client_id):
    unverified = {**TOKEN_INFO, "email_verified": "false"}
    monkeypatch.setattr(auth_services.httpx, "Client", _FakeGoogle(unverified))
    with pytest.raises(AuthenticationError, match="verified"):
        auth_services.verify_google_id_token("token")


def test_valid_token_yields_identity(monkeypatch, client_id):
    monkeypatch.setattr(auth_services.httpx, "Client", _FakeGoogle(TOKEN_INFO))
    identity = auth_services.verify_google_id_token("token")
    assert identity.provider_user_id == "google-user-1"
    assert identity.email == "person@example.com"


@pytest.mark.parametrize("path", ["/api/v1/auth/signup", "/api/v1/auth/login"])
def test_password_routes_are_gone(client, path):
    response = client.post(path, json={"email": "a@b.co", "password": "password123"})
    assert response.status_code in (404, 405)
