"""Weather + soil snapshot service, backed by Open-Meteo (no API key
required; the endpoint comes from WEATHER_API_URL)."""
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

logger = logging.getLogger("cropvision.services.weather")

HEATWAVE_THRESHOLD_C = 40.0
HEAVY_RAIN_THRESHOLD_MM = 25.0


class WeatherServiceError(Exception):
    pass


def _safe_index(lst: Optional[List[Any]], i: int) -> Any:
    if not lst or i >= len(lst):
        return None
    return lst[i]


def _closest_time_index(times: List[str], target: Optional[str]) -> int:
    if not times or not target:
        return 0
    try:
        target_dt = datetime.fromisoformat(target)
    except ValueError:
        return 0

    best_idx, best_diff = 0, None
    for i, t in enumerate(times):
        try:
            t_dt = datetime.fromisoformat(t)
        except ValueError:
            continue
        diff = abs((t_dt - target_dt).total_seconds())
        if best_diff is None or diff < best_diff:
            best_idx, best_diff = i, diff
    return best_idx


def fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    url = current_app.config.get("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast")
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,rain,precipitation,weather_code",
        "hourly": "soil_moisture_0_to_1cm,soil_temperature_0cm",
        "daily": (
            "temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,"
            "precipitation_probability_max,et0_fao_evapotranspiration,wind_speed_10m_max"
        ),
        "forecast_days": 7,
        "timezone": "auto",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("Weather API request failed: %s", type(exc).__name__)
        raise WeatherServiceError("Failed to fetch weather data.") from exc

    current = data.get("current", {}) or {}
    hourly = data.get("hourly", {}) or {}
    daily = data.get("daily", {}) or {}

    hourly_times = hourly.get("time", []) or []
    idx = _closest_time_index(hourly_times, current.get("time"))
    soil_moisture = _safe_index(hourly.get("soil_moisture_0_to_1cm"), idx)
    soil_temperature = _safe_index(hourly.get("soil_temperature_0cm"), idx)

    daily_times = daily.get("time", []) or []
    daily_forecast = []
    for i, day in enumerate(daily_times):
        daily_forecast.append({
            "date": day,
            "temp_max_c": _safe_index(daily.get("temperature_2m_max"), i),
            "temp_min_c": _safe_index(daily.get("temperature_2m_min"), i),
            "precipitation_sum_mm": _safe_index(daily.get("precipitation_sum"), i),
            "rain_sum_mm": _safe_index(daily.get("rain_sum"), i),
            "precipitation_probability_max_pct": _safe_index(daily.get("precipitation_probability_max"), i),
            "et0_fao_evapotranspiration_mm": _safe_index(daily.get("et0_fao_evapotranspiration"), i),
            "wind_speed_max_kmh": _safe_index(daily.get("wind_speed_10m_max"), i),
        })

    return {
        "location": {"latitude": lat, "longitude": lon},
        "timezone": data.get("timezone"),
        "current": {
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "relative_humidity_pct": current.get("relative_humidity_2m"),
            "rain_mm": current.get("rain"),
            "precipitation_mm": current.get("precipitation"),
            "weather_code": current.get("weather_code"),
        },
        "soil": {
            "moisture_0_1cm_m3m3": soil_moisture,
            "temperature_0cm_c": soil_temperature,
        },
        "daily_forecast": daily_forecast,
    }


def check_weather_thresholds(weather: Dict[str, Any]) -> Dict[str, Any]:
    heatwave_days, heavy_rain_days = [], []
    for day in weather.get("daily_forecast", []):
        tmax = day.get("temp_max_c")
        rain = day.get("rain_sum_mm")
        if rain is None:
            rain = day.get("precipitation_sum_mm")

        if tmax is not None and tmax > HEATWAVE_THRESHOLD_C:
            heatwave_days.append({"date": day["date"], "temp_max_c": tmax})
        if rain is not None and rain > HEAVY_RAIN_THRESHOLD_MM:
            heavy_rain_days.append({"date": day["date"], "rain_mm": rain})

    return {
        "heatwave_alert": len(heatwave_days) > 0,
        "heatwave_days": heatwave_days,
        "heavy_rain_alert": len(heavy_rain_days) > 0,
        "heavy_rain_days": heavy_rain_days,
    }


def summarize_weather_for_crop_health(weather: Dict[str, Any]) -> Dict[str, Any]:
    """Compact snapshot of the conditions that drive disease/pest spread
    (humidity, rain, heat), used as context for crop health analysis."""
    current = weather.get("current", {}) or {}
    next_3_days = (weather.get("daily_forecast") or [])[:3]
    rain_values = [
        d.get("rain_sum_mm") if d.get("rain_sum_mm") is not None else d.get("precipitation_sum_mm")
        for d in next_3_days
    ]
    rain_probabilities = [d.get("precipitation_probability_max_pct") for d in next_3_days]
    alerts = check_weather_thresholds(weather)
    return {
        "temperature_c": current.get("temperature_c"),
        "humidity_pct": current.get("relative_humidity_pct"),
        "rain_next_3_days_mm": round(sum(r for r in rain_values if r is not None), 1),
        "max_rain_probability_pct": max((p for p in rain_probabilities if p is not None), default=None),
        "heatwave_alert": alerts["heatwave_alert"],
        "heavy_rain_alert": alerts["heavy_rain_alert"],
    }


def geocode_place(name: str, count: int = 3) -> List[Dict[str, Any]]:
    """Look up a village / town / district name (Open-Meteo geocoding, no
    API key). Returns up to `count` matches, best first."""
    url = current_app.config.get("GEOCODING_API_URL", "https://geocoding-api.open-meteo.com/v1/search")
    try:
        resp = requests.get(url, params={"name": name, "count": count, "language": "en", "format": "json"}, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except requests.RequestException as exc:
        logger.error("Geocoding request failed: %s", type(exc).__name__)
        raise WeatherServiceError("Failed to look up that place.") from exc

    return [
        {
            "name": r.get("name"),
            "district": r.get("admin2"),
            "state": r.get("admin1"),
            "country": r.get("country"),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
        }
        for r in results
    ]


# Growing-season windows (month, day) -> (month, day); rabi crosses the new year.
SEASON_WINDOWS = {
    "kharif": ((6, 1), (10, 31)),
    "rabi": ((11, 1), (3, 31)),
    "zaid": ((3, 1), (6, 15)),
}


def upcoming_sowing_season(today: Optional[date] = None) -> str:
    """The next season a farmer would be planning to sow (Indian calendar)."""
    month = (today or date.today()).month
    if month in (4, 5, 6, 7):
        return "kharif"
    if month in (8, 9, 10, 11):
        return "rabi"
    return "zaid"


def _last_completed_window(season: str, today: date):
    (sm, sd), (em, ed) = SEASON_WINDOWS[season]
    # The archive lags a few days behind today.
    cutoff = today - timedelta(days=7)
    for start_year in range(today.year, today.year - 3, -1):
        start = date(start_year, sm, sd)
        end = date(start_year + (1 if em < sm else 0), em, ed)
        if end <= cutoff:
            return start, end
    raise WeatherServiceError("No completed season window found.")


def fetch_season_climate(lat: float, lon: float, season: str, today: Optional[date] = None) -> Dict[str, Any]:
    """Rain and average temperature at a location over the most recent
    completed run of a growing season (Open-Meteo historical archive, no
    key). A realistic stand-in for "typical conditions" farmers don't know
    as numbers."""
    if season not in SEASON_WINDOWS:
        raise WeatherServiceError(f"Unknown season {season!r}.")
    start, end = _last_completed_window(season, today or date.today())

    url = current_app.config.get("CLIMATE_API_URL", "https://archive-api.open-meteo.com/v1/archive")
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum",
        "timezone": "auto",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        daily = resp.json().get("daily", {}) or {}
    except requests.RequestException as exc:
        logger.error("Climate archive request failed: %s", type(exc).__name__)
        raise WeatherServiceError("Failed to fetch past season weather.") from exc

    rain = [v for v in (daily.get("precipitation_sum") or []) if v is not None]
    temps = [v for v in (daily.get("temperature_2m_mean") or []) if v is not None]
    if not rain or not temps:
        raise WeatherServiceError("No past weather data for this location.")

    return {
        "season": season,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "rainfall_mm": round(sum(rain)),
        "avg_temp_c": round(sum(temps) / len(temps), 1),
        "source": "Open-Meteo historical weather (last completed season)",
    }
