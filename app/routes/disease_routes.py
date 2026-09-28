import logging
import time
import uuid

from flask import Blueprint, current_app, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from werkzeug.exceptions import HTTPException

from app.extensions import limiter
from app.models.disease_pest_prediction import DiseasePestPrediction
from app.services.ai.disease_pest_service import InvalidAIResponseError, analyze_crop_image
from app.services.ai.llm_client import AIServiceError
from app.services.weather_service import WeatherServiceError, fetch_weather, summarize_weather_for_crop_health
from app.utils.image_validation import ImageValidationError, resize_for_analysis, validate_and_load_image
from app.utils.pagination import get_pagination_params, paginated_response
from app.utils.responses import error_response, success_response

logger = logging.getLogger("cropvision.routes.disease")


def _weather_for_request(request_id: str):
    """Weather snapshot for the farmer's coordinates, if the browser sent
    them. Best-effort: bad coordinates or a weather outage just mean the
    analysis runs without weather context."""
    try:
        lat = float(request.form.get("latitude"))
        lon = float(request.form.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    try:
        return summarize_weather_for_crop_health(fetch_weather(lat, lon))
    except WeatherServiceError:
        logger.info("[%s] Weather unavailable; analyzing without it.", request_id)
        return None

disease_bp = Blueprint("disease", __name__, url_prefix="/api/disease")

_AI_ERROR_STATUS = {
    "AI_NOT_CONFIGURED": 503,
    "AI_TIMEOUT": 504,
    "AI_RATE_LIMITED": 429,
    "AI_AUTH_FAILED": 502,
    "AI_UNAVAILABLE": 502,
    "AI_EMPTY_RESPONSE": 502,
}

_AI_ERROR_MESSAGES = {
    "AI_NOT_CONFIGURED": "Image analysis is temporarily unavailable. Please try again later.",
    "AI_TIMEOUT": "The analysis took too long. Please try again with a clearer, smaller photo.",
    "AI_RATE_LIMITED": "Too many analysis requests right now. Please try again in a minute.",
    "AI_AUTH_FAILED": "Image analysis is temporarily unavailable. Please try again later.",
    "AI_UNAVAILABLE": "Unable to analyze the crop image right now. Please try again shortly.",
    "AI_EMPTY_RESPONSE": "The analysis did not return a result. Please try again.",
}


@disease_bp.post("/analyze")
@jwt_required()
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_ANALYZE", "10 per minute"))
def analyze():
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
        location = (request.form.get("location") or "").strip() or None
        growth_stage = (request.form.get("growth_stage") or "").strip() or None
        additional_context = (request.form.get("additional_context") or "").strip() or None
        farm_id = request.form.get("farm_id") or None
        crop_id = request.form.get("crop_id") or None

        weather = _weather_for_request(request_id)

        processed_bytes = resize_for_analysis(raw_bytes, current_app.config["MAX_IMAGE_DIMENSION_PX"])

        try:
            result = analyze_crop_image(
                image_bytes=processed_bytes,
                mime_type="image/jpeg",
                crop_name=crop_name,
                location=location,
                growth_stage=growth_stage,
                additional_context=additional_context,
                weather=weather,
            )
        except AIServiceError as exc:
            logger.warning("[%s] AI service error: %s", request_id, exc.code)
            status = _AI_ERROR_STATUS.get(exc.code, 502)
            message = _AI_ERROR_MESSAGES.get(exc.code, "Unable to analyze the crop image.")
            return error_response("AI_ANALYSIS_FAILED", message, status)
        except InvalidAIResponseError:
            logger.error("[%s] AI returned invalid/unparseable response.", request_id)
            return error_response(
                "AI_ANALYSIS_FAILED",
                "The image quality or content made it hard to produce a reliable analysis. "
                "Please upload a clear, well-lit photo of the affected leaf or plant.",
                502,
            )

        record = DiseasePestPrediction.create(
            user_id=user_id,
            farm_id=farm_id,
            crop_id=crop_id,
            crop_name=result["crop"]["name"] or crop_name,
            analysis_type=result["analysis"]["type"],
            condition_name=result["analysis"]["name"],
            confidence=result["analysis"]["confidence"],
            severity=result["analysis"]["severity"],
            assessment_level=result["analysis"]["assessment_level"],
            symptoms=result["analysis"]["symptoms"],
            possible_causes=result["analysis"]["possible_causes"],
            recommendations=result["recommendations"],
            additional_observations=result.get("additional_observations", []),
            needs_expert_confirmation=result["needs_expert_confirmation"],
            raw_response_reference=request_id,
            report=result.get("report"),
            growth_stage=result["crop"].get("growth_stage") or growth_stage,
            weather=weather,
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "[%s] Disease analysis complete user_id=%s duration_ms=%s type=%s",
            request_id, user_id, duration_ms, result["analysis"]["type"],
        )

        return success_response(record.to_dict(), "Crop image analyzed successfully.")

    except HTTPException:
        raise  # e.g. 413 upload too large -- let the app's error handlers answer
    except Exception:
        logger.exception("[%s] Unhandled error during disease analysis.", request_id)
        return error_response(
            "AI_ANALYSIS_FAILED", "Unable to analyze the crop image due to an unexpected error.", 500
        )


@disease_bp.get("/history")
@jwt_required()
def history():
    user_id = get_jwt_identity()
    page, per_page = get_pagination_params()

    crop_filter = request.args.get("crop_name")
    type_filter = request.args.get("analysis_type")

    items, total = DiseasePestPrediction.find_paginated(
        user_id=user_id, page=page, per_page=per_page, crop_name=crop_filter, analysis_type=type_filter
    )

    result = paginated_response(items, total, page, per_page, lambda item: item.to_dict())
    return success_response(result)


@disease_bp.get("/history/<prediction_id>")
@jwt_required()
def history_detail(prediction_id: str):
    user_id = get_jwt_identity()
    record = DiseasePestPrediction.find_one_for_user(prediction_id, user_id)
    if not record:
        return error_response("NOT_FOUND", "Analysis record not found.", 404)
    return success_response(record.to_dict())
