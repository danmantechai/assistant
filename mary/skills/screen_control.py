"""screen: act on what's on screen, in any app: click a button, link, tab or menu by what it says; type into
a field; scroll; or list what's clickable.

How MARY finds things: Windows UI Automation describes every app's controls (name, kind, position), the
same information screen readers use. One cached query lists a window's visible clickable elements in
70-300 ms (measured: Edge with a YouTube results page, File Explorer, VLC, Spotify, Terminal). Apps that
describe nothing (games, some canvas apps) fall back to OCR of the window.

A click uses the element's own action when it has one (no mouse movement); otherwise a real mouse click
in its middle, after which the pointer goes back where it was.

This is also what stops invented links: the model can't see the address behind a search result (once it
made up a YouTube link that didn't exist), but it can click the result by its title.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from mary import winutil
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("Clicking, typing into a box, or scrolling in any app or page: screen. To open a search result or a "
         "video, click its title with screen; never make up a link.")
GROUNDING = [
    ("screen", lambda a: a.get("action") == "click",
     r"\b(click|press|tap|select|choose|pick|open|play|watch|hit|go|yes|sure|okay|ok|the first|the second|"
     r"button|link|tab)\b", "the user didn't ask to click anything"),
    ("screen", lambda a: a.get("action") == "type",
     r"\b(type|write|enter|put|fill|search|message|send|reply)\b", "the user didn't ask to type anything"),
    ("screen", lambda a: a.get("action") == "scroll",
     r"\b(scroll|page|up|down|top|bottom|more|further|next)\b", "the user didn't ask to scroll"),
]

log = logging.getLogger(__name__)

# Labels that can spend money, lose work, send something or change an account: ask first.
RISKY = re.compile(r"\b(buy|purchase|pay|place (your )?order|order now|check ?out|delete|remove|uninstall|erase|"
                   r"format|reset|send|submit|post|publish|transfer|confirm|accept|agree|allow|sign ?out|"
                   r"log ?out|sign off|unsubscribe|deactivate|close account|empty|restart|shut ?down|discard|"
                   r"don'?t save)\b", re.I)
_SECRET_FIELD = re.compile(r"\b(password|passcode|pin|cvv|cvc|card number|security code|one[- ]time code)\b", re.I)
# Words that say what kind of thing it is ("the search box", "the subscribe button"), not its name.
# Matched as parts of the kind Windows reports, so "button" also covers "split button" and File
# Explorer's "app bar button".
_ROLE = {"button": {"button"}, "link": {"link"}, "tab": {"tab"}, "menu": {"menu"},
         "box": {"edit", "combo box", "document"}, "field": {"edit", "combo box"}, "bar": {"edit", "combo box"},
         "checkbox": {"check box"}, "option": {"radio button", "list item"}}
_ORDINALS = {"first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
             "fifth": 5, "5th": 5, "last": -1}
_DURATION = re.compile(r"\b\d+ (?:seconds?|minutes?|hours?)\b|\b\d{1,2}:\d{2}\b", re.I)
_NAV = re.compile(r"^(go to channel|skip navigation|more actions|action menu|new content available)", re.I)


@dataclass
class Element:
    kind: str                            # "button", "link", "edit", "menu item", … (or "text" from OCR)
    name: str
    rect: tuple[int, int, int, int]      # screen pixels: left, top, right, bottom
    com: Any = field(default=None, repr=False)  # the UI Automation element (None for OCR text)
    hidden: bool = False                 # reported off-screen (e.g. a video player's auto-hidden controls)

    @property
    def centre(self) -> tuple[int, int]:
        return (self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2


# ------------------------------------------------------------------ finding elements

def _uia():
    from pywinauto.uia_defines import IUIA
    return IUIA()


def elements(hwnd: int, hidden: bool = False) -> list[Element]:
    """The window's visible, clickable/typable elements in reading order (one cached UIA query).
    hidden=True lists only the ones reported off-screen instead: YouTube hides its player controls while a
    video plays, so "click pause" found nothing, though the button still works without the mouse."""
    uia = _uia()
    iuia, U = uia.iuia, uia.UIA_dll
    kinds = [U.UIA_ButtonControlTypeId, U.UIA_HyperlinkControlTypeId, U.UIA_MenuItemControlTypeId,
             U.UIA_ListItemControlTypeId, U.UIA_TabItemControlTypeId, U.UIA_CheckBoxControlTypeId,
             U.UIA_RadioButtonControlTypeId, U.UIA_EditControlTypeId, U.UIA_ComboBoxControlTypeId,
             U.UIA_TreeItemControlTypeId, U.UIA_SplitButtonControlTypeId, U.UIA_DataItemControlTypeId]
    cond = iuia.CreateAndCondition(
        iuia.CreateOrConditionFromArray([iuia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, k) for k in kinds]),
        iuia.CreatePropertyCondition(U.UIA_IsOffscreenPropertyId, hidden))
    cache = iuia.CreateCacheRequest()
    for prop in (U.UIA_NamePropertyId, U.UIA_LocalizedControlTypePropertyId, U.UIA_BoundingRectanglePropertyId):
        cache.AddProperty(prop)
    found = iuia.ElementFromHandle(hwnd).FindAllBuildCache(U.TreeScope_Descendants, cond, cache)
    out, seen = [], set()
    for i in range(found.Length):
        e = found.GetElement(i)
        r = e.CachedBoundingRectangle
        name, kind = (e.CachedName or "").strip(), (e.CachedLocalizedControlType or "").lower()
        rect = (r.left, r.top, r.right, r.bottom)
        if (not hidden and (r.right - r.left < 2 or r.bottom - r.top < 2)) or \
                (not name and kind not in ("edit", "combo box")):
            continue
        if (name, rect) in seen:
            continue
        seen.add((name, rect))
        out.append(Element(kind, name, rect, e, hidden))
    out.sort(key=lambda el: (el.rect[1] // 24, el.rect[0]))
    return out


def ocr_elements(hwnd: int) -> list[Element]:
    """For apps that describe nothing to UI Automation: the window's text lines, as click targets."""
    import win32gui

    from mary.ocr import ocr_window

    left, top = win32gui.GetWindowRect(hwnd)[:2]
    return [Element("text", line.text, (int(left + line.x), int(top + line.y), int(left + line.x + line.w),
                                        int(top + line.y + line.h))) for line in ocr_window(hwnd)]


# ------------------------------------------------------------------ matching what was said

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s$']", " ", text.lower())).strip()


def _wanted_kinds(target: str) -> tuple[str, set[str]]:
    """'the search box' -> ('search', {'edit', …}). Role words narrow the kind but aren't the name."""
    words, kinds = [], set()
    for w in _norm(target).split():
        if w in _ROLE:
            kinds |= _ROLE[w]
        elif w not in ("the", "a", "an", "on", "that", "this", "it", "please", "for", "me"):
            words.append(w)
    return " ".join(words), kinds


def score(target: str, el: Element) -> float:
    name, n = _norm(target), _norm(el.name)
    if not n or not name:
        return 0.0
    if n == name:
        return 100.0
    if n.startswith(name):
        return 97.0
    if re.search(rf"\b{re.escape(name)}\b", n):
        return 95.0
    return float(fuzz.token_set_ratio(name, n)) * 0.9


def nth(elements_: list[Element], n: int, noun: str = "", videos_first: bool = False) -> Element | None:
    """'the first video' / 'the second result': the n-th content link in reading order, skipping navigation.
    Videos are recognised by a duration in their name ("… 57 minutes"), which YouTube puts in its links."""
    links, names = [], set()
    for el in elements_:
        if el.kind not in ("link", "hyperlink", "list item") or len(el.name) < 12 or _NAV.match(el.name):
            continue
        key = _norm(el.name)
        if key in names:  # a result's thumbnail and its title are separate links with the same name
            continue
        names.add(key)
        links.append(el)
    if (noun.startswith("video") or videos_first) and any(_DURATION.search(el.name) for el in links):
        links = [el for el in links if _DURATION.search(el.name)]
    if not links:
        return None
    if n == -1:
        return links[-1]
    return links[n - 1] if 1 <= n <= len(links) else None


_NTH_TARGET = re.compile(r"^(?:the )?(first|1st|top|second|2nd|third|3rd|fourth|4th|fifth|5th|last)"
                         r"(?: (one|result|link|video|item|option|entry))?$")


def find(elements_: list[Element], target: str, numbered: list[Element] | None = None,
         videos_first: bool = False) -> tuple[Element | None, str]:
    """(element, '') or (None, why). `numbered` is the last list shown, for "click number 3".
    videos_first: on YouTube "the first one" means the first video, not the channel card above it."""
    t = _norm(target)
    num = re.fullmatch(r"(?:number |no |#)?(\d{1,2})", t)
    if num and numbered:
        i = int(num.group(1))
        return (numbered[i - 1], "") if 1 <= i <= len(numbered) else (None, f"there's no number {i}")
    ordinal = _NTH_TARGET.match(t)
    if ordinal:
        el = nth(elements_, _ORDINALS[ordinal.group(1)], ordinal.group(2) or "", videos_first)
        return (el, "") if el else (None, "I can't tell which result that is")
    name, kinds = _wanted_kinds(target)
    pool = [el for el in elements_ if not kinds or any(k in el.kind for k in kinds)] or elements_
    if not name and kinds:  # "the search box": the only/first field of that kind
        return (pool[0], "") if pool and pool is not elements_ else (None, f"nothing like '{target}' is showing")
    ranked = sorted(((score(name, el), i, el) for i, el in enumerate(pool)), key=lambda x: (-x[0], x[1]))
    if not ranked or ranked[0][0] < 80:
        return None, f"nothing called '{target}' is showing"
    best_score, _, best = ranked[0]
    rivals = [el for s, _, el in ranked[1:4] if s >= best_score - 2 and _norm(el.name) != _norm(best.name)]
    if rivals and best_score < 95:
        options = ", ".join(f"'{el.name[:40]}'" for el in [best, *rivals])
        return None, f"it could be {options}"
    return best, ""


def short(name: str, words: int = 7) -> str:
    """A result's name as MARY says it: no duration, at most a few words."""
    name = _DURATION.sub("", name).strip(" ,.-")
    parts = name.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


# ------------------------------------------------------------------ acting

def _restore_mouse(fn):
    import win32api

    before = win32api.GetCursorPos()
    try:
        fn()
    finally:
        win32api.SetCursorPos(before)


def click(el: Element) -> str:
    """Click `el`: its own action if it has one, else the mouse. Returns how."""
    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    if el.com is not None:
        for pattern, call in (("Invoke", "Invoke"), ("SelectionItem", "Select"), ("Toggle", "Toggle"),
                              ("ExpandCollapse", "Expand")):
            try:
                getattr(get_elem_interface(el.com, pattern), call)()
                return pattern.lower()
            except (NoPatternInterfaceError, Exception):
                continue
    if el.hidden:
        raise RuntimeError(f"'{el.name}' is hidden right now and can't be clicked with the mouse")
    from pywinauto import mouse
    _restore_mouse(lambda: mouse.click(coords=el.centre))
    return "mouse"


def focus_field(el: Element) -> None:
    try:
        el.com.SetFocus()
    except Exception:
        from pywinauto import mouse
        _restore_mouse(lambda: mouse.click(coords=el.centre))


def scroll(hwnd: int, direction: str, amount: str = "") -> None:
    """Mouse-wheel scroll over the middle of the window (works in every app), pointer put back after."""
    import win32gui
    from pywinauto import mouse

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    centre = ((left + right) // 2, (top + bottom) // 2)
    notches = {"top": 60, "bottom": 60}.get(direction, 3 if amount in ("a bit", "a little", "little") else 8)
    sign = 1 if direction in ("up", "top") else -1
    _restore_mouse(lambda: mouse.scroll(coords=centre, wheel_dist=sign * notches))


# ------------------------------------------------------------------ the tool

def _window(target: str | None) -> winutil.WindowInfo | None:
    from mary.skills.read_screen import _target_window
    return _target_window(target)


def tools(ctx: ToolContext) -> list[Tool]:
    resolved: dict = {}  # filled by tier() (risk check), reused by the action moments later

    def resolve(args: dict):
        key = (args.get("action"), args.get("target") or "", args.get("window") or None)
        hit = resolved.get(key)
        if hit and time.monotonic() - hit[0] < 5:
            return hit[1:]
        win = _window(args.get("window"))
        el, why = None, "no window to act on"
        if win:
            els = elements(win.hwnd)
            if args.get("action") == "type":
                els = [e for e in els if e.kind in ("edit", "combo box", "document")] or els
            el, why = find(els, args.get("target") or "", ctx.screen_elements, "youtube" in win.title.lower())
            plain_click = args.get("action") == "click" and not _NTH_TARGET.match(_norm(args.get("target") or ""))
            if el is None and plain_click:  # a control that's there but hidden ("pause" while a video plays)
                hidden_el, _ = find(elements(win.hwnd, hidden=True), args.get("target") or "")
                el = hidden_el if hidden_el and hidden_el.kind != "link" else None
            if el is None and plain_click:  # an app that describes nothing: read the window instead
                ocr_el, ocr_why = find(ocr_elements(win.hwnd), args.get("target") or "")
                el, why = (ocr_el, "") if ocr_el else (None, why)
        resolved[key] = (time.monotonic(), win, el, why)
        return win, el, why

    def tier(args: dict) -> int:
        if args.get("action") != "click":
            return 1
        _, el, _ = resolve(args)
        return 2 if el is not None and RISKY.search(el.name) else 1

    def screen(action: str, target: str = "", text: str = "", window: str = "", submit: bool = False) -> ToolResult:
        if action == "scroll":
            win = _window(window or None)
            if not win:
                return ToolResult(False, "No window to scroll.")
            direction = (target or "down").lower().split()[0]
            if direction not in ("up", "down", "top", "bottom"):
                return ToolResult(False, "scroll target must be up, down, top or bottom.")
            scroll(win.hwnd, direction, " ".join((target or "").lower().split()[1:]))
            return ToolResult(True, f"Scrolled {direction} in '{win.title}'.", say="Done.")
        if action == "list":
            win = _window(window or None)
            if not win:
                return ToolResult(False, "No window to look at.")
            els = elements(win.hwnd)[:40]
            ctx.screen_elements = els
            lines = [f"{i}. {el.kind}: {el.name[:70]}" for i, el in enumerate(els, 1)]
            return ToolResult(True, f"Clickable in '{win.title}':\n" + "\n".join(lines) if lines
                              else f"'{win.title}' doesn't list its controls; try read_screen.")
        if action not in ("click", "type"):
            return ToolResult(False, "action must be click, type, scroll or list.")
        win, el, why = resolve({"action": action, "target": target, "window": window or None})
        resolved.clear()
        if el is None:
            return ToolResult(False, f"Couldn't {action} '{target}': {why}.", say=f"I couldn't find that: {why}.")
        if action == "type":
            if _SECRET_FIELD.search(el.name) or _SECRET_FIELD.search(target):
                return ToolResult(False, "That looks like a password or card field; MARY never types into those.",
                                  say="That looks like a password or card field, so I'll leave that to you.")
            winutil.focus_window(win.hwnd)
            focus_field(el)
            time.sleep(0.15)
            import pyautogui
            pyautogui.hotkey("ctrl", "a")  # replace what's in the box, like clicking in and typing fresh
            winutil.type_unicode(text)
            if submit:
                pyautogui.press("enter")
            return ToolResult(True, f"Typed into '{el.name or el.kind}' in '{win.title}'"
                                    f"{' and pressed Enter' if submit else ''}.", say="Done." if submit else "Typed it.")
        before = win.title
        how = click(el)
        time.sleep(0.8)
        after = winutil.window_title(win.hwnd)
        moved = f" The window now shows: {after}." if after and after != before else ""
        ctx.last_web_at = time.monotonic() if win.exe in ("msedge.exe", "chrome.exe", "firefox.exe") else ctx.last_web_at
        what = "Opening" if el.kind in ("link", "hyperlink") else "Clicked"
        return ToolResult(True, f"Clicked {el.kind} '{el.name[:80]}' ({how}).{moved}", say=f"{what} {short(el.name)}.")

    return [Tool(
        "screen",
        "Act on what's on screen in any app or web page: click a button, link, tab, menu or search result by its "
        "visible text (or 'first video', 'second result', or a number from list), type into a field (target = "
        "the field, e.g. 'search box'), scroll (target = up/down/top/bottom), or list what's clickable. Use this "
        "to open search results; never make up a link.",
        {"action": {"type": "string", "enum": ["click", "type", "scroll", "list"]},
         "target": {"type": "string", "description": "what to click or type into, as shown on screen"},
         "text": {"type": "string", "description": "for type: the text"},
         "submit": {"type": "boolean", "description": "for type: press Enter afterwards (e.g. to search)"},
         "window": {"type": "string", "description": "app or window; omit for the one in front"}},
        ["action"], screen, tier=tier, speaks_for_itself=True, private=True,
        announce=lambda a: {"click": f"Clicking {a.get('target', '')}…", "type": "Typing…",
                            "scroll": "Scrolling…", "list": "Looking at the screen…"}.get(a.get("action"), "…"),
        confirm_text=lambda a: f"click '{short((resolve(a)[1] or Element('', a.get('target', ''), (0, 0, 0, 0))).name)}'",
    )]
