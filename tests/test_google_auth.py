"""Sign in with Google. Google's tokeninfo / userinfo endpoints are faked."""
import pytest
import requests

from app.services import google_auth_service

CLIENT_ID = "test-client.apps.googleusercontent.com"
TOKENINFO = {"aud": CLIENT_ID, "azp": CLIENT_ID, "sub": "1234567890", "scope": "openid email profile"}
USERINFO = {"sub": "1234567890", "email": "Ramesh@Gmail.com", "email_verified": True, "name": "Ramesh Patil"}


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


@pytest.fixture()
def google_on(app):
    app.config["GOOGLE_CLIENT_ID"] = CLIENT_ID
    yield
    app.config["GOOGLE_CLIENT_ID"] = ""


def _fake_google(monkeypatch, tokeninfo=None, userinfo=None, tokeninfo_status=200, error=None):
    def fake_get(url, **_kwargs):
        if error:
            raise error
        if url == google_auth_service.TOKENINFO_URL:
            return FakeResponse(tokeninfo_status, tokeninfo or TOKENINFO)
        if url == google_auth_service.USERINFO_URL:
            return FakeResponse(200, userinfo or USERINFO)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(google_auth_service.requests, "get", fake_get)


def _sign_in(client):
    return client.post("/api/auth/google", json={"access_token": "google-token"})


def test_providers_hidden_when_not_configured(client):
    assert client.get("/api/auth/providers").get_json()["data"]["google_client_id"] is None


def test_providers_exposes_client_id(client, google_on):
    assert client.get("/api/auth/providers").get_json()["data"]["google_client_id"] == CLIENT_ID


def test_google_sign_in_disabled_without_client_id(client):
    assert _sign_in(client).status_code == 503


def test_google_sign_in_requires_token(client, google_on):
    assert client.post("/api/auth/google", json={}).status_code == 400


def test_google_sign_in_creates_then_reuses_account(client, google_on, monkeypatch):
    _fake_google(monkeypatch)

    first = _sign_in(client)
    assert first.status_code == 201
    body = first.get_json()["data"]
    assert body["created"] is True
    assert body["user"]["email"] == "ramesh@gmail.com"
    assert body["user"]["name"] == "Ramesh Patil"

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    second = _sign_in(client)
    assert second.status_code == 200
    assert second.get_json()["data"]["created"] is False
    assert second.get_json()["data"]["user"]["id"] == body["user"]["id"]


def test_google_sign_in_uses_existing_email_account(client, google_on, monkeypatch, auth_headers):
    _fake_google(monkeypatch, userinfo={**USERINFO, "email": "farmer@example.com"})
    resp = _sign_in(client)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["created"] is False


def test_rejects_token_issued_to_another_app(client, google_on, monkeypatch):
    _fake_google(monkeypatch, tokeninfo={**TOKENINFO, "aud": "other-app", "azp": "other-app"})
    resp = _sign_in(client)
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "GOOGLE_AUTH_FAILED"


def test_rejects_invalid_or_expired_token(client, google_on, monkeypatch):
    _fake_google(monkeypatch, tokeninfo={"error": "invalid_token"}, tokeninfo_status=400)
    assert _sign_in(client).status_code == 401


def test_rejects_unverified_email(client, google_on, monkeypatch):
    _fake_google(monkeypatch, userinfo={**USERINFO, "email_verified": False})
    assert _sign_in(client).status_code == 401


def test_rejects_mismatched_account(client, google_on, monkeypatch):
    _fake_google(monkeypatch, userinfo={**USERINFO, "sub": "someone-else"})
    assert _sign_in(client).status_code == 401


def test_google_unreachable(client, google_on, monkeypatch):
    _fake_google(monkeypatch, error=requests.ConnectionError("down"))
    resp = _sign_in(client)
    assert resp.status_code == 502
    assert resp.get_json()["error"]["code"] == "GOOGLE_UNAVAILABLE"
