"""Farm assistant agent: a chat agent for farmers built on the OpenAI SDK's
function calling.

The model decides which tools to call (the farmer's farms, their crop health
reports, live weather, and the rule-based crop/fertilizer/yield engines),
this module runs them against the farmer's own data, and the loop repeats
until the model produces a final answer. Every tool is scoped to the
logged-in user -- the model never sees another farmer's records.
"""
import json
import logging
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from app.models.crop import Crop
from app.models.disease_pest_prediction import DiseasePestPrediction
from app.models.farm import Farm
from app.services.ai.llm_client import llm_client
from app.services.irrigation_service import build_irrigation_advisory, build_irrigation_plan
from app.services.recommendation_service import recommend_crops, recommend_fertilizer
from app.services.weather_service import WeatherServiceError, check_weather_thresholds, fetch_weather, geocode_place
from app.services.yield_service import estimate_yield_detailed

logger = logging.getLogger("cropvision.ai.farm_agent")

MAX_TOOL_ROUNDS = 5
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 2000

SYSTEM_PROMPT = """You are Krishi Mitra, the CropVision AI farm assistant. You chat with \
smallholder farmers (mostly in India) about their crops.

How to behave:
- Be warm, practical and brief. Use simple words, short paragraphs and bullet points.
- Reply in the same language the farmer writes in (e.g. Hindi, Marathi, English).
- For anything about the farmer's own farms, crops or past crop health checks, call the tools \
instead of guessing. If a tool returns nothing, say so plainly.
- To check crop health from a photo, tell the farmer to use the "Disease & Pest" page \
(upload a clear leaf photo). You cannot see images in this chat.
- Weather: call get_weather_forecast / get_irrigation_advice. With no arguments they use the \
farmer's current location (if shared); pass place_name for any named village/town/district, or \
coordinates from a saved farm. Only ask the farmer where they are if a tool says no location is known.
- Turn weather into farm decisions: irrigate or not, and spray timing -- don't spray if rain is \
likely within 24 hours or wind is above ~15 km/h; spray early morning or evening. Warn about \
heatwaves and heavy rain (drainage, harvest timing, fungal disease risk when humid).
- When suggesting pesticides or fungicides: name the active ingredient, tell them to follow the \
product label dose and wear protective gear, and prefer organic / low-toxicity options first.
- Your advice is not a lab diagnosis. For severe, spreading or uncertain problems, recommend \
contacting the local Krishi Vigyan Kendra (KVK) or agriculture officer.
- Only discuss farming, crops, weather, soil, livestock basics and this app. Politely decline \
unrelated requests.
"""

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_my_farms",
            "description": "List the farmer's saved farms (location, coordinates, area, soil type) and the crops on each.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_crop_health_checks",
            "description": "List the farmer's most recent AI crop health checks (photo analyses), newest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "How many to return (default 5)."},
                    "crop_name": {"type": "string", "description": "Optional crop name filter, e.g. 'tomato'."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_crop_health_report",
            "description": "Get the full detailed report of one crop health check by its id.",
            "parameters": {
                "type": "object",
                "properties": {"report_id": {"type": "string"}},
                "required": ["report_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": (
                "Current weather, soil moisture/temperature and 7-day forecast (rain, rain chance, max wind, "
                "temperatures, evapotranspiration) with heatwave / heavy-rain alerts. With no arguments it uses "
                "the farmer's current location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "place_name": {"type": "string", "description": "Village, town or district, e.g. 'Nashik'. Optional."},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_irrigation_advice",
            "description": (
                "Irrigate-or-skip decision for today plus a 7-day watering plan (FAO crop water balance: "
                "how many mm / litres per acre and on which days). With no location arguments it uses the "
                "farmer's current location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "place_name": {"type": "string", "description": "Village, town or district, e.g. 'Nashik'. Optional."},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "crop_name": {"type": "string"},
                    "stage": {"type": "string", "description": "initial, development, mid or late"},
                    "method": {"type": "string", "description": "flood, sprinkler or drip"},
                    "area_acres": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_place",
            "description": "Look up a village/town/district name and get matching places with coordinates.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_crops",
            "description": (
                "Rule-based ranking of which crops suit a field (soil, season rain, temperature, season, irrigation), "
                "with a per-factor score breakdown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "soil_type": {"type": "string", "description": "clay, loamy, sandy, black or alluvial"},
                    "rainfall_mm": {"type": "number", "description": "Expected rain over the whole growing season, mm"},
                    "irrigation": {"type": "string", "description": "none, limited or full"},
                    "avg_temp_c": {"type": "number"},
                    "season": {"type": "string", "description": "kharif, rabi or zaid"},
                    "nitrogen": {"type": "number"},
                    "phosphorus": {"type": "number"},
                    "potassium": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_fertilizer",
            "description": (
                "Fertilizer plan for a crop: bags of Urea / DAP / MOP to buy, a when-to-apply schedule and "
                "approximate cost; adjusted for Soil Health Card values if given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "crop_name": {"type": "string"},
                    "soil_n": {"type": "number", "description": "Soil nitrogen, kg/ha"},
                    "soil_p": {"type": "number", "description": "Soil phosphorus, kg/ha"},
                    "soil_k": {"type": "number", "description": "Soil potassium, kg/ha"},
                    "growth_stage": {"type": "string"},
                    "area_acres": {"type": "number"},
                },
                "required": ["crop_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_yield",
            "description": (
                "Yield estimate (quintals) with a factor breakdown: soil, water, care level. Rainfall is for the "
                "whole growing season."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "crop_name": {"type": "string"},
                    "area_acres": {"type": "number"},
                    "soil_type": {"type": "string"},
                    "rainfall_mm": {"type": "number"},
                    "irrigation": {"type": "string", "description": "none, limited or full"},
                    "management": {"type": "string", "description": "basic, average or good"},
                    "season": {"type": "string"},
                },
                "required": ["crop_name", "area_acres"],
                "additionalProperties": False,
            },
        },
    },
]


class InvalidChatRequest(ValueError):
    pass


def _summarize_report(record: DiseasePestPrediction) -> Dict[str, Any]:
    data = record.to_dict()
    analysis = data["analysis"]
    return {
        "report_id": data["id"],
        "date": data["created_at"],
        "crop": data["crop"]["name"],
        "result": analysis["type"],
        "condition": analysis["name"],
        "severity": analysis["severity"],
        "health_score": data.get("report", {}).get("health_score"),
    }


def _full_report(record: DiseasePestPrediction) -> Dict[str, Any]:
    data = record.to_dict()
    data.pop("user_id", None)
    return data


def _build_tool_handlers(user_id: str, location: Optional[Dict[str, float]] = None) -> Dict[str, Callable[..., Any]]:
    def _resolve_location(place_name=None, latitude=None, longitude=None):
        """Explicit coordinates > a named place > the farmer's shared location.
        Returns ({"latitude", "longitude", "label"}, None) or (None, error)."""
        if latitude is not None and longitude is not None:
            return {"latitude": float(latitude), "longitude": float(longitude), "label": "given coordinates"}, None
        if place_name:
            matches = geocode_place(place_name, count=1)
            if not matches:
                return None, {"error": f"Couldn't find a place called {place_name!r}. Ask for a nearby town or district."}
            m = matches[0]
            label = ", ".join(p for p in (m["name"], m["district"], m["state"], m["country"]) if p)
            return {"latitude": m["latitude"], "longitude": m["longitude"], "label": label}, None
        if location:
            return {**location, "label": "farmer's current location"}, None
        return None, {"error": "No location known. Ask the farmer for their village, town or district."}

    def _weather_at(**loc_args):
        place, err = _resolve_location(**loc_args)
        if err:
            return None, None, err
        weather = fetch_weather(place["latitude"], place["longitude"])
        return place, weather, None

    def get_my_farms() -> Any:
        farms = Farm.find_by_user(user_id)
        crops = Crop.find_by_farm_ids([f.id for f in farms])
        result = []
        for farm in farms:
            farm_dict = farm.to_dict()
            farm_dict.pop("user_id", None)
            farm_dict["crops"] = [c.to_dict() for c in crops if c.to_dict()["farm_id"] == farm.id]
            result.append(farm_dict)
        return result or {"message": "The farmer has not saved any farms yet."}

    def get_crop_health_checks(limit: int = 5, crop_name: Optional[str] = None) -> Any:
        limit = max(1, min(int(limit or 5), 10))
        items, total = DiseasePestPrediction.find_paginated(
            user_id=user_id, page=1, per_page=limit, crop_name=crop_name
        )
        if not items:
            return {"message": "No crop health checks found."}
        return {"total_checks": total, "checks": [_summarize_report(i) for i in items]}

    def get_crop_health_report(report_id: str) -> Any:
        record = DiseasePestPrediction.find_one_for_user(report_id, user_id)
        if not record:
            return {"error": "Report not found."}
        return _full_report(record)

    def get_weather_forecast(place_name=None, latitude=None, longitude=None) -> Any:
        try:
            place, weather, err = _weather_at(place_name=place_name, latitude=latitude, longitude=longitude)
        except WeatherServiceError:
            return {"error": "Weather service is unavailable right now."}
        if err:
            return err
        weather["place"] = place["label"]
        weather["alerts"] = check_weather_thresholds(weather)
        return weather

    def get_irrigation_advice(place_name=None, latitude=None, longitude=None, crop_name=None, stage=None,
                              method=None, area_acres=None) -> Any:
        try:
            place, weather, err = _weather_at(place_name=place_name, latitude=latitude, longitude=longitude)
        except WeatherServiceError:
            return {"error": "Weather service is unavailable right now."}
        if err:
            return err
        advice = build_irrigation_advisory(weather, check_weather_thresholds(weather))
        plan = build_irrigation_plan(weather, crop=crop_name, stage=stage, method=method, area_acres=area_acres)
        if plan["today"]:
            advice["should_irrigate"] = plan["today"]["action"] == "irrigate"
            advice["reason"] = plan["today"]["note"]
        return {"place": place["label"], **advice, "soil": weather.get("soil"), "week_plan": plan}

    def find_place(name: str) -> Any:
        try:
            matches = geocode_place(name)
        except WeatherServiceError:
            return {"error": "Place lookup is unavailable right now."}
        return matches or {"message": f"No place found for {name!r}."}

    def _recommend_crops(**kwargs) -> Any:
        return recommend_crops(
            kwargs.get("soil_type"), kwargs.get("rainfall_mm"), kwargs.get("avg_temp_c"), kwargs.get("season"),
            kwargs.get("nitrogen"), kwargs.get("phosphorus"), kwargs.get("potassium"),
            irrigation=kwargs.get("irrigation"),
        )

    def _recommend_fertilizer(crop_name: str, soil_n=None, soil_p=None, soil_k=None, growth_stage=None,
                              area_acres=None) -> Any:
        return recommend_fertilizer(crop_name, soil_n, soil_p, soil_k, growth_stage, area_acres or 1)

    def _estimate_yield(crop_name: str, area_acres: float, soil_type=None, rainfall_mm=None, irrigation=None,
                        management=None, season=None) -> Any:
        return estimate_yield_detailed(crop_name, float(area_acres), soil_type, rainfall_mm, irrigation,
                                       management, season=season)

    return {
        "get_my_farms": get_my_farms,
        "get_crop_health_checks": get_crop_health_checks,
        "get_crop_health_report": get_crop_health_report,
        "get_weather_forecast": get_weather_forecast,
        "get_irrigation_advice": get_irrigation_advice,
        "find_place": find_place,
        "recommend_crops": _recommend_crops,
        "recommend_fertilizer": _recommend_fertilizer,
        "estimate_yield": _estimate_yield,
    }


def _run_tool(handlers: Dict[str, Callable[..., Any]], name: str, arguments: str) -> str:
    handler = handlers.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool {name!r}."})
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        result = handler(**args)
    except (ValueError, TypeError) as exc:
        logger.info("Tool %s called with bad arguments: %s", name, type(exc).__name__)
        return json.dumps({"error": "Invalid arguments for this tool."})
    except Exception:  # noqa: BLE001 - a failing tool must not crash the chat
        logger.exception("Tool %s failed.", name)
        return json.dumps({"error": "This lookup failed. Continue without it."})
    return json.dumps(result, default=str)


def _validate_history(messages: Any) -> List[Dict[str, str]]:
    if not isinstance(messages, list) or not messages:
        raise InvalidChatRequest("'messages' must be a non-empty list.")

    cleaned = []
    for msg in messages[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(msg, dict) or msg.get("role") not in {"user", "assistant"}:
            raise InvalidChatRequest("Each message needs a role of 'user' or 'assistant'.")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise InvalidChatRequest("Each message needs non-empty text content.")
        cleaned.append({"role": msg["role"], "content": content.strip()[:MAX_MESSAGE_CHARS]})

    if cleaned[-1]["role"] != "user":
        raise InvalidChatRequest("The last message must be from the user.")
    return cleaned


def _validate_location(location: Any) -> Optional[Dict[str, float]]:
    """Browser-shared coordinates are optional; anything malformed is ignored."""
    if not isinstance(location, dict):
        return None
    try:
        lat, lon = float(location.get("latitude")), float(location.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"latitude": lat, "longitude": lon}


def run_farm_agent(
    *,
    user_id: str,
    user_name: Optional[str],
    messages: Any,
    report_id: Optional[str] = None,
    location: Any = None,
) -> Dict[str, Any]:
    """Run one agent turn for the farmer's latest message.

    Returns {"reply": str, "tools_used": [tool names]}. Raises
    InvalidChatRequest for a malformed request and AIServiceError when the
    model can't be reached.
    """
    history = _validate_history(messages)
    coords = _validate_location(location)
    handlers = _build_tool_handlers(user_id, coords)

    system = SYSTEM_PROMPT + f"\nToday's date: {date.today().isoformat()}."
    if user_name:
        system += f"\nThe farmer's name is {user_name}."
    system += (
        "\nThe farmer has shared their current location, so weather tools work without arguments."
        if coords else
        "\nThe farmer has not shared their location."
    )
    if report_id:
        record = DiseasePestPrediction.find_one_for_user(report_id, user_id)
        if record:
            system += (
                "\nThe farmer is currently looking at this crop health report -- questions like "
                "'what should I do?' refer to it:\n" + json.dumps(_full_report(record), default=str)
            )

    convo: List[Dict[str, Any]] = [{"role": "system", "content": system}, *history]
    tools_used: List[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        result = llm_client.chat_completion(messages=convo, tools=TOOLS)
        if not result.tool_calls:
            reply = result.content or "Sorry, I couldn't come up with an answer. Please try rephrasing."
            return {"reply": reply, "tools_used": tools_used}

        convo.append({
            "role": "assistant",
            "content": result.content,
            "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in result.tool_calls
            ],
        })
        for tc in result.tool_calls:
            tools_used.append(tc["name"])
            convo.append({"role": "tool", "tool_call_id": tc["id"], "content": _run_tool(handlers, tc["name"], tc["arguments"])})

    # Out of tool rounds: ask for a final answer with what's been gathered.
    final = llm_client.chat_completion(messages=convo, tools=TOOLS, tool_choice="none")
    return {
        "reply": final.content or "Sorry, I couldn't finish that request. Please try asking in a simpler way.",
        "tools_used": tools_used,
    }
