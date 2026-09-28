"""Agricultural crop image analysis via a vision-capable LLM.

This module owns the domain-specific logic: the system prompt for
agricultural image analysis, the expected structured-JSON schema, and
validation of whatever the model returns. It never talks to the SDK
directly -- that's `llm_client.py`'s job -- so the underlying vision
provider can be swapped without touching this file's callers.

Disease/pest detection here is explicitly NOT a locally trained CNN. See
README.md "AI limitations" section.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.services.ai.llm_client import llm_client, parse_json_text

logger = logging.getLogger("cropvision.ai.disease_pest_service")

VALID_ANALYSIS_TYPES = {"healthy", "disease", "pest", "nutrient_deficiency", "unknown"}
VALID_SEVERITIES = {"none", "low", "medium", "high", "critical"}
VALID_ASSESSMENT_LEVELS = {"confirmed", "likely", "possible", "unknown"}
VALID_SPREAD_RISKS = {"low", "medium", "high"}
VALID_URGENCIES = {"none", "monitor", "within_week", "immediate"}

SYSTEM_PROMPT = """You are an agricultural vision assistant helping smallholder farmers understand \
photos of their crops and leaves. You analyze ONE uploaded image at a time.

Your task:
1. Identify the crop/plant in the image if possible, and its growth stage (seedling, vegetative, \
flowering, fruiting or maturity) when it can be judged from the photo.
2. Determine whether the visible evidence indicates: healthy growth, disease, pest damage, \
nutrient deficiency, or an uncertain/unknown condition.
3. If disease or pest damage is visible, name the single most likely condition -- but only when \
the visual evidence actually supports it. Never invent a specific disease/pest name if the image \
does not show clear supporting symptoms.
4. Describe only symptoms you can actually see in the image. Do not invent symptoms.
5. Classify your certainty using exactly one of these four levels, and let this level drive the \
"confidence" number and "severity" you report:
   - "confirmed": strong, unambiguous visual evidence.
   - "likely": visual evidence points fairly strongly to this condition, but isn't fully conclusive.
   - "possible": some supporting evidence, but multiple explanations are plausible.
   - "unknown": insufficient visual evidence to make any determination (e.g. blurry, poorly lit, \
no plant visible, or genuinely ambiguous).
6. Confidence is a subjective assessment (0-100), not a scientifically validated probability. Never \
report high confidence for an "unknown" assessment.
7. Give practical, safe recommendations a farmer can act on: immediate actions, treatment options, \
and preventive measures.
8. Set "needs_expert_confirmation" to true whenever the assessment level is "possible" or "unknown", \
or whenever severity is "high" or "critical", or whenever you are recommending chemical treatment.
9. Do not fabricate certainty. It is always acceptable, and often correct, to say the condition is \
unclear from the photo alone.
10. Fill the "report" block for the farmer's health report:
   - "health_score": overall plant health 0-100 (100 = fully healthy) judged from what is visible.
   - "summary": 2-3 plain-language sentences a farmer can understand, no jargon.
   - "affected_area_pct": rough share (0-100) of the visible leaf/plant area showing symptoms.
   - "spread_risk": how likely it is to spread to nearby plants -- low, medium or high.
   - "urgency": none (healthy), monitor, within_week, or immediate.
   - "organic_options": organic / low-cost treatments, if any apply.
   - "monitoring_plan": what to check over the next 1-2 weeks and how often.
   - "recovery_outlook": one sentence on the expected outcome if the advice is followed.
   - "weather_advice": if local weather is provided, 1-2 sentences on how it affects this \
condition and the timing of any spraying (e.g. avoid spraying before rain). Otherwise null.

Respond with ONLY a single JSON object -- no prose, no markdown fences -- matching exactly this shape:

{
  "crop": {"name": "string or null", "confidence": 0-100, "growth_stage": "string or null"},
  "analysis": {
    "type": "healthy|disease|pest|nutrient_deficiency|unknown",
    "name": "string or null",
    "confidence": 0-100,
    "severity": "none|low|medium|high|critical",
    "assessment_level": "confirmed|likely|possible|unknown",
    "symptoms": ["string", ...],
    "possible_causes": ["string", ...]
  },
  "recommendations": {
    "immediate_action": ["string", ...],
    "treatment": ["string", ...],
    "prevention": ["string", ...]
  },
  "additional_observations": ["string", ...],
  "needs_expert_confirmation": true|false,
  "report": {
    "health_score": 0-100,
    "summary": "string",
    "affected_area_pct": 0-100,
    "spread_risk": "low|medium|high",
    "urgency": "none|monitor|within_week|immediate",
    "organic_options": ["string", ...],
    "monitoring_plan": ["string", ...],
    "recovery_outlook": "string",
    "weather_advice": "string or null"
  }
}
"""


class InvalidAIResponseError(Exception):
    pass


def _build_user_prompt(crop_name: Optional[str], location: Optional[str],
                        growth_stage: Optional[str], additional_context: Optional[str],
                        weather: Optional[Dict[str, Any]] = None) -> str:
    context_lines = ["Additional context provided by the farmer (may be empty or partial):"]
    context_lines.append(f"- Stated crop name: {crop_name or 'not provided'}")
    context_lines.append(f"- Location: {location or 'not provided'}")
    context_lines.append(f"- Growth stage: {growth_stage or 'not provided'}")
    context_lines.append(f"- Extra notes: {additional_context or 'not provided'}")
    if weather:
        context_lines.append(f"- Local weather now and next 3 days: {json.dumps(weather)}")
    context_lines.append(
        "\nUse this context only to help interpret the image. Never let stated context override "
        "what is actually visible in the photo."
    )
    return "\n".join(context_lines)


def _validate_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise InvalidAIResponseError("Top-level response is not a JSON object.")

    crop = data.get("crop")
    analysis = data.get("analysis")
    recommendations = data.get("recommendations")

    if not isinstance(crop, dict) or "name" not in crop:
        raise InvalidAIResponseError("Missing or malformed 'crop' field.")
    if not isinstance(analysis, dict):
        raise InvalidAIResponseError("Missing or malformed 'analysis' field.")
    if not isinstance(recommendations, dict):
        raise InvalidAIResponseError("Missing or malformed 'recommendations' field.")

    if analysis.get("type") not in VALID_ANALYSIS_TYPES:
        raise InvalidAIResponseError(f"Invalid analysis.type: {analysis.get('type')!r}")
    if analysis.get("severity") not in VALID_SEVERITIES:
        raise InvalidAIResponseError(f"Invalid analysis.severity: {analysis.get('severity')!r}")
    if analysis.get("assessment_level") not in VALID_ASSESSMENT_LEVELS:
        raise InvalidAIResponseError(f"Invalid analysis.assessment_level: {analysis.get('assessment_level')!r}")

    for key in ("symptoms", "possible_causes"):
        if not isinstance(analysis.get(key), list):
            raise InvalidAIResponseError(f"analysis.{key} must be a list.")

    for key in ("immediate_action", "treatment", "prevention"):
        if not isinstance(recommendations.get(key), list):
            raise InvalidAIResponseError(f"recommendations.{key} must be a list.")

    if not isinstance(data.get("additional_observations", []), list):
        raise InvalidAIResponseError("additional_observations must be a list.")

    if not isinstance(data.get("needs_expert_confirmation"), bool):
        raise InvalidAIResponseError("needs_expert_confirmation must be a boolean.")

    def _clamp_confidence(value: Any) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(100.0, num))

    crop["confidence"] = _clamp_confidence(crop.get("confidence", 0))
    analysis["confidence"] = _clamp_confidence(analysis.get("confidence", 0))

    if analysis["assessment_level"] == "unknown" and analysis["confidence"] > 40:
        analysis["confidence"] = 40.0

    data.setdefault("additional_observations", [])
    stage = crop.get("growth_stage")
    crop["growth_stage"] = stage.strip() if isinstance(stage, str) and stage.strip() else None
    data["report"] = _normalize_report(data.get("report"), analysis)
    return data


def _normalize_report(report: Any, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """The report block is best-effort: a missing or partly malformed block
    never fails the analysis, bad fields are just dropped or defaulted."""
    report = report if isinstance(report, dict) else {}

    def _pct(value: Any) -> Optional[float]:
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return None

    def _str_list(value: Any) -> list:
        return [str(v) for v in value if str(v).strip()] if isinstance(value, list) else []

    def _text(value: Any) -> Optional[str]:
        return value.strip() if isinstance(value, str) and value.strip() else None

    urgency = report.get("urgency")
    if urgency not in VALID_URGENCIES:
        urgency = "none" if analysis["type"] == "healthy" else "monitor"
    spread_risk = report.get("spread_risk")
    if spread_risk not in VALID_SPREAD_RISKS:
        spread_risk = None

    return {
        "health_score": _pct(report.get("health_score")),
        "summary": _text(report.get("summary")),
        "affected_area_pct": _pct(report.get("affected_area_pct")),
        "spread_risk": spread_risk,
        "urgency": urgency,
        "organic_options": _str_list(report.get("organic_options")),
        "monitoring_plan": _str_list(report.get("monitoring_plan")),
        "recovery_outlook": _text(report.get("recovery_outlook")),
        "weather_advice": _text(report.get("weather_advice")),
    }


def analyze_crop_image(
    image_bytes: bytes,
    mime_type: str,
    crop_name: Optional[str] = None,
    location: Optional[str] = None,
    growth_stage: Optional[str] = None,
    additional_context: Optional[str] = None,
    weather: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyze a crop/leaf image via the configured vision LLM.

    Returns a validated dict matching the documented schema.
    Raises AIServiceError (service-level failure) or InvalidAIResponseError
    (model responded, but not with valid/expected JSON) -- callers are
    expected to catch both and translate to a controlled API error response.
    """
    user_prompt = _build_user_prompt(crop_name, location, growth_stage, additional_context, weather)

    ai_response = llm_client.generate_structured_json_from_image(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        image_bytes=image_bytes,
        mime_type=mime_type,
    )

    try:
        parsed = parse_json_text(ai_response.text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Vision AI returned non-JSON response (model=%s).", ai_response.model)
        raise InvalidAIResponseError("Vision AI response was not valid JSON.") from exc

    validated = _validate_schema(parsed)
    validated["_meta"] = {"model": ai_response.model, "latency_ms": ai_response.latency_ms}
    return validated
