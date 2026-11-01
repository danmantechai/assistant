"""keyboard: type text or press a hotkey in the focused window."""
from __future__ import annotations

import time

from mary import winutil
from mary.parsers import parse_hotkey
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Typing text or pressing keys in the active window: keyboard. To type into a box on a page, use screen."
GROUNDING = [
    ("keyboard", lambda a: True,
     r"\b(type|typing|write|enter|put|dictate|press|key|keys|hotkey|shortcut|ctrl|control|alt|shift|paste|copy|"
     r"undo|select)\b", "the user didn't ask to type or press keys"),
]

# Hotkeys that can lose work, so they need a yes first.
_RISKY_HOTKEYS = {("alt", "f4"), ("ctrl", "w"), ("ctrl", "shift", "w"), ("ctrl", "q")}
_PYAUTOGUI_NAMES = {"win": "winleft"}


def _wait_for_target_window(ctx: ToolContext, seconds: float = 6.0) -> bool:
    """In text mode MARY's own terminal has focus; give the user a moment to click elsewhere."""
    fg = winutil.foreground_window()
    if not fg or not winutil.is_own_console(fg.hwnd):
        return True
    ctx.output.tool_log(f"▸ Click the window you want me to use ({seconds:.0f} s)…")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.2)
        fg = winutil.foreground_window()
        if fg and not winutil.is_own_console(fg.hwnd):
            time.sleep(0.3)  # let the click finish
            return True
    return False


_SETTLE_S = 1.2  # measured: Windows 11 Notepad garbles keys typed within ~0.5 s of its window appearing


def _settle_if_just_opened(ctx: ToolContext, target: winutil.WindowInfo) -> None:
    """A window that has only just appeared may not be ready for keys yet: they get lost or garbled."""
    if ctx.last_launch and ctx.last_launch[0] == target.hwnd:
        winutil.wait_input_idle(target.pid)
        remaining = ctx.last_launch[1] + _SETTLE_S - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def _landed(hwnd: int, text: str) -> bool | None:
    """Did the text arrive? None when the window doesn't expose its text (then we can't tell)."""
    try:
        from mary import uia

        time.sleep(0.3)
        win = uia.window_for(hwnd)
        editors = win.descendants(control_type="Document") + win.descendants(control_type="Edit")
        contents = [e.window_text() for e in editors]
    except Exception:
        return None
    if not any(contents):
        return None
    probe = text.strip()[-30:]  # the end of what we typed is the part most likely to be cut
    return any(probe in c for c in contents)


def tools(ctx: ToolContext) -> list[Tool]:
    long_text = ctx.cfg.safety.long_text_chars

    def keyboard(action: str, text: str) -> ToolResult:
        if action not in ("type", "hotkey"):
            return ToolResult(False, "action must be 'type' or 'hotkey'.")
        if action == "hotkey":
            import pyautogui

            keys = [_PYAUTOGUI_NAMES.get(k, k) for k in parse_hotkey(text)]
            unknown = [k for k in keys if k not in pyautogui.KEYBOARD_KEYS]
            if not keys or unknown:
                return ToolResult(False, f"Unknown key(s): {unknown or text}.")
        if not _wait_for_target_window(ctx):
            return ToolResult(False, "Focus stayed on MARY's own terminal, so nothing was typed.")
        target = winutil.foreground_window()
        _settle_if_just_opened(ctx, target)
        if action == "type":
            winutil.type_unicode(text)
            if _landed(target.hwnd, text) is False:
                return ToolResult(True, f"Typed into '{target.title}', but the text there doesn't match.",
                                  say="I typed it, but please check it came through cleanly.")
            return ToolResult(True, f"Typed {len(text)} characters into '{target.title}'.", say="Typed it.")
        import pyautogui

        pyautogui.FAILSAFE = False  # we never move the mouse, so the corner fail-safe only gets in the way
        pyautogui.hotkey(*keys)
        return ToolResult(True, f"Pressed {'+'.join(keys)} in '{target.title}'.", say="Done.")

    def tier(args: dict) -> int:
        if args.get("action") == "type":
            return 2 if len(args.get("text", "")) > long_text else 1
        return 2 if tuple(parse_hotkey(args.get("text", ""))) in _RISKY_HOTKEYS else 1

    return [Tool(
        "keyboard",
        "Type text into the focused window, or press a key combination like 'ctrl+s', 'win+d', 'alt+tab'.",
        {"action": {"type": "string", "enum": ["type", "hotkey"]},
         "text": {"type": "string", "description": "text to type, or keys joined with +"}},
        ["action", "text"], keyboard, tier=tier, speaks_for_itself=True, acts_on="front",
        announce=lambda a: ("Typing…" if a.get("action") == "type" else f"Pressing {a.get('text', '')}…"),
        confirm_text=lambda a: (f"type {len(a.get('text', ''))} characters into the focused window"
                                if a.get("action") == "type" else f"press {a.get('text', '')}"),
        ask=lambda a: (f"That's a long text, {len(a.get('text', ''))} characters. Type it into the window?"
                       if a.get("action") == "type" else f"Press {a.get('text', '')}?"),
    )]
