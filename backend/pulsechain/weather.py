"""Optional live weather (Open-Meteo, no API key). Falls back cleanly when disabled or offline.

This is deliberately separate from the frozen-date demo forecast: the demo dataset is anchored to a fixed
demo calendar, so live weather is shown for information and never mixed into the simulated forecasts.
"""
from __future__ import annotations

import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_live_outlook(lat: float, lon: float, enabled: bool, timeout: float = 6.0) -> dict:
    if not enabled:
        return {"available": False, "reason": "Live weather is disabled (set OPEN_METEO_ENABLED=true). Using the synthetic weather dataset."}
    try:
        resp = requests.get(OPEN_METEO_URL, timeout=timeout, params={
            "latitude": round(lat, 4), "longitude": round(lon, 4), "forecast_days": 14, "timezone": "auto",
            "daily": "precipitation_sum,temperature_2m_mean"})
        resp.raise_for_status()
        daily = resp.json()["daily"]
        days = [{"date": d, "rain_mm": p, "temp_c": t}
                for d, p, t in zip(daily["time"], daily["precipitation_sum"], daily["temperature_2m_mean"])]
        return {"available": True, "source": "Open-Meteo", "days": days,
                "total_rain_mm": round(sum(d["rain_mm"] or 0 for d in days), 1)}
    except (requests.RequestException, KeyError, ValueError) as exc:
        return {"available": False, "reason": f"Open-Meteo unreachable ({type(exc).__name__}). Using the synthetic weather dataset."}
