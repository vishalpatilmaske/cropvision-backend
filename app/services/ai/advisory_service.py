"""Text-only field advisory: crop name + farmer notes + live weather in,
one plain-language advisory paragraph out. No image required -- this backs
the "Field Advisory" mode of the standalone analyzer, for farmers who want
guidance without uploading a photo.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.services.ai.llm_client import llm_client, parse_json_text

logger = logging.getLogger("cropvision.ai.advisory_service")

SYSTEM_PROMPT = """You are an agricultural advisory assistant helping smallholder farmers who have \
described their field in their own words, without a photo.

Your task:
1. Read the farmer's crop name, notes, and the live weather/soil snapshot provided.
2. Write a short, practical, farmer-friendly advisory: what the described symptoms (if any) might \
suggest, what to watch for, and concrete next steps -- grounded in the weather/soil data given.
3. If the notes don't describe any specific problem, give general crop-care guidance for the \
current weather conditions instead (e.g. irrigation timing, heat/rain precautions).
4. Do not diagnose a specific disease with confidence from text description alone -- text-only \
descriptions are inherently ambiguous. Recommend a photo-based check (Photo Diagnosis mode) when \
symptoms are described but a confident answer isn't possible from text alone.
5. Keep it concise: 2-4 short paragraphs and/or bullet points, plain language, no markdown headers.

Respond with ONLY a single JSON object -- no prose, no markdown fences -- matching exactly this shape:

{
  "advisory": "string containing the full advisory text, using \\n for line breaks and '- ' for bullet points"
}
"""


class InvalidAIResponseError(Exception):
    pass


def _build_user_prompt(crop_name: Optional[str], user_notes: Optional[str], weather: Dict[str, Any]) -> str:
    current = weather.get("current", {}) if weather else {}
    soil = weather.get("soil", {}) if weather else {}
    lines = [
        f"Crop: {crop_name or 'not specified'}",
        f"Farmer's notes: {user_notes or 'none provided'}",
        "Current weather snapshot:",
        f"- Temperature: {current.get('temperature_c', 'unknown')} C",
        f"- Humidity: {current.get('relative_humidity_pct', 'unknown')}%",
        f"- Rain now: {current.get('rain_mm', 'unknown')} mm",
        f"- Soil moisture (0-1cm): {soil.get('moisture_0_1cm_m3m3', 'unknown')} m3/m3",
    ]
    return "\n".join(lines)


def generate_field_advisory(
    crop_name: Optional[str],
    user_notes: Optional[str],
    weather: Dict[str, Any],
) -> str:
    """Returns the advisory as a plain string, ready for direct display."""
    user_prompt = _build_user_prompt(crop_name, user_notes, weather)

    ai_response = llm_client.generate_structured_json_from_text(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    try:
        parsed = parse_json_text(ai_response.text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Advisory AI returned non-JSON response (model=%s).", ai_response.model)
        raise InvalidAIResponseError("Advisory AI response was not valid JSON.") from exc

    advisory = parsed.get("advisory")
    if not isinstance(advisory, str) or not advisory.strip():
        raise InvalidAIResponseError("Missing or malformed 'advisory' field.")

    return advisory.strip()
