"""check_weather: the weather now, today, tomorrow or for the next days, from wttr.in's JSON (?format=j1): a plain GET
through mary/net.py, with no browser and nothing read off the screen.

wttr.in gives the current conditions and three days (today, tomorrow, the day after) in 3-hour steps. A day is
summed up as its low and high, the condition seen most over the daytime, and its highest chance of rain or snow and
when. Readings are cached for 10 minutes. The place is said only when one was named (for your own location the
site's area name, e.g. a district, gets in the way).
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote

from mary import net
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("The weather, now or for today, tomorrow or the next few days: check_weather. Answer from its result; never "
         "guess the weather.")
SLOW = {"check_weather": ["Let me check the weather.", "One moment, checking the weather."]}

log = logging.getLogger(__name__)

URL = "https://wttr.in/{place}"
TTL_S = 600
WHEN = ["now", "today", "tomorrow", "forecast"]
_FORECAST_WORDS = re.compile(r"\b(tomorrow|tonight|later|weekend|next|week|forecast|going to|will it)\b", re.I)
_WHEN_WORDS = [  # the user's own words, when they say more than the call does
    ("tomorrow", re.compile(r"\btomorrow\b", re.I)),
    ("forecast", re.compile(r"\b(weekend|week|forecast|next (few |couple of )?days|coming days|days ahead)\b", re.I)),
    ("today", re.compile(r"\b(today|tonight|later|this (morning|afternoon|evening))\b", re.I)),
]
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_PART_OF_DAY = {0: "overnight", 300: "overnight", 600: "in the morning", 900: "in the morning",
                1200: "in the afternoon", 1500: "in the afternoon", 1800: "in the evening", 2100: "in the evening"}
_DAYTIME = {900: 1.0, 1200: 1.5, 1500: 1.0, 1800: 1.0}  # noon breaks a tie
_WORTH_SAYING = 30  # a chance of rain or snow (%) worth saying out loud


@dataclass
class Weather:
    place: str
    condition: str
    temp: int
    feels: int | None
    humidity: int | None
    wind_kmh: int | None = None


@dataclass
class Day:
    date: date
    low: int
    high: int
    condition: str
    rain: int                 # highest chance of rain over the day, %
    rain_when: str            # "in the afternoon"
    snow: int = 0
    snow_when: str = ""


def _text(field) -> str:
    """wttr.in wraps text as [{"value": "Partly Cloudy "}]: 'Partly cloudy'."""
    value = (field[0]["value"] if isinstance(field, list) and field else str(field or "")).strip()
    return value[:1].upper() + value[1:].lower()


def _int(value) -> int | None:
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def area(data: dict) -> str:
    """'Alcantara, Portugal': the place the reading is for."""
    near = (data.get("nearest_area") or [{}])[0]
    return ", ".join(part for part in (_text(near.get("areaName")).title(), _text(near.get("country")).title())
                     if part)


def current(data: dict) -> Weather:
    now = data["current_condition"][0]
    temp = _int(now["temp_C"])
    if temp is None:
        raise ValueError("no temperature in the reading")
    return Weather(area(data), _text(now.get("weatherDesc")), temp, _int(now.get("FeelsLikeC")),
                   _int(now.get("humidity")), _int(now.get("windspeedKmph")))


def _peak(hours: list[dict], key: str) -> tuple[int, str]:
    best = max(hours, key=lambda h: _int(h.get(key)) or 0, default=None)
    if best is None:
        return 0, ""
    return _int(best.get(key)) or 0, _PART_OF_DAY.get(_int(best.get("time")) or 0, "")


def days(data: dict) -> list[Day]:
    out = []
    for d in data.get("weather") or []:
        hours = d.get("hourly") or []
        seen: Counter = Counter()
        for h in hours:
            weight = _DAYTIME.get(_int(h.get("time")) or 0)
            if weight:
                seen[_text(h.get("weatherDesc"))] += weight
        condition = seen.most_common(1)[0][0] if seen else _text((hours or [{}])[len(hours) // 2].get("weatherDesc"))
        rain, rain_when = _peak(hours, "chanceofrain")
        snow, snow_when = _peak(hours, "chanceofsnow")
        out.append(Day(date.fromisoformat(d["date"]), _int(d["mintempC"]), _int(d["maxtempC"]), condition,
                       rain, rain_when, snow, snow_when))
    return out


def spoken(w: Weather, place: str = "") -> str:
    """'It's 28 degrees, feeling like 32. Patchy rain nearby.' The condition gets its own sentence, since the
    site's wording ("Mist", "Light rain shower") doesn't fit "and …"."""
    feels = f", feeling like {w.feels}" if w.feels is not None and w.feels != w.temp else ""
    where = f"In {place.strip().title()} it's" if place else "It's"
    condition = w.condition[:1].upper() + w.condition[1:]
    return f"{where} {w.temp} degrees{feels}. {condition}."


def _label(d: Day, first: date) -> str:
    gap = (d.date - first).days
    return "Today" if gap == 0 else "Tomorrow" if gap == 1 else f"{d.date:%A}"


def _dated(day: date) -> str:
    return f"{day:%A} {day.day} {day:%B}"


def _chance(kind: str, pct: int, when: str) -> str:
    return f"chance of {kind} up to {pct}%" + (f" {when}" if when else "")


def _fact(d: Day, first: date) -> str:
    """'Tomorrow (Monday 2 November): Sunny, 15 to 25°C, chance of rain up to 4% in the morning.'"""
    parts = [d.condition, f"{d.low} to {d.high}°C", _chance("rain", d.rain, d.rain_when)]
    if d.snow:
        parts.append(_chance("snow", d.snow, d.snow_when))
    label = _label(d, first)
    when = f"{label} ({_dated(d.date)})" if label in ("Today", "Tomorrow") else _dated(d.date)
    return f"{when}: {', '.join(p for p in parts if p)}."


def spoken_day(d: Day, first: date) -> str:
    """'Tomorrow, 15 to 25 degrees, sunny, with a 60% chance of rain in the afternoon.' Only chances worth
    saying are said."""
    chances = [f"a {pct}% chance of {kind}" + (f" {when}" if when else "")
               for kind, pct, when in (("rain", d.rain, d.rain_when), ("snow", d.snow, d.snow_when))
               if pct >= _WORTH_SAYING]
    tail = f", with {' and '.join(chances)}" if chances else ""
    return f"{_label(d, first)}, {d.low} to {d.high} degrees, {d.condition.lower()}{tail}."


def wanted(when: str, request: str, ahead: list[Day]) -> str | date:
    """What was asked for: "now", "today", "tomorrow", "forecast", or a day of the week as its date. The user's
    own words win when they say more than the call does ("will it rain on Friday?" with when="now")."""
    words = request or ""
    for i, day in enumerate(_WEEKDAYS):
        if ahead and re.search(rf"\b{day}\b", words, re.I):
            first = ahead[0].date
            return first + timedelta(days=(i - first.weekday()) % 7)
    if when not in ("today", "tomorrow"):
        for name, rx in _WHEN_WORDS:
            if rx.search(words):
                return name
    return when if when in WHEN else "now"


def report(data: dict, when: str, request: str = "", place: str = "") -> ToolResult:
    """The tool's result for a wttr.in reading: short facts for the brain, and a spoken line."""
    now, ahead = current(data), days(data)
    asked = wanted(when, request, ahead)
    named = place.strip().title()
    where = (f"{named} ({now.place})" if named and named.lower() not in now.place.lower() else now.place or named
             or "your area")
    if asked == "now" or not ahead:
        details = [now.condition, f"{now.temp}°C"]
        details += [f"feels like {now.feels}°C"] if now.feels is not None else []
        details += [f"humidity {now.humidity}%"] if now.humidity is not None else []
        details += [f"wind {now.wind_kmh} km/h"] if now.wind_kmh is not None else []
        today = (f" Today: {ahead[0].low} to {ahead[0].high}°C, {_chance('rain', ahead[0].rain, ahead[0].rain_when)}."
                 if ahead else "")
        return ToolResult(True, f"Weather in {where} now: {', '.join(details)}.{today}", say=spoken(now, place))
    first = ahead[0].date
    if asked == "forecast":
        lines = [f"Forecast for {where}, {len(ahead)} days (as far as it goes):", *(_fact(d, first) for d in ahead)]
        return ToolResult(True, "\n".join(lines), say=" ".join(spoken_day(d, first) for d in ahead))
    target = asked if isinstance(asked, date) else first + timedelta(days=1 if asked == "tomorrow" else 0)
    day = next((d for d in ahead if d.date == target), None)
    if day is None:
        last = ahead[-1].date
        return ToolResult(True, "\n".join([f"The forecast for {where} only goes to {_dated(last)}, so "
                                           f"{target:%A} isn't in it yet:", *(_fact(d, first) for d in ahead)]),
                          say=f"The forecast only goes as far as {last:%A}.")
    return ToolResult(True, f"Weather in {where}. {_fact(day, first)}", say=spoken_day(day, first))


def tools(ctx: ToolContext) -> list[Tool]:
    def check_weather(when: str = "now", place: str = "") -> ToolResult:
        place = (place or ctx.cfg.get("weather", {}).get("place") or "").strip()
        url = URL.format(place=quote(place.replace(" ", "+"), safe="+~"))
        try:
            data = net.get_json(url, params={"format": "j1"}, ttl=TTL_S)
            return report(data, when, ctx.request, place)
        except net.NetError as e:
            if e.status and re.search(r"not found|unknown location", e.body, re.I):
                return ToolResult(False, f"The weather service doesn't know a place called '{place}'.",
                                  say=f"I couldn't find a place called {place}.")
            return ToolResult(False, f"Couldn't get the weather: {e}", say=f"I couldn't get the weather. {e}")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            log.warning("unexpected weather data: %r", e)
            return ToolResult(False, "The weather service sent something unexpected.",
                              say="I couldn't read the weather just now.")

    return [Tool(
        "check_weather",
        "The weather from a weather service: now (temperature, conditions, humidity, wind), today, tomorrow, or the "
        "next three days (forecast), for the user's location or a named place.",
        {"when": {"type": "string", "enum": WHEN},
         "place": {"type": "string", "description": "a city; omit for the user's own location"}},
        [], check_weather, speaks_for_itself=True, watch=False, announce=lambda a: "Checking the weather…",
    )]
