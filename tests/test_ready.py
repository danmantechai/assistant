"""The feedback loop around every tool call (mary/ready.py, mary/tools/registry.py), on a scripted desktop with
virtual time: windows appear, keep drawing, stay busy or close at set seconds. No real window is looked at."""
import json

import pytest

from mary import ready
from mary.config import load_config
from mary.output import Output
from mary.ready import Loop, Snapshot
from mary.tools.registry import Tool, ToolContext, ToolRegistry, ToolResult
from mary.winutil import WindowInfo


class FakeDesk(ready.Desk):
    def __init__(self):
        self.t = 0.0
        self.wins: dict[int, dict] = {}
        self.front_hwnd: int | None = None
        self.focused: list[int] = []

    def add(self, hwnd, exe, title, at=0.0, busy_until=0.0, drawing_until=0.0, closes_at=None, front=True,
            titles=()):
        """A window from `at` s, unresponsive until `busy_until`, still drawing until `drawing_until`; `titles`:
        [(from_second, title)] for a title that changes as it loads."""
        self.wins[hwnd] = dict(info=WindowInfo(hwnd, title, 1000 + hwnd, exe), at=at, busy=busy_until,
                               drawing=drawing_until, closes=closes_at, titles=list(titles))
        if front:
            self.front_hwnd = hwnd

    def draw(self, hwnd, until):
        self.wins[hwnd]["drawing"] = until

    def _live(self, hwnd):
        w = self.wins.get(hwnd)
        return w is not None and w["at"] <= self.t and (w["closes"] is None or self.t < w["closes"])

    def _title(self, w):
        title = w["info"].title
        for since, t in w["titles"]:
            if self.t >= since:
                title = t
        return title

    def windows(self):
        return [WindowInfo(h, self._title(w), w["info"].pid, w["info"].exe) for h, w in self.wins.items() if self._live(h)]

    def front(self):
        h = self.front_hwnd
        if h is None or not self._live(h):
            return None
        w = self.wins[h]
        return WindowInfo(h, self._title(w), w["info"].pid, w["info"].exe)

    def title(self, hwnd):
        return self._title(self.wins[hwnd]) if self._live(hwnd) else None

    def responsive(self, hwnd):
        return self.t >= self.wins[hwnd]["busy"]

    def picture(self, hwnd):
        w = self.wins[hwnd]
        return bytes([int(self.t * 40) % 250] * 384) if self.t < w["drawing"] else bytes([9] * 384)

    def focus(self, hwnd):
        self.front_hwnd = hwnd
        self.focused.append(hwnd)

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Recorder(Output):
    def __init__(self):
        self.asides, self.logs = [], []

    def aside(self, text):
        self.asides.append(text)

    def tool_log(self, line):
        self.logs.append(line)


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture
def out():
    return Recorder()


@pytest.fixture
def loop(desk, out, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "WRITE_LOG", True)
    return Loop(desk, out, log_path=tmp_path / "ready.jsonl")


# ------------------------------------------------------------------ settling


def test_a_window_still_drawing_is_waited_for(desk, loop):
    desk.add(1, "spotify.exe", "Spotify", busy_until=2.0, drawing_until=3.0)
    r = loop.settle(loop.window(), 20)
    assert r.state == "ready" and r.waited and 3.0 <= r.seconds <= 4.0
    assert r.line() == "Now in front: Spotify, ready."


def test_at_the_limit_it_says_so_honestly(desk, loop):
    desk.add(1, "winword.exe", "Word", busy_until=999)
    r = loop.settle(loop.window(), 5)
    assert r.state == "starting" and 5.0 <= r.seconds < 5.5
    assert r.line() == "Word is still starting: it isn't responding yet, after 5 s."


def test_a_video_counts_as_settled_once_its_title_is_steady(desk, loop):
    desk.add(1, "msedge.exe", "Cat video - YouTube", drawing_until=999)
    r = loop.settle(loop.window(), 20)
    assert r.state == "ready" and r.seconds <= ready.MOTION_S + 0.5
    assert r.line() == "Now in front: Edge (Cat video - YouTube), ready."


def test_a_window_that_closes_is_gone(desk, loop):
    desk.add(1, "notepad.exe", "Notepad", drawing_until=999, closes_at=1.0)
    assert loop.settle(loop.window(), 20).state == "gone"


# ------------------------------------------------------------------ after an action


def test_the_app_an_action_started_is_waited_for_and_brought_to_the_front(desk, loop):
    desk.add(1, "explorer.exe", "Documents")
    before = loop.snapshot()
    desk.add(2, "spotify.exe", "Spotify Premium", at=4.0, busy_until=5.0, drawing_until=6.0, front=False)
    r = loop.after(before, "spotify.exe")
    assert r.state == "ready" and r.appeared and desk.focused == [2]
    assert 6.0 <= r.seconds <= 7.5


def test_an_app_that_never_appears_is_reported(desk, loop):
    r = loop.after(loop.snapshot(), "spotify.exe")
    assert r.state == "starting" and r.line() == "Spotify is still starting: its window hasn't appeared, after 20 s."


def test_a_page_loading_in_front_is_waited_for(desk, loop):
    desk.add(1, "msedge.exe", "New tab")
    before = loop.snapshot()
    desk.wins[1]["titles"] = [(0.3, "sarkodie - YouTube")]
    desk.draw(1, until=1.5)
    r = loop.after(before, "front")
    assert r.state == "ready" and r.window.title == "sarkodie - YouTube" and r.seconds >= 1.5


def test_nothing_changed_costs_only_a_glance(desk, loop):
    desk.add(1, "notepad.exe", "Notepad")
    r = loop.after(loop.snapshot())
    assert r.state == "none" and r.line() == "" and desk.t <= ready.GRACE_S + ready.EVERY_S


def test_the_still_loading_line_is_said_once(desk, loop, out):
    desk.add(1, "spotify.exe", "Spotify", busy_until=15)
    loop.new_call()
    loop.settle(loop.window(), 20, starting=True)
    loop.settle(loop.window(), 20, starting=True)
    assert out.asides == ["Spotify is still starting…"]


# ------------------------------------------------------------------ learned limits and the log


def test_limits_are_learned_per_app(tmp_path, monkeypatch):
    log = tmp_path / "ready.jsonl"
    lines = [{"app": "winword.exe", "state": "ready", "waited": True, "seconds": s} for s in (10, 12, 30)] + \
            [{"app": "notepad.exe", "state": "ready", "waited": True, "seconds": 0.8}] * 5 + \
            [{"app": "slow.exe", "state": "ready", "waited": True, "seconds": 60}] * 3
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    loop = Loop(FakeDesk(), None, log_path=log)
    assert loop.limit_for("winword.exe") == 18.0           # 1.5 x its slow end
    assert loop.limit_for("notepad.exe") == ready.MIN_LIMIT_S
    assert loop.limit_for("slow.exe") == ready.MAX_LIMIT_S
    assert loop.limit_for("never-seen.exe") == ready.DEFAULT_LIMIT_S


def test_each_wait_is_logged_without_window_titles(desk, loop, tmp_path):
    desk.add(1, "explorer.exe", "Documents")
    before = loop.snapshot()
    desk.add(2, "whatsapp.exe", "Chat with Ama", at=1.0, drawing_until=2.0)
    loop.after(before, "whatsapp.exe")
    line = json.loads((tmp_path / "ready.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["app"] == "whatsapp.exe" and line["state"] == "ready" and line["why"] == "after"
    assert "Ama" not in json.dumps(line)


def test_a_request_waits_a_minute_at_most(desk, loop):
    desk.add(1, "slow.exe", "Slow", busy_until=999)
    loop.new_request()
    loop.settle(loop.window(), loop.left(45))
    assert loop.left(45) == pytest.approx(15, abs=0.5)
    loop.settle(loop.window(), loop.left(45))
    assert loop.left(45) == 0


# ------------------------------------------------------------------ the loop around any tool


def registry(desk, out, tmp_path, *tools):
    cfg = load_config()
    cfg["skills"] = ["clock", "waiting"]
    ctx = ToolContext(cfg, None, out)
    ctx.loop = Loop(desk, out, log_path=tmp_path / "ready.jsonl")
    reg = ToolRegistry(ctx)
    for tool in tools:
        reg.tools[tool.name] = tool
    return reg


def test_a_tool_made_up_for_this_test_gets_the_whole_loop(desk, out, tmp_path):
    desk.add(1, "someapp.exe", "Some app")
    calls = []

    def slowpoke():  # its list isn't there yet twice, while the window is still drawing it
        calls.append(desk.t)
        if len(calls) < 3:
            desk.draw(1, until=desk.t + 1.0)
            return ToolResult(False, "The list isn't there yet.", status="not_yet")
        return ToolResult(True, "Found the list.")

    reg = registry(desk, out, tmp_path, Tool("slowpoke", "A tool written only for this test.", {}, [], slowpoke))
    r = reg.run("slowpoke", {})
    assert r.ok and r.text == "Found the list." and len(calls) == 3


def test_not_yet_on_a_steady_screen_stops_at_once(desk, out, tmp_path):
    desk.add(1, "someapp.exe", "Some app")
    calls = []

    def missing():
        calls.append(desk.t)
        return ToolResult(False, "Couldn't click 'Buy': no such button.", status="not_yet")

    r = registry(desk, out, tmp_path, Tool("missing", "x", {}, [], missing)).run("missing", {})
    assert not r.ok and len(calls) == 1 and desk.t < 1.5
    assert r.text.startswith("Couldn't click 'Buy': no such button.") and "call wait" in r.text


def test_not_yet_for_an_app_it_started_waits_for_the_app_then_tries_again(desk, out, tmp_path):
    desk.add(9, "explorer.exe", "Documents")
    calls = []

    def play():
        calls.append(desk.t)
        if not any(w.exe == "spotify.exe" for w in desk.windows()):
            if len(calls) == 1:
                desk.add(2, "spotify.exe", "Spotify", at=4.0, busy_until=6.0, drawing_until=7.0, front=False)
            return ToolResult(False, "Spotify is starting.", status="not_yet", waits_for="spotify.exe")
        return ToolResult(True, "Playing Sarkodie.")

    reg = registry(desk, out, tmp_path, Tool("play", "x", {}, [], play))
    r = reg.run("play", {})
    assert r.ok and len(calls) == 2 and calls[1] >= 7.0
    assert r.text == "Playing Sarkodie. Now in front: Spotify, ready."
    assert reg.ctx.last_launch[0] == 2 and out.asides == ["Spotify is still starting…"]


def test_an_action_waits_for_its_window_first(desk, out, tmp_path):
    desk.add(1, "winword.exe", "Document1 - Word", busy_until=3.0)
    typed_at = []
    tool = Tool("type", "x", {}, [], lambda: typed_at.append(desk.t) or ToolResult(True, "Typed it."),
                acts_on="front")
    registry(desk, out, tmp_path, tool).run("type", {})
    assert typed_at[0] >= 3.0


def test_work_off_screen_is_polled_through_its_check(desk, out, tmp_path):
    def download():
        done_at = desk.t + 4
        return ToolResult(True, "Downloading.", check=lambda: desk.t >= done_at)

    r = registry(desk, out, tmp_path, Tool("download", "x", {}, [], download)).run("download", {})
    assert r.text == "Downloading." and desk.t >= 4

    def stuck():
        return ToolResult(True, "Downloading.", check=lambda: False)

    r = registry(desk, out, tmp_path, Tool("stuck", "x", {}, [], stuck)).run("stuck", {})
    assert "isn't finished yet after 20 s" in r.text


def test_tools_that_never_touch_a_window_are_left_alone(desk, out, tmp_path):
    desk.add(1, "notepad.exe", "Notepad", busy_until=99, drawing_until=99)
    tool = Tool("volume", "x", {}, [], lambda: ToolResult(True, "Volume's at 40%."), watch=False)
    r = registry(desk, out, tmp_path, tool).run("volume", {})
    assert r.text == "Volume's at 40%." and desk.t == 0


# ------------------------------------------------------------------ the wait tool


def test_wait_for_an_app_that_is_still_starting(desk, out, tmp_path):
    desk.add(9, "explorer.exe", "Documents")
    desk.add(2, "spotify.exe", "Spotify", at=3.0, busy_until=5.0, front=False)
    r = registry(desk, out, tmp_path).run("wait", {"target": "Spotify", "seconds": 20})
    assert r.ok and r.text.startswith("Waited ") and "Now in front: Spotify, ready." in r.text
    assert 5.0 <= desk.t <= 7.0


def test_wait_is_capped(desk, out, tmp_path):
    reg = registry(desk, out, tmp_path)
    r = reg.run("wait", {"target": "Spotify", "seconds": 999})
    assert not r.ok and "hasn't appeared" in r.text and desk.t == pytest.approx(30, abs=0.5)
    r = reg.run("wait", {"seconds": 999})        # same request: only the minute's remainder is left
    assert r.ok and desk.t == pytest.approx(60, abs=0.5)
