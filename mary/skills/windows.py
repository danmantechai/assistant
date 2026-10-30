"""window_control: minimize / maximize / restore / snap / focus."""
from __future__ import annotations

from pathlib import Path

from rapidfuzz import fuzz, utils

from mary import winutil
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Arranging windows (minimise, maximise, snap left or right, switch to one): window_control."
GROUNDING = [
    ("window_control", lambda a: True,
     r"\b(minimi[sz]e|maximi[sz]e|snap|restore|switch|focus|bring|move|hide|show|full ?screen|left|right|window|"
     r"go to|front)", "the user didn't ask to arrange windows"),
]

ACTIONS = ["minimize", "maximize", "restore", "snap_left", "snap_right", "focus"]


def means_current(query: str | None) -> bool:
    return not query or query.lower() in ("this", "it", "current", "active", "this window")


def find_window(query: str | None) -> winutil.WindowInfo | None:
    """The best-matching open window, or the foreground window when no target is given."""
    if means_current(query):
        return winutil.foreground_window()
    return best_match(query, winutil.list_windows())


def best_match(query: str, windows) -> winutil.WindowInfo | None:
    best, best_score = None, 0.0
    for w in windows:
        score = max(fuzz.WRatio(query, Path(w.exe).stem, processor=utils.default_process),
                    fuzz.WRatio(query, w.title, processor=utils.default_process))
        if score > best_score:
            best, best_score = w, score
    return best if best_score >= 75 else None


def tools(ctx: ToolContext) -> list[Tool]:
    def window_control(action: str, target: str | None = None) -> ToolResult:
        if action not in ACTIONS:
            return ToolResult(False, f"Unknown action '{action}'. Use one of {ACTIONS}.")
        win = find_window(target)
        if win is None:
            return ToolResult(False, f"No open window matches '{target}'.")
        label = win.title if len(win.title) <= 40 else Path(win.exe).stem
        if action == "focus":
            ok = winutil.focus_window(win.hwnd)
            return ToolResult(ok, f"Switched to {label}." if ok else f"Windows refused to focus {label}.",
                              say=f"Here's {label}.")
        if action.startswith("snap_"):
            winutil.snap_window(win.hwnd, action.split("_")[1])
        else:
            winutil.show_window(win.hwnd, action)
        past = {"minimize": "Minimized", "maximize": "Maximized", "restore": "Restored",
                "snap_left": "Snapped left:", "snap_right": "Snapped right:"}[action]
        return ToolResult(True, f"{past} {label}.", say="Done.")

    return [Tool(
        "window_control",
        "Minimize, maximize, restore, snap left/right half, or focus (switch to) a window. "
        "Without target it acts on the active window.",
        {"action": {"type": "string", "enum": ACTIONS},
         "target": {"type": "string", "description": "app name or window title; omit for the active window"}},
        ["action"], window_control, speaks_for_itself=True,
        announce=lambda a: f"{a.get('action', '').replace('_', ' ').capitalize()} {a.get('target') or 'window'}…",
    )]
