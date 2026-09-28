"""One person, one account: email typos and repeated phone numbers are caught."""
from app.models.user import User
from app.utils.contact_checks import phone_key, suggest_email_fix
from tests.conftest import sign_up


def _register(client, email, phone=None, name="Ramesh"):
    return client.post(
        "/api/auth/otp/request", json={"email": email, "purpose": "register", "name": name, "phone": phone}
    )


def test_suggest_email_fix():
    assert suggest_email_fix("ramesh@gamil.com") == "ramesh@gmail.com"
    assert suggest_email_fix("ramesh@yaho.com") == "ramesh@yahoo.com"
    assert suggest_email_fix("ramesh@gmail.com") is None
    assert suggest_email_fix("ramesh@krishi.co.in") is None  # real domains are left alone


def test_phone_key_matches_all_indian_formats():
    assert phone_key("+91 74105 38585") == phone_key("07410538585") == phone_key("7410538585") == "7410538585"
    assert phone_key("") is None and phone_key(None) is None


def test_signup_with_typo_email_is_stopped_with_suggestion(client):
    resp = _register(client, "ramesh@gamil.com")
    assert resp.status_code == 400
    body = resp.get_json()["error"]
    assert body["code"] == "EMAIL_TYPO"
    assert body["details"]["suggestion"] == "ramesh@gmail.com"


def test_signup_with_used_phone_is_rejected(client):
    sign_up(client, email="first@example.com", phone="7410538585")
    resp = _register(client, "second@example.com", phone="+91 74105 38585")
    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "PHONE_EXISTS"


def test_login_with_typo_suggests_the_real_account(client):
    sign_up(client, email="ramesh@gmail.com")
    resp = client.post("/api/auth/otp/request", json={"email": "ramesh@gamil.com", "purpose": "login"})
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["suggestion"] == "ramesh@gmail.com"


def test_database_rejects_duplicate_phone_even_without_the_route_check(app):
    User.create(name="A", email="a@example.com", phone="7410538585")
    try:
        User.create(name="B", email="b@example.com", phone="07410538585")
        raise AssertionError("duplicate phone was accepted")
    except Exception as exc:  # pymongo DuplicateKeyError
        assert "phone_key" in str(exc)


def test_admin_create_and_update_check_phone_and_typos(client, admin_headers):
    first = client.post("/api/admin/users", json={"name": "A", "email": "a@example.com", "phone": "7410538585"},
                        headers=admin_headers)
    assert first.status_code == 201
    dup_phone = client.post("/api/admin/users", json={"name": "B", "email": "b@example.com", "phone": "917410538585"},
                            headers=admin_headers)
    assert dup_phone.status_code == 409
    typo = client.post("/api/admin/users", json={"name": "C", "email": "c@gmial.com"}, headers=admin_headers)
    assert typo.status_code == 400

    second = client.post("/api/admin/users", json={"name": "B", "email": "b@example.com"}, headers=admin_headers)
    second_id = second.get_json()["data"]["user"]["id"]
    clash = client.put(f"/api/admin/users/{second_id}", json={"phone": "07410538585"}, headers=admin_headers)
    assert clash.status_code == 409
    first_id = first.get_json()["data"]["user"]["id"]
    keep_own = client.put(f"/api/admin/users/{first_id}", json={"phone": "+91 7410538585"}, headers=admin_headers)
    assert keep_own.status_code == 200  # re-saving your own number is fine


def test_newsletter_catches_typos(client):
    resp = client.post("/api/newsletter/subscribe", json={"email": "ramesh@gamil.com"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "EMAIL_TYPO"


def test_old_records_get_phone_key_backfilled(app):
    from app.extensions import mongo

    mongo.db.users.insert_one({"name": "Old", "email": "old@example.com", "phone": "+91 98765 43210"})
    User.ensure_indexes()
    assert mongo.db.users.find_one({"email": "old@example.com"})["phone_key"] == "9876543210"
