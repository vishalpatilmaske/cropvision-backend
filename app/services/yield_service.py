"""Yield estimation: a transparent heuristic (baseline yield/acre adjusted
for soil, water, care and crop health), NOT a trained ML model. Documented as an
estimate with a range, never a precise guarantee."""
from typing import Any, Dict, Optional

# crop -> average yield in quintals/acre under normal conditions
_BASELINE_YIELD_QUINTAL_PER_ACRE: Dict[str, float] = {
    "rice": 22.0,
    "wheat": 18.0,
    "maize": 24.0,
    "cotton": 8.0,
    "sugarcane": 320.0,
    "soybean": 10.0,
    "groundnut": 12.0,
    "chickpea": 8.0,
    "mustard": 7.0,
    "tomato": 80.0,
    "onion": 70.0,
    "potato": 90.0,
    "pigeon pea": 4.5,
    "pearl millet": 6.0,
    "sorghum": 6.0,
    "green gram": 3.5,
    "chilli": 8.0,  # dry chilli
}

# Care level: how closely the crop gets timely seed, fertilizer, weeding and pest control.
_MANAGEMENT_FACTOR = {"basic": 0.7, "average": 0.85, "good": 1.0}

_SOIL_QUALITY_MULTIPLIER = {
    "alluvial": 1.1,
    "loamy": 1.05,
    "black": 1.0,
    "clay": 0.95,
    "sandy": 0.85,
}


def get_baseline_yield(crop_name: Optional[str]) -> Optional[float]:
    """Average quintals/acre for a crop under normal conditions, or None
    when the crop isn't in the reference table."""
    return _BASELINE_YIELD_QUINTAL_PER_ACRE.get((crop_name or "").strip().lower())


def known_crops() -> list:
    return sorted(_BASELINE_YIELD_QUINTAL_PER_ACRE)


def _water_factor(crop_key: str, soil_key, season, rainfall_mm, irrigation) -> tuple:
    """(factor, note) from how much of the crop's water need is available."""
    from app.services.recommendation_service import available_water_mm, crop_water_need

    need = crop_water_need(crop_key)
    if not need or (rainfall_mm is None and not irrigation):
        return 1.0, None
    available = available_water_mm(soil_key, season, rainfall_mm, irrigation)
    low, high = need
    if available >= low:
        if rainfall_mm is not None and rainfall_mm > high * 1.5:
            return 0.85, "Too much rain -- some loss to waterlogging"
        return 1.0, "Enough water for this crop"
    share = available / low
    return round(max(0.4, 0.4 + 0.6 * share), 2), f"Only ~{round(share * 100)}% of the water it needs"


def estimate_yield_detailed(
    crop_name: str,
    area_acres: float,
    soil_type: Optional[str] = None,
    rainfall_mm: Optional[float] = None,
    irrigation: Optional[str] = None,
    management: Optional[str] = None,
    health_score: Optional[float] = None,
    season: Optional[str] = None,
) -> Dict[str, Any]:
    """Yield estimate that shows its working: a well-managed baseline per acre,
    then one multiplier per factor (soil, water, care, crop health)."""
    crop_key = (crop_name or "").strip().lower()
    baseline = _BASELINE_YIELD_QUINTAL_PER_ACRE.get(crop_key)
    try:
        area = float(area_acres)
    except (TypeError, ValueError):
        area = 0.0
    if not baseline or area <= 0:
        return {
            "value": None, "unit": "quintals", "range_low": None, "range_high": None, "factors": [],
            "method": "heuristic_baseline",
            "notes": f"No yield baseline available for '{crop_name}' or invalid area.",
        }

    factors = []
    soil_key = (soil_type or "").strip().lower()
    if soil_key in _SOIL_QUALITY_MULTIPLIER:
        m = _SOIL_QUALITY_MULTIPLIER[soil_key]
        factors.append({"factor": "soil", "multiplier": m, "note": f"{soil_key.capitalize()} soil"})

    rainfall_mm = float(rainfall_mm) if rainfall_mm not in (None, "") else None
    water_m, water_note = _water_factor(crop_key, soil_key, (season or "").lower() or None, rainfall_mm, irrigation)
    if water_note:
        factors.append({"factor": "water", "multiplier": water_m, "note": water_note})

    if management in _MANAGEMENT_FACTOR:
        factors.append({
            "factor": "care", "multiplier": _MANAGEMENT_FACTOR[management],
            "note": {"basic": "Basic care", "average": "Average care", "good": "Good care (timely inputs)"}[management],
        })

    if health_score is not None:
        score = max(0.0, min(100.0, float(health_score)))
        m = round(0.6 + 0.4 * score / 100, 2)
        factors.append({"factor": "health", "multiplier": m, "note": f"Latest health check: {round(score)}/100"})

    per_acre = baseline
    for f in factors:
        per_acre *= f["multiplier"]
        f["change_pct"] = round((f["multiplier"] - 1) * 100)
    total = per_acre * area

    return {
        "value": round(total, 1),
        "unit": "quintals",
        "range_low": round(total * 0.85, 1),
        "range_high": round(total * 1.15, 1),
        "per_acre": round(per_acre, 1),
        "baseline_per_acre": baseline,
        "area_acres": area,
        "factors": factors,
        "method": "heuristic_baseline",
        "notes": (
            "Starts from a typical well-managed yield and adjusts it for your soil, water, care and crop "
            "health. A planning estimate, not a guarantee."
        ),
    }
