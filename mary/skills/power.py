"""Brick: shut down, restart, sleep, hibernate or sign out (power), and cancelling a pending shutdown.

Old mode: always asks first. New mode: when his words clearly asked (the grounding check), shutting down or
restarting starts a 10-second countdown he can stop by saying "stop"; sleep follows after 3 seconds, so she
can say so first. Signing out still asks: it closes every app at once.
"""
from __future__ import annotations

import ctypes
import subprocess
import threading
import time

from mary.brain import mode as brain_mode
from mary.reflexes import Reflex
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Shut down, restart, sleep or sign out: power, only when he asks for exactly that."
GROUNDING = [
    ("power", lambda a: True,
     r"\b(shut ?down|turn (it |the pc |the computer )?off|power off|switch off|restart|reboot|sleep|"
     r"hibernate|sign (me )?out|log ?(me )?(out|off))\b", "the user didn't ask to shut down or restart"),
    ("cancel_power", lambda a: True, r"\b(stop|cancel|abort|wait|don'?t|no|hold on)\b",
     "the user didn't ask to cancel anything"),
]

POWER_ACTIONS = ["shutdown", "restart", "sleep", "hibernate", "sign_out"]
COUNTDOWN_S = 10
_pending = {"until": 0.0}  # when the countdown of a pending shutdown/restart ends (monotonic)


def _cancel_args(text: str) -> dict | None:
    """'stop' cancels a shutdown only while one is counting down; otherwise it's the media reflex's."""
    return {} if time.monotonic() < _pending["until"] else None


REFLEXES = [
    Reflex(r"(?:stop|cancel|abort|wait|hold on)(?: (?:it|that|the shutdown|the restart|shutting down|"
           r"restarting|now))?", "cancel_power", _cancel_args, first=True),
]


def tools(ctx: ToolContext) -> list[Tool]:
    new_mode = brain_mode(ctx.cfg) == "new"
    flags = subprocess.CREATE_NO_WINDOW

    def power(action: str) -> ToolResult:
        if action not in POWER_ACTIONS:
            return ToolResult(False, f"Unknown power action '{action}'. Use one of {POWER_ACTIONS}.")
        wait = COUNTDOWN_S if new_mode else 5
        if action in ("shutdown", "restart"):
            subprocess.Popen(["shutdown", "/s" if action == "shutdown" else "/r", "/t", str(wait)], creationflags=flags)
            if new_mode:
                _pending["until"] = time.monotonic() + wait
                verb = "Shutting down" if action == "shutdown" else "Restarting"
                return ToolResult(True, f"{verb} in {wait} seconds. He can say stop to cancel.",
                                  say=f"{verb} in {wait} seconds. Say stop to cancel.")
        elif action == "sign_out":
            subprocess.Popen(["shutdown", "/l"], creationflags=flags)
        else:  # SetSuspendState(hibernate, force, disable_wake_events), a moment later so she can say so
            def suspend():
                ctypes.WinDLL("powrprof").SetSuspendState(action == "hibernate", False, False)
            if new_mode:
                threading.Timer(3, suspend).start()
                return ToolResult(True, f"Going to {action} in 3 seconds.", say="Good night. Going to sleep now.")
            suspend()
        return ToolResult(True, f"{action} started.", say="See you soon.")

    def cancel_power() -> ToolResult:
        if time.monotonic() >= _pending["until"]:
            return ToolResult(True, "No shutdown or restart was pending.", say="There's nothing to cancel.")
        subprocess.Popen(["shutdown", "/a"], creationflags=flags)
        _pending["until"] = 0.0
        return ToolResult(True, "Cancelled the pending shutdown/restart.", say="Okay, cancelled. I'm staying on.")

    found = [
        Tool("power", "Shut down, restart, sleep, hibernate or sign out of the PC.",
             {"action": {"type": "string", "enum": POWER_ACTIONS}},
             ["action"], power, tier=2, speaks_for_itself=True, watch=False,
             announce=lambda a: f"Power: {a.get('action', '')}…",
             confirm_text=lambda a: f"{a.get('action', 'shut down').replace('_', ' ')} the PC",
             new_tier=lambda a: 2 if a.get("action") == "sign_out" else 1,
             ask=lambda a: "Signing out closes all your apps, and anything unsaved is lost. Sign out now?"),
    ]
    if new_mode:  # only the new mode counts down (the old one asks first, then shuts down in 5 s)
        found.append(Tool("cancel_power", "Cancel a shutdown or restart that is counting down.", {}, [],
                          cancel_power, watch=False, announce=lambda a: "Cancelling the shutdown…"))
    return found
