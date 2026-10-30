"""read_screen: the text of a window, via UI Automation, or OCR when the app doesn't expose it."""
from __future__ import annotations

from mary import uia, winutil
from mary.ocr import lines_to_text, ocr_window
from mary.tools.registry import Tool, ToolContext, ToolResult
from mary.skills.windows import find_window

GUIDE = '"What\'s on my screen", reading a window or page aloud, or questions about what it shows: read_screen.'
SLOW = {"read_screen": ["Let me have a look.", "One second, let me read it."]}

_MIN_UIA_CHARS = 120  # less than this usually means a browser/canvas app: use OCR instead


def _target_window(target: str | None) -> winutil.WindowInfo | None:
    if target:
        return find_window(target)
    fg = winutil.foreground_window()
    if fg and not winutil.is_own_console(fg.hwnd):
        return fg
    # Text mode: MARY's terminal has focus, so use the window the user was in before it.
    return next((w for w in winutil.list_windows() if not winutil.is_own_console(w.hwnd)), None)


def tools(ctx: ToolContext) -> list[Tool]:
    limit = 900  # screen text is long and every token costs ~30 ms of prompt processing on CPU

    def read_screen(target: str | None = None) -> ToolResult:
        win = _target_window(target)
        if win is None:
            return ToolResult(False, f"No window matches '{target}'." if target else "No window to read.")
        if winutil.user32.IsIconic(win.hwnd):
            winutil.show_window(win.hwnd, "restore")
        try:
            text, source = uia.window_text(win.hwnd), "ui"
        except Exception:
            text, source = "", "ui"
        if len(text) < _MIN_UIA_CHARS:
            text, source = lines_to_text(ocr_window(win.hwnd)), "ocr"
        if not text.strip():
            return ToolResult(False, f"I couldn't read any text in '{win.title}'.")
        if len(text) > limit:
            text = text[:limit] + "\n…(more not shown)"
        return ToolResult(True, f"Window '{win.title}' ({source}):\n{text}")

    return [Tool(
        "read_screen",
        "Read the text shown in a window, to answer questions about what's on screen. "
        "Without target it reads the window the user is looking at.",
        {"target": {"type": "string", "description": "app name or window title; omit for the current window"}},
        [], read_screen, private=True, announce=lambda a: f"Reading {a.get('target') or 'the screen'}…",
    )]
