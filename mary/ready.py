"""Waiting until things are really ready: the feedback loop around every tool call (mary/tools/registry.py).

A tool's work often finishes after it returns: an app is still starting, a page still loading, a dialog still
drawing. Instead of each tool guessing with fixed sleeps, the loop looks:

  before   the window an action works on (typing, clicking, reading) has to be ready first
  act      the tool runs
  look     what changed on the desktop: a new window, a different one in front, a title that changed
  settle   wait until what changed has settled, by signs that work for any app, even ones that describe little of
           themselves to UI Automation (Spotify):
             - it responds to input (Windows doesn't call it hung, and its process is idle)
             - its title has stopped changing
             - a tiny picture of it (24 x 16 grey dots, compared in memory, never kept) has stopped changing
           A window that keeps moving (a video, an animation) counts as settled once its title has been steady
           a little longer.
  not yet  a tool that didn't find what it needs yet (a search box, a result, any text) says "not yet" rather than
           "failed". It's tried again while something is still changing, or until the app it started is ready;
           once the screen is steady and it's still not there, it isn't coming, and the loop stops.
  report   the result the model reads ends with what was seen: "Now in front: Spotify, ready."

How long to wait is learned per app from logs/ready.jsonl, where each wait adds a line (the app, the seconds, the
outcome; never a window title): about 1.5x its slow end, kept between 8 and 45 s; 20 s for an app not seen yet. An
app still starting at its limit is watched in the background for up to a minute, so `wait`
(mary/skills/waiting.py) and the next step carry on from there. A request waits 60 s at most in all.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from mary.winutil import WindowInfo

log = logging.getLogger(__name__)

QUIET_S = 0.5            # unchanged this long = steady
MOTION_S = 1.5           # still moving, but with a steady title this long: settled anyway (a video, an animation)
EVERY_S = 0.25
GRACE_S = 0.4            # after an action, how long a change has to show up (longer when the tool says what started)
RECENT_S = 1.5           # a window seen steady this recently needs no look before the next action
SLOW_LINE_S = 6.0        # past this, say one "still loading" line
DEFAULT_LIMIT_S, MIN_LIMIT_S, MAX_LIMIT_S = 20.0, 8.0, 45.0
REQUEST_BUDGET_S = 60.0  # all the waiting in one request
WATCH_S = 60.0           # how long an app still starting at its limit keeps being watched
PICTURE_TOLERANCE = 3.0  # mean difference (0-255) two pictures may have and still count as the same (a caret)
FRONT = "front"          # a tool's waits_for: whatever comes to the front (a web page, an opened file)

_NAMES = {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "explorer": "File Explorer",
          "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "olk": "Outlook", "ms-teams": "Teams"}


def label(win: WindowInfo | None, fallback: str = "") -> str:
    """A friendly name for a window's app: 'spotify.exe' -> Spotify, 'msedge.exe' -> Edge."""
    if win is None:
        return fallback
    stem = Path(win.exe or "").stem.lower()
    if not stem or stem in ("applicationframehost", "python", "pythonw"):
        return win.title[:40] or fallback
    return _NAMES.get(stem) or stem[:1].upper() + stem[1:]


def _same_picture(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None or len(a) != len(b) or not a:
        return a == b
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) < PICTURE_TOLERANCE


def matches(win: WindowInfo, wanted: str) -> bool:
    """Is this window the app `wanted` names ('spotify.exe', 'Spotify', 'notepad')?"""
    want = (wanted or "").strip().lower()
    stem = want.removesuffix(".exe")
    return bool(stem) and (win.exe == want or Path(win.exe).stem == stem or stem in win.title.lower())


# ------------------------------------------------------------------ what the loop can see


class Desk:
    """The desktop as the loop sees it. RealDesk looks at Windows; tests and replays bring their own."""

    def windows(self) -> list[WindowInfo]:
        return []

    def front(self) -> WindowInfo | None:
        return None

    def title(self, hwnd: int) -> str | None:  # None: the window is gone
        return None

    def responsive(self, hwnd: int) -> bool:
        return True

    def picture(self, hwnd: int) -> bytes | None:
        return None

    def focus(self, hwnd: int) -> None:
        pass

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class StillDesk(Desk):
    """Nothing ever changes (replays, and tests of other things), and time is virtual: waiting costs nothing."""

    def __init__(self):
        self.clock = 0.0

    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds


class RealDesk(Desk):
    def windows(self) -> list[WindowInfo]:
        from mary import winutil

        return winutil.list_windows()

    def front(self) -> WindowInfo | None:
        from mary import winutil

        return winutil.foreground_window()

    def title(self, hwnd: int) -> str | None:
        from mary import winutil

        return winutil.window_title(hwnd) if winutil.user32.IsWindow(hwnd) else None

    def focus(self, hwnd: int) -> None:
        from mary import winutil

        winutil.focus_window(hwnd)

    def responsive(self, hwnd: int) -> bool:
        from mary import winutil

        if winutil.user32.IsHungAppWindow(hwnd):
            return False
        handle = winutil.kernel32.OpenProcess(0x00100000 | 0x0400, False, winutil._window_pid(hwnd))
        if not handle:
            return True
        try:  # 0 = idle, 258 = still busy starting up; anything else (a console app) can't tell, so yes
            return winutil.user32.WaitForInputIdle(handle, 0) != 258
        finally:
            winutil.kernel32.CloseHandle(handle)

    def picture(self, hwnd: int) -> bytes | None:
        import ctypes
        from ctypes import wintypes

        from PIL import ImageGrab

        from mary import winutil

        rect = wintypes.RECT()
        if winutil.user32.IsIconic(hwnd) or not winutil.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        if rect.right - rect.left < 8 or rect.bottom - rect.top < 8:
            return None
        try:
            shot = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
            return shot.convert("L").resize((24, 16)).tobytes()
        except OSError:
            return None


DESK: type[Desk] = RealDesk   # what a new Loop looks at; the tests set StillDesk
WRITE_LOG = True              # logs/ready.jsonl; the tests switch it off


# ------------------------------------------------------------------ what the loop found


@dataclass
class Snapshot:
    front: WindowInfo | None
    windows: dict[int, WindowInfo] = field(default_factory=dict)


@dataclass
class Readiness:
    state: str = "none"                 # none (nothing to wait for) | ready | starting | gone
    window: WindowInfo | None = None
    seconds: float = 0.0
    stage: str = ""                     # why it isn't ready yet
    waited: bool = False                # something had to be waited for
    appeared: bool = False              # a window that wasn't there before
    name: str = ""                      # what was waited for, when no window showed up

    def line(self) -> str:
        """What the model reads at the end of a result."""
        who = label(self.window, self.name) or "it"
        if self.state == "ready" and self.window:
            title = self.window.title.strip()
            shown = f" ({title[:70]})" if title and title.lower() != who.lower() else ""
            return f"Now in front: {who}{shown}, ready."
        if self.state == "starting":
            return f"{who[:1].upper() + who[1:]} is still starting: {self.stage}, after {self.seconds:.0f} s."
        if self.state == "gone":
            return f"{who}'s window has closed."
        return ""


# ------------------------------------------------------------------ the loop


class Loop:
    def __init__(self, desk: Desk | None = None, output=None, log_path: Path | None = None, patient: bool = True):
        self.desk = desk or DESK()
        self.output = output
        self.log_path = log_path
        self.patient = patient           # False (replays): report "not yet" straight away instead of waiting
        self.spent = 0.0                 # seconds waited in this request
        self.said_slow = False           # the "still loading" line, once per tool call
        self.call_started: float | None = None
        self.watching: dict[int, threading.Event] = {}   # apps still starting at their limit; set once ready
        self.steady_at: dict[int, float] = {}            # when each window was last seen ready
        self._history: dict[str, list[float]] | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- budget and limits

    def new_request(self) -> None:
        self.spent = 0.0

    def new_call(self) -> None:
        self.said_slow = False
        self.call_started = self.desk.now()

    def left(self, limit: float) -> float:
        return max(0.0, min(limit, REQUEST_BUDGET_S - self.spent)) if self.patient else 0.0

    def limit_for(self, exe: str) -> float:
        """About 1.5x this app's slow end, from how long it took before; DEFAULT_LIMIT_S until it's been seen 3 times."""
        if self._history is None:
            self._history = self._read_history()
        times = self._history.get((exe or "").lower(), [])[-30:]
        if len(times) < 3:
            return DEFAULT_LIMIT_S
        slow_end = sorted(times)[int(0.9 * (len(times) - 1))]
        return max(MIN_LIMIT_S, min(MAX_LIMIT_S, slow_end * 1.5))

    def _read_history(self) -> dict[str, list[float]]:
        history: dict[str, list[float]] = {}
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines() if self.log_path else []
        except OSError:
            lines = []
        for line in lines[-2000:]:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("state") == "ready" and d.get("app") and d.get("waited"):
                history.setdefault(d["app"], []).append(float(d.get("seconds") or 0))
        return history

    def _record(self, r: Readiness, why: str) -> None:
        if r.state == "none":
            return
        app = (r.window.exe if r.window else r.name or "").lower()
        if r.state == "ready" and r.waited:
            (self._history if self._history is not None else {}).setdefault(app, []).append(r.seconds)
        if not self.log_path or not WRITE_LOG:
            return
        line = {"at": datetime.now().isoformat(timespec="seconds"), "app": app, "why": why, "state": r.state,
                "seconds": round(r.seconds, 2), "waited": r.waited, "stage": r.stage}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        except OSError:
            pass

    def _spend(self, seconds: float) -> None:
        self.spent += seconds

    def _slow(self, who: str, seconds: float, starting: bool) -> None:
        """One "still starting" line once a tool call (all its waits together) has taken SLOW_LINE_S."""
        if self.call_started is not None:
            seconds = max(seconds, self.desk.now() - self.call_started)
        if seconds >= SLOW_LINE_S and not self.said_slow and self.output is not None:
            self.said_slow = True
            self.output.aside(f"{who or 'It'} is still {'starting' if starting else 'loading'}…")

    # ---------------------------------------------------------------- looking

    def snapshot(self) -> Snapshot:
        return Snapshot(self.desk.front(), {w.hwnd: w for w in self.desk.windows()})

    def _look(self, hwnd: int) -> tuple[str, bytes | None] | None:
        title = self.desk.title(hwnd)
        return None if title is None else (title, self.desk.picture(hwnd))

    def window(self, name: str | None = None) -> WindowInfo | None:
        """The window an action works on: the one named (an app or title), else the one in front."""
        if name:
            front = self.desk.front()
            if front and matches(front, name):
                return front
            return next((w for w in self.desk.windows() if matches(w, name)), None)
        return self.desk.front()

    def _changed(self, before: Snapshot, wanted: str | None) -> tuple[WindowInfo | None, bool]:
        """The window an action changed: (window, it's new)."""
        front, windows = self.desk.front(), self.desk.windows()
        new = [w for w in windows if w.hwnd not in before.windows]
        if wanted and wanted != FRONT:
            mine = [w for w in ([front] if front else []) + new if matches(w, wanted)]
            return (mine[0], mine[0].hwnd not in before.windows) if mine else (None, False)
        if front:
            old = before.windows.get(front.hwnd)
            moved = before.front is None or front.hwnd != before.front.hwnd
            if old is None or moved or old.title != front.title:
                return front, old is None
        return (new[0], True) if new else (None, False)

    # ---------------------------------------------------------------- waiting

    def settle(self, win: WindowInfo, limit: float, starting: bool = False) -> Readiness:
        """Wait until the window responds and has stopped changing, up to `limit` seconds."""
        d = self.desk
        start = d.now()
        last = self._look(win.hwnd)
        title_since = picture_since = start
        responsive_since: float | None = None   # a video draws while it responds; a loading app doesn't yet
        while True:
            now = d.now()
            seconds = now - start
            cur = self._look(win.hwnd)
            if cur is None or last is None:
                self._spend(seconds)
                return Readiness("gone", win, seconds, waited=seconds > 0)
            if cur[0] != last[0]:
                title_since = now
            if not _same_picture(cur[1], last[1]):
                picture_since = now
            last = cur
            responsive = d.responsive(win.hwnd)
            if not responsive:
                responsive_since = None
            elif responsive_since is None:
                responsive_since = now
            steady_title = now - title_since >= QUIET_S
            moving_but_usable = responsive_since is not None and now - max(title_since, responsive_since) >= MOTION_S
            if responsive and steady_title and (now - picture_since >= QUIET_S or moving_but_usable):
                self._spend(seconds)
                self.steady_at[win.hwnd] = now
                return Readiness("ready", replace(win, title=cur[0]), seconds, waited=seconds > QUIET_S + 0.05)
            if seconds >= limit:
                self._spend(seconds)
                return Readiness("starting", replace(win, title=cur[0]), seconds, waited=True,
                                 stage="it's still loading" if responsive else "it isn't responding yet")
            self._slow(label(win), seconds, starting)
            d.sleep(EVERY_S)

    def before(self, win: WindowInfo | None, fresh: bool = False) -> Readiness:
        """Before an action works on this window: if it's still starting or changing, wait until it's ready.
        fresh: look now, even if it was seen steady a moment ago."""
        if win is None or not self.patient:
            return Readiness()
        watched = self.watching.get(win.hwnd)
        if watched is not None and not watched.is_set():
            return self._await(win, watched, self.left(self.limit_for(win.exe)))
        if not fresh and self.desk.now() - self.steady_at.get(win.hwnd, -RECENT_S) < RECENT_S:
            return Readiness("ready", win)  # seen steady a moment ago (the last step's look)
        a = self._look(win.hwnd)
        self.desk.sleep(0.25)
        b = self._look(win.hwnd)
        if a and b and a[0] == b[0] and _same_picture(a[1], b[1]) and self.desk.responsive(win.hwnd):
            self.steady_at[win.hwnd] = self.desk.now()
            return Readiness("ready", replace(win, title=b[0]), 0.25)
        r = self.settle(win, self.left(self.limit_for(win.exe)))
        self._record(r, "before")
        return r

    def after(self, before: Snapshot, wanted: str | None = None) -> Readiness:
        """After an action: find what it changed (or the app it says it started) and wait until that's settled."""
        if not self.patient:
            return Readiness()
        d = self.desk
        start = d.now()
        limit = self.left(self.limit_for(wanted if wanted and wanted != FRONT else ""))
        window = limit if wanted else min(GRACE_S, limit)
        while True:
            win, new = self._changed(before, wanted)
            seconds = d.now() - start
            if win is not None or seconds >= window:
                break
            self._slow(wanted if wanted and wanted != FRONT else "", seconds, True)
            d.sleep(EVERY_S)
        if win is None:
            self._spend(seconds)
            if not wanted:
                return Readiness()
            name = "" if wanted == FRONT else Path(wanted).stem
            r = Readiness("starting", None, seconds, stage="its window hasn't appeared", waited=True,
                          name=name[:1].upper() + name[1:] if name else "the page")
            self._record(r, "after")
            return r
        self._spend(seconds)
        front = d.front()
        if wanted and wanted != FRONT and (front is None or front.hwnd != win.hwnd):
            d.focus(win.hwnd)  # the app the action started, in front, so what follows lands in it
        r = self.settle(win, max(0.0, self.left(limit) - seconds) if wanted else self.left(self.limit_for(win.exe)),
                        starting=new)
        r = replace(r, appeared=new, seconds=r.seconds + seconds, waited=r.waited or seconds > EVERY_S)
        self._record(r, "after")
        if r.state == "starting":
            self.watch(r.window)
        return r

    def _await(self, win: WindowInfo, event: threading.Event, limit: float) -> Readiness:
        start = self.desk.now()
        while not event.is_set() and self.desk.now() - start < limit:
            self._slow(label(win), self.desk.now() - start, True)
            event.wait(EVERY_S)
        seconds = self.desk.now() - start
        self._spend(seconds)
        title = self.desk.title(win.hwnd)
        if title is None:
            return Readiness("gone", win, seconds, waited=True)
        win = replace(win, title=title)
        return Readiness("ready", win, seconds, waited=True) if event.is_set() else \
            Readiness("starting", win, seconds, stage="it's still loading", waited=True)

    def watch(self, win: WindowInfo | None) -> None:
        """Keep watching an app that's still starting, in the background, so `wait` and the next step carry on."""
        if win is None or win.hwnd in self.watching:
            return
        event = self.watching[win.hwnd] = threading.Event()

        def run() -> None:
            watcher = Loop(self.desk, None, None)
            r = watcher.settle(win, WATCH_S)
            if r.state == "ready":
                event.set()
            else:
                self.watching.pop(win.hwnd, None)

        threading.Thread(target=run, name=f"ready-watch-{win.hwnd}", daemon=True).start()

    def wait_for(self, name: str, seconds: float) -> Readiness:
        """The `wait` tool: until the app or window called `name` (or the one in front) is ready."""
        d = self.desk
        start, limit = d.now(), self.left(seconds)
        win = self.window(name or None)
        while win is None and name and d.now() - start < limit:  # not open yet: give it the time to appear
            self._slow(name, d.now() - start, True)
            d.sleep(EVERY_S)
            win = self.window(name)
        waited = d.now() - start
        if win is None:
            self._spend(waited)
            r = Readiness("starting", None, waited, stage="its window hasn't appeared", waited=True, name=name) \
                if name else Readiness()
        else:
            self._spend(waited)
            watched = self.watching.get(win.hwnd)
            left = max(0.0, limit - waited)
            r = self._await(win, watched, left) if watched is not None else self.settle(win, left)
            r = replace(r, seconds=r.seconds + waited, waited=True)
        self._record(r, "wait")
        return r

    def pause(self, seconds: float) -> float:
        seconds = self.left(seconds)
        self.desk.sleep(seconds)
        self._spend(seconds)
        return seconds

    def until(self, check: Callable[[], bool], limit: float) -> tuple[bool, float]:
        """Work that doesn't show on screen (a download, a copy): poll its check."""
        d = self.desk
        start, limit = d.now(), self.left(limit)
        while True:
            seconds = d.now() - start
            if check():
                self._spend(seconds)
                return True, seconds
            if seconds >= limit:
                self._spend(seconds)
                return False, seconds
            self._slow("It", seconds, False)
            d.sleep(0.5)
