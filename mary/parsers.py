"""Small natural-language parsers shared by tools and (later) the router's slot extraction.

Pure functions with no Windows dependencies, so they are easy to unit-test.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_UNITS = {
    "ones": {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
             "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
             "nineteen": 19},
    "tens": {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
             "eighty": 80, "ninety": 90},
}


def words_to_numbers(text: str) -> str:
    """'twenty five minutes' -> '25 minutes', 'a minute' -> '1 minute', 'a couple of' -> '2'."""
    text = text.lower()
    text = re.sub(r"\ba couple( of)?\b", "2", text)
    text = re.sub(r"\ba few\b", "3", text)
    text = re.sub(r"\b(a|an|one)\s+hundred\b", "100", text)

    def tens(m: re.Match) -> str:
        value = _UNITS["tens"][m.group(1)]
        if m.group(2):
            value += _UNITS["ones"][m.group(2)]
        return str(value)

    ones_alt = "|".join(k for k in _UNITS["ones"] if 1 <= _UNITS["ones"][k] <= 9)
    text = re.sub(rf"\b({'|'.join(_UNITS['tens'])})(?:[\s-]+({ones_alt}))?\b", tens, text)
    text = re.sub(rf"\b({'|'.join(_UNITS['ones'])})\b", lambda m: str(_UNITS["ones"][m.group(1)]), text)
    # "a"/"an" directly before a unit means one: "an hour", "a minute".
    text = re.sub(r"\b(a|an)\s+(?=(hour|hr|minute|min|second|sec|day|week|month)s?\b)", "1 ", text)
    return text


_DURATION_UNITS = {
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}


def parse_duration(text: str | int | float) -> int | None:
    """Duration in seconds from '5 minutes', '90s', '1h30m', 'a minute and a half',
    'half an hour', 'quarter of an hour', '00:05:00'. Bare numbers are seconds. None if unparseable."""
    if isinstance(text, (int, float)):
        return int(text) if text > 0 else None
    raw = str(text).strip().lower()
    if not raw:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return int(float(raw)) or None
    clock = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", raw)
    if clock:
        h, m, s = (int(g) if g else 0 for g in clock.groups())
        return h * 3600 + m * 60 + s or None

    # Fractions first, before "an hour" is rewritten to "1 hour".
    text = re.sub(r"\bhalf an? (hour|minute)\b", r"0.5 \1", raw)
    text = re.sub(r"\b(?:a )?quarter (?:of an? )?hour\b", "0.25 hour", text)
    text = words_to_numbers(text)
    text = re.sub(r"(\d+) and a half (?=[a-z])", r"\1.5 ", text)  # "1 and a half hours"
    total = 0.0
    unit_re = "|".join(sorted(_DURATION_UNITS, key=len, reverse=True))
    for m in re.finditer(rf"(\d+(?:\.\d+)?)\s*({unit_re})(?![a-z])(\s+and\s+a\s+half)?", text):
        amount = float(m.group(1)) + (0.5 if m.group(3) else 0)
        total += amount * _DURATION_UNITS[m.group(2)]
    return int(round(total)) or None


def format_duration(seconds: int) -> str:
    """90 -> '1 minute 30 seconds'."""
    parts = []
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        n, seconds = divmod(seconds, size)
        if n:
            parts.append(f"{n} {unit}{'s' if n != 1 else ''}")
    return " ".join(parts) or "0 seconds"


def parse_time_window(text: str | None, now: datetime | None = None) -> datetime | None:
    """Cut-off datetime for 'modified within' phrases: '7d', '24h', '2w', 'today', 'yesterday',
    'this week', 'last 3 days', 'this month', 'recently'. 'This week' and 'last week' both mean
    the last 7 days (friendlier than calendar weeks). None means no limit / unparseable."""
    if not text:
        return None
    now = now or datetime.now()
    t = words_to_numbers(str(text).strip().lower())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if t in ("today", "since today"):
        return midnight
    if t == "yesterday":
        return midnight - timedelta(days=1)
    if re.search(r"\b(this|last|past) week\b|\brecent(ly)?\b", t):
        return now - timedelta(days=7)
    if re.search(r"\bthis month\b", t):
        return midnight.replace(day=1)
    if re.search(r"\b(last|past) month\b", t):
        return now - timedelta(days=31)
    if re.search(r"\bthis year\b", t):
        return midnight.replace(month=1, day=1)
    if re.search(r"\b(last|past) year\b", t):
        return now - timedelta(days=365)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mos?|months?|mins?|minutes?|m|h|hrs?|hours?|d|days?|"
                  r"w|wks?|weeks?|y|yrs?|years?)\b", t)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    key = "mo" if unit.startswith("mo") else unit[0]
    days = {"m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 31, "y": 365}[key]
    return now - timedelta(days=n * days)


_UP = re.compile(r"\b(up|louder|raise|increase|higher|boost|crank|pump|more|brighter|lighten|brighten)\b|"
                 r"can'?t hear|too (quiet|soft|low|dark|dim)|can'?t see", re.I)
_DOWN = re.compile(r"\b(down|quieter|lower|decrease|reduce|softer|less|dim|dimmer|darker|darken)\b|"
                   r"too (loud|bright|high)", re.I)
_LARGE = re.compile(r"\b(a lot|lots|way|much|significantly|loads)\b", re.I)


def parse_level(text: str) -> tuple[str, int | None] | None:
    """Volume/brightness change from words: ('up'|'down', step) | ('set', level) | ('mute'|'unmute', None).

    'a bit' = 10, 'a lot' = 25, 'max' = 100, 'half' = 50; 'to 30' / '30 percent' set a level and
    'up by 20' is a step. None when no clear change is asked for.
    """
    t = words_to_numbers(text.lower())
    if re.search(r"\bunmute\b|\bsound (back )?on\b|\bturn (the )?sound (back )?on\b", t):
        return ("unmute", None)
    if re.search(r"\bmute\b|\bsilence\b", t):
        return ("mute", None)
    if re.search(r"\b(max(imum)?|full|all the way up|as (loud|bright) as)\b", t):
        return ("set", 100)
    if re.search(r"\b(min(imum)?|all the way down|zero)\b", t):
        return ("set", 0)
    if re.search(r"\b(half(way)?|middle)\b", t):
        return ("set", 50)
    to_level = re.search(r"\b(?:to|at)\s+(\d{1,3})\b", t)
    if to_level:
        return ("set", min(100, int(to_level.group(1))))
    number = re.search(r"\b(\d{1,3})\s*(%|percent)?", t)
    direction = "up" if _UP.search(t) else "down" if _DOWN.search(t) else None
    if direction:
        if number:
            step = int(number.group(1))
        else:
            step = 25 if _LARGE.search(t) else 10
        return (direction, max(1, min(100, step)))
    if number:  # "volume 50 percent", "brightness 40"
        return ("set", min(100, int(number.group(1))))
    return None


_KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "windows": "win", "cmd": "win", "super": "win", "meta": "win",
    "option": "alt", "escape": "esc", "return": "enter", "del": "delete", "ins": "insert",
    "pgup": "pageup", "page up": "pageup", "pgdn": "pagedown", "page down": "pagedown",
    "spacebar": "space", "arrow up": "up", "arrow down": "down", "arrow left": "left",
    "arrow right": "right", "prtsc": "printscreen", "print screen": "printscreen",
}


def parse_hotkey(text: str) -> list[str]:
    """'Ctrl+Shift+Esc' / 'ctrl shift esc' / 'Win-D' -> ['ctrl', 'shift', 'esc'] (pyautogui names)."""
    t = text.strip().lower()
    for phrase, alias in _KEY_ALIASES.items():
        if " " in phrase:
            t = t.replace(phrase, alias)
    parts = [p for p in re.split(r"\s*[+\-]\s*|\s+", t) if p]
    return [_KEY_ALIASES.get(p, p) for p in parts]
