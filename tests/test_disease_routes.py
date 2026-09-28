import io

from app.routes import disease_routes
from app.services.ai.llm_client import AIServiceError

VALID_RESULT = {
    "crop": {"name": "Tomato", "confidence": 80},
    "analysis": {
        "type": "disease",
        "name": "Late Blight",
        "confidence": 65,
        "severity": "medium",
        "assessment_level": "likely",
        "symptoms": ["dark lesions"],
        "possible_causes": ["fungal infection"],
    },
    "recommendations": {
        "immediate_action": ["Remove affected leaves"],
        "treatment": ["Apply fungicide"],
        "prevention": ["Improve air circulation"],
    },
    "additional_observations": [],
    "needs_expert_confirmation": False,
    "_meta": {"model": "gpt-4o", "latency_ms": 100},
}


def test_analyze_requires_auth(client, sample_image_bytes):
    data = {"image": (io.BytesIO(sample_image_bytes), "leaf.jpg")}
    resp = client.post("/api/disease/analyze", data=data, content_type="multipart/form-data")
    assert resp.status_code == 401


def test_analyze_success(client, auth_headers, sample_image_bytes, monkeypatch):
    monkeypatch.setattr(disease_routes, "analyze_crop_image", lambda **kwargs: dict(VALID_RESULT))

    data = {"image": (io.BytesIO(sample_image_bytes), "leaf.jpg"), "crop_name": "Tomato"}
    resp = client.post(
        "/api/disease/analyze", data=data, content_type="multipart/form-data", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["analysis"]["name"] == "Late Blight"


def test_analyze_rejects_invalid_image(client, auth_headers):
    data = {"image": (io.BytesIO(b"not an image"), "leaf.jpg")}
    resp = client.post(
        "/api/disease/analyze", data=data, content_type="multipart/form-data", headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_analyze_handles_ai_timeout(client, auth_headers, sample_image_bytes, monkeypatch):
    def _raise(**kwargs):
        raise AIServiceError("timed out", code="AI_TIMEOUT")

    monkeypatch.setattr(disease_routes, "analyze_crop_image", _raise)

    data = {"image": (io.BytesIO(sample_image_bytes), "leaf.jpg")}
    resp = client.post(
        "/api/disease/analyze", data=data, content_type="multipart/form-data", headers=auth_headers
    )
    assert resp.status_code == 504
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "AI_ANALYSIS_FAILED"


def test_history_empty(client, auth_headers):
    resp = client.get("/api/disease/history", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["items"] == []


def test_history_after_analysis(client, auth_headers, sample_image_bytes, monkeypatch):
    monkeypatch.setattr(disease_routes, "analyze_crop_image", lambda **kwargs: dict(VALID_RESULT))
    data = {"image": (io.BytesIO(sample_image_bytes), "leaf.jpg")}
    client.post("/api/disease/analyze", data=data, content_type="multipart/form-data", headers=auth_headers)

    resp = client.get("/api/disease/history", headers=auth_headers)
    body = resp.get_json()
    assert len(body["data"]["items"]) == 1

    detail_id = body["data"]["items"][0]["id"]
    detail_resp = client.get(f"/api/disease/history/{detail_id}", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.get_json()["data"]["analysis"]["name"] == "Late Blight"


def test_history_detail_not_found(client, auth_headers):
    resp = client.get("/api/disease/history/9999", headers=auth_headers)
    assert resp.status_code == 404


FAKE_WEATHER = {
    "current": {"temperature_c": 27.5, "relative_humidity_pct": 88},
    "daily_forecast": [
        {"date": "d1", "temp_max_c": 30, "rain_sum_mm": 12.0, "precipitation_probability_max_pct": 80},
        {"date": "d2", "temp_max_c": 31, "rain_sum_mm": 30.0, "precipitation_probability_max_pct": 90},
        {"date": "d3", "temp_max_c": 29, "rain_sum_mm": None, "precipitation_sum_mm": 1.5},
    ],
}


def test_analyze_with_location_adds_weather(client, auth_headers, sample_image_bytes, monkeypatch):
    seen = {}

    def fake_analyze(**kwargs):
        seen.update(kwargs)
        return dict(VALID_RESULT)

    monkeypatch.setattr(disease_routes, "analyze_crop_image", fake_analyze)
    monkeypatch.setattr(disease_routes, "fetch_weather", lambda lat, lon: FAKE_WEATHER)

    data = {"image": (io.BytesIO(sample_image_bytes), "leaf.jpg"), "latitude": "19.99", "longitude": "73.79"}
    resp = client.post("/api/disease/analyze", data=data, content_type="multipart/form-data", headers=auth_headers)

    assert resp.status_code == 200
    weather = resp.get_json()["data"]["weather"]
    assert weather["humidity_pct"] == 88
    assert weather["rain_next_3_days_mm"] == 43.5
    assert weather["heavy_rain_alert"] is True
    assert seen["weather"] == weather


def test_analyze_survives_weather_outage(client, auth_headers, sample_image_bytes, monkeypatch):
    from app.services.weather_service import WeatherServiceError

    def boom(lat, lon):
        raise WeatherServiceError("down")

    monkeypatch.setattr(disease_routes, "analyze_crop_image", lambda **kwargs: dict(VALID_RESULT))
    monkeypatch.setattr(disease_routes, "fetch_weather", boom)

    data = {"image": (io.BytesIO(sample_image_bytes), "leaf.jpg"), "latitude": "19.99", "longitude": "73.79"}
    resp = client.post("/api/disease/analyze", data=data, content_type="multipart/form-data", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.get_json()["data"]["weather"] is None


def test_yield_baseline_lookup(client, auth_headers):
    resp = client.get("/api/yield-baseline?crop_name=Tomato", headers=auth_headers)
    data = resp.get_json()["data"]
    assert resp.status_code == 200
    assert data["per_acre_quintal"] == 80.0
    assert "wheat" in data["known_crops"]

    unknown = client.get("/api/yield-baseline?crop_name=Dragonfruit", headers=auth_headers).get_json()["data"]
    assert unknown["per_acre_quintal"] is None


def test_history_crop_filter_treats_input_as_plain_text(client, auth_headers):
    resp = client.get("/api/disease/history?crop_name=(tomato", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["items"] == []


def test_oversized_upload_returns_413(client, auth_headers, app):
    too_big = b"\0" * (app.config["MAX_CONTENT_LENGTH"] + 1)
    resp = client.post(
        "/api/disease/analyze",
        data={"image": (io.BytesIO(too_big), "leaf.jpg")},
        headers=auth_headers,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413
    assert resp.get_json()["error"]["code"] == "FILE_TOO_LARGE"
