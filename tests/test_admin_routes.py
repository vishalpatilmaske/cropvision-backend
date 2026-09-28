def test_admin_login_success(client):
    resp = client.post("/api/admin/login", json={"email": "admin@gmail.com", "password": "123456"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "access_token" in body["data"]


def test_admin_login_wrong_password(client):
    resp = client.post("/api/admin/login", json={"email": "admin@gmail.com", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.get_json()["success"] is False


def test_admin_routes_require_admin_token(client, auth_headers):
    """A regular farmer's valid JWT must not grant admin access."""
    resp = client.get("/api/admin/users", headers=auth_headers)
    assert resp.status_code == 403


def test_admin_routes_require_any_auth(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_admin_can_list_users(client, admin_headers, auth_headers):
    resp = client.get("/api/admin/users", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["pagination"]["total_items"] == 1
    assert body["data"]["items"][0]["email"] == "farmer@example.com"


def test_admin_crud_cycle(client, admin_headers):
    create_resp = client.post(
        "/api/admin/users",
        json={"name": "New Person", "email": "newperson@example.com"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201
    user_id = create_resp.get_json()["data"]["user"]["id"]

    update_resp = client.put(
        f"/api/admin/users/{user_id}", json={"name": "Renamed Person"}, headers=admin_headers
    )
    assert update_resp.status_code == 200
    assert update_resp.get_json()["data"]["user"]["name"] == "Renamed Person"

    get_resp = client.get(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.get_json()["data"]["user"]["name"] == "Renamed Person"

    delete_resp = client.delete(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert delete_resp.status_code == 200

    missing_resp = client.get(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert missing_resp.status_code == 404


def test_admin_create_user_duplicate_email(client, admin_headers, auth_headers):
    resp = client.post(
        "/api/admin/users",
        json={"name": "Dup", "email": "farmer@example.com"},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_admin_stats(client, admin_headers, auth_headers):
    resp = client.get("/api/admin/stats", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["totals"]["users"] == 1
    assert data["totals"]["health_checks"] == 0
    assert data["last_7_days"]["new_users"] == 1
    assert len(data["trend"]["signups"]) == data["trend"]["days"] == 14
    assert data["trend"]["signups"][-1]["count"] == 1  # signed up today


def test_admin_user_search_treats_input_as_plain_text(client, admin_headers, auth_headers):
    """Regex characters in the search box must not cause a server error."""
    resp = client.get("/api/admin/users?search=(farmer", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["pagination"]["total_items"] == 0

    resp = client.get("/api/admin/users?search=farmer@example.com", headers=admin_headers)
    assert resp.get_json()["data"]["pagination"]["total_items"] == 1


def test_admin_rejects_invalid_email(client, admin_headers):
    resp = client.post("/api/admin/users", json={"name": "X", "email": "not-an-email"}, headers=admin_headers)
    assert resp.status_code == 400


def test_timestamps_are_utc_tagged(client, admin_headers, auth_headers):
    user = client.get("/api/admin/users", headers=admin_headers).get_json()["data"]["items"][0]
    assert user["created_at"].endswith("+00:00")
