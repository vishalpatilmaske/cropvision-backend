from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.services.weather_service import WeatherServiceError, check_weather_thresholds, fetch_weather, geocode_place
from app.utils.responses import error_response, success_response

weather_bp = Blueprint("weather", __name__, url_prefix="/api/weather")


@weather_bp.get("")
@jwt_required()
def get_weather():
    try:
        lat = float(request.args.get("latitude"))
        lon = float(request.args.get("longitude"))
    except (TypeError, ValueError):
        return error_response("VALIDATION_ERROR", "latitude and longitude query params are required.", 400)

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return error_response("VALIDATION_ERROR", "latitude/longitude out of range.", 400)

    try:
        weather = fetch_weather(lat, lon)
    except WeatherServiceError as exc:
        return error_response("WEATHER_SERVICE_FAILED", str(exc), 502)

    threshold_alerts = check_weather_thresholds(weather)
    return success_response({"weather": weather, "alerts": threshold_alerts})


@weather_bp.get("/places")
@jwt_required()
def search_places():
    """Village / town / district search, for farmers who don't share location."""
    name = (request.args.get("name") or "").strip()
    if len(name) < 2:
        return error_response("VALIDATION_ERROR", "Type at least 2 letters.", 400)
    try:
        places = geocode_place(name, count=5)
    except WeatherServiceError:
        return error_response("PLACE_LOOKUP_FAILED", "Place search is unavailable right now.", 502)
    return success_response({"places": places})
