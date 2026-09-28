"""Crop and fertilizer recommendation logic.

These are transparent, rule-based recommendation engines (a small reference
table + explainable scoring), NOT trained ML models -- consistent with the
project decision not to ship fake/placeholder ML artifacts. They can later
be swapped for a real ML service behind the same function signatures.
"""
from typing import Any, Dict, List, Optional

# Reference agronomy ranges (general Indian conditions). "water" is the total
# water the crop needs over its growing season (rain + irrigation), in mm.
_CROP_TABLE: Dict[str, Dict[str, Any]] = {
    "rice":        {"label": "Rice (Paddy)",   "soils": {"clay", "loamy", "alluvial"},          "water": (900, 2000), "temp": (20, 35), "seasons": {"kharif"},                  "days": 120},
    "wheat":       {"label": "Wheat",          "soils": {"loamy", "clay", "black", "alluvial"}, "water": (450, 650),  "temp": (10, 25), "seasons": {"rabi"},                    "days": 120},
    "maize":       {"label": "Maize",          "soils": {"loamy", "sandy", "alluvial", "black"},"water": (500, 800),  "temp": (18, 32), "seasons": {"kharif", "rabi", "zaid"},  "days": 100},
    "cotton":      {"label": "Cotton",         "soils": {"black", "alluvial", "loamy"},         "water": (700, 1300), "temp": (21, 35), "seasons": {"kharif"},                  "days": 170},
    "sugarcane":   {"label": "Sugarcane",      "soils": {"loamy", "clay", "alluvial", "black"}, "water": (1500, 2500),"temp": (20, 35), "seasons": {"kharif", "rabi"},          "days": 330},
    "soybean":     {"label": "Soybean",        "soils": {"black", "loamy"},                     "water": (450, 700),  "temp": (20, 30), "seasons": {"kharif"},                  "days": 100},
    "groundnut":   {"label": "Groundnut",      "soils": {"sandy", "loamy", "black"},            "water": (500, 700),  "temp": (20, 30), "seasons": {"kharif", "rabi", "zaid"},  "days": 110},
    "chickpea":    {"label": "Chickpea (Gram)","soils": {"loamy", "black", "clay"},             "water": (300, 450),  "temp": (10, 25), "seasons": {"rabi"},                    "days": 110},
    "mustard":     {"label": "Mustard",        "soils": {"loamy", "sandy", "alluvial"},         "water": (250, 400),  "temp": (10, 25), "seasons": {"rabi"},                    "days": 115},
    "tomato":      {"label": "Tomato",         "soils": {"loamy", "sandy", "alluvial", "black"},"water": (400, 600),  "temp": (18, 29), "seasons": {"kharif", "rabi", "zaid"},  "days": 120},
    "onion":       {"label": "Onion",          "soils": {"loamy", "alluvial", "black"},         "water": (350, 550),  "temp": (13, 28), "seasons": {"kharif", "rabi"},          "days": 130},
    "potato":      {"label": "Potato",         "soils": {"loamy", "sandy", "alluvial"},         "water": (500, 700),  "temp": (15, 25), "seasons": {"rabi"},                    "days": 100},
    "pigeon pea":  {"label": "Pigeon Pea (Tur)","soils": {"black", "loamy", "sandy"},           "water": (600, 1000), "temp": (20, 35), "seasons": {"kharif"},                  "days": 170},
    "pearl millet":{"label": "Pearl Millet (Bajra)","soils": {"sandy", "loamy", "black"},       "water": (250, 500),  "temp": (25, 35), "seasons": {"kharif", "zaid"},          "days": 80},
    "sorghum":     {"label": "Sorghum (Jowar)","soils": {"black", "loamy", "clay"},             "water": (400, 650),  "temp": (20, 35), "seasons": {"kharif", "rabi"},          "days": 110},
    "green gram":  {"label": "Green Gram (Moong)","soils": {"loamy", "sandy", "black"},         "water": (250, 400),  "temp": (25, 35), "seasons": {"kharif", "zaid"},          "days": 65},
    "chilli":      {"label": "Chilli",         "soils": {"loamy", "black", "alluvial"},         "water": (500, 800),  "temp": (20, 30), "seasons": {"kharif", "rabi"},          "days": 150},
}

# Water a farmer can add on top of rain, in mm per season.
IRRIGATION_WATER_MM = {"none": 0, "limited": 250, "full": 3000}

# Grown almost only with irrigation, whatever the rain.
_NEEDS_IRRIGATION = {"sugarcane", "tomato", "onion", "potato", "chilli"}

# Monsoon water still held in the soil at rabi sowing (mm) -- why rainfed
# chickpea / sorghum work on black soil even with almost no winter rain.
RESIDUAL_MOISTURE_MM = {"black": 200, "clay": 150, "alluvial": 120, "loamy": 100, "sandy": 40}

# Points per factor (a missing input is left out and the score re-scaled).
_WEIGHTS = {"season": 25, "soil": 25, "water": 30, "temperature": 20}


def crop_names() -> List[str]:
    return sorted(_CROP_TABLE)


def available_water_mm(soil_type, season, rainfall_mm, irrigation) -> float:
    """Season rain + irrigation + (in rabi) monsoon moisture stored in the soil."""
    stored = RESIDUAL_MOISTURE_MM.get(soil_type, 0) if season == "rabi" else 0
    return (rainfall_mm or 0) + IRRIGATION_WATER_MM.get(irrigation or "none", 0) + stored


def crop_water_need(crop: str):
    spec = _CROP_TABLE.get((crop or "").strip().lower())
    return spec["water"] if spec else None


def _fit_label(score: float) -> str:
    if score >= 80:
        return "excellent"
    if score >= 60:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def _score_crop(name: str, spec: Dict[str, Any], soil_type, rainfall_mm, avg_temp_c, season, irrigation) -> Dict[str, Any]:
    factors: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if season:
        ok = season in spec["seasons"]
        factors.append({
            "factor": "season", "points": _WEIGHTS["season"] if ok else 0, "max": _WEIGHTS["season"], "ok": ok,
            "note": f"Sown in {season}" if ok else f"Not usually sown in {season} (sown in {', '.join(sorted(spec['seasons']))})",
        })

    if soil_type:
        ok = soil_type in spec["soils"]
        factors.append({
            "factor": "soil", "points": _WEIGHTS["soil"] if ok else 5, "max": _WEIGHTS["soil"], "ok": ok,
            "note": f"Grows well in {soil_type} soil" if ok else f"Prefers {', '.join(sorted(spec['soils']))} soil",
        })

    if rainfall_mm is not None or irrigation:
        lo, hi = spec["water"]
        stored = RESIDUAL_MOISTURE_MM.get(soil_type, 0) if season == "rabi" else 0
        available = available_water_mm(soil_type, season, rainfall_mm, irrigation)
        if available >= lo:
            points, ok = _WEIGHTS["water"], True
            note = f"Enough water (needs {lo}-{hi} mm)"
            # Heavy rain alone (not controllable irrigation) can waterlog dry-land crops.
            if rainfall_mm is not None and rainfall_mm > hi * 1.5:
                points, ok = round(_WEIGHTS["water"] * 0.6), False
                note = f"Too much rain for this crop (needs {lo}-{hi} mm)"
                warnings.append("Risk of waterlogging -- needs good drainage.")
        else:
            points, ok = round(_WEIGHTS["water"] * available / lo), False
            note = f"Short of water: ~{round(available)} of {lo} mm needed" + (
                f" (incl. ~{stored} mm stored in the soil)" if stored and irrigation != "full" else ""
            )
            if irrigation != "full":
                warnings.append("Needs more irrigation than you have.")
        if irrigation == "none" and name in _NEEDS_IRRIGATION:
            points, ok = round(points * 0.5), False
            note = "Needs irrigation -- rarely grown on rain alone"
            warnings.append("Needs a reliable water source (well, canal or drip).")
        factors.append({"factor": "water", "points": points, "max": _WEIGHTS["water"], "ok": ok, "note": note})

    if avg_temp_c is not None:
        lo, hi = spec["temp"]
        gap = max(lo - avg_temp_c, avg_temp_c - hi, 0)
        points = max(0, round(_WEIGHTS["temperature"] - 4 * gap))
        factors.append({
            "factor": "temperature", "points": points, "max": _WEIGHTS["temperature"], "ok": gap == 0,
            "note": f"Ideal temperature ({lo}-{hi}°C)" if gap == 0 else
                    f"{'Too cold' if avg_temp_c < lo else 'Too hot'} (ideal {lo}-{hi}°C)",
        })
        if avg_temp_c > hi + 3:
            warnings.append("Heat stress likely.")

    possible = sum(f["max"] for f in factors)
    score = round(100 * sum(f["points"] for f in factors) / possible, 1) if possible else 0.0
    return {"score": score, "factors": factors, "warnings": warnings}


def recommend_crops(
    soil_type: Optional[str],
    rainfall_mm: Optional[float],
    avg_temp_c: Optional[float],
    season: Optional[str],
    nitrogen: Optional[float] = None,
    phosphorus: Optional[float] = None,
    potassium: Optional[float] = None,
    irrigation: Optional[str] = None,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    """Rank crops for a field. `rainfall_mm` is the expected rain over the
    growing season; `irrigation` is "none", "limited" or "full". Every score
    comes with a per-factor breakdown so the farmer can see why."""
    from app.services.yield_service import get_baseline_yield

    soil_type = (soil_type or "").strip().lower() or None
    season = (season or "").strip().lower() or None
    irrigation = (irrigation or "").strip().lower() or None
    if irrigation not in IRRIGATION_WATER_MM:
        irrigation = None
    rainfall_mm = float(rainfall_mm) if rainfall_mm not in (None, "") else None
    avg_temp_c = float(avg_temp_c) if avg_temp_c not in (None, "") else None

    results = []
    for name, spec in _CROP_TABLE.items():
        scored = _score_crop(name, spec, soil_type, rainfall_mm, avg_temp_c, season, irrigation)
        reasons = [f["note"] for f in scored["factors"]]
        results.append({
            "name": name,
            "label": spec["label"],
            "suitability_score": scored["score"],
            "fit": _fit_label(scored["score"]),
            "factors": scored["factors"],
            "warnings": scored["warnings"],
            "reason": "; ".join(reasons) if reasons else "insufficient input to assess suitability",
            "water_need_mm": list(spec["water"]),
            "duration_days": spec["days"],
            "yield_per_acre_quintal": get_baseline_yield(name),
        })

    # Ties: crops typical of the chosen season first, then the less thirsty ones.
    def _rank(r):
        spec = _CROP_TABLE[r["name"]]
        season_specialist = bool(season) and spec["seasons"] == {season}
        return (-r["suitability_score"], not season_specialist, spec["water"][0])

    results.sort(key=_rank)
    return results[:limit]


# --- Fertilizer ---------------------------------------------------------
# General recommended doses (kg per hectare of N : P2O5 : K2O) -- typical
# state-level recommendations; a Soil Health Card / KVK advice beats these.
_RDF_KG_PER_HA: Dict[str, tuple] = {
    "rice": (120, 60, 40), "wheat": (120, 60, 40), "maize": (120, 60, 40), "cotton": (120, 60, 60),
    "sugarcane": (250, 115, 115), "soybean": (30, 60, 30), "groundnut": (25, 50, 25),
    "chickpea": (20, 40, 20), "mustard": (80, 40, 40), "tomato": (120, 60, 60), "onion": (100, 50, 50),
    "potato": (150, 80, 100), "pigeon pea": (25, 50, 20), "pearl millet": (60, 30, 20),
    "sorghum": (80, 40, 40), "green gram": (20, 40, 20), "chilli": (120, 60, 60),
}
HA_PER_ACRE = 0.4047

# Soil Health Card ratings (available nutrient, kg/ha): (low below, high above).
_SOIL_RATING_LIMITS = {"n": (280, 560), "p": (10, 25), "k": (110, 280)}
# Rating-based dose adjustment used across Indian soil testing labs.
_RATING_FACTOR = {"low": 1.25, "medium": 1.0, "high": 0.75}

# How the dose is split over the season: (stage, timing, share of N, P, K).
_SCHEDULES = {
    "cereal": [
        ("At sowing (basal)", "Mix into soil at sowing", 0.5, 1.0, 1.0),
        ("First top dressing", "~25-30 days after sowing", 0.25, 0, 0),
        ("Second top dressing", "~45-60 days, before flowering", 0.25, 0, 0),
    ],
    "pulse": [
        ("At sowing (basal)", "All at sowing -- pulses make their own nitrogen", 1.0, 1.0, 1.0),
    ],
    "oilseed": [
        ("At sowing (basal)", "Mix into soil at sowing", 0.5, 1.0, 1.0),
        ("Top dressing", "~25-30 days, at first irrigation", 0.5, 0, 0),
    ],
    "long": [
        ("At sowing / planting (basal)", "Mix into soil at planting", 1 / 3, 1.0, 0.5),
        ("First top dressing", "~30 days after planting", 1 / 3, 0, 0.5),
        ("Second top dressing", "~60 days after planting", 1 / 3, 0, 0),
    ],
}
_CROP_SCHEDULE = {
    "rice": "cereal", "wheat": "cereal", "maize": "cereal", "sorghum": "cereal", "pearl millet": "cereal",
    "soybean": "pulse", "groundnut": "pulse", "chickpea": "pulse", "pigeon pea": "pulse", "green gram": "pulse",
    "mustard": "oilseed",
    "cotton": "long", "sugarcane": "long", "tomato": "long", "onion": "long", "potato": "long", "chilli": "long",
}

# Straight fertilizers: nutrient content and bag size.
_UREA_N, _DAP_N, _DAP_P, _MOP_K = 0.46, 0.18, 0.46, 0.60
BAG_KG = {"urea": 45, "dap": 50, "mop": 50}
# Approximate subsidised MRP per bag (India) -- farmers should check their dealer.
BAG_PRICE_RS = {"urea": 266.5, "dap": 1350, "mop": 1700}

_TIPS = {
    "cereal": [
        "Apply urea when the soil is moist, not before heavy rain, so it isn't washed away.",
        "Splitting nitrogen as shown gives more grain than putting it all at sowing.",
    ],
    "pulse": [
        "Treat seed with Rhizobium culture -- it can cut the nitrogen your crop needs.",
        "Too much urea on pulses makes leaves, not pods. Keep nitrogen low.",
    ],
    "oilseed": [
        "Mustard responds well to sulphur: SSP instead of DAP adds it for free.",
        "Give the top dressing with the first irrigation.",
    ],
    "long": [
        "Apply top dressings in a ring around plants and cover with soil.",
        "Water lightly after each dose so it reaches the roots.",
    ],
}


def fertilizer_crops() -> List[str]:
    return sorted(_RDF_KG_PER_HA)


def rate_soil_nutrient(nutrient: str, value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    low, high = _SOIL_RATING_LIMITS[nutrient]
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "medium"


def _bags(kg: float, product: str) -> Dict[str, Any]:
    size = BAG_KG[product]
    full = int(kg // size)
    loose = round(kg - full * size)
    if loose >= size - 2:  # round up nearly-full bags
        full, loose = full + 1, 0
    return {"bags": full, "loose_kg": loose, "bag_kg": size}


def recommend_fertilizer(
    crop_name: str,
    soil_n: Optional[float],
    soil_p: Optional[float],
    soil_k: Optional[float],
    growth_stage: Optional[str] = None,
    area_acres: Optional[float] = 1.0,
) -> Dict[str, Any]:
    """Fertilizer plan as products to buy (Urea / DAP / MOP) and when to apply
    them. Soil values are Soil Health Card numbers (available N, P, K in
    kg/ha); each one rated low / medium / high adjusts that nutrient's dose
    by +25% / 0 / -25%."""
    crop_key = (crop_name or "").strip().lower()
    rdf = _RDF_KG_PER_HA.get(crop_key)
    if not rdf:
        return {
            "crop": crop_key or None,
            "fertilizer_plan": [],
            "notes": (
                f"No fertilizer baseline available for '{crop_name}'. "
                "Consult a local agronomist or Krishi Vigyan Kendra for a tailored plan."
            ),
        }

    try:
        area = float(area_acres) if area_acres not in (None, "") else 1.0
    except (TypeError, ValueError):
        area = 1.0
    area = max(0.1, min(area, 1000.0))

    soil = {}
    factors = {}
    for key, value in (("n", soil_n), ("p", soil_p), ("k", soil_k)):
        value = float(value) if value not in (None, "") else None
        rating = rate_soil_nutrient(key, value)
        soil[key] = {"value": value, "rating": rating}
        factors[key] = _RATING_FACTOR.get(rating, 1.0)

    # Nutrients needed per acre after the soil-test adjustment.
    n_need = rdf[0] * HA_PER_ACRE * factors["n"]
    p_need = rdf[1] * HA_PER_ACRE * factors["p"]
    k_need = rdf[2] * HA_PER_ACRE * factors["k"]

    schedule_type = _CROP_SCHEDULE[crop_key]
    schedule = []
    totals = {"urea": 0.0, "dap": 0.0, "mop": 0.0}
    for stage, timing, n_share, p_share, k_share in _SCHEDULES[schedule_type]:
        dap = p_need * p_share / _DAP_P
        mop = k_need * k_share / _MOP_K
        urea = max(0.0, n_need * n_share - dap * _DAP_N) / _UREA_N
        products = [
            {"product": name, "kg_per_acre": round(kg, 1), "kg_total": round(kg * area, 1)}
            for name, kg in (("urea", urea), ("dap", dap), ("mop", mop)) if kg >= 0.5
        ]
        for p in products:
            totals[p["product"]] += p["kg_total"]
        schedule.append({"stage": stage, "timing": timing, "products": products})

    shopping = []
    for product, kg in totals.items():
        if kg < 0.5:
            continue
        bag = _bags(kg, product)
        shopping.append({
            "product": product,
            "kg_total": round(kg, 1),
            **bag,
            "approx_cost_rs": round(kg / BAG_KG[product] * BAG_PRICE_RS[product]),
        })

    nutrients = {"n": round(n_need, 1), "p2o5": round(p_need, 1), "k2o": round(k_need, 1)}
    plan_lines = [
        f"{step['stage']} ({step['timing']}): "
        + ", ".join(f"{p['product'].upper()} {p['kg_per_acre']} kg/acre" for p in step["products"])
        for step in schedule if step["products"]
    ]

    return {
        "crop": crop_key,
        "area_acres": area,
        "soil": soil,
        "soil_test_used": any(v["value"] is not None for v in soil.values()),
        "nutrients_kg_per_acre": nutrients,
        "schedule": schedule,
        "shopping_list": shopping,
        "approx_total_cost_rs": sum(item["approx_cost_rs"] for item in shopping),
        "tips": _TIPS[schedule_type],
        "fertilizer_plan": plan_lines,
        "growth_stage": growth_stage,
        "notes": (
            "Based on general recommended doses" + (" adjusted for your soil test" if any(
                v["value"] is not None for v in soil.values()) else "")
            + ". Prices are approximate subsidised MRP -- check with your dealer. "
            "Your Soil Health Card or local KVK advice comes first."
        ),
    }
