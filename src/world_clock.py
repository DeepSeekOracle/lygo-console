"""World time + weather pulse. RESOURCE (Open-Meteo), not CANON."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from web_tools import _json_get

# Spread around the globe — same spirit as the public-witness live events strip.
CITIES = [
    {"id": "hnl", "name": "Honolulu", "tz": "Pacific/Honolulu", "lat": 21.3, "lon": -157.8},
    {"id": "lax", "name": "Los Angeles", "tz": "America/Los_Angeles", "lat": 34.05, "lon": -118.25},
    {"id": "nyc", "name": "New York", "tz": "America/New_York", "lat": 40.71, "lon": -74.01},
    {"id": "sao", "name": "São Paulo", "tz": "America/Sao_Paulo", "lat": -23.55, "lon": -46.63},
    {"id": "utc", "name": "UTC", "tz": "UTC", "lat": 0.0, "lon": 0.0},
    {"id": "lon", "name": "London", "tz": "Europe/London", "lat": 51.51, "lon": -0.13},
    {"id": "cai", "name": "Cairo", "tz": "Africa/Cairo", "lat": 30.04, "lon": 31.24},
    {"id": "nbo", "name": "Nairobi", "tz": "Africa/Nairobi", "lat": -1.29, "lon": 36.82},
    {"id": "dxb", "name": "Dubai", "tz": "Asia/Dubai", "lat": 25.2, "lon": 55.27},
    {"id": "del", "name": "Delhi", "tz": "Asia/Kolkata", "lat": 28.61, "lon": 77.21},
    {"id": "sin", "name": "Singapore", "tz": "Asia/Singapore", "lat": 1.35, "lon": 103.82},
    {"id": "tyo", "name": "Tokyo", "tz": "Asia/Tokyo", "lat": 35.68, "lon": 139.69},
    {"id": "syd", "name": "Sydney", "tz": "Australia/Sydney", "lat": -33.87, "lon": 151.21},
]

_WX = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "drizzle",
    61: "rain",
    63: "rain",
    65: "heavy rain",
    71: "snow",
    73: "snow",
    75: "heavy snow",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}

_cache: dict[str, Any] = {"t": 0.0, "wx": {}}


def _wx_label(code: int) -> str:
    if code in _WX:
        return _WX[code]
    if 50 <= code <= 59:
        return "drizzle"
    if 60 <= code <= 69:
        return "rain"
    if 70 <= code <= 79:
        return "snow"
    if 80 <= code <= 84:
        return "showers"
    if code >= 95:
        return "thunder"
    return "cloud"


def _fetch_weather() -> dict[str, Any]:
    now = time.time()
    if now - float(_cache.get("t") or 0) < 600 and _cache.get("wx"):
        return _cache["wx"]
    lats = ",".join(str(c["lat"]) for c in CITIES)
    lons = ",".join(str(c["lon"]) for c in CITIES)
    url = (
        "https://api.open-meteo.com/v1/forecast?latitude="
        + lats
        + "&longitude="
        + lons
        + "&current=temperature_2m,weather_code,wind_speed_10m&timezone=auto"
    )
    data = _json_get(url)
    wx: dict[str, Any] = {}
    if isinstance(data, list) and len(data) == len(CITIES):
        rows = data
    elif isinstance(data, dict) and "latitude" in data:
        rows = [data]
    else:
        rows = []
    for i, city in enumerate(CITIES):
        if i >= len(rows) or not isinstance(rows[i], dict):
            continue
        cur = rows[i].get("current") or {}
        code = int(cur.get("weather_code") or 0)
        wx[city["id"]] = {
            "c": cur.get("temperature_2m"),
            "wind": cur.get("wind_speed_10m"),
            "code": code,
            "label": _wx_label(code),
            "source": "Open-Meteo",
        }
    if wx:
        _cache["t"] = now
        _cache["wx"] = wx
    return _cache.get("wx") or {}


def pulse_stamps() -> dict[str, Any]:
    utc = datetime.now(timezone.utc)
    local = datetime.now().astimezone()
    return {
        "unix": int(time.time()),
        "utc_iso": utc.isoformat(timespec="seconds"),
        "local_iso": local.isoformat(timespec="seconds"),
        "local_tz": str(local.tzinfo),
        "weekday": utc.strftime("%A"),
    }


def pulse() -> dict[str, Any]:
    utc = datetime.now(timezone.utc)
    local = datetime.now().astimezone()
    try:
        wx = _fetch_weather()
    except Exception:
        wx = {}
    cities = []
    for c in CITIES:
        try:
            t = utc.astimezone(ZoneInfo(c["tz"]))
        except Exception:
            t = utc
        row = {
            "id": c["id"],
            "name": c["name"],
            "tz": c["tz"],
            "iso": t.isoformat(timespec="seconds"),
            "clock": t.strftime("%H:%M"),
            "date": t.strftime("%Y-%m-%d"),
            "offset": t.strftime("%z"),
            "weather": wx.get(c["id"]),
        }
        cities.append(row)
    return {
        "ok": True,
        "class": "RESOURCE",
        "note": "Clocks are local math; weather is Open-Meteo (RESOURCE, not CANON). Use for stamps, place, past/present.",
        "unix": int(time.time()),
        "utc_iso": utc.isoformat(timespec="seconds"),
        "local_iso": local.isoformat(timespec="seconds"),
        "local_tz": str(local.tzinfo),
        "weekday": utc.strftime("%A"),
        "cities": cities,
        "events_page": "https://chatagent.ca/sources/",
    }
