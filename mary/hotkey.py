"""System-wide hotkey (default Ctrl+Space) via Win32 RegisterHotKey.

Runs on its own thread with a plain GetMessage loop: no keyboard hook, no admin rights, and no CPU
use while waiting. Windows delivers the combination to MARY instead of the focused app.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)

_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8, "windows": 0x8}
_MOD_NOREPEAT = 0x4000
_KEYS = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
         "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
         "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
         "pause": 0x13, "`": 0xC0, "backtick": 0xC0}
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012


def parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+space' -> (modifier flags, virtual-key code)."""
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _KEYS:
            key = _KEYS[p]
        elif len(p) == 1 and p.isalnum():
            key = ord(p.upper())
        elif p.startswith("f") and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            key = 0x70 + int(p[1:]) - 1
        else:
            raise ValueError(f"unknown key '{p}' in hotkey '{combo}'")
    if key is None:
        raise ValueError(f"hotkey '{combo}' has no main key")
    return mods, key


class GlobalHotkey(threading.Thread):
    def __init__(self, combo: str, callback: Callable[[], None]):
        super().__init__(name="hotkey", daemon=True)
        self.combo = combo
        self.mods, self.vk = parse_combo(combo)
        self.callback = callback
        self.error: str | None = None
        self.registered = threading.Event()

    def run(self) -> None:
        if not user32.RegisterHotKey(None, 1, self.mods | _MOD_NOREPEAT, self.vk):
            code = ctypes.get_last_error()
            self.error = ("another app already uses it" if code == 1409 else f"Windows error {code}")
            log.warning("could not register hotkey %s: %s", self.combo, self.error)
            self.registered.set()
            return
        self.registered.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:
                        log.exception("hotkey callback failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self.native_id:
            user32.PostThreadMessageW(self.native_id, _WM_QUIT, 0, 0)
