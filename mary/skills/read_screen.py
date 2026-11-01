"""read_screen: what a window shows, read by layout (mary/page.py): the area you're working in (or the one asked
for) in full and in order, the other areas in a line each. Names and places come from UI Automation, and anything
it leaves out from the window's picture (OCR)."""
from __future__ import annotations

import logging

from mary import winutil
from mary.tools.registry import Tool, ToolContext, ToolResult
from mary.skills.windows import find_window

GUIDE = ('"What\'s on my screen", reading a window or page aloud, or questions about what it shows: read_screen. '
         "It reads one area in full; to read another, call it again with part (e.g. 'left panel', or words that "
         "area shows). Answer only from what it read; if what was asked isn't there, say so.")
SLOW = {"read_screen": ["Let me have a look.", "One second, let me read it."]}

log = logging.getLogger(__name__)


def _target_window(target: str | None) -> winutil.WindowInfo | None:
    if target:
        return find_window(target)
    fg = winutil.foreground_window()
    if fg and not winutil.is_own_console(fg.hwnd):
        return fg
    # Text mode: MARY's terminal has focus, so use the window the user was in before it.
    return next((w for w in winutil.list_windows() if not winutil.is_own_console(w.hwnd)), None)


def _budget(cfg) -> int:
    """Characters read in full: fewer for a model on this PC, where every token costs ~30 ms on a CPU."""
    from mary.llm.server import is_cloud

    brain = (cfg.get("brain") or {}).get("provider", "ollama")
    return 4000 if brain != "ollama" or is_cloud(cfg.llm.model) else 1500


def tools(ctx: ToolContext) -> list[Tool]:
    def read_screen(target: str | None = None, part: str | None = None) -> ToolResult:
        from mary.page import page_for

        win = _target_window(target)
        if win is None:
            return ToolResult(False, f"No window matches '{target}'." if target else "No window to read.")
        if winutil.user32.IsIconic(win.hwnd):
            winutil.show_window(win.hwnd, "restore")
        try:
            page = page_for(win)
        except Exception as e:
            log.warning("couldn't read the window: %s", e)
            return ToolResult(False, f"I couldn't read '{win.title}': {e}")
        if not page.regions:  # maybe still loading: the feedback loop tries again while it changes
            return ToolResult(False, f"I couldn't read any text in '{win.title}'.", status="not_yet")
        return ToolResult(True, f"Window '{win.title}':\n{page.render(part, _budget(ctx.cfg))}")

    return [Tool(
        "read_screen",
        "Read what a window shows, to answer questions about what's on screen. It reads by layout: the area the "
        "user is working in (or the one asked for) in full, the other areas in brief. Without target it reads the "
        "window the user is looking at.",
        {"target": {"type": "string", "description": "app name or window title; omit for the current window"},
         "part": {"type": "string", "description": "an area to read in full: 'left panel', 'main area', 'top "
                                                   "bar', or words that area shows (e.g. a chat's name)"}},
        [], read_screen, private=True, acts_on="target",
        announce=lambda a: f"Reading {a.get('part') or a.get('target') or 'the screen'}…",
    )]
