from datetime import date

import pytest

from app.routes import recommendation_routes
from app.services import weather_service
from app.services.recommendation_service import recommend_crops


def _names(results):
    return [r["name"] for r in results]


def test_black_soil_rabi_favours_rabi_crops():
    top = _names(recommend_crops("black", 120, 24, "rabi", irrigation="limited")[:5])
    assert "chickpea" in top and "wheat" in top
    assert "rice" not in top


def test_heavy_monsoon_on_clay_favours_rice():
    assert recommend_crops("clay", 1100, 28, "kharif", irrigation="none")[0]["name"] == "rice"


def test_dry_sandy_kharif_favours_millets_and_pulses():
    top = _names(recommend_crops("sandy", 300, 31, "kharif", irrigation="none")[:3])
    assert "pearl millet" in top


def test_irrigation_closes_water_gap():
    rainfed = {r["name"]: r for r in recommend_crops("loamy", 100, 20, "rabi", irrigation="none", limit=20)}
    irrigated = {r["name"]: r for r in recommend_crops("loamy", 100, 20, "rabi", irrigation="full", limit=20)}
    assert irrigated["wheat"]["suitability_score"] > rainfed["wheat"]["suitability_score"]
    assert "Needs more irrigation than you have." in rainfed["wheat"]["warnings"]


def test_waterlogging_warning_for_dryland_crop():
    chickpea = {r["name"]: r for r in recommend_crops("black", 1200, 20, "rabi", limit=20)}["chickpea"]
    assert any("waterlogging" in w for w in chickpea["warnings"])


def test_missing_inputs_are_left_out_not_penalised():
    result = recommend_crops("black", None, None, "rabi", limit=20)
    wheat = next(r for r in result if r["name"] == "wheat")
    assert {f["factor"] for f in wheat["factors"]} == {"season", "soil"}
    assert wheat["suitability_score"] == 100


def test_result_shape():
    crop = recommend_crops("loamy", 500, 22, "rabi", irrigation="limited")[0]
    for key in ("label", "fit", "factors", "warnings", "reason", "water_need_mm", "duration_days"):
        assert key in crop


def test_preview_does_not_save(client, auth_headers):
    body = {"soil_type": "black", "season": "rabi", "rainfall_mm": 120, "avg_temp_c": 24, "irrigation": "limited"}
    preview = client.post("/api/crop-recommendation", json={**body, "save": False}, headers=auth_headers)
    assert preview.status_code == 200
    assert "id" not in preview.get_json()["data"]

    history = client.get("/api/crop-recommendation/history", headers=auth_headers).get_json()["data"]
    assert history["pagination"]["total_items"] == 0

    saved = client.post("/api/crop-recommendation", json=body, headers=auth_headers)
    assert "id" in saved.get_json()["data"]


def test_bad_numbers_rejected(client, auth_headers):
    resp = client.post("/api/crop-recommendation", json={"rainfall_mm": "lots"}, headers=auth_headers)
    assert resp.status_code == 400


def test_crop_conditions_uses_climate(client, auth_headers, monkeypatch):
    fake = {"season": "rabi", "window": {}, "rainfall_mm": 95, "avg_temp_c": 21.4, "source": "test"}
    monkeypatch.setattr(recommendation_routes, "fetch_season_climate", lambda lat, lon, season: fake)
    data = client.get(
        "/api/crop-conditions?latitude=19.99&longitude=73.79&season=rabi", headers=auth_headers
    ).get_json()["data"]
    assert data["season"] == "rabi"
    assert data["climate"]["rainfall_mm"] == 95


def test_crop_conditions_without_location(client, auth_headers):
    data = client.get("/api/crop-conditions?season=kharif", headers=auth_headers).get_json()["data"]
    assert data["climate"] is None
    assert data["suggested_season"] in {"kharif", "rabi", "zaid"}


def test_crop_conditions_rejects_bad_season(client, auth_headers):
    assert client.get("/api/crop-conditions?season=winter", headers=auth_headers).status_code == 400


@pytest.mark.parametrize(
    "today, expected",
    [(date(2026, 9, 27), "rabi"), (date(2026, 5, 10), "kharif"), (date(2026, 1, 15), "zaid")],
)
def test_upcoming_sowing_season(today, expected):
    assert weather_service.upcoming_sowing_season(today) == expected


def test_last_completed_rabi_window_crosses_year():
    start, end = weather_service._last_completed_window("rabi", date(2026, 9, 27))
    assert (start, end) == (date(2025, 11, 1), date(2026, 3, 31))


def test_kharif_window_uses_previous_year_until_season_ends():
    start, end = weather_service._last_completed_window("kharif", date(2026, 9, 27))
    assert (start, end) == (date(2025, 6, 1), date(2025, 10, 31))


def test_rainfed_rabi_on_black_soil_uses_stored_moisture():
    # Almost no winter rain (Nashik rabi): stored soil moisture should still favour chickpea.
    ranked = recommend_crops("black", 5, 22.7, "rabi", irrigation="none", limit=20)
    names = _names(ranked)
    assert names[0] == "chickpea"
    assert names.index("chickpea") < names.index("wheat") < names.index("sugarcane")
    assert len({r["suitability_score"] for r in ranked[:4]}) > 1


def test_ties_prefer_season_specialists():
    top = _names(recommend_crops("black", 5, 22.7, "rabi", irrigation="full")[:3])
    assert top[0] in {"wheat", "chickpea"}
    assert "sugarcane" not in top


def test_rain_only_demotes_irrigation_crops():
    ranked = {r["name"]: r for r in recommend_crops("black", 5, 22.7, "rabi", irrigation="none", limit=20)}
    assert ranked["onion"]["suitability_score"] < ranked["sorghum"]["suitability_score"]
    assert any("reliable water source" in w for w in ranked["onion"]["warnings"])
