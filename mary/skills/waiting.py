"""Brick: wait for an app or page that's still starting or loading (wait), the model's part of the feedback loop.

Every action already waits by itself until what it changed has settled (mary/ready.py), and each result ends with
what's on screen. When that line says something is still starting, the model calls wait and then carries on with
the same step, instead of trying another way (or another app).
"""
from __future__ import annotations

from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("Each action's result ends with what's on screen now. If it says an app or page is still starting or "
         "loading, call wait with its name, then try the same step again; don't switch to another way of doing it.")

MAX_S = 30  # per call; mary/ready.py caps a whole request at 60 s


def tools(ctx: ToolContext) -> list[Tool]:
    def wait(target: str = "", seconds: int | float = 10) -> ToolResult:
        try:
            seconds = max(1.0, min(float(seconds), MAX_S))
        except (TypeError, ValueError):
            seconds = 10.0
        loop = ctx.loop
        if loop is None:
            return ToolResult(False, "Waiting isn't available.")
        if not target:
            took = loop.pause(seconds)
            return ToolResult(True, f"Waited {took:.0f} s.")
        r = loop.wait_for(target, seconds)
        if r.state == "ready":
            return ToolResult(True, f"Waited {r.seconds:.0f} s. {r.line()} Carry on with the step.")
        if r.state == "gone":
            return ToolResult(False, f"{r.line()} It may have closed or crashed.")
        if r.state == "starting":
            return ToolResult(False, f"Waited {r.seconds:.0f} s. {r.line()} Tell the user it's taking a while.")
        return ToolResult(False, "Nothing to wait for: no budget left for this request, or no such window.")

    return [Tool(
        "wait",
        "Wait until an app or page that's still starting or loading is ready (or just pause), then continue.",
        {"target": {"type": "string", "description": "the app or window to wait for, e.g. 'Spotify'"},
         "seconds": {"type": "number", "description": f"at most this long (default 10, up to {MAX_S})"}},
        [], wait, watch=False, announce=lambda a: f"Waiting for {a.get('target') or 'a moment'}…")]
