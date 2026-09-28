from pymongo.database import Database


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert set(body) == {"status", "database", "ai_configured", "email_configured", "google_sign_in"}


def test_health_reports_database_down(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(Database, "command", fail)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "degraded"
    assert resp.get_json()["database"] == "unreachable"



def _preflight(client, origin):
    return client.options(
        "/api/auth/otp/request",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


def test_default_origins_include_deployed_frontend(monkeypatch):
    import importlib

    from app import config

    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)  # ignore the local .env
    fresh = importlib.reload(config)
    try:
        assert "https://cropvision-frontend.vercel.app" in fresh.Config.ALLOWED_ORIGINS
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_cors_allows_configured_origin_despite_typos():
    """A trailing slash and stray spaces in ALLOWED_ORIGINS must still match."""
    from app import create_app
    from app.config import TestConfig

    class Cfg(TestConfig):
        ALLOWED_ORIGINS = " https://cropvision-frontend.vercel.app/ , http://localhost:5173"

    client = create_app(Cfg).test_client()
    allowed = _preflight(client, "https://cropvision-frontend.vercel.app")
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://cropvision-frontend.vercel.app"
    blocked = _preflight(client, "https://evil.example.com")
    assert "Access-Control-Allow-Origin" not in blocked.headers
