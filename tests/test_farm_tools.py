"""Fertilizer, irrigation and yield engines + their routes and history."""
from app.models.disease_pest_prediction import DiseasePestPrediction
from app.routes import recommendation_routes
from app.services.irrigation_service import build_irrigation_plan, irrigation_crops
from app.services.recommendation_service import crop_names, fertilizer_crops, recommend_fertilizer
from app.services.yield_service import estimate_yield_detailed, known_crops


def test_every_crop_is_covered_by_every_tool():
    crops = set(crop_names())
    assert set(fertilizer_crops()) == crops
    assert set(irrigation_crops()) == crops
    assert set(known_crops()) == crops


# --- Fertilizer ---------------------------------------------------------

def _products(plan):
    return {item["product"]: item for item in plan["shopping_list"]}


def test_wheat_plan_is_realistic_and_scales_with_area():
    one = recommend_fertilizer("wheat", None, None, None, area_acres=1)
    two = recommend_fertilizer("wheat", None, None, None, area_acres=2)
    p1 = _products(one)
    # 120:60:40 kg/ha -> ~85 kg urea, ~53 kg DAP, ~27 kg MOP per acre.
    assert 80 <= p1["urea"]["kg_total"] <= 90
    assert 50 <= p1["dap"]["kg_total"] <= 56
    assert 25 <= p1["mop"]["kg_total"] <= 29
    assert _products(two)["dap"]["kg_total"] == round(p1["dap"]["kg_total"] * 2, 1)
    assert [s["stage"] for s in one["schedule"]][0].startswith("At sowing")
    assert len(one["schedule"]) == 3


def test_pulses_get_everything_at_sowing():
    plan = recommend_fertilizer("chickpea", None, None, None)
    assert len(plan["schedule"]) == 1


def test_soil_test_ratings_adjust_dose():
    base = recommend_fertilizer("maize", None, None, None)["nutrients_kg_per_acre"]
    tested = recommend_fertilizer("maize", 200, 30, 150)
    assert tested["soil"]["n"]["rating"] == "low"
    assert tested["soil"]["p"]["rating"] == "high"
    assert tested["soil"]["k"]["rating"] == "medium"
    n = tested["nutrients_kg_per_acre"]
    assert abs(n["n"] - base["n"] * 1.25) < 0.2
    assert abs(n["p2o5"] - base["p2o5"] * 0.75) < 0.2
    assert n["k2o"] == base["k2o"]


def test_bags_breakdown():
    urea = _products(recommend_fertilizer("wheat", None, None, None, area_acres=2))["urea"]
    assert urea["bags"] * 45 + urea["loose_kg"] == round(urea["kg_total"]) or urea["loose_kg"] == 0


def test_unknown_crop_fertilizer():
    assert recommend_fertilizer("dragonfruit", None, None, None)["fertilizer_plan"] == []


def test_fertilizer_preview_and_history(client, auth_headers):
    body = {"crop_name": "wheat", "area_acres": 2}
    preview = client.post("/api/fertilizer-recommendation", json={**body, "save": False}, headers=auth_headers)
    assert preview.status_code == 200 and "id" not in preview.get_json()["data"]
    client.post("/api/fertilizer-recommendation", json=body, headers=auth_headers)
    history = client.get("/api/fertilizer-recommendation/history", headers=auth_headers).get_json()["data"]
    assert history["pagination"]["total_items"] == 1


# --- Irrigation ---------------------------------------------------------

def _weather(rain=(0, 0, 0, 0, 0, 0, 0), et0=5.0, moisture=0.2):
    return {
        "soil": {"moisture_0_1cm_m3m3": moisture},
        "daily_forecast": [
            {"date": f"2026-10-0{i + 1}", "rain_sum_mm": r, "et0_fao_evapotranspiration_mm": et0,
             "precipitation_probability_max_pct": 90 if r else 5, "temp_max_c": 30}
            for i, r in enumerate(rain)
        ],
    }


def test_drip_waters_more_often_but_less_than_flood():
    drip = build_irrigation_plan(_weather(), "tomato", "mid", "drip")
    flood = build_irrigation_plan(_weather(), "tomato", "mid", "flood")
    assert drip["irrigations_this_week"] > flood["irrigations_this_week"]
    drip_days = [d for d in drip["days"] if d["action"] == "irrigate"]
    assert max(d["gross_mm"] for d in drip_days) < 20


def test_rain_counts_as_watering():
    plan = build_irrigation_plan(_weather(rain=(20, 0, 0, 0, 0, 0, 0)), "wheat", "mid", "flood")
    assert plan["days"][0]["action"] == "rain"


def test_waits_when_rain_is_due_tomorrow():
    plan = build_irrigation_plan(_weather(rain=(0, 30, 0, 0, 0, 0, 0), moisture=0.1), "wheat", "mid", "sprinkler")
    assert plan["days"][0]["action"] == "wait"


def test_crop_stage_changes_water_use():
    early = build_irrigation_plan(_weather(), "wheat", "initial", "flood")
    peak = build_irrigation_plan(_weather(), "wheat", "mid", "flood")
    assert early["week_crop_water_use_mm"] < peak["week_crop_water_use_mm"]


def test_litres_scale_with_area():
    plan = build_irrigation_plan(_weather(moisture=0.1), "tomato", "mid", "drip", area_acres=2)
    day = next(d for d in plan["days"] if d["action"] == "irrigate")
    assert abs(day["litres_total"] - day["gross_mm"] * 4047 * 2) <= 500
    assert abs(day["litres_total"] - 2 * day["litres_per_acre"]) <= 200


def test_irrigation_route_preview_and_history(client, auth_headers, monkeypatch):
    monkeypatch.setattr(recommendation_routes, "fetch_weather", lambda lat, lon: _weather(moisture=0.1))
    body = {"latitude": 19.99, "longitude": 73.79, "crop_name": "tomato", "stage": "mid", "method": "drip"}
    preview = client.post("/api/irrigation-recommendation", json={**body, "save": False}, headers=auth_headers)
    data = preview.get_json()["data"]
    assert data["plan"]["method"] == "drip"
    assert data["advisory"]["should_irrigate"] == (data["plan"]["today"]["action"] == "irrigate")

    client.post("/api/irrigation-recommendation", json=body, headers=auth_headers)
    history = client.get("/api/irrigation-recommendation/history", headers=auth_headers).get_json()["data"]
    assert history["items"][0]["plan"]["crop"] == "tomato"


# --- Yield --------------------------------------------------------------

def test_yield_factors_multiply():
    r = estimate_yield_detailed("wheat", 2, "black", 5, "none", "average", 50, season="rabi")
    expected = r["baseline_per_acre"]
    for f in r["factors"]:
        expected *= f["multiplier"]
    assert r["per_acre"] == round(expected, 1)
    assert {f["factor"] for f in r["factors"]} == {"soil", "water", "care", "health"}


def test_yield_more_water_more_grain():
    dry = estimate_yield_detailed("rice", 1, "clay", 300, "none", "good")
    wet = estimate_yield_detailed("rice", 1, "clay", 300, "full", "good")
    assert wet["value"] > dry["value"]


def test_yield_uses_latest_health_check(client, auth_headers):
    me = client.get("/api/auth/me", headers=auth_headers).get_json()["data"]["user"]
    DiseasePestPrediction.create(
        user_id=me["id"], crop_name="Wheat", analysis_type="disease", condition_name="Rust",
        report={"health_score": 60},
    )
    body = {"crop_name": "wheat", "area_acres": 1, "use_health_check": True, "save": False}
    data = client.post("/api/yield-prediction", json=body, headers=auth_headers).get_json()["data"]
    estimate = data["estimated_yield"]
    assert estimate["health_check"]["health_score"] == 60
    assert any(f["factor"] == "health" for f in estimate["factors"])


def test_yield_history(client, auth_headers):
    client.post("/api/yield-prediction", json={"crop_name": "wheat", "area_acres": 1}, headers=auth_headers)
    history = client.get("/api/yield-prediction/history", headers=auth_headers).get_json()["data"]
    assert history["pagination"]["total_items"] == 1


def test_place_search(client, auth_headers, monkeypatch):
    from app.routes import weather_routes

    monkeypatch.setattr(
        weather_routes, "geocode_place",
        lambda name, count=5: [{"name": "Nashik", "district": None, "state": "Maharashtra", "country": "India",
                                "latitude": 20.0, "longitude": 73.8}],
    )
    data = client.get("/api/weather/places?name=nash", headers=auth_headers).get_json()["data"]
    assert data["places"][0]["name"] == "Nashik"
    assert client.get("/api/weather/places?name=n", headers=auth_headers).status_code == 400
