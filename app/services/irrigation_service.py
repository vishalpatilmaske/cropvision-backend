"""Irrigation advisory: rule-based, combining weather forecast + soil
moisture snapshot. No ML model -- a transparent, explainable rule set."""
from typing import Any, Dict, Optional

from app.services.weather_service import HEATWAVE_THRESHOLD_C


def build_irrigation_advisory(weather: Dict[str, Any], threshold_alerts: Dict[str, Any]) -> Dict[str, Any]:
    today = (weather.get("daily_forecast") or [{}])[0]
    tomorrow = (weather.get("daily_forecast") or [{}, {}])[1] if len(weather.get("daily_forecast", [])) > 1 else {}
    soil_moisture = (weather.get("soil") or {}).get("moisture_0_1cm_m3m3")

    rain_today = today.get("rain_sum_mm") or today.get("precipitation_sum_mm") or 0
    rain_tomorrow = tomorrow.get("rain_sum_mm") or tomorrow.get("precipitation_sum_mm") or 0
    temp_max_today = today.get("temp_max_c")

    should_irrigate = True
    reasons = []

    if rain_tomorrow and rain_tomorrow > 10:
        should_irrigate = False
        reasons.append(f"Heavy rain expected tomorrow ({rain_tomorrow:.1f} mm) -- skip irrigation today.")
    elif rain_today and rain_today > 5:
        should_irrigate = False
        reasons.append(f"Rain already expected today ({rain_today:.1f} mm) -- irrigation likely unnecessary.")
    elif soil_moisture is not None and soil_moisture > 0.35:
        should_irrigate = False
        reasons.append(f"Soil moisture is already high ({soil_moisture:.2f} m3/m3).")
    else:
        if temp_max_today and temp_max_today > HEATWAVE_THRESHOLD_C:
            reasons.append(f"Heatwave expected today ({temp_max_today:.1f} C) -- irrigate early morning or evening.")
        elif soil_moisture is not None and soil_moisture < 0.15:
            reasons.append(f"Soil moisture is low ({soil_moisture:.2f} m3/m3).")
        else:
            reasons.append("No significant rain expected and soil moisture is moderate.")

    best_time = "early morning (before 8 AM) or evening (after 5 PM)" if should_irrigate else "not needed today"

    return {
        "should_irrigate": should_irrigate,
        "reason": " ".join(reasons),
        "best_time": best_time,
        "heatwave_alert": threshold_alerts.get("heatwave_alert", False),
        "heavy_rain_alert": threshold_alerts.get("heavy_rain_alert", False),
    }


# --- 7-day irrigation plan (FAO-56 crop water balance) -------------------
# Crop coefficient Kc by growth stage: initial, mid-season, late. The
# development stage uses the average of initial and mid.
_KC = {
    "rice": (1.05, 1.20, 0.90), "wheat": (0.40, 1.15, 0.40), "maize": (0.30, 1.20, 0.60),
    "cotton": (0.35, 1.15, 0.70), "sugarcane": (0.40, 1.25, 0.75), "soybean": (0.40, 1.15, 0.50),
    "groundnut": (0.40, 1.15, 0.60), "chickpea": (0.40, 1.00, 0.35), "mustard": (0.35, 1.15, 0.35),
    "tomato": (0.60, 1.15, 0.80), "onion": (0.70, 1.05, 0.75), "potato": (0.50, 1.15, 0.75),
    "pigeon pea": (0.40, 1.15, 0.60), "pearl millet": (0.30, 1.00, 0.30), "sorghum": (0.30, 1.00, 0.55),
    "green gram": (0.40, 1.05, 0.60), "chilli": (0.60, 1.05, 0.90),
}
_GENERIC_KC = (0.70, 1.00, 0.80)
STAGES = ("initial", "development", "mid", "late")

# Method: water that actually reaches the roots, and how dry the root zone
# may get (mm) before the next watering.
_METHODS = {
    "flood": {"efficiency": 0.60, "trigger_mm": 40},
    "sprinkler": {"efficiency": 0.75, "trigger_mm": 25},
    "drip": {"efficiency": 0.90, "trigger_mm": 8},
}
LITRES_PER_MM_PER_ACRE = 4047  # 1 mm of water over one acre


def irrigation_crops() -> list:
    return sorted(_KC)


def crop_coefficient(crop: Optional[str], stage: Optional[str]) -> float:
    ini, mid, end = _KC.get((crop or "").strip().lower(), _GENERIC_KC)
    return {"initial": ini, "development": (ini + mid) / 2, "mid": mid, "late": end}.get(stage or "mid", mid)


def _effective_rain(rain_mm: Optional[float]) -> float:
    # Very light showers mostly evaporate; heavier rain is ~80% useful.
    return 0.8 * rain_mm if rain_mm and rain_mm >= 2 else 0.0


def build_irrigation_plan(
    weather: Dict[str, Any],
    crop: Optional[str] = None,
    stage: Optional[str] = None,
    method: Optional[str] = None,
    area_acres: Optional[float] = 1.0,
) -> Dict[str, Any]:
    """Day-by-day water balance for the forecast week: crop water use
    (ET0 x Kc) minus useful rain; water when the root zone has dried by the
    method's trigger depth, skipping a watering if good rain is due next day."""
    method = method if method in _METHODS else "flood"
    stage = stage if stage in STAGES else "mid"
    try:
        area = max(0.1, float(area_acres)) if area_acres not in (None, "") else 1.0
    except (TypeError, ValueError):
        area = 1.0
    cfg = _METHODS[method]
    kc = crop_coefficient(crop, stage)
    days = weather.get("daily_forecast") or []

    # Starting dryness from surface soil moisture (wet >= 0.35, dry <= 0.15 m3/m3).
    moisture = (weather.get("soil") or {}).get("moisture_0_1cm_m3m3")
    if moisture is None:
        deficit = cfg["trigger_mm"] / 2
    else:
        dryness = min(1.0, max(0.0, (0.35 - moisture) / 0.20))
        deficit = dryness * cfg["trigger_mm"]

    plan = []
    for i, day in enumerate(days):
        et0 = day.get("et0_fao_evapotranspiration_mm") or 0.0
        rain = day.get("rain_sum_mm")
        if rain is None:
            rain = day.get("precipitation_sum_mm") or 0.0
        etc = et0 * kc
        eff_rain = _effective_rain(rain)
        deficit = max(0.0, deficit + etc - eff_rain)

        tomorrow = days[i + 1] if i + 1 < len(days) else {}
        rain_tomorrow = tomorrow.get("rain_sum_mm")
        if rain_tomorrow is None:
            rain_tomorrow = tomorrow.get("precipitation_sum_mm") or 0.0

        entry = {
            "date": day.get("date"),
            "crop_water_use_mm": round(etc, 1),
            "rain_mm": round(rain, 1),
            "rain_probability_pct": day.get("precipitation_probability_max_pct"),
            "temp_max_c": day.get("temp_max_c"),
            "action": "none",
            "net_mm": 0.0,
            "gross_mm": 0.0,
            "litres_per_acre": 0,
            "litres_total": 0,
        }
        if eff_rain >= 5:
            entry["action"] = "rain"
            entry["note"] = f"Rain ({rain:.0f} mm) waters the field."
        elif deficit >= cfg["trigger_mm"]:
            if _effective_rain(rain_tomorrow) >= deficit * 0.6:
                entry["action"] = "wait"
                entry["note"] = f"Field is dry, but {rain_tomorrow:.0f} mm rain is expected tomorrow -- wait."
            else:
                gross = deficit / cfg["efficiency"]
                entry.update({
                    "action": "irrigate",
                    "net_mm": round(deficit, 1),
                    "gross_mm": round(gross, 1),
                    "litres_per_acre": round(gross * LITRES_PER_MM_PER_ACRE, -2),
                    "litres_total": round(gross * LITRES_PER_MM_PER_ACRE * area, -2),
                    "note": f"Give about {gross:.0f} mm of water.",
                })
                deficit = 0.0
        else:
            entry["note"] = "Soil still has enough water."
        entry["root_zone_dryness_pct"] = round(min(100.0, 100 * deficit / cfg["trigger_mm"]))
        plan.append(entry)

    today = plan[0] if plan else None
    week_use = sum(d["crop_water_use_mm"] for d in plan)
    week_rain = sum(_effective_rain(d["rain_mm"]) for d in plan)
    return {
        "crop": (crop or "").strip().lower() or None,
        "stage": stage,
        "method": method,
        "area_acres": area,
        "kc": round(kc, 2),
        "today": today,
        "days": plan,
        "irrigations_this_week": sum(1 for d in plan if d["action"] == "irrigate"),
        "week_crop_water_use_mm": round(week_use, 1),
        "week_useful_rain_mm": round(week_rain, 1),
        "week_litres_total": sum(d["litres_total"] for d in plan),
        "best_time": "Early morning (before 8 AM) or evening (after 5 PM), when less water is lost to heat.",
        "notes": (
            "Crop water use = reference evaporation x crop factor (FAO-56 method) from the live forecast. "
            "Check the soil by hand before watering: if it sticks together when squeezed, it can wait."
        ),
    }
