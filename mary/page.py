"""What's on screen, as areas read in order: one model whatever the source (read_screen, screen).

A window is read the way a person looks at it, by areas (a top bar, a list on the left, the main area), each one
top to bottom, instead of as one stream. Where an area begins and ends is found the same way for every app:
  - dividers: long straight edges in the window's picture (a line, or a change of background between panels),
    found in its pixels, which MARY can always capture
  - otherwise, wide gaps that no text crosses
Each piece of text, from the most exact source there is, then goes into the area it sits in:
  from_uia   Windows UI Automation (what screen readers use): names, roles, positions
  from_ocr   Windows OCR, for what exposes nothing; it also fills the gaps UI Automation leaves
  (a page in MARY's own browser comes from its own structure: mary/web)
In an area that has both, rows against the right edge are marked → and rows against the left ←: in any chat app
that says who wrote what. A long area keeps its start and its end, with the number of lines skipped between, so
nothing is cut silently. The area you're working in (where the keyboard is), or the one asked for, is read in full,
and the others in a line each.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

EDGE = 12                # brightness step (0-255) between neighbouring pixels that counts as an edge
DIVIDER_SHARE = 0.8      # a divider is an edge along at least this share of the area it divides
MIN_COLUMN = 0.025       # columns narrower than this share of the width (a scrollbar) hold no area of their own
GAP_SHARE = 0.06         # without dividers: a gap this wide (share of the width) no text crosses splits columns
TOP_BAR, BOTTOM_BAR = 0.25, 0.15   # bars are found within this share of the height from the top / bottom
BUDGET = 3000            # characters of the area read in full
SPLIT_GAP = 0.2          # in a chat, parts of a row this far apart (share of the width) are separate lines
_CODE_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$|^ic[-_]", re.I)   # an icon's file name, not words


@dataclass
class Item:
    text: str
    role: str                                  # "text", "button", "link", "list item", "image", …
    rect: tuple[int, int, int, int]            # window pixels: left, top, right, bottom
    clickable: bool = False
    ref: Any = field(default=None, repr=False)  # the element itself, for acting on it

    @property
    def cx(self) -> float:
        return (self.rect[0] + self.rect[2]) / 2

    @property
    def cy(self) -> float:
        return (self.rect[1] + self.rect[3]) / 2

    @property
    def height(self) -> int:
        return max(1, self.rect[3] - self.rect[1])


@dataclass
class Region:
    place: str                                 # "top bar", "left panel", "main area", …
    rect: tuple[int, int, int, int]
    items: list[Item] = field(default_factory=list)
    heading: str = ""
    lines: list[str] = field(default_factory=list)
    chat_like: bool = False                    # rows on both sides: lines carry ←/→

    def contains(self, x: float, y: float) -> bool:
        return self.rect[0] <= x < self.rect[2] and self.rect[1] <= y < self.rect[3]


@dataclass
class Page:
    title: str
    size: tuple[int, int]
    regions: list[Region]
    source: str = ""                           # "ui", "ocr", "ui+ocr", "page"
    focus: Region | None = None

    # ---------------------------------------------------------------- reading

    def region(self, part: str | None) -> Region | None:
        """The area called `part` ("left panel", "main") or the one whose text best matches its words."""
        if not part:
            return None
        want = part.lower().strip()
        for r in self.regions:
            if r.place == want or want in r.place or (r.heading and want == r.heading.lower()):
                return r
        words = [w for w in re.findall(r"\w+", want) if len(w) > 2]
        scored = [(sum(" ".join(r.lines).lower().count(w) for w in words), i) for i, r in enumerate(self.regions)]
        best = max(scored, default=(0, -1))
        return self.regions[best[1]] if best[0] > 0 else None

    def main(self) -> Region | None:
        bodies = [r for r in self.regions if "bar" not in r.place and "strip" not in r.place] or self.regions
        return max(bodies, key=lambda r: (r.rect[2] - r.rect[0]) * (r.rect[3] - r.rect[1]), default=None)

    def find(self, text: str) -> list[tuple[Region, Item]]:
        want = text.lower()
        return [(r, it) for r in self.regions for it in r.items if want in it.text.lower()]

    def render(self, part: str | None = None, budget: int = BUDGET) -> str:
        """The area asked for (else the one you're working in, else the main one) in full; the others in brief."""
        if not self.regions:
            return ""
        chosen = self.region(part) or self.focus or self.main()
        places = ", ".join(r.place for r in self.regions)
        out = [f"Read by layout, {len(self.regions)} area{'s' if len(self.regions) > 1 else ''}: {places}."]
        if chosen and chosen.chat_like:
            out.append("→ marks lines against the right edge (in a chat: the user's own), ← the left (the other "
                       "person).")
        if part and self.region(part) is None:
            out.append(f"No area matches '{part}', so here's the {chosen.place}.")
        if chosen:
            out.append(f"[{_label(chosen)}] in full:")
            out.append(read(chosen, budget))
        others = [r for r in self.regions if r is not chosen and r.lines]
        if others:
            out.append("Other areas (read one in full with part):")
            out += [f"[{_label(r)}] {_brief(r)}" for r in others]
        return "\n".join(out)


def _label(r: Region) -> str:
    return f"{r.place}: {r.heading}" if r.heading else r.place


def _brief(r: Region, chars: int = 110) -> str:
    text = " · ".join(line.lstrip("←→· ") for line in r.lines[:6])
    more = f" ({len(r.lines)} lines)" if len(r.lines) > 6 else ""
    return (text[:chars] + "…" if len(text) > chars else text) + more


def read(r: Region, budget: int = BUDGET) -> str:
    """An area in order. Too long: its start and its end, with how many lines were skipped between."""
    text = "\n".join(r.lines)
    if len(text) <= budget:
        return text
    head, tail, size = [], [], 0
    for line in r.lines:                       # the start: 40% of the budget
        if size + len(line) > budget * 0.4:
            break
        head.append(line)
        size += len(line) + 1
    for line in reversed(r.lines[len(head):]):  # the end (in a chat or a feed: the newest): the rest
        if size + len(line) > budget:
            break
        tail.insert(0, line)
        size += len(line) + 1
    skipped = len(r.lines) - len(head) - len(tail)
    return "\n".join(head + [f"… {skipped} lines skipped …"] + tail)


# ------------------------------------------------------------------ finding the areas


def _merge_close(values: list[int], within: int = 6) -> list[int]:
    out: list[list[int]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= within:
            out[-1].append(v)
        else:
            out.append([v])
    return [group[len(group) // 2] for group in out]


def _runs(mask, gap: int = 6) -> tuple[int, int]:
    """The longest stretch of True in a 1-D mask, allowing short breaks: (start, end)."""
    best, start, last = (0, 0), None, None
    for i, on in enumerate(mask):
        if not on:
            continue
        if start is None or i - last > gap:
            start = i
        last = i
        if last - start > best[1] - best[0]:
            best = (start, last + 1)
    return best


def dividers(pixels) -> tuple[list[tuple[int, int, int]], list[int]]:
    """Long straight edges in a window's picture (BGRA or grey pixels).
    Returns (vertical dividers as (x, top, bottom), y of full-width horizontal ones)."""
    import numpy as np

    grey = pixels[..., :3].mean(axis=2) if pixels.ndim == 3 else pixels.astype(float)
    h, w = grey.shape
    if h < 40 or w < 40:
        return [], []
    rows = slice(int(h * 0.15), int(h * 0.95))
    vedge = np.abs(np.diff(grey, axis=1)) > EDGE                      # (h, w-1): edge between x and x+1
    share = vedge[rows].mean(axis=0)
    xs = [int(x) + 1 for x in np.nonzero(share >= DIVIDER_SHARE)[0] if w * 0.02 < x < w * 0.98]
    vertical = []
    for x in _merge_close(xs):
        lo, hi = max(0, x - 2), min(w - 1, x + 1)
        top, bottom = _runs(vedge[:, lo:hi].any(axis=1))
        if bottom - top >= h * 0.5:
            vertical.append((int(x), int(top), int(bottom)))
    hedge = np.abs(np.diff(grey, axis=0)) > EDGE                      # (h-1, w)
    hshare = hedge[:, int(w * 0.02):int(w * 0.98)].mean(axis=1)
    horizontal = [int(y) + 1 for y in np.nonzero(hshare >= DIVIDER_SHARE)[0]]
    return vertical, _merge_close(horizontal)


def _dedupe(items: list[Item]) -> list[Item]:
    """Drop empty and repeated items, icons named by code ("msg-dblcheck"), and containers whose own name only
    repeats the items inside them."""
    clean, seen = [], set()
    for it in items:
        text = re.sub(r"\s+", " ", it.text or "").strip()
        if not text or it.rect[2] - it.rect[0] < 2 or it.rect[3] - it.rect[1] < 2 or (text, it.rect) in seen:
            continue
        if it.role in ("image", "graphic") and _CODE_NAME.match(text):
            continue
        seen.add((text, it.rect))
        it.text = text
        clean.append(it)

    def inside(a: Item, b: Item) -> bool:      # a within b
        return a is not b and a.rect[0] >= b.rect[0] - 2 and a.rect[1] >= b.rect[1] - 2 and \
            a.rect[2] <= b.rect[2] + 2 and a.rect[3] <= b.rect[3] + 2

    out = []
    for it in clean:
        parts = [o for o in clean if inside(o, it)]
        if parts and sum(len(p.text) for p in parts if p.text in it.text) >= len(it.text) * 0.5:
            continue
        out.append(it)
    return out


def _gap_columns(items: list[Item], x0: int, x1: int, y0: int, y1: int) -> list[int]:
    """Without dividers: x positions of wide gaps no item crosses, between two sides that each fill the height."""
    if not items:
        return []
    covered = sorted((it.rect[0], it.rect[2]) for it in items)
    cuts, reach = [], covered[0][1]
    for left, right in covered[1:]:
        if left - reach >= (x1 - x0) * GAP_SHARE:
            cut = (reach + left) // 2
            sides = ([it for it in items if it.cx < cut], [it for it in items if it.cx >= cut])
            if all(sum(it.height for it in side) >= (y1 - y0) * 0.3 for side in sides):
                cuts.append(cut)
        reach = max(reach, right)
    return cuts


def _rows(items: list[Item]) -> list[list[Item]]:
    rows: list[list[Item]] = []
    for it in sorted(items, key=lambda i: (i.rect[1], i.rect[0])):
        row = rows[-1] if rows else None
        if row is not None:
            top, bottom = min(i.rect[1] for i in row), max(i.rect[3] for i in row)
            overlap = min(bottom, it.rect[3]) - max(top, it.rect[1])
            if overlap >= min(it.height, bottom - top) * 0.5:
                row.append(it)
                continue
        rows.append([it])
    return [sorted(row, key=lambda i: i.rect[0]) for row in rows]


def _side(group: list[Item], r: Region) -> str:
    width = max(1, r.rect[2] - r.rect[0])
    left, right = min(i.rect[0] for i in group) - r.rect[0], r.rect[2] - max(i.rect[2] for i in group)
    if left > width * 0.2 and right > width * 0.2 and abs(left - right) < width * 0.05:
        return "·"  # centred: a date, a notice
    # Against the right edge, or starting well into the right side (a short message in a wide bubble, where only
    # the text's own box is known)
    if left > width * 0.3 and (right < width * 0.15 or left > width * 0.4):
        return "→"
    return "←"


def _groups(row: list[Item], width: int) -> list[list[Item]]:
    """A row's parts, split where a wide empty gap separates them."""
    parts = [[row[0]]]
    for it in row[1:]:
        if it.rect[0] - max(i.rect[2] for i in parts[-1]) > width * SPLIT_GAP:
            parts.append([it])
        else:
            parts[-1].append(it)
    return parts


def _text(group: list[Item]) -> str:
    return " ".join(f"[image: {i.text}]" if i.role in ("image", "graphic") else i.text for i in group)


def _finish(r: Region) -> None:
    """Lines in reading order, the heading, and ←/→ when the area reads like a chat.

    A chat and a table both have text at both edges; the difference is that in a chat almost every row is
    one-sided (a message from one person), while every table row has both (a name and a size). So: mostly
    one-sided rows on both sides = a chat, and a row holding both a left and a right part (two messages at the same
    height) becomes two lines. Anything else keeps its rows whole."""
    width = max(1, r.rect[2] - r.rect[0])
    rows = _rows(r.items)
    split = [_groups(row, width) for row in rows]
    single = [g[0] for g in split if len(g) == 1]
    sides = [_side(g, r) for g in single]
    r.chat_like = len(single) >= max(3, 0.6 * len(rows)) and sides.count("→") >= 1 and sides.count("←") >= 1
    r.lines = []
    if r.chat_like:
        # The title row at the top and a row with a box to type in are the frame, not the conversation: whole,
        # unmarked (a call button at the far right of the title isn't the user's message).
        frame = [n for n, row in enumerate(rows) if n == 0 or any(i.role == "edit" for i in row)]
        parts = [(row, True) for n, row in enumerate(rows) if n in frame] + \
                [(g, False) for n, groups in enumerate(split) if n not in frame for g in groups]
        parts.sort(key=lambda p: (min(i.rect[1] for i in p[0]), min(i.rect[0] for i in p[0])))
        r.lines = [_text(g) if whole else f"{_side(g, r)} {_text(g)}" for g, whole in parts]
    else:
        r.lines = [_text(row) for row in rows]
    r.heading = ""
    if rows:  # the first short text at the top: a title, a chat's name ("Inbox", "Ama"); plain text before buttons
        top = [it for it in rows[0] if it.role not in ("image", "graphic", "edit") and 0 < len(it.text) <= 60]
        plain = [it for it in top if not it.clickable]
        r.heading = (plain or top)[0].text if top else ""


def segment(items: list[Item], size: tuple[int, int], vertical=(), horizontal=(), title: str = "",
            focus_point: tuple[float, float] | None = None, source: str = "") -> Page:
    """Areas from dividers (or gaps), the items placed in them, each in reading order."""
    w, h = size
    items = _dedupe([it for it in items if it.cx >= 0 and it.cy >= 0 and it.cx < w and it.cy < h])
    # The body: below the top bar, above the bottom bar. With panel dividers, the bar ends where they begin.
    if vertical:
        top = min(t for _, t, _ in vertical)
        bottom = max(b for _, _, b in vertical)
        top = top if top > h * 0.03 else 0
        bottom = bottom if bottom < h * 0.97 else h
    else:
        ups = [y for y in horizontal if h * 0.03 < y <= h * TOP_BAR]
        downs = [y for y in horizontal if h * (1 - BOTTOM_BAR) <= y < h * 0.97]
        top, bottom = (max(ups) if ups else 0), (min(downs) if downs else h)
    body_items = [it for it in items if top <= it.cy < bottom]
    xs = [x for x, t, b in vertical] if vertical else _gap_columns(body_items, 0, w, top, bottom)
    edges = [0] + [x for x in sorted(xs) if w * MIN_COLUMN < x < w * (1 - MIN_COLUMN)] + [w]
    columns = [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= w * MIN_COLUMN]
    regions: list[Region] = []
    if top > 0:
        regions.append(Region("top bar", (0, 0, w, top)))
    widest = max(range(len(columns)), key=lambda i: columns[i][1] - columns[i][0]) if columns else 0
    for i, (a, b) in enumerate(columns):
        narrow = b - a < w * 0.08
        if i == widest:
            place = "main area"
        elif i < widest:
            place = "left strip" if narrow else "left panel"
        else:
            place = "right strip" if narrow else "right panel"
        regions.append(Region(place, (a, top, b, bottom)))
    if bottom < h:
        regions.append(Region("bottom bar", (0, bottom, w, h)))
    _number_repeats(regions)
    for it in items:
        home = next((r for r in regions if r.contains(it.cx, it.cy)), None)
        if home is None:  # on a divider: the nearest area
            home = min(regions, key=lambda r: abs(it.cx - (r.rect[0] + r.rect[2]) / 2) +
                       abs(it.cy - (r.rect[1] + r.rect[3]) / 2))
        home.items.append(it)
    regions = [r for r in regions if r.items]
    for r in regions:
        _finish(r)
    focus = next((r for r in regions if focus_point and r.contains(*focus_point)), None)
    return Page(title, size, regions, source, focus)


def _number_repeats(regions: list[Region]) -> None:
    """Two left panels become "left panel 1" and "left panel 2"."""
    for place in {r.place for r in regions}:
        same = [r for r in regions if r.place == place]
        if len(same) > 1:
            for n, r in enumerate(same, 1):
                r.place = f"{place} {n}"


# ------------------------------------------------------------------ sources


_TEXT_KINDS = ("Text", "Edit", "ListItem", "DataItem", "Hyperlink", "Button", "MenuItem", "TabItem", "TreeItem",
               "Header", "HeaderItem", "CheckBox", "RadioButton", "ComboBox", "SplitButton", "Image",
               "Group")  # a named group is often a whole block of text (a message without a reply in it)
_CLICKABLE = {"button", "link", "hyperlink", "menu item", "tab item", "list item", "tree item", "check box",
              "radio button", "split button", "combo box", "data item"}


def from_uia(hwnd: int) -> tuple[list[Item], tuple[float, float] | None]:
    """Text-bearing elements with their places (one cached query), and where the keyboard focus is."""
    import win32gui
    from pywinauto.uia_defines import IUIA

    uia = IUIA()
    iuia, U = uia.iuia, uia.UIA_dll
    left, top = win32gui.GetWindowRect(hwnd)[:2]
    kinds = [getattr(U, f"UIA_{k}ControlTypeId") for k in _TEXT_KINDS]
    cond = iuia.CreateAndCondition(
        iuia.CreateOrConditionFromArray([iuia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, k) for k in kinds]),
        iuia.CreatePropertyCondition(U.UIA_IsOffscreenPropertyId, False))
    cache = iuia.CreateCacheRequest()
    for prop in (U.UIA_NamePropertyId, U.UIA_LocalizedControlTypePropertyId, U.UIA_BoundingRectanglePropertyId):
        cache.AddProperty(prop)
    found = iuia.ElementFromHandle(hwnd).FindAllBuildCache(U.TreeScope_Descendants, cond, cache)
    items = []
    for i in range(found.Length):
        e = found.GetElement(i)
        r = e.CachedBoundingRectangle
        kind = (e.CachedLocalizedControlType or "").lower()
        items.append(Item(e.CachedName or "", "image" if kind == "image" else kind or "text",
                          (r.left - left, r.top - top, r.right - left, r.bottom - top), kind in _CLICKABLE, e))
    focus = None
    try:
        f = iuia.GetFocusedElement().CurrentBoundingRectangle
        focus = ((f.left + f.right) / 2 - left, (f.top + f.bottom) / 2 - top)
    except Exception:
        pass
    return items, focus


def from_ocr(pixels) -> list[Item]:
    from mary.ocr import ocr_pixels

    return [Item(line.text, "text", (int(line.x), int(line.y), int(line.right), int(line.y + line.h)))
            for line in ocr_pixels(pixels)]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1]


def _already_read(line: Item, items: list[Item]) -> bool:
    """Does UI Automation already give this OCR line's words at that place?"""
    words = _words(line.text)
    if not words:
        return True
    near = " ".join(it.text.lower() for it in items
                    if min(line.rect[2], it.rect[2]) > max(line.rect[0], it.rect[0]) - 20
                    and min(line.rect[3], it.rect[3]) > max(line.rect[1], it.rect[1]) - 20)
    return sum(w in near for w in words) >= len(words) * 0.5


def page_for(win) -> Page:
    """A window, read by layout. UI Automation gives names, roles and places, but even a rich tree can leave whole
    parts out (WhatsApp Web exposes replies, not plain messages: 7 of 30 visible lines were missing), so the
    window's picture is always read too, and every line UI Automation didn't already give is added."""
    from mary import web
    from mary.ocr import capture_window

    if web.owns(win):  # MARY's own browser: the page's own structure, never pixels
        try:
            return web.page_from(web.browser().snapshot(), win.title)
        except Exception as e:
            log.warning("couldn't read the page in MARY's browser: %s", e)
    pixels = capture_window(win.hwnd)
    h, w = pixels.shape[:2]
    try:
        items, focus = from_uia(win.hwnd)
    except Exception as e:
        log.debug("UI Automation couldn't read the window: %s", e)
        items, focus = [], None
    named = [it for it in items if (it.text or "").strip()]
    source = "ui" if named else ""
    try:
        extra = [line for line in from_ocr(pixels) if not _already_read(line, named)]
        if extra:
            items, source = items + extra, ("ui+ocr" if named else "ocr")
    except Exception as e:
        log.debug("OCR couldn't read the window: %s", e)
    vertical, horizontal = dividers(pixels)
    return segment(items, (w, h), vertical, horizontal, title=win.title, focus_point=focus, source=source)
