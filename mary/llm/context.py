"""Small live context injected at the end of each request (time, active window, volume)."""
from __future__ import annotations

import logging
from datetime import datetime

from mary import winutil

log = logging.getLogger(__name__)


def live_context(hint: str | None = None) -> str:
    lines = [f"time: {datetime.now():%A %d %B %Y, %H:%M}"]
    fg = winutil.foreground_window()
    if fg and not winutil.is_own_console(fg.hwnd):
        lines.append(f"active window: {fg.title[:80]} ({fg.exe})")
    try:
        from mary.skills.sound_screen import get_volume

        volume, muted = get_volume()
        lines.append(f"volume: {volume}%{' (muted)' if muted else ''}")
    except Exception as e:  # no audio device etc. is not worth failing a request over
        log.debug("volume unavailable: %s", e)
    if hint:
        lines.append(f"router guess: {hint}")
    return "<context>\n" + "\n".join(lines) + "\n</context>\n"
