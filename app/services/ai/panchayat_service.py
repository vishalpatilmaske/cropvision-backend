"""Krishi Panchayat: a single vision-LLM call prompted to reason as four
distinct domain-expert personas (pathology, climate/soil, organic farming,
dosage/economics) and produce a structured, cross-referenced council output.

This is one real model call with a multi-persona prompt -- a well-established
prompting technique -- not four separate agents and not fabricated content.
Every "agent" section is genuinely model-generated for this specific image.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.services.ai.llm_client import llm_client, parse_json_text

logger = logging.getLogger("cropvision.ai.panchayat_service")

AGENT_KEYS = ("pathologist", "agronomist", "organic", "economist")

SYSTEM_PROMPT = """You are simulating the "Krishi Panchayat": a council of four autonomous \
agricultural expert personas jointly reviewing ONE uploaded crop/leaf photo for a smallholder \
farmer. Reason as all four personas, cross-referencing each other's findings, then converge on a \
single unified action plan.

The four personas:
- Dr. Vanaspati, Senior Plant Pathologist: identifies visible disease/pest symptoms and likely \
biological cause, strictly from what is visible in the image.
- Prof. Mausam & Mitti, Microclimate Agronomist: uses the provided live weather/soil snapshot to \
judge spray timing, disease-favorable conditions, and irrigation implications.
- Kisan Vaidya, Natural Farming Specialist: proposes low-cost organic/ZBNF remedies (e.g. Neemastra, \
Dashaparni, Trichoderma) appropriate to the finding.
- Krishi Arthashastri, Precision Dosage & Economics Advisor: gives a rough, clearly-labeled-as-estimate \
treatment cost and dosage guidance, and the economic rationale for acting.

Rules:
1. Only describe symptoms actually visible in the photo. Never invent a specific disease/pest name \
the image doesn't support -- if the photo is unclear, say so and recommend a clearer photo.
2. Ground the agronomist's verdict in the weather/soil snapshot given below, not generic advice.
3. The four verdicts must be consistent with each other (e.g. the organic and economist verdicts \
should address the same condition the pathologist identified).
4. "debate" is a short back-and-forth (4-6 turns) where the personas reference each other's points \
by name before reaching agreement -- this should read as a real exchange, not four independent \
monologues.
5. "consensus" is the final unified 3-5 point action plan all four personas agree on.
6. Keep each "verdict" to 2-4 short sentences or bullet lines (use '- ' for bullets, '\\n' for line \
breaks). Keep "dialogue" lines to 1-2 sentences each.

Respond with ONLY a single JSON object -- no prose, no markdown fences -- matching exactly this shape:

{
  "agents": {
    "pathologist": {"verdict": "string"},
    "agronomist": {"verdict": "string"},
    "organic": {"verdict": "string"},
    "economist": {"verdict": "string"}
  },
  "debate": [
    {"agent_name": "string", "agent_role": "string", "dialogue": "string"}
  ],
  "consensus": ["string", ...]
}
"""


class InvalidAIResponseError(Exception):
    pass


def _build_user_prompt(
    crop_name: Optional[str],
    user_notes: Optional[str],
    weather: Dict[str, Any],
) -> str:
    current = weather.get("current", {}) if weather else {}
    soil = weather.get("soil", {}) if weather else {}
    lines = [
        "Additional context provided by the farmer (may be empty or partial):",
        f"- Stated crop name: {crop_name or 'not provided'}",
        f"- Farm notes: {user_notes or 'not provided'}",
        "Live weather/soil snapshot for this field:",
        f"- Temperature: {current.get('temperature_c', 'unknown')} C",
        f"- Humidity: {current.get('relative_humidity_pct', 'unknown')}%",
        f"- Rain now: {current.get('rain_mm', 'unknown')} mm",
        f"- Soil moisture (0-1cm): {soil.get('moisture_0_1cm_m3m3', 'unknown')} m3/m3",
        "\nUse this context to inform the council's reasoning, but never let it override what is "
        "actually visible in the photo.",
    ]
    return "\n".join(lines)


def _validate_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise InvalidAIResponseError("Top-level response is not a JSON object.")

    agents = data.get("agents")
    debate = data.get("debate")
    consensus = data.get("consensus")

    if not isinstance(agents, dict):
        raise InvalidAIResponseError("Missing or malformed 'agents' field.")
    for key in AGENT_KEYS:
        verdict_obj = agents.get(key)
        if not isinstance(verdict_obj, dict) or not isinstance(verdict_obj.get("verdict"), str):
            raise InvalidAIResponseError(f"Missing or malformed agents.{key}.verdict.")

    if not isinstance(debate, list):
        raise InvalidAIResponseError("'debate' must be a list.")
    for turn in debate:
        if not isinstance(turn, dict) or not all(
            isinstance(turn.get(k), str) for k in ("agent_name", "agent_role", "dialogue")
        ):
            raise InvalidAIResponseError("Each 'debate' entry needs agent_name, agent_role, dialogue strings.")

    if not isinstance(consensus, list) or not all(isinstance(c, str) for c in consensus):
        raise InvalidAIResponseError("'consensus' must be a list of strings.")

    return data


def analyze_panchayat(
    image_bytes: bytes,
    mime_type: str,
    crop_name: Optional[str] = None,
    user_notes: Optional[str] = None,
    weather: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Runs the multi-persona council analysis. Returns a validated dict
    with 'agents', 'debate', 'consensus' keys.

    Raises AIServiceError (service-level failure) or InvalidAIResponseError
    (model responded, but not with valid/expected JSON)."""
    user_prompt = _build_user_prompt(crop_name, user_notes, weather or {})

    ai_response = llm_client.generate_structured_json_from_image(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        image_bytes=image_bytes,
        mime_type=mime_type,
    )

    try:
        parsed = parse_json_text(ai_response.text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Panchayat AI returned non-JSON response (model=%s).", ai_response.model)
        raise InvalidAIResponseError("Panchayat AI response was not valid JSON.") from exc

    validated = _validate_schema(parsed)
    validated["_meta"] = {"model": ai_response.model, "latency_ms": ai_response.latency_ms}
    return validated
