from app.routes.newsletter_routes import unsubscribe_token
from app.services import email_service


def _subscribe(client, email):
    return client.post("/api/newsletter/subscribe", json={"email": email})


def test_subscribe_saves_and_sends_welcome(client):
    resp = _subscribe(client, "  Ramesh@Example.com ")
    assert resp.status_code == 201
    assert resp.get_json()["data"]["status"] == "subscribed"
    mail = email_service.outbox[-1]
    assert mail["to"] == "ramesh@example.com"
    assert "unsubscribe?" in mail["html"] and "token=" in mail["text"]


def test_subscribing_twice_is_friendly_and_sends_no_second_email(client):
    _subscribe(client, "ramesh@example.com")
    sent = len(email_service.outbox)
    resp = _subscribe(client, "RAMESH@example.com")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["status"] == "already_subscribed"
    assert len(email_service.outbox) == sent


def test_rejects_invalid_email(client):
    assert _subscribe(client, "not-an-email").status_code == 400
    assert client.post("/api/newsletter/subscribe", json={}).status_code == 400


def test_unsubscribe_needs_valid_token_and_allows_resubscribe(client, admin_headers):
    _subscribe(client, "ramesh@example.com")
    bad = client.get("/api/newsletter/unsubscribe?email=ramesh@example.com&token=wrong")
    assert bad.status_code == 400

    with client.application.app_context():
        token = unsubscribe_token("ramesh@example.com")
    ok = client.get(f"/api/newsletter/unsubscribe?email=ramesh@example.com&token={token}")
    assert ok.status_code == 200 and b"unsubscribed" in ok.data
    stats = client.get("/api/admin/stats", headers=admin_headers).get_json()["data"]
    assert stats["newsletter_subscribers"] == 0

    assert _subscribe(client, "ramesh@example.com").status_code == 201  # welcome back


def test_admin_newsletter_export(client, admin_headers, auth_headers):
    _subscribe(client, "a@example.com")
    _subscribe(client, "b@example.com")
    resp = client.get("/api/admin/newsletter/export", headers=admin_headers)
    assert resp.status_code == 200 and resp.mimetype == "text/csv"
    rows = resp.get_data(as_text=True).strip().splitlines()
    assert rows[0] == "Email,Subscribed" and len(rows) == 3
    assert client.get("/api/admin/newsletter/export", headers=auth_headers).status_code == 403
