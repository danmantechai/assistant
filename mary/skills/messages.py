"""check_messages: read WhatsApp Web's chat list (unread count + who) without opening any chat.

Edge doesn't expose web-page content to UI Automation, so this reads the window with Windows OCR.
The tab title carries the unread total ("(31) WhatsApp"). Chats are never opened, so nothing gets
marked as read. By default only names and counts are reported; message text only when asked.
"""
from __future__ import annotations

import logging
import os
import re
import statistics
import time
from dataclasses import dataclass

import numpy as np
from rapidfuzz import fuzz, process, utils

from mary import winutil
from mary.ocr import OcrLine, capture_window, ocr_pixels
from mary.skills._browser import browser_window, select_tab
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = 'Messages, chats, WhatsApp, "did anyone text me": check_messages (it opens WhatsApp itself).'
GROUNDING = [
    ("check_messages", lambda a: True,
     r"\b(messag\w*|whatsapp|chats?|text\w*|inbox|unread|dms?|sent me|anyone|anybody|wrote|replied)\b",
     "the user didn't ask about messages"),
]
SLOW = {"check_messages": ["Let me check your messages.", "One moment, opening WhatsApp."]}

log = logging.getLogger(__name__)

URL = "https://web.whatsapp.com"
_TIME_RE = re.compile(r"^(\d{1,2}[:.]\d{2}(\s?[ap]\.?m\.?)?|yesterday|today|monday|tuesday|wednesday|thursday|"
                      r"friday|saturday|sunday|\d{1,2}/\d{1,2}/\d{2,4})$", re.I)
_BADGE_RE = re.compile(r"^\d{1,3}\+?$")
_TITLE_RE = re.compile(r"^\((\d+)\)")
_LOGIN_RE = re.compile(r"scan (the )?qr|log into whatsapp|link with phone|steps to log in|use whatsapp on your",
                       re.I)
_NOT_CHATS = {"chats", "archived", "all", "unread", "favourites", "favorites", "groups", "whatsapp"}


@dataclass
class ChatRow:
    name: str
    time: str
    preview: str
    unread: int
    anchor: tuple[float, float, float, float] | None = None  # time label (x, right, cy, h), for badges


@dataclass
class _Seg:
    text: str
    x: float
    right: float
    cy: float
    h: float


def _segments(lines: list[OcrLine]) -> list[_Seg]:
    """Split OCR lines at wide gaps, so 'Mum      10:42' becomes 'Mum' and '10:42'."""
    segs = []
    for line in lines:
        words = line.words or [(line.text, line.x, line.right)]
        gap = max(25.0, line.h * 1.6)
        current = [words[0]]
        for word in words[1:]:
            if word[1] - current[-1][2] > gap:
                segs.append(_Seg(" ".join(w[0] for w in current), current[0][1], current[-1][2], line.cy, line.h))
                current = []
            current.append(word)
        segs.append(_Seg(" ".join(w[0] for w in current), current[0][1], current[-1][2], line.cy, line.h))
    return segs


def unread_from_title(title: str) -> int | None:
    m = _TITLE_RE.match(title.strip())
    return int(m.group(1)) if m else (0 if "whatsapp" in title.lower() else None)


def parse_chat_rows(lines: list[OcrLine]) -> list[ChatRow]:
    """Turn an OCR'd WhatsApp window into chat rows, top to bottom.

    A chat row is: name (left) + time (right-aligned column) on one line, then preview (left)
    + optional unread badge (under the time) on the next. Times also appear on message bubbles
    in the open conversation, so only the right-aligned column holding the most times counts.
    """
    segs = _segments(lines)
    times = [s for s in segs if _TIME_RE.match(s.text.strip())]
    if not times:
        return []
    column = statistics.mode(round(t.right / 15) for t in times)
    anchors = sorted((t for t in times if abs(round(t.right / 15) - column) <= 1), key=lambda t: t.cy)
    rows = []
    for a in anchors:
        same_line = [s for s in segs if s is not a and abs(s.cy - a.cy) <= a.h * 0.8 and s.right < a.x]
        if not same_line:
            continue
        name = max(same_line, key=lambda s: s.right)
        if name.text.strip().lower() in _NOT_CHATS:
            continue
        below = [s for s in segs if a.cy + a.h * 0.6 < s.cy < a.cy + a.h * 2.8
                 and s.x >= name.x - 15 and s.right <= a.right + 25]
        badge = next((s for s in below if _BADGE_RE.match(s.text.strip()) and s.x >= a.x - 35), None)
        previews = [s for s in below if s is not badge]
        preview = min(previews, key=lambda s: s.x).text if previews else ""
        unread = int(badge.text.strip().rstrip("+")) if badge else 0
        clean = re.sub(r"[\s.,„…(]+$", "", name.text.strip())  # names cut off with "…"
        rows.append(ChatRow(clean, a.text.strip(), preview.strip(), unread, (a.x, a.right, a.cy, a.h)))
    return rows


def unread_chats_from_chip(lines: list[OcrLine]) -> int | None:
    """The 'Unread' filter chip shows how many chats are unread ('Unread 11')."""
    for seg in _segments(lines):
        m = re.fullmatch(r"unread\s+(\d+)", seg.text.strip(), re.I)
        if m:
            return int(m.group(1))
    return None


def _green_mask(region: np.ndarray) -> np.ndarray:
    b, g, r = (region[..., i].astype(int) for i in range(3))
    return (g > 140) & (g - r > 60) & (g - b > 20)  # WhatsApp's unread green (#25D366 / #00A884)


def detect_badges(rows: list[ChatRow], pixels: np.ndarray) -> None:
    """OCR misses white-on-green badge digits, so find badges by colour, then read just the badge.

    Sets row.unread to the badge number, or -1 when a badge is there but its digits can't be read.
    """
    height = pixels.shape[0]
    for row in rows:
        if row.unread or row.anchor is None:
            continue
        x, right, cy, h = row.anchor
        y0, y1 = int(cy + h * 0.6), min(height, int(cy + h * 3.2))
        x0, x1 = int(right - 3 * h), int(right + h * 0.5)
        region = pixels[y0:y1, x0:x1]
        mask = _green_mask(region)
        if mask.sum() < h * h * 0.5:  # no badge-sized green blob
            continue
        ys, xs = np.nonzero(mask)
        count = _read_badge(region[ys.min():ys.max() + 1, xs.min():xs.max() + 1, :3])
        row.unread = count if count else -1


def _read_badge(blob: np.ndarray) -> int | None:
    """Read the number on a badge crop (BGR).

    Windows OCR returns nothing for a lone glyph, but reads it fine with a word beside it, so the
    badge is redrawn as dark-on-white greyscale, enlarged, and placed after a rendered "Total:".
    """
    from PIL import Image, ImageDraw, ImageFont

    bh, bw = blob.shape[:2]
    yy, xx = np.mgrid[0:bh, 0:bw]
    inside = ((yy - (bh - 1) / 2) / (bh * 0.40)) ** 2 + ((xx - (bw - 1) / 2) / (bw * 0.43)) ** 2 <= 1
    lum = blob.astype(float) @ [0.114, 0.587, 0.299]  # BGR -> luminance
    lo, hi = np.percentile(lum[inside], 5), np.percentile(lum[inside], 60)
    gray = np.clip((lum - lo) / max(hi - lo, 1.0), 0, 1) * 255
    if np.median(lum[inside]) < lum[inside].mean():  # light digits (light theme): invert
        gray = 255 - gray
    gray[~inside] = 255
    badge = Image.fromarray(gray.astype(np.uint8)).resize((bw * 3, bh * 3), Image.BICUBIC)
    canvas = Image.new("L", (badge.width + 160, badge.height), 255)
    ImageDraw.Draw(canvas).text((4, badge.height // 3), "Total:", fill=0,
                                font=ImageFont.load_default(size=max(12, badge.height // 2)))
    canvas.paste(badge, (150, 0))
    page = np.pad(np.array(canvas), 30, constant_values=255)
    text = " ".join(l.text for l in ocr_pixels(np.dstack([page, page, page, np.full_like(page, 255)])))
    text = text.split(":", 1)[-1].replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = re.search(r"\d{1,3}", text)
    return int(m.group(0)) if m else None


def _whatsapp_window() -> winutil.WindowInfo | None:
    return browser_window("whatsapp", extra_exes={"whatsapp.exe"})


def _select_whatsapp_tab() -> bool:
    """WhatsApp may be open in a background tab."""
    return select_tab("whatsapp")


def _click_at(hwnd: int, seg: _Seg) -> None:
    """Click the middle of an OCR'd text segment, then put the mouse back where it was."""
    import win32api
    import win32gui
    from pywinauto import mouse

    left, top = win32gui.GetWindowRect(hwnd)[:2]
    before = win32api.GetCursorPos()
    mouse.click(coords=(int(left + (seg.x + seg.right) / 2), int(top + seg.cy)))
    win32api.SetCursorPos(before)


def _click_text(hwnd: int, lines: list[OcrLine], pattern: str) -> bool:
    seg = next((s for s in _segments(lines) if re.fullmatch(pattern, s.text.strip(), re.I)), None)
    if seg:
        _click_at(hwnd, seg)
    return seg is not None


def _click_chip(hwnd: int, lines: list[OcrLine], label: str) -> bool:
    """Click a filter chip ("All", "Unread") found by OCR in the top part of the window."""
    import win32gui

    rect = win32gui.GetWindowRect(hwnd)
    chip = next((s for s in _segments(lines) if s.cy < (rect[3] - rect[1]) * 0.45
                 and s.text.strip().lower().split()[0] == label), None)
    if chip:
        _click_at(hwnd, chip)
    return chip is not None


def _unread_filter_rows(hwnd: int, lines: list[OcrLine]) -> list[ChatRow]:
    """Show only unread chats (WhatsApp's own filter), read them, then switch back to All.

    A view filter only: no chat is opened, so nothing is marked as read.
    """
    if not _click_chip(hwnd, lines, "unread"):
        return []
    try:
        time.sleep(0.8)
        pixels = capture_window(hwnd)
        rows = parse_chat_rows(ocr_pixels(pixels))
        detect_badges(rows, pixels)
        for row in rows:
            row.unread = row.unread or -1  # everything in this view is unread, count unknown
        return rows
    finally:
        _click_chip(hwnd, ocr_pixels(capture_window(hwnd)), "all")


def focus_existing() -> bool:
    """Bring an open WhatsApp Web tab to the front. WhatsApp Web only works in one tab at a time
    ("WhatsApp is open in another window"), so opening a second one breaks the first."""
    win = _whatsapp_window()
    if win is None and _select_whatsapp_tab():
        time.sleep(0.5)
        win = _whatsapp_window()
    if win is None:
        return False
    winutil.focus_window(win.hwnd)
    return True


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


_TIME = re.compile(r"\s*(?:\d{1,2}:\d{2}\s*(?:[AP]M)?|yesterday|today|monday|tuesday|wednesday|thursday|friday|"
                   r"saturday|sunday|\d{1,2}/\d{1,2}/\d{2,4})\s*$", re.I)
_UNREAD = re.compile(r"(\d+)\s+unread message", re.I)


def _chat_list(page):
    """The area that lists chats: the one with the most lines ending in a time ("6:19 PM", "Yesterday")."""
    def times(r):
        return sum(bool(_TIME.search(line)) for line in r.lines)
    best = max(page.regions, key=times, default=None)
    return best if best is not None and times(best) >= 2 else None


def check_in_own_browser(cfg, read: str | None) -> ToolResult:
    """WhatsApp Web in MARY's own browser, read through the page's structure: the chat list area, the unread
    badges by their accessible names, and (only when asked) one chat, opened by clicking its name."""
    from mary import web
    from mary.page import read as read_area
    from mary.skills.web import open_page

    b = web.browser(cfg)
    open_page(URL, cfg)  # the tab already showing it, if there is one
    page, chats = None, None
    deadline = time.monotonic() + 25  # a fresh WhatsApp Web load takes a while
    while time.monotonic() < deadline:
        data = b.snapshot()
        if _LOGIN_RE.search(" ".join(t["text"] for t in data["texts"])):
            return ToolResult(False, "WhatsApp Web isn't linked in MARY's browser yet; it's showing its QR code.",
                              say="WhatsApp needs linking once: scan the QR code in my browser window with your "
                                  "phone, under WhatsApp, Linked devices.")
        page = web.page_from(data)
        chats = _chat_list(page)
        if chats is not None:
            break
        time.sleep(0.6)
    if chats is None:
        return ToolResult(False, "WhatsApp Web is open in MARY's browser, but its chat list didn't show.")
    if read:  # only now does message text leave the screen
        try:
            b.click_text(read)
        except Exception:
            return ToolResult(False, f"No chat called '{read}' is showing in the list.")
        time.sleep(0.8)
        page = web.page_from(b.snapshot())
        convo = next((r for r in page.regions if r is not chats and r.chat_like), None) or page.main()
        return ToolResult(True, f"The chat with {read}, oldest first; → marks the user's own messages:\n"
                                f"{read_area(convo, 2500)}", say=None)
    unread, names = 0, []
    for n, line in enumerate(chats.lines):
        hit = _UNREAD.search(line)
        if hit:
            unread += 1
            above = chats.lines[n - 1] if n else ""
            names.append(_TIME.sub("", above).strip() or "someone")
    if not unread:
        return ToolResult(True, "No unread WhatsApp messages.", say="No new WhatsApp messages.")
    plural = "chat" if unread == 1 else "chats"
    listing = "\n".join(chats.lines[:30])
    return ToolResult(True, f"{unread} WhatsApp {plural} with unread messages, from {', '.join(names)}. The chat "
                            f"list as shown (message text not read):\n{listing}",
                      say=f"You have unread messages in {unread} {plural}, from {_join(names[:3])}"
                          f"{' and others' if unread > 3 else ''}.")


def tools(ctx: ToolContext) -> list[Tool]:
    def check_messages(app: str = "whatsapp", read: str | None = None) -> ToolResult:
        if "whatsapp" not in app.lower():
            return ToolResult(False, f"I can only check WhatsApp so far, not {app}.")
        from mary.skills.web import own_browser

        if own_browser(ctx.cfg):
            try:
                return check_in_own_browser(ctx.cfg, read)
            except Exception as e:  # MARY's browser can't run here: the screen way still works
                log.warning("WhatsApp in MARY's browser failed: %s", e)
        win = _whatsapp_window()
        if win is None and _select_whatsapp_tab():
            time.sleep(0.8)
            win = _whatsapp_window()
        if win is None:
            os.startfile(URL)
            deadline = time.monotonic() + 25
            while win is None and time.monotonic() < deadline:
                time.sleep(0.5)
                win = _whatsapp_window()
            if win is None:
                return ToolResult(False, "WhatsApp Web didn't open in the browser.")
        winutil.focus_window(win.hwnd)

        rows: list[ChatRow] = []
        lines: list[OcrLine] = []
        deadline = time.monotonic() + 20  # a fresh WhatsApp Web load takes a while
        while time.monotonic() < deadline:
            time.sleep(0.6)
            pixels = capture_window(win.hwnd)
            lines = ocr_pixels(pixels)
            if any(_LOGIN_RE.search(l.text) for l in lines):
                return ToolResult(False, "WhatsApp Web isn't logged in; it's showing the QR code.",
                                  say="WhatsApp Web needs you to scan the QR code with your phone first.")
            if any(re.search(r"open in another window", l.text, re.I) for l in lines):
                _click_text(win.hwnd, lines, r"use here")  # move the session to this tab
                continue
            rows = parse_chat_rows(lines)
            if rows:
                detect_badges(rows, pixels)
                break
        # The "(N)" in the tab title and the "Unread N" filter chip both count *chats*, not messages.
        chats = unread_from_title(winutil.window_title(win.hwnd)) or unread_chats_from_chip(lines) or 0
        if not rows:
            return ToolResult(False, "WhatsApp Web is open but I couldn't read the chat list.")
        if not read and chats > sum(1 for r in rows if r.unread):
            rows = _unread_filter_rows(win.hwnd, lines) or rows  # most unread chats are off-screen

        if read:  # only now does message text leave the screen
            names = [r.name for r in rows]
            hit = process.extractOne(read, names, scorer=fuzz.WRatio, processor=utils.default_process)
            if not hit or hit[1] < 70:
                return ToolResult(False, f"No visible chat matches '{read}'.")
            row = rows[hit[2]]
            return ToolResult(True, f"Latest from {row.name} ({row.time}): {row.preview}",
                              say=f"{row.name} wrote: {row.preview}")

        unread = [r for r in rows if r.unread]
        chats = max(chats, len(unread))
        if not chats:
            return ToolResult(True, "No unread WhatsApp messages.", say="No new WhatsApp messages.")
        if not unread:
            return ToolResult(True, f"{chats} WhatsApp chats have unread messages; couldn't read which.",
                              say=f"You have unread messages in {chats} chats.")
        listed = ", ".join(f"{r.name} ({r.unread})" if r.unread > 0 else r.name for r in unread[:8])
        top = [r.name for r in unread[:3]]
        more = " and others" if chats > len(top) else ""
        plural = "chat" if chats == 1 else "chats"
        return ToolResult(True, f"{chats} WhatsApp {plural} with unread messages. Most recent (unread count): "
                                f"{listed}. Message text not read.",
                          say=f"You have unread messages in {chats} {plural}, from {_join(top)}{more}.")

    return [Tool(
        "check_messages",
        "Check WhatsApp for unread messages (opens WhatsApp Web itself). Reports who and how many. "
        "Set read to a chat name only when the user asks what someone said.",
        {"app": {"type": "string", "description": "only 'whatsapp' for now"},
         "read": {"type": "string", "description": "chat name whose latest message to read out"}},
        [], check_messages, speaks_for_itself=True, private=True, watch=False,
        announce=lambda a: "Checking WhatsApp…",
    )]
