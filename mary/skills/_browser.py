"""Finding and bringing forward a browser tab by its title (shared by the WhatsApp and weather skills)."""
from __future__ import annotations

from mary import winutil

BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"}


def browser_window(title_part: str, extra_exes: set[str] = frozenset()) -> winutil.WindowInfo | None:
    """A browser window whose *active* tab title contains `title_part`."""
    part = title_part.lower()
    for w in winutil.list_windows():
        if part in w.title.lower() and w.exe in BROWSERS | extra_exes:
            return w
    return None


def select_tab(title_part: str) -> bool:
    """Make a background tab whose title contains `title_part` the active one. Browsers expose their tab
    strip to UI Automation even when they hide page content from it."""
    from pywinauto import Desktop

    part = title_part.lower()
    for w in Desktop(backend="uia").windows():
        if winutil._process_name(w.element_info.process_id) not in BROWSERS:
            continue
        for control_type in ("TabItem", "ListItem"):
            try:
                tabs = w.descendants(control_type=control_type)
            except Exception:
                continue
            for tab in tabs:
                if part in (tab.element_info.name or "").lower():
                    try:
                        tab.select()
                    except Exception:
                        tab.click_input()
                    return True
    return False
