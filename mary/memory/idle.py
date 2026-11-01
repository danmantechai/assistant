"""Is the PC quiet? (for the memory's background pass: it waits until nobody is using MARY or the PC.)"""
from __future__ import annotations

import ctypes
import sys


def input_idle_seconds() -> float | None:
    """Seconds since the last key or mouse movement anywhere in Windows; None where that can't be told."""
    if sys.platform != "win32":
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def on_battery() -> bool:
    """Running on battery (False on a desktop, or when it can't be told)."""
    try:
        import psutil

        battery = psutil.sensors_battery()
        return bool(battery and not battery.power_plugged)
    except Exception:
        return False
