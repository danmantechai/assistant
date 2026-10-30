"""check_weather: open a one-line weather page (wttr.in) in the browser and read it off the screen with OCR.

The page is plain text ("Lisbon: Patchy rain nearby, +28°C, feels +32°C, humidity 75%"), which Windows
OCR reads reliably, except the degree sign: it comes back as "0" or "•" ("+280 C"). The page always
prints one, so the last character before "C" is dropped when it looks like one.

Only the current weather can be read this way. For a forecast the full page is opened for you to see,
and MARY says so instead of guessing.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

from mary import winutil
from mary.ocr import OcrLine, capture_window, ocr_pixels
from mary.skills._browser import browser_window, select_tab
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "The weather or temperature: check_weather. Answer from its result; never guess the weather."
SLOW = {"check_weather": ["Let me check the weather.", "One moment, checking the weather."]}

log = logging.getLogger(__name__)

FORMAT = "%l: %C, %t, feels %f, humidity %h"
_FORECAST_WORDS = re.compile(r"\b(tomorrow|tonight|later|weekend|next|week|forecast|going to|will it)\b", re.I)
# "+28°C" comes back as "+280 C", "+32 0 C", "+32 •C" or "+15 oc": digits, maybe the degree sign, then C.
_TEMP = re.compile(r"([+-]?)(\d{1,3})\s*([0oO°º•]?)\s*[Cc]\b")
_last_url: str | None = None  # the page MARY opened last, so its tab can be refreshed instead of reopened


@dataclass
class Weather:
    place: str
    condition: str
    temp: int
    feels: int | None
    humidity: int | None


def _degrees(sign: str, digits: str, degree: str) -> int:
    if not degree and len(digits) >= 2 and digits.endswith("0"):
        digits = digits[:-1]  # "+280 C": the "0" is the degree sign
    return int(sign + digits) if sign == "-" else int(digits)


def rows(lines: list[OcrLine]) -> str:
    """OCR splits one printed line into pieces ("Paris: Light rain shower," | "+240 C, feels…" |
    "humidity 70%"); put the pieces that sit on the same row back together, left to right."""
    out: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda l: l.cy):
        if out and abs(out[-1][0].cy - line.cy) < max(6.0, out[-1][0].h * 0.6):
            out[-1].append(line)
        else:
            out.append([line])
    return "\n".join(" ".join(l.text for l in sorted(row, key=lambda l: l.x)) for row in out)


def parse(text: str) -> Weather | None:
    """The weather from the page's OCR text, or None if it isn't readable (yet). Only the line holding
    the reading counts: the rest of the window (tabs, favourites, address bar) is noise."""
    for line in text.splitlines():
        if "format=" in line or "wttr.in" in line:
            continue
        head = re.search(r"([^:]{2,80}):\s*([^,]{2,60}),\s*[+-]?\d", line)
        temps = list(_TEMP.finditer(line))
        if not head or not temps:
            continue
        temp = _degrees(*temps[0].groups())
        feels = _degrees(*temps[1].groups()) if len(temps) > 1 else None
        if not -40 <= temp <= 55 or (feels is not None and abs(feels - temp) > 15):
            return None  # a misread; better to say so than to say something wrong
        humidity = re.search(r"hum\s*i\s*dity\s*(\d{1,3})\s*%", line, re.I)
        return Weather(head.group(1).strip(), head.group(2).strip(), temp, feels,
                       int(humidity.group(1)) if humidity else None)
    return None


def spoken(w: Weather, place: str = "") -> str:
    """'It's 28 degrees, feeling like 32. Patchy rain nearby.' The condition gets its own sentence, since
    the site's wording ("Mist", "Light rain shower") doesn't fit "and …". The place is said only when you
    named one (for your own location the site's region name, e.g. "Greater London, GB", gets in the way)."""
    feels = f", feeling like {w.feels}" if w.feels is not None and w.feels != w.temp else ""
    where = f"In {place.strip().title()} it's" if place else "It's"
    condition = w.condition[:1].upper() + w.condition[1:]
    return f"{where} {w.temp} degrees{feels}. {condition}."


def _reuse_tab(url: str) -> winutil.WindowInfo | None:
    """Show `url` in the weather tab that's already open (refresh, or point it at another place), so
    asking about the weather doesn't pile up tabs. None if there's no such tab."""
    win = browser_window("wttr")
    if win is None and select_tab("wttr"):
        time.sleep(0.4)
        win = browser_window("wttr")
    if win is None or not winutil.focus_window(win.hwnd):
        return None
    time.sleep(0.2)

    def still_there() -> bool:  # keys only ever go to that tab, checked right before each one
        fg = winutil.foreground_window()
        return bool(fg and fg.hwnd == win.hwnd and "wttr" in fg.title.lower())

    import pyautogui
    if not still_there():
        return None
    if url == _last_url:
        pyautogui.press("f5")
        return win
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.15)
    if not still_there():
        return None
    pyautogui.write(url)
    pyautogui.press("enter")
    return win


def _open(url: str) -> winutil.WindowInfo | None:
    """Show `url` in the browser: in the existing weather tab if there is one, else a new tab."""
    global _last_url
    win = _reuse_tab(url)
    _last_url = url
    if win:
        return win
    os.startfile(url)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        time.sleep(0.5)
        win = browser_window("wttr")
        if win:
            return win
    return None


def tools(ctx: ToolContext) -> list[Tool]:
    def check_weather(when: str = "now", place: str = "") -> ToolResult:
        place = (place or ctx.cfg.get("weather", {}).get("place") or "").strip()
        where = quote(place.replace(" ", "+"), safe="+")
        if when != "now" or _FORECAST_WORDS.search(ctx.request or ""):
            _open(f"https://wttr.in/{where}?m")
            return ToolResult(True, "Opened the forecast page in the browser. Forecasts can't be read off the "
                                    "screen; only the current weather can.",
                              say="I can only read the current weather so far, so I've opened the forecast "
                                  "in your browser.")
        win = _open(f"https://wttr.in/{where}?format={quote(FORMAT)}&m")
        # Read only once the address bar shows this page: right after opening, the window can still show
        # the previous one (another place, or the last reading), and that must never be reported.
        marker = f"wttr.in/{where}?".lower()
        if win is None:
            return ToolResult(False, "The weather page didn't open in the browser.",
                              say="The weather page didn't open. Is the internet connected?")
        weather, text = None, ""
        deadline = time.monotonic() + 12  # the page may still be loading
        while weather is None and time.monotonic() < deadline:
            time.sleep(0.8)
            text = rows(ocr_pixels(capture_window(win.hwnd)))
            if marker in text.lower().replace(" ", ""):
                weather = parse(text)
            if re.search(r"unknown location|sorry|not found", text, re.I):
                break
        if weather is None:
            log.info("weather page unreadable: %r", text[:300])
            return ToolResult(False, "Couldn't read the weather page; it's open in the browser.",
                              say="I've opened the weather, but I couldn't read it clearly. It's in your browser.")
        humidity = f", humidity {weather.humidity}%" if weather.humidity is not None else ""
        return ToolResult(True, f"Weather in {weather.place}: {weather.condition}, {weather.temp}°C, feels like "
                                f"{weather.feels}°C{humidity}.", say=spoken(weather, place))

    return [Tool(
        "check_weather",
        "Current weather (temperature, conditions, humidity) for the user's location or a named place, read "
        "from a weather page. Forecasts are only opened in the browser.",
        {"when": {"type": "string", "enum": ["now", "forecast"]},
         "place": {"type": "string", "description": "a city; omit for the user's own location"}},
        [], check_weather, speaks_for_itself=True, announce=lambda a: "Checking the weather…",
    )]
