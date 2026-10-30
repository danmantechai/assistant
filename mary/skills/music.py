"""play_music: search Spotify and play the top result, through the desktop app's UI Automation tree.

Probed on the Store version of Spotify: the `spotify:search:` URI doesn't navigate, but typing into
the "What do you want to play?" box shows results within ~3 s as buttons named "Play <name>".
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote_plus

from rapidfuzz import fuzz, utils

from mary import uia, winutil
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ('"Play <song, artist, genre, playlist>" or "play some music": play_music. media_control only pauses, '
         "resumes or skips what's already playing.")
GROUNDING = [
    ("play_music", lambda a: True,
     r"\b(play|music|songs?|listen|put on|hear|spotify|playlist|album|track|spin|queue)\b",
     "the user didn't ask for music"),
]
SLOW = {"play_music": ["Let me find that for you.", "One moment, I'll put it on."]}

log = logging.getLogger(__name__)

EXE = "spotify.exe"
_IDLE_TITLES = {"spotify", "spotify premium", "spotify free"}  # window title when nothing is playing
_VK_MEDIA_PLAY_PAUSE = 0xB3


def _is_playing(win) -> bool:
    return (win.element_info.name or "").strip().lower() not in _IDLE_TITLES


def _wait_playing(win, seconds: float = 5.0, not_title: str | None = None) -> bool:
    """Wait until something plays (and, with `not_title`, until it's a different track than before)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _is_playing(win) and (not_title is None or win.element_info.name != not_title):
            return True
        time.sleep(0.3)
    return False


def _play_buttons(win) -> list:
    return uia.named(win.descendants(control_type="Button"), prefix="play ")


def _search_box(win):
    boxes = uia.named(win.descendants(control_type="ComboBox"), contains="what do you want to")
    return boxes[0] if boxes else None


def _short(name: str, limit: int = 45) -> str:
    """'Country Songs 70'S 80'S 90'S: Classic Top Country…' -> 'Country Songs 70'S 80'S 90'S' (for speech)."""
    for sep in (":", " | ", " - ", " ("):
        name = name.split(sep)[0]
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]


def _relevance(button, query: str) -> float:
    name = (button.element_info.name or "")[len("Play "):]
    return fuzz.WRatio(query, name, processor=utils.default_process)


def _wait_for_results(win, before: list[str], query: str, seconds: float = 8.0) -> list:
    """Play buttons once results for `query` are showing and have stopped changing.

    Clearing the search box briefly shows Spotify's default page, so wait until some result
    resembles the query. Searching the same thing twice leaves the list unchanged, so "changed
    from before" can't be required; a short minimum wait lets Spotify swap in the new results.
    """
    start = time.monotonic()
    last, buttons = None, []
    while time.monotonic() - start < seconds:
        time.sleep(0.5)
        buttons = _play_buttons(win)
        names = [b.element_info.name for b in buttons]
        relevant = any(_relevance(b, query) >= 60 for b in buttons)
        if names and names == last and relevant and time.monotonic() - start >= 1.5:
            return buttons
        last = names
    return [b for b in buttons if b.element_info.name not in before] or buttons  # best effort


def _pick(results: list, before: list[str], query: str) -> list:
    """Results to try, best first: Spotify's top result unless another is a clearly better match.

    The top result's big card always shows its Play button; smaller cards only reveal theirs on
    hover, so the top result is also the fallback when pressing another one doesn't start.
    """
    fresh = [b for b in results if b.element_info.name not in before] or results
    top, best = fresh[0], max(fresh, key=lambda b: _relevance(b, query))
    if best is not top and _relevance(best, query) >= _relevance(top, query) + 15:
        return [best, top]
    return [top]


def tools(ctx: ToolContext) -> list[Tool]:
    def youtube_fallback(query: str, why: str) -> ToolResult:
        os.startfile(f"https://www.youtube.com/results?search_query={quote_plus(query or 'music')}")
        return ToolResult(True, f"{why} Opened a YouTube search for '{query}' instead.",
                          say=f"{why} I've opened YouTube results for {query or 'music'} instead.")

    def play_music(query: str | None = None) -> ToolResult:
        query = (query or "").strip()
        win = uia.app_window(EXE)
        if win is None:
            match = ctx.apps.match("spotify")
            if match.app is None:
                return youtube_fallback(query, "Spotify isn't installed.")
            ctx.apps.launch(match.app)
            win = uia.app_window(EXE, timeout=15)
            if win is None:
                return youtube_fallback(query, "Spotify didn't open.")
            time.sleep(2)  # let the home page finish loading before we type
        winutil.focus_window(win.handle)

        if not query:  # "play some music": resume, or start Liked Songs
            if _is_playing(win):
                return ToolResult(True, f"Already playing: {win.element_info.name}.", say="It's already playing.")
            winutil.tap_vk(_VK_MEDIA_PLAY_PAUSE, extended=True)
            if not _wait_playing(win, 3):
                liked = uia.named(_play_buttons(win), prefix="play liked songs")
                if liked:
                    uia.press(liked[0])
            if _wait_playing(win):
                return ToolResult(True, f"Now playing: {win.element_info.name}.", say="Here's some music.")
            return ToolResult(False, "Spotify didn't start playing. Is it logged in?")

        box = _search_box(win)
        if box is None:
            return youtube_fallback(query, "I couldn't find Spotify's search box.")
        before = [b.element_info.name for b in _play_buttons(win)]
        box.set_focus()
        from pywinauto.keyboard import send_keys

        send_keys("^a{BACKSPACE}")
        winutil.type_unicode(query)
        results = _wait_for_results(win, before, query)
        if not results:
            return ToolResult(False, f"Spotify showed no results for '{query}'.")
        previous = win.element_info.name  # a track that was already playing doesn't count as success
        tried = []
        for choice in _pick(results, before, query):
            label = choice.element_info.name
            name = label[len("Play "):]
            tried.append(name)
            uia.press(choice)
            started = _wait_playing(win, 2.5, not_title=previous)
            if not started:  # results may have re-rendered under us: find the button again, click for real
                again = [b for b in _play_buttons(win) if b.element_info.name == label]
                if again:
                    again[0].click_input()
                    started = _wait_playing(win, 3, not_title=previous)
            if started:
                now = win.element_info.name
                log.info("spotify: '%s' -> %s / %s", query, name, now)
                return ToolResult(True, f"Playing {name}. Now playing: {now}.", say=f"Playing {_short(name)}.")
        return ToolResult(False, f"Pressed play on {' and '.join(tried)}, but Spotify didn't start.",
                          say="Spotify didn't start playing. Is it logged in?")

    return [Tool(
        "play_music",
        "Play a song, artist, album, playlist or genre on Spotify (searches and plays the top result). "
        "Without query, resumes or plays Liked Songs.",
        {"query": {"type": "string", "description": "what to play, e.g. 'country music', 'Don Williams'"}},
        [], play_music, speaks_for_itself=True,
        announce=lambda a: f"Finding {a.get('query') or 'some music'} on Spotify…",
    )]
