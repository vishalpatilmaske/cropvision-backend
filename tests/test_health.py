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
