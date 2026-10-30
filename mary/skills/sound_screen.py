"""Brick: volume, mute, screen brightness, and locking the PC (system_control)."""
from __future__ import annotations

from mary import winutil
from mary.parsers import parse_level
from mary.reflexes import Reflex
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Volume, mute, screen brightness, and locking the PC: system_control."
GROUNDING = [
    # Given a misheard "It's the current grader", qwen3.5:2b set the volume to 100%; actions need words of their kind.
    ("system_control", lambda a: str(a.get("action", "")).startswith(("volume", "mute", "unmute")),
     r"\b(volume|sound|audio|loud|louder|quiet|quieter|softer|mute|unmute|silence|hear|speakers?|crank|"
     r"turn (it|that|this|the \w+ )?(up|down)|up|down|max|half|percent|%|\d+)", "the user didn't ask about the sound"),
    ("system_control", lambda a: str(a.get("action", "")).startswith("brightness"),
     r"\b(bright|brighter|brightness|dim|dimmer|dark|darker|screen|display|light|see)", "the user didn't ask about "
     "the screen brightness"),
    ("system_control", lambda a: a.get("action") == "lock",
     r"\b(lock|locking|stepping away|secure)\b", "the user didn't ask to lock the PC"),
]


def _level_args(kind: str):
    """'turn it down a bit' -> {'action': 'volume_down', 'value': 10}; None if no clear change."""
    def args(text: str) -> dict | None:
        level = parse_level(text)
        if level is None:
            return None
        op, value = level
        if op in ("mute", "unmute"):
            return {"action": op} if kind == "volume" else None
        return {"action": f"{kind}_{op}", "value": value}
    return args


_AMOUNT = r"(?: (?:a bit|a little|a little bit|a lot|some|more|a bit more|a little more|by \d{1,3}(?: ?percent|%)?))?"
REFLEXES = [
    Reflex(r"(?:(?:turn|crank|put) )?(?:the )?(?:volume|sound|music|audio|it) (?:up|down)" + _AMOUNT
           + r"|(?:louder|quieter|softer)" + _AMOUNT + r"|make it (?:louder|quieter|softer)" + _AMOUNT
           + r"|(?:set |put |turn )?(?:the )?volume (?:to |at )?(?:\d{1,3}(?: ?percent|%)?|max|maximum|full|half)"
           + r"|(?:mute|unmute)(?: (?:the )?(?:sound|volume|audio|it|music|speakers))?",
           "system_control", _level_args("volume")),
    Reflex(r"(?:(?:turn|set) )?(?:the )?(?:screen |display )?brightness (?:up|down)" + _AMOUNT
           + r"|make (?:the |my )?(?:screen|display) (?:brighter|dimmer|darker)" + _AMOUNT
           + r"|(?:dim|brighten)(?: (?:the|my))? (?:screen|display)" + _AMOUNT + r"|brighter|dimmer"
           + r"|(?:set )?(?:the )?(?:screen )?brightness (?:to |at )?(?:\d{1,3}(?: ?percent|%)?|max|maximum|full|half)",
           "system_control", _level_args("brightness")),
    Reflex(r"lock (?:the |my )?(?:pc|computer|laptop|screen|workstation)|lock it(?: up)?|lock up",
           "system_control", lambda text: {"action": "lock"}),
]

SYSTEM_ACTIONS = ["volume_up", "volume_down", "volume_set", "mute", "unmute",
                  "brightness_up", "brightness_down", "brightness_set", "lock"]


def _endpoint():
    winutil.ensure_com()
    from pycaw.pycaw import AudioUtilities  # comtypes import is slowish; load on first use

    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> tuple[int, bool]:
    """(percent, muted) of the default speakers."""
    ep = _endpoint()
    return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())


def set_volume(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(percent / 100, None)
    if percent > 0 and ep.GetMute():
        ep.SetMute(0, None)
    return percent


def get_brightness() -> int:
    import screen_brightness_control as sbc

    return int(sbc.get_brightness(display=0)[0])


def set_brightness(percent: int) -> int:
    import screen_brightness_control as sbc

    percent = max(0, min(100, int(percent)))
    sbc.set_brightness(percent, display=0)
    return percent


def _as_int(value, default: int) -> int:
    try:
        return int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        return default


def tools(ctx: ToolContext) -> list[Tool]:
    def system_control(action: str, value: int | str | None = None) -> ToolResult:
        if action not in SYSTEM_ACTIONS:
            return ToolResult(False, f"Unknown action '{action}'. Use one of {SYSTEM_ACTIONS}.")
        if action == "lock":
            winutil.user32.LockWorkStation()
            return ToolResult(True, "Locked the PC.", say="Locking up.")
        if action in ("mute", "unmute"):
            _endpoint().SetMute(1 if action == "mute" else 0, None)
            return ToolResult(True, f"{action.capitalize()}d.", say="Muted." if action == "mute" else "Sound's back.")

        kind, op = action.split("_")
        current = get_volume()[0] if kind == "volume" else get_brightness()
        if op == "set":
            if value is None:
                return ToolResult(False, f"{action} needs a value from 0 to 100.")
            target = _as_int(value, current)
        else:
            step = _as_int(value, 10) if value is not None else 10
            target = current + step if op == "up" else current - step
        new = set_volume(target) if kind == "volume" else set_brightness(target)
        return ToolResult(True, f"{kind.capitalize()} {current}% -> {new}%.",
                          say=f"{'Volume' if kind == 'volume' else 'Brightness'}'s at {new}%.")

    return [
        Tool("system_control",
             "Volume, mute, screen brightness, or lock the PC. For *_up/*_down, value is the step "
             "(default 10; 'a bit' ~ 10, 'a lot' ~ 25). For *_set, value is the level 0-100.",
             {"action": {"type": "string", "enum": SYSTEM_ACTIONS},
              "value": {"type": "integer", "description": "step or level, 0-100"}},
             ["action"], system_control, speaks_for_itself=True,
             announce=lambda a: f"{a.get('action', '').replace('_', ' ').capitalize()}"
                                f"{' ' + str(a['value']) if 'value' in a else ''}…"),
    ]
