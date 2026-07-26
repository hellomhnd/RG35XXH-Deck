"""Live weather via Open-Meteo (no API key required).

Two endpoints, both keyless: geocoding (city name -> coordinates) and forecast.
The chosen location is saved to weather.json so it persists between runs.
"""

import datetime
import json
import os
import urllib.parse
import urllib.request

import config

_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE = os.path.join(_DIR, "weather.json")

# WMO weather-interpretation codes -> short text.
_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm, hail", 99: "thunderstorm, hail",
}


def code_desc(code):
    return _CODES.get(code, "?")


def day_label(date):
    try:
        return datetime.datetime.strptime(date, "%Y-%m-%d").strftime("%a")
    except ValueError:
        return date[5:]


def _get(base, params):
    url = base + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def geocode(name):
    """City name -> {name, country, lat, lon}, or None."""
    try:
        d = _get("https://geocoding-api.open-meteo.com/v1/search",
                 {"name": name, "count": 1})
        results = d.get("results")
        if not results:
            return None
        r = results[0]
        return {"name": r["name"], "country": r.get("country", ""),
                "lat": r["latitude"], "lon": r["longitude"]}
    except Exception:
        return None


def fetch(loc):
    """Current conditions + a few-day forecast for a location, or None."""
    try:
        imperial = getattr(config, "WEATHER_UNITS", "metric") == "imperial"
        params = {
            "latitude": loc["lat"], "longitude": loc["lon"],
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
            "timezone": "auto", "forecast_days": 4,
        }
        if imperial:
            params["temperature_unit"] = "fahrenheit"
            params["wind_speed_unit"] = "mph"
        d = _get("https://api.open-meteo.com/v1/forecast", params)
        cur, units, dl = d["current"], d["current_units"], d["daily"]
        days = [
            {"date": dl["time"][i], "hi": dl["temperature_2m_max"][i],
             "lo": dl["temperature_2m_min"][i], "code": dl["weather_code"][i]}
            for i in range(len(dl["time"]))
        ]
        return {
            "temp": cur["temperature_2m"], "tunit": units["temperature_2m"],
            "code": cur["weather_code"], "wind": cur["wind_speed_10m"],
            "wunit": units["wind_speed_10m"], "days": days,
        }
    except Exception:
        return None


def load_location():
    try:
        with open(_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_location(loc):
    try:
        with open(_STATE, "w") as f:
            json.dump(loc, f)
    except OSError:
        pass
