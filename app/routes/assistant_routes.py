"""Farm assistant chat endpoint (the floating chat widget in the React app)."""
import logging

from flask import Blueprint, current_app, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import limiter
from app.models.user import User
from app.services.ai.farm_agent import InvalidChatRequest, run_farm_agent
from app.services.ai.llm_client import AIServiceError
from app.utils.responses import error_response, success_response

logger = logging.getLogger("cropvision.routes.assistant")

assistant_bp = Blueprint("assistant", __name__, url_prefix="/api/assistant")

_AI_ERROR_STATUS = {
    "AI_NOT_CONFIGURED": 503,
    "AI_TIMEOUT": 504,
    "AI_RATE_LIMITED": 429,
}


@assistant_bp.post("/chat")
@jwt_required()
@limiter.limit(lambda: current_app.config.get("RATE_LIMIT_CHAT", "20 per minute"))
def chat():
    """Body: {"messages": [{"role": "user"|"assistant", "content": str}, ...],
    "report_id": optional crop health report the farmer is viewing,
    "location": optional {"latitude", "longitude"} shared by the browser}."""
    user_id = get_jwt_identity()
    payload = request.get_json(silent=True) or {}
    user = User.find_by_id(user_id)

    try:
        result = run_farm_agent(
            user_id=user_id,
            user_name=user.to_dict()["name"] if user else None,
            messages=payload.get("messages"),
            report_id=payload.get("report_id"),
            location=payload.get("location"),
        )
    except InvalidChatRequest as exc:
        return error_response("VALIDATION_ERROR", str(exc), 400)
    except AIServiceError as exc:
        logger.warning("Assistant AI error: %s", exc.code)
        return error_response(
            "AI_CHAT_FAILED",
            "The assistant is unavailable right now. Please try again in a moment.",
            _AI_ERROR_STATUS.get(exc.code, 502),
        )

    return success_response(result)
