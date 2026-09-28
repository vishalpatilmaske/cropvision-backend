"""Routes backing the standalone analyzer's "Field Advisory" (text-only) and
"Krishi Panchayat" (multi-persona council) modes. Both are additive AI
features layered on the existing weather + vision-AI services; neither
persists to the database (the standalone site keeps its own client-side
history), matching the scope of the standalone prototype they serve.
"""
import logging
import time
import uuid

from flask import Blueprint, current_app, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from werkzeug.exceptions import HTTPException

from app.extensions import limiter
from app.services.ai.advisory_service import InvalidAIResponseError as AdvisoryInvalidResponseError
from app.services.ai.advisory_service import generate_field_advisory
from app.services.ai.llm_client import AIServiceError
from app.services.ai.panchayat_service import InvalidAIResponseError as PanchayatInvalidResponseError
from app.services.ai.panchayat_service import analyze_panchayat
from app.services.weather_service import WeatherServiceError, check_weather_thresholds, fetch_weather
from app.utils.image_validation import ImageValidationError, resize_for_analysis, validate_and_load_image
from app.utils.responses import error_response, success_response

logger = logging.getLogger("cropvision.routes.advisory")

advisory_bp = Blueprint("advisory", __name__, url_prefix="/api/advisory")

_AI_ERROR_STATUS = {
    "AI_NOT_CONFIGURED": 503,
    "AI_TIMEOUT": 504,
    "AI_RATE_LIMITED": 429,
    "AI_AUTH_FAILED": 502,
    "AI_UNAVAILABLE": 502,
    "AI_EMPTY_RESPONSE": 502,
}

_AI_ERROR_MESSAGES = {
    "AI_NOT_CONFIGURED": "Advisory generation is temporarily unavailable. Please try again later.",
    "AI_TIMEOUT": "The advisory took too long to generate. Please try again.",
    "AI_RATE_LIMITED": "Too many requests right now. Please try again in a minute.",
    "AI_AUTH_FAILED": "Advisory generation is temporarily unavailable. Please try again later.",
    "AI_UNAVAILABLE": "Unable to generate an advisory right now. Please try again shortly.",
    "AI_EMPTY_RESPONSE": "The advisory did not return a result. Please try again.",
}


def _parse_lat_lon(source):
    try:
        lat = float(source.get("latitude"))
        lon = float(source.get("longitude"))
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None
    return lat, lon


def _fetch_weather_safely(lat, lon):
    if lat is None or lon is None:
        return {}, {}
    try:
        weather = fetch_weather(lat, lon)
    except WeatherServiceError as exc:
        logger.warning("Weather fetch failed for advisory request: %s", exc)
        return {}, {}
    return weather, check_weather_thresholds(weather)


@advisory_bp.post("/field")
@jwt_required()
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_ANALYZE", "10 per minute"))
def field_advisory():
    request_id = uuid.uuid4().hex[:12]
    start = time.monotonic()

    payload = request.get_json(silent=True) or {}
    crop_name = (payload.get("crop_name") or "").strip() or None
    language = (payload.get("language") or "english").strip()
    user_notes = (payload.get("user_notes") or "").strip() or None

    lat, lon = _parse_lat_lon(payload)
    if lat is None:
        return error_response("VALIDATION_ERROR", "Valid latitude and longitude are required.", 400)

    try:
        weather = fetch_weather(lat, lon)
    except WeatherServiceError as exc:
        return error_response("WEATHER_SERVICE_FAILED", str(exc), 502)

    threshold_alerts = check_weather_thresholds(weather)

    try:
        advisory_text = generate_field_advisory(crop_name, user_notes, weather)
    except AIServiceError as exc:
        logger.warning("[%s] AI service error: %s", request_id, exc.code)
        status = _AI_ERROR_STATUS.get(exc.code, 502)
        message = _AI_ERROR_MESSAGES.get(exc.code, "Unable to generate the advisory.")
        return error_response("AI_ADVISORY_FAILED", message, status)
    except AdvisoryInvalidResponseError:
        logger.error("[%s] Advisory AI returned invalid/unparseable response.", request_id)
        return error_response(
            "AI_ADVISORY_FAILED",
            "Couldn't generate a reliable advisory from the notes given. Please add a bit more detail.",
            502,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info("[%s] Field advisory generated duration_ms=%s", request_id, duration_ms)

    return success_response(
        {
            "crop_name": crop_name,
            "language": language,
            "location": {"latitude": lat, "longitude": lon},
            "weather": weather,
            "threshold_alerts": threshold_alerts,
            "advisory": advisory_text,
            "user_notes": user_notes,
        },
        "Field advisory generated successfully.",
    )


@advisory_bp.post("/panchayat")
@jwt_required()
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_ANALYZE", "10 per minute"))
def panchayat_advisory():
    request_id = uuid.uuid4().hex[:12]
    user_id = get_jwt_identity()
    start = time.monotonic()

    try:
        file_storage = request.files.get("image")
        try:
            raw_bytes, _mime_type = validate_and_load_image(
                file_storage,
                max_size_bytes=current_app.config["MAX_UPLOAD_SIZE_BYTES"],
                allowed_mime_types=current_app.config["ALLOWED_IMAGE_MIME_TYPES"],
                allowed_extensions=current_app.config["ALLOWED_IMAGE_EXTENSIONS"],
            )
        except ImageValidationError as exc:
            logger.info("[%s] Image validation failed: %s", request_id, exc.code)
            return error_response(exc.code, str(exc), 400)

        crop_name = (request.form.get("crop_name") or "").strip() or None
        language = (request.form.get("language") or "english").strip()
        user_notes = (request.form.get("user_notes") or "").strip() or None
        lat, lon = _parse_lat_lon(request.form)

        weather, threshold_alerts = _fetch_weather_safely(lat, lon)

        processed_bytes = resize_for_analysis(raw_bytes, current_app.config["MAX_IMAGE_DIMENSION_PX"])

        try:
            result = analyze_panchayat(
                image_bytes=processed_bytes,
                mime_type="image/jpeg",
                crop_name=crop_name,
                user_notes=user_notes,
                weather=weather,
            )
        except AIServiceError as exc:
            logger.warning("[%s] AI service error: %s", request_id, exc.code)
            status = _AI_ERROR_STATUS.get(exc.code, 502)
            message = _AI_ERROR_MESSAGES.get(exc.code, "Unable to convene the council.")
            return error_response("AI_ADVISORY_FAILED", message, status)
        except PanchayatInvalidResponseError:
            logger.error("[%s] Panchayat AI returned invalid/unparseable response.", request_id)
            return error_response(
                "AI_ADVISORY_FAILED",
                "The council couldn't reach a reliable verdict from this photo. Please upload a "
                "clear, well-lit photo of the affected leaf or plant.",
                502,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("[%s] Panchayat analysis complete user_id=%s duration_ms=%s", request_id, user_id, duration_ms)

        return success_response(
            {
                "crop_name": crop_name,
                "language": language,
                "location": {"latitude": lat, "longitude": lon} if lat is not None else {},
                "weather": weather,
                "threshold_alerts": threshold_alerts,
                "panchayat": {
                    "agents": result["agents"],
                    "debate": result["debate"],
                    "consensus": result["consensus"],
                },
            },
            "Krishi Panchayat deliberation complete.",
        )

    except HTTPException:
        raise  # e.g. 413 upload too large -- let the app's error handlers answer
    except Exception:
        logger.exception("[%s] Unhandled error during panchayat analysis.", request_id)
        return error_response(
            "AI_ADVISORY_FAILED", "Unable to convene the council due to an unexpected error.", 500
        )
