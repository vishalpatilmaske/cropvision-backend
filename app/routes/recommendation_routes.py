from datetime import datetime, timedelta, timezone

from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.models.disease_pest_prediction import DiseasePestPrediction
from app.models.recommendation import (
    CropRecommendation,
    FertilizerRecommendation,
    IrrigationRecommendation,
    YieldPrediction,
)
from app.services.irrigation_service import build_irrigation_advisory, build_irrigation_plan
from app.services.recommendation_service import recommend_crops, recommend_fertilizer
from app.services.weather_service import (
    SEASON_WINDOWS,
    WeatherServiceError,
    check_weather_thresholds,
    fetch_season_climate,
    fetch_weather,
    upcoming_sowing_season,
)
from app.services.yield_service import estimate_yield_detailed, get_baseline_yield, known_crops
from app.utils.pagination import get_pagination_params, paginated_response
from app.utils.responses import error_response, success_response

recommendation_bp = Blueprint("recommendations", __name__, url_prefix="/api")


@recommendation_bp.post("/crop-recommendation")
@jwt_required()
def crop_recommendation():
    user_id = get_jwt_identity()
    payload = request.get_json(silent=True) or {}

    inputs = {
        "soil_type": payload.get("soil_type"),
        "rainfall_mm": payload.get("rainfall_mm"),
        "avg_temp_c": payload.get("avg_temp_c"),
        "season": payload.get("season"),
        "nitrogen": payload.get("nitrogen"),
        "phosphorus": payload.get("phosphorus"),
        "potassium": payload.get("potassium"),
        "irrigation": payload.get("irrigation"),
    }
    try:
        recommended = recommend_crops(**inputs)
    except (TypeError, ValueError):
        return error_response("VALIDATION_ERROR", "rainfall_mm and avg_temp_c must be numbers.", 400)

    # "save": false gives a live preview (the page re-ranks as the farmer
    # changes a choice) without filling their history with every tweak.
    if payload.get("save") is False:
        return success_response({"inputs": inputs, "recommended_crops": recommended})

    record = CropRecommendation.create(
        user_id=user_id, farm_id=payload.get("farm_id"), inputs=inputs, recommended_crops=recommended
    )
    return success_response(record.to_dict(), "Crop recommendation generated.")


@recommendation_bp.get("/crop-conditions")
@jwt_required()
def crop_conditions():
    """Season rain + temperature at the farmer's location, from last year's
    actual weather, so they don't have to know these numbers."""
    suggested = upcoming_sowing_season()
    season = (request.args.get("season") or suggested).strip().lower()
    if season not in SEASON_WINDOWS:
        return error_response("VALIDATION_ERROR", "season must be kharif, rabi or zaid.", 400)
    try:
        lat = float(request.args.get("latitude"))
        lon = float(request.args.get("longitude"))
    except (TypeError, ValueError):
        return success_response({"season": season, "suggested_season": suggested, "climate": None})
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error_response("VALIDATION_ERROR", "latitude/longitude out of range.", 400)

    try:
        climate = fetch_season_climate(lat, lon, season)
    except WeatherServiceError:
        climate = None
    return success_response({"season": season, "suggested_season": suggested, "climate": climate})


@recommendation_bp.get("/crop-recommendation/history")
@jwt_required()
def crop_recommendation_history():
    user_id = get_jwt_identity()
    page, per_page = get_pagination_params()
    items, total = CropRecommendation.find_paginated(user_id=user_id, page=page, per_page=per_page)
    return success_response(paginated_response(items, total, page, per_page, lambda item: item.to_dict()))


@recommendation_bp.post("/fertilizer-recommendation")
@jwt_required()
def fertilizer_recommendation():
    user_id = get_jwt_identity()
    payload = request.get_json(silent=True) or {}

    crop_name = (payload.get("crop_name") or "").strip()
    if not crop_name:
        return error_response("VALIDATION_ERROR", "crop_name is required.", 400)

    inputs = {
        "crop_name": crop_name,
        "soil_n": payload.get("soil_n"),
        "soil_p": payload.get("soil_p"),
        "soil_k": payload.get("soil_k"),
        "growth_stage": payload.get("growth_stage"),
        "area_acres": payload.get("area_acres"),
    }
    try:
        recommendation = recommend_fertilizer(**inputs)
    except (TypeError, ValueError):
        return error_response("VALIDATION_ERROR", "Soil test values and area must be numbers.", 400)

    if payload.get("save") is False:
        return success_response({"inputs": inputs, "recommendation": recommendation})

    record = FertilizerRecommendation.create(
        user_id=user_id, farm_id=payload.get("farm_id"), crop_id=payload.get("crop_id"),
        inputs=inputs, recommendation=recommendation,
    )
    return success_response(record.to_dict(), "Fertilizer plan saved.")


@recommendation_bp.post("/irrigation-recommendation")
@jwt_required()
def irrigation_recommendation():
    user_id = get_jwt_identity()
    payload = request.get_json(silent=True) or {}

    try:
        lat = float(payload.get("latitude"))
        lon = float(payload.get("longitude"))
    except (TypeError, ValueError):
        return error_response("VALIDATION_ERROR", "latitude and longitude are required.", 400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error_response("VALIDATION_ERROR", "latitude/longitude out of range.", 400)

    try:
        weather = fetch_weather(lat, lon)
    except WeatherServiceError as exc:
        return error_response("WEATHER_SERVICE_FAILED", str(exc), 502)

    threshold_alerts = check_weather_thresholds(weather)
    advisory = build_irrigation_advisory(weather, threshold_alerts)
    plan = build_irrigation_plan(
        weather,
        crop=payload.get("crop_name"),
        stage=payload.get("stage"),
        method=payload.get("method"),
        area_acres=payload.get("area_acres"),
    )
    # The water-balance plan decides today's action; keep the legacy fields in step with it.
    if plan["today"]:
        advisory["should_irrigate"] = plan["today"]["action"] == "irrigate"
        advisory["reason"] = plan["today"]["note"]

    body = {
        "advisory": advisory,
        "plan": plan,
        "current": weather.get("current"),
        "soil_snapshot": weather.get("soil"),
    }
    if payload.get("save") is False:
        return success_response(body)

    record = IrrigationRecommendation.create(
        user_id=user_id, farm_id=payload.get("farm_id"), crop_id=payload.get("crop_id"),
        weather_snapshot=weather, soil_snapshot=weather.get("soil"), advisory=advisory, plan=plan,
    )
    return success_response({**record.to_dict(), "current": weather.get("current")}, "Irrigation plan saved.")


@recommendation_bp.post("/yield-prediction")
@jwt_required()
def yield_prediction():
    user_id = get_jwt_identity()
    payload = request.get_json(silent=True) or {}

    crop_name = (payload.get("crop_name") or "").strip()
    try:
        area_acres = float(payload.get("area_acres"))
        rainfall_mm = payload.get("rainfall_mm")
        rainfall_mm = float(rainfall_mm) if rainfall_mm not in (None, "") else None
    except (TypeError, ValueError):
        return error_response("VALIDATION_ERROR", "crop_name and area_acres are required.", 400)
    if not crop_name:
        return error_response("VALIDATION_ERROR", "crop_name is required.", 400)

    # Use the farmer's latest health check for this crop (last 60 days), if asked.
    health_check = None
    if payload.get("use_health_check"):
        items, _ = DiseasePestPrediction.find_paginated(user_id=user_id, page=1, per_page=1, crop_name=crop_name)
        if items:
            latest = items[0].to_dict()
            created = latest["created_at"]
            recent = created and datetime.fromisoformat(created) > (
                datetime.now(timezone.utc) - timedelta(days=60)
            )
            score = (latest.get("report") or {}).get("health_score")
            if recent and score is not None:
                health_check = {
                    "id": latest["id"],
                    "date": created,
                    "health_score": score,
                    "condition": latest["analysis"]["name"] or latest["analysis"]["type"],
                }

    inputs = {
        "crop_name": crop_name,
        "area_acres": area_acres,
        "soil_type": payload.get("soil_type"),
        "rainfall_mm": rainfall_mm,
        "irrigation": payload.get("irrigation"),
        "management": payload.get("management"),
        "season": payload.get("season"),
        "health_score": health_check["health_score"] if health_check else None,
    }
    estimate = estimate_yield_detailed(**inputs)
    estimate["health_check"] = health_check

    if payload.get("save") is False:
        return success_response({"inputs": inputs, "estimated_yield": estimate})

    record = YieldPrediction.create(
        user_id=user_id, farm_id=payload.get("farm_id"), crop_id=payload.get("crop_id"),
        inputs=inputs, estimated_yield=estimate,
    )
    return success_response(record.to_dict(), "Yield estimate saved.")


@recommendation_bp.get("/yield-baseline")
@jwt_required()
def yield_baseline():
    """Read-only lookup (nothing is saved): average quintals/acre for a crop,
    used by the 3D Digital Twin to turn yield loss % into quintals and rupees."""
    crop_name = (request.args.get("crop_name") or "").strip()
    return success_response({
        "crop_name": crop_name or None,
        "per_acre_quintal": get_baseline_yield(crop_name),
        "known_crops": known_crops(),
    })


def _history(model):
    user_id = get_jwt_identity()
    page, per_page = get_pagination_params()
    items, total = model.find_paginated(user_id=user_id, page=page, per_page=per_page)
    return success_response(paginated_response(items, total, page, per_page, lambda item: item.to_dict()))


@recommendation_bp.get("/fertilizer-recommendation/history")
@jwt_required()
def fertilizer_history():
    return _history(FertilizerRecommendation)


@recommendation_bp.get("/irrigation-recommendation/history")
@jwt_required()
def irrigation_history():
    return _history(IrrigationRecommendation)


@recommendation_bp.get("/yield-prediction/history")
@jwt_required()
def yield_history():
    return _history(YieldPrediction)
