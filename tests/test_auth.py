from app.extensions import mongo
from app.services import email_service
from tests.conftest import last_code, sign_up

EMAIL = "alice@example.com"


def _request(client, purpose="register", email=EMAIL, **extra):
    return client.post("/api/auth/otp/request", json={"email": email, "purpose": purpose, **extra})


def _verify(client, code, purpose="register", email=EMAIL):
    return client.post("/api/auth/otp/verify", json={"email": email, "purpose": purpose, "code": code})


def test_sign_up_with_emailed_code(client):
    resp = _request(client, name="Alice", phone="+91 90000 00000")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["expires_in_seconds"] == 600

    email = email_service.outbox[-1]
    assert email["to"] == EMAIL
    code = last_code(EMAIL)
    assert code in email["subject"] and code in email["html"]

    resp = _verify(client, code)
    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert data["user"]["name"] == "Alice"
    assert data["user"]["phone"] == "+91 90000 00000"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.get_json()["data"]["user"]["email"] == EMAIL


def test_sign_in_with_emailed_code(client):
    sign_up(client, email=EMAIL, name="Alice")
    assert _request(client, purpose="login").status_code == 200
    resp = _verify(client, last_code(EMAIL), purpose="login")
    assert resp.status_code == 200
    assert "access_token" in resp.get_json()["data"]


def test_code_is_not_stored_in_plain_text(client):
    _request(client, name="Alice")
    doc = mongo.db.otp_codes.find_one({"email": EMAIL})
    assert last_code(EMAIL) not in str(doc)


def test_code_is_single_use(client):
    _request(client, name="Alice")
    code = last_code(EMAIL)
    assert _verify(client, code).status_code == 201
    assert _verify(client, code).get_json()["error"]["code"] == "OTP_EXPIRED"


def test_login_needs_an_account(client):
    resp = _request(client, purpose="login", email="nobody@example.com")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert email_service.outbox == []


def test_sign_up_rejects_existing_email(client):
    sign_up(client, email=EMAIL, name="Alice")
    resp = _request(client, name="Alice again")
    assert resp.status_code == 409


def test_sign_up_needs_name_and_valid_email(client):
    assert _request(client).status_code == 400
    assert _request(client, email="not-an-email", name="A").status_code == 400
    assert _request(client, purpose="hack", name="A").status_code == 400


def test_wrong_code_counts_attempts_then_locks(client):
    _request(client, name="Alice")
    real = last_code(EMAIL)
    wrong = "000000" if real != "000000" else "111111"

    first = _verify(client, wrong)
    assert first.status_code == 400
    assert first.get_json()["error"]["details"]["attempts_left"] == 4

    for _ in range(3):
        _verify(client, wrong)
    locked = _verify(client, wrong)
    assert locked.status_code == 429
    assert locked.get_json()["error"]["code"] == "OTP_TOO_MANY_ATTEMPTS"

    # Even the right code no longer works -- a new one must be requested.
    assert _verify(client, real).get_json()["error"]["code"] == "OTP_EXPIRED"


def test_expired_code_rejected(client):
    _request(client, name="Alice")
    mongo.db.otp_codes.update_one(
        {"email": EMAIL}, [{"$set": {"expires_at": {"$subtract": ["$expires_at", 11 * 60 * 1000]}}}]
    )
    assert _verify(client, last_code(EMAIL)).get_json()["error"]["code"] == "OTP_EXPIRED"


def test_resend_has_cooldown(client):
    _request(client, name="Alice")
    resp = _request(client, name="Alice")
    assert resp.status_code == 429
    assert resp.get_json()["error"]["details"]["retry_after_seconds"] > 0

    # After the cooldown a new code replaces the old one.
    mongo.db.otp_codes.update_one(
        {"email": EMAIL}, [{"$set": {"created_at": {"$subtract": ["$created_at", 61 * 1000]}}}]
    )
    old = last_code(EMAIL)
    assert _request(client, name="Alice").status_code == 200
    new = last_code(EMAIL)
    if new != old:
        assert _verify(client, old).status_code == 400
    assert _verify(client, new).status_code == 201


def test_malformed_code_rejected(client):
    _request(client, name="Alice")
    assert _verify(client, "12ab").status_code == 400


def test_password_endpoints_removed(client):
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": "x"}).status_code == 404
    assert client.post("/api/auth/register", json={"email": EMAIL, "password": "x"}).status_code == 404


def test_me_requires_auth(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_signup_email_escapes_name(client):
    from app.services import email_service

    client.post(
        "/api/auth/otp/request",
        json={"email": "html@example.com", "purpose": "register", "name": "<b>Ramesh</b>"},
    )
    html = email_service.outbox[-1]["html"]
    assert "<b>Ramesh</b>" not in html
    assert "&lt;b&gt;Ramesh&lt;/b&gt;" in html
