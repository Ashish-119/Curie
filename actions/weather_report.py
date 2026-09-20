"""Weather via wttr.in — no API key, no cloud LLM, minimal outbound params.

Only the city name + 'today'/'tomorrow' leave the box.
"""
from urllib.parse import quote
import requests


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = (parameters.get("city") or "").strip()
    when = (parameters.get("time") or "today").strip().lower()

    if not city:
        return "I need a city name to get the weather."

    try:
        url = f"https://wttr.in/{quote(city)}?format=j1"
        r   = requests.get(url, timeout=6, headers={"Accept-Language": "en"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return f"Could not fetch weather for {city}: {e}"

    try:
        if "tomorrow" in when:
            w     = data["weather"][1] if len(data["weather"]) > 1 else data["weather"][0]
            lo    = w["mintempC"]
            hi    = w["maxtempC"]
            desc  = w["hourly"][4]["weatherDesc"][0]["value"]
            return f"Tomorrow in {city}: {desc}, low {lo} degrees, high {hi} degrees Celsius."
        else:
            cc     = data["current_condition"][0]
            temp   = cc["temp_C"]
            feels  = cc["FeelsLikeC"]
            desc   = cc["weatherDesc"][0]["value"]
            hum    = cc["humidity"]
            wind   = cc["windspeedKmph"]
            return (
                f"Right now in {city}: {desc}, {temp} degrees Celsius, "
                f"feels like {feels}, humidity {hum} percent, "
                f"wind {wind} kilometres per hour."
            )
    except (KeyError, IndexError):
        return f"Got weather data for {city} but could not parse it."
