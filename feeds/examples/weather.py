"""Weather for one place from Open-Meteo (free, no key): now, the next 24 hours, the week.

Shows: metrics, a temperature line chart and a rain-chance bar chart, a 7-day table, sunrise and
sunset as key/value rows, a callout and an alert when rain is likely tomorrow. Lives in the
"Daily" space.

    python feeds/examples/weather.py --place "Greenwich" --lat 51.4769 --lon -0.0005 [--units imperial] [--dry-run]
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feedkit  # noqa: E402

from orbital.feeds import blocks as b  # noqa: E402

FEED = "weather"
API = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes → words and an SF Symbol.
CODES = {
    0: ("Clear", "sun.max.fill"), 1: ("Mostly clear", "sun.max.fill"), 2: ("Partly cloudy", "cloud.sun.fill"),
    3: ("Overcast", "cloud.fill"), 45: ("Fog", "cloud.fog.fill"), 48: ("Freezing fog", "cloud.fog.fill"),
    51: ("Light drizzle", "cloud.drizzle.fill"), 53: ("Drizzle", "cloud.drizzle.fill"), 55: ("Heavy drizzle", "cloud.drizzle.fill"),
    56: ("Freezing drizzle", "cloud.sleet.fill"), 57: ("Freezing drizzle", "cloud.sleet.fill"),
    61: ("Light rain", "cloud.rain.fill"), 63: ("Rain", "cloud.rain.fill"), 65: ("Heavy rain", "cloud.heavyrain.fill"),
    66: ("Freezing rain", "cloud.sleet.fill"), 67: ("Freezing rain", "cloud.sleet.fill"),
    71: ("Light snow", "cloud.snow.fill"), 73: ("Snow", "cloud.snow.fill"), 75: ("Heavy snow", "cloud.snow.fill"),
    77: ("Snow grains", "cloud.snow.fill"), 80: ("Showers", "cloud.rain.fill"), 81: ("Showers", "cloud.rain.fill"),
    82: ("Violent showers", "cloud.heavyrain.fill"), 85: ("Snow showers", "cloud.snow.fill"), 86: ("Snow showers", "cloud.snow.fill"),
    95: ("Thunderstorm", "cloud.bolt.rain.fill"), 96: ("Thunderstorm, hail", "cloud.bolt.rain.fill"),
    99: ("Thunderstorm, hail", "cloud.bolt.rain.fill"),
}


def describe(code) -> tuple[str, str]:
    return CODES.get(int(code) if code is not None else -1, ("Unknown", "cloud.fill"))


def gather(lat: float, lon: float, units: str = "metric") -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    }
    if units == "imperial":
        params.update({"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"})
    return feedkit.fetch_json(API, params=params)


def build(data: dict, *, place: str, units: str = "metric", section_id: str = FEED,
          rain_alert_pct: int = 80) -> tuple[dict, list[dict]]:
    deg = "°F" if units == "imperial" else "°C"
    wind_unit = "mph" if units == "imperial" else "km/h"
    current = data.get("current") or {}
    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}
    words, symbol = describe(current.get("weather_code"))
    now_key = (current.get("time") or "")[:13]
    times = hourly.get("time") or []
    start = next((i for i, t in enumerate(times) if t[:13] >= now_key), 0) if now_key else 0
    window = range(start, min(start + 24, len(times)))
    t2m = hourly.get("temperature_2m") or []
    chance = hourly.get("precipitation_probability") or []
    temps = [(times[i][11:16], t2m[i]) for i in window if i < len(t2m) and t2m[i] is not None]
    rain = [(times[i][11:16], round((chance[i] or 0) / 100, 2)) for i in window if i < len(chance)]

    def col(name: str, i: int, default=None):
        values = daily.get(name) or []
        return values[i] if i < len(values) and values[i] is not None else default

    days = []
    for i, day in enumerate(daily.get("time") or []):
        label = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d")
        dwords, _ = describe(col("weather_code", i))
        high, low = col("temperature_2m_max", i), col("temperature_2m_min", i)
        days.append([label, dwords, f"{high:.0f}{deg}" if high is not None else "-",
                     f"{low:.0f}{deg}" if low is not None else "-", f"{col('precipitation_probability_max', i, 0)}%"])
    page: list[dict] = []
    alerts: list[dict] = []
    chances = daily.get("precipitation_probability_max") or []
    if len(chances) > 1 and chances[1] is not None and chances[1] >= rain_alert_pct:
        tomorrow = daily["time"][1]
        page.append(b.callout("rain-tomorrow", f"{chances[1]}% chance of rain tomorrow.", tone="info", title="Take an umbrella"))
        alerts.append({"id": f"weather-{b.slug(place, 30)}-rain-{tomorrow}", "title": f"Rain likely tomorrow in {place}",
                       "body": f"{chances[1]}% chance of rain on {tomorrow}.", "level": "info"})
    page += [
        b.metrics("now", [
            b.metric("Now", f"{current.get('temperature_2m', 0):.0f}{deg}", id="temp", caption=words),
            b.metric("Feels like", f"{current.get('apparent_temperature', 0):.0f}{deg}", id="feels"),
            b.metric("Humidity", f"{current.get('relative_humidity_2m', 0):.0f}%", id="humidity"),
            b.metric("Wind", f"{current.get('wind_speed_10m', 0):.0f} {wind_unit}", id="wind"),
        ]),
        b.chart("temp-24h", b.chart_series("Temperature", temps, color="#F59E0B"), kind="line",
                title="Next 24 hours", y_label=deg),
        b.chart("rain-24h", b.chart_series("Chance of rain", rain, color="#4F8EF7"), kind="bar",
                title="Chance of rain", y_format="percent"),
        b.table("week", ["Day", "Weather", "High", "Low", "Rain"], days, title="This week"),
    ]
    if col("sunrise", 0) and col("sunset", 0):
        page.append(b.key_value("sun", [("Sunrise", col("sunrise", 0)[11:16]), ("Sunset", col("sunset", 0)[11:16]),
                                        ("Time zone", data.get("timezone") or "-")], title="Today"))
    page.append(b.text("credit", "Forecast by [Open-Meteo](https://open-meteo.com) (CC BY 4.0)."))
    section = b.section(section_id, f"Weather · {place}", page, icon=symbol, order=40,
                        summary=f"{current.get('temperature_2m', 0):.0f}{deg}, {words}"[:80],
                        space="daily", space_title="Daily", space_icon="sun.max.fill")
    return section, alerts


def main(argv: list[str] | None = None, client=None) -> int:
    parser = feedkit.arg_parser(__doc__.splitlines()[0])
    parser.add_argument("--place", default="Greenwich")
    parser.add_argument("--lat", type=float, default=51.4769)
    parser.add_argument("--lon", type=float, default=-0.0005)
    parser.add_argument("--units", choices=["metric", "imperial"], default="metric")
    parser.add_argument("--rain-alert-pct", type=int, default=80)
    parser.add_argument("--section-id", default=FEED)
    args = parser.parse_args(argv)
    data = gather(args.lat, args.lon, args.units)
    section, alerts = build(data, place=args.place, units=args.units, section_id=args.section_id,
                            rain_alert_pct=args.rain_alert_pct)
    result = feedkit.push(section, alerts, dry_run=args.dry_run, client=client, home=args.home)
    temp = (data.get("current") or {}).get("temperature_2m")
    feedkit.result_line(FEED, pushed=1 if not args.dry_run else 0, temp=temp, forecast="ok",
                        alerts=result["alerts"], revision=result["revision"])
    return 0


if __name__ == "__main__":
    sys.exit(feedkit.run_feed(main))
