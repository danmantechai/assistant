"""Brick: countdown timers that ring when done (set_timer)."""
from __future__ import annotations

import threading
import time
import winsound

from mary.parsers import format_duration, parse_duration
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Timers and reminders: set_timer."
GROUNDING = [
    ("set_timer", lambda a: True,
     r"\b(timer|remind|reminder|alarm|countdown|wake me|minutes?|seconds?|hours?|mins?|secs?)\b",
     "the user didn't ask for a timer"),
]


def tools(ctx: ToolContext) -> list[Tool]:
    def set_timer(duration: str, label: str | None = None) -> ToolResult:
        seconds = parse_duration(duration)
        if not seconds:
            return ToolResult(False, f"Couldn't understand the duration '{duration}'. Ask how long.")
        name = label or format_duration(seconds) + " timer"

        def ring() -> None:
            ctx.output.notify(f"Time's up: {name}.")
            for _ in range(ctx.cfg.timers.alarm_beeps):
                winsound.Beep(880, 250)
                time.sleep(0.15)

        timer = threading.Timer(seconds, ring)
        timer.daemon = True
        timer.start()
        ctx.timers.append((timer, name, time.time() + seconds))
        spoken = format_duration(seconds)
        return ToolResult(True, f"Timer set for {spoken} ({name}).",
                          say=f"Timer set for {spoken}{' for ' + label if label else ''}.")

    return [
        Tool("set_timer", "Start a countdown timer that alerts when done.",
             {"duration": {"type": "string", "description": "e.g. '5 minutes', '90 seconds', '1h30m'"},
              "label": {"type": "string", "description": "optional, e.g. 'pasta'"}},
             ["duration"], set_timer, speaks_for_itself=True,
             announce=lambda a: f"Setting a {a.get('duration', '')} timer…"),
    ]
