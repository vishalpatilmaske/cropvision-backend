from pymongo.database import Database


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert set(body) == {"status", "database", "ai_configured", "email_configured"}


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



def _app_with_origins(extra):
    from app import create_app
    from app.config import TestConfig

    class Cfg(TestConfig):
        ALLOWED_ORIGINS = extra

    return create_app(Cfg).test_client()


def _allowed(client, origin):
    return _preflight(client, origin).headers.get("Access-Control-Allow-Origin") == origin


def test_deployed_frontend_always_allowed_even_if_env_lists_only_localhost():
    """The real-world bug: Vercel had ALLOWED_ORIGINS=http://localhost:5173."""
    client = _app_with_origins("http://localhost:5173")
    assert _allowed(client, "https://cropvision-frontend.vercel.app")
    assert _allowed(client, "http://localhost:5173")


def test_allowed_origins_adds_extra_sites_despite_typos():
    client = _app_with_origins(" https://cropvision.in/ , ")
    assert _allowed(client, "https://cropvision.in")
    assert _allowed(client, "https://cropvision-frontend.vercel.app")
    assert not _allowed(client, "https://evil.example.com")
