"""Small UI Automation helpers (pywinauto's UIA backend, imported lazily because it loads slowly)."""
from __future__ import annotations

import time

import psutil


def app_window(exe: str, timeout: float = 0.0):
    """The titled top-level UIA window of a process (e.g. 'spotify.exe'), waiting up to `timeout` s."""
    from pywinauto import Desktop

    deadline = time.monotonic() + timeout
    while True:
        for w in Desktop(backend="uia").windows():
            try:
                if w.element_info.name and psutil.Process(w.element_info.process_id).name().lower() == exe:
                    return w
            except (psutil.Error, ValueError):
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.4)


def window_for(hwnd: int):
    from pywinauto import Desktop

    return Desktop(backend="uia").window(handle=hwnd).wrapper_object()


def named(elements, prefix: str = "", contains: str = "") -> list:
    """Elements whose name starts with `prefix` and/or contains `contains` (case-insensitive)."""
    out = []
    for e in elements:
        name = (e.element_info.name or "").lower()
        if name and name.startswith(prefix.lower()) and contains.lower() in name:
            out.append(e)
    return out


def press(element) -> None:
    """Invoke a button the accessible way (no mouse movement); fall back to a real click."""
    try:
        element.invoke()
    except Exception:
        element.click_input()


def window_text(hwnd: int, limit: int = 600) -> str:
    """Visible text a window exposes to UI Automation (labels, edits, list items)."""
    win = window_for(hwnd)
    seen, parts = set(), []
    for control_type in ("Text", "Edit", "ListItem", "DataItem", "Hyperlink", "Document"):
        try:
            elements = win.descendants(control_type=control_type)
        except Exception:
            continue
        for e in elements[:limit]:
            name = (e.element_info.name or "").strip()
            if name and name not in seen:
                seen.add(name)
                parts.append(name)
    return "\n".join(parts)
