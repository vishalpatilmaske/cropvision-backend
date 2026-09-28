"""Admin dashboard: stats with data, user activity, CSV export, health checks."""
import io

import pytest

from app.routes import disease_routes
from tests.conftest import sign_up
from tests.test_disease_routes import VALID_RESULT


def _analyze(client, headers, image_bytes, result=None):
    data = {"image": (io.BytesIO(image_bytes), "leaf.jpg")}
    resp = client.post("/api/disease/analyze", data=data, content_type="multipart/form-data", headers=headers)
    assert resp.status_code == 200
    return resp.get_json()["data"]["id"]


@pytest.fixture()
def farmer_with_checks(client, auth_headers, sample_image_bytes, monkeypatch):
    healthy = {**VALID_RESULT, "crop": {"name": "wheat", "confidence": 90},
               "analysis": {**VALID_RESULT["analysis"], "type": "healthy", "name": None, "severity": "none"}}
    results = iter([dict(VALID_RESULT), dict(VALID_RESULT), healthy])
    monkeypatch.setattr(disease_routes, "analyze_crop_image", lambda **kwargs: next(results))
    ids = [_analyze(client, auth_headers, sample_image_bytes) for _ in range(3)]
    return ids


def test_stats_summarise_health_checks(client, admin_headers, farmer_with_checks):
    data = client.get("/api/admin/stats", headers=admin_headers).get_json()["data"]
    assert data["totals"]["health_checks"] == 3
    assert data["health_checks_by_type"] == {"disease": 2, "healthy": 1}
    assert data["top_conditions"][0] == {"name": "Late Blight", "count": 2}
    assert data["top_crops"][0] == {"name": "Tomato", "count": 2}
    assert data["trend"]["health_checks"][-1]["count"] == 3


def test_user_list_includes_activity_and_sorts(client, admin_headers, farmer_with_checks):
    sign_up(client, email="aaron@example.com", name="Aaron")
    items = client.get("/api/admin/users?sort=name", headers=admin_headers).get_json()["data"]["items"]
    assert [u["name"] for u in items] == ["Aaron", "Test Farmer"]
    farmer = items[1]
    assert farmer["health_checks"] == 3
    assert farmer["last_active"]
    assert items[0]["health_checks"] == 0


def test_user_detail_has_activity(client, admin_headers, farmer_with_checks):
    user_id = client.get("/api/admin/users", headers=admin_headers).get_json()["data"]["items"][0]["id"]
    data = client.get(f"/api/admin/users/{user_id}", headers=admin_headers).get_json()["data"]
    assert data["user"]["id"] == user_id
    assert data["activity"]["counts"]["health_checks"] == 3
    assert len(data["activity"]["recent_health_checks"]) == 3


def test_export_users_csv(client, admin_headers, farmer_with_checks):
    resp = client.get("/api/admin/users/export", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0].startswith("Name,Email")
    assert "farmer@example.com" in lines[1] and ",3," in lines[1]


def test_health_checks_list_filter_detail_delete(client, admin_headers, farmer_with_checks):
    body = client.get("/api/admin/health-checks", headers=admin_headers).get_json()["data"]
    assert body["pagination"]["total_items"] == 3
    assert body["items"][0]["user"]["email"] == "farmer@example.com"

    healthy = client.get("/api/admin/health-checks?analysis_type=healthy", headers=admin_headers).get_json()["data"]
    assert healthy["pagination"]["total_items"] == 1
    crops = client.get("/api/admin/health-checks?crop_name=tomato", headers=admin_headers).get_json()["data"]
    assert crops["pagination"]["total_items"] == 2

    check_id = farmer_with_checks[0]
    detail = client.get(f"/api/admin/health-checks/{check_id}", headers=admin_headers).get_json()["data"]
    assert detail["analysis"]["name"] == "Late Blight"
    assert detail["user"]["name"] == "Test Farmer"

    assert client.delete(f"/api/admin/health-checks/{check_id}", headers=admin_headers).status_code == 200
    assert client.get(f"/api/admin/health-checks/{check_id}", headers=admin_headers).status_code == 404
    assert client.delete("/api/admin/health-checks/not-an-id", headers=admin_headers).status_code == 404


@pytest.mark.parametrize("path", [
    "/api/admin/stats", "/api/admin/users/export", "/api/admin/health-checks",
])
def test_farmer_token_cannot_use_admin_endpoints(client, auth_headers, path):
    assert client.get(path, headers=auth_headers).status_code == 403
