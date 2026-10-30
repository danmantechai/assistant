"""The screen skill's decisions, on fake elements shaped like real ones (a YouTube results page as UI
Automation reported it), plus the link check and the router commands. Nothing is clicked."""
import time

import pytest

from mary.config import load_config
from mary.skills.screen_control import RISKY, Element, find, nth, short
from tests.conftest import needs_router

YOUTUBE = [  # names as Edge reported them for a "MrBeast" search (positions simplified)
    Element("edit", "Search", (500, 20, 900, 60)),
    Element("button", "Search", (900, 20, 950, 60)),
    Element("link", "MrBeast Verified @MrBeast•523M subscribers SUBSCRIBE FOR A COOKIE!", (980, 263, 1748, 433)),
    Element("button", "Subscribe", (1600, 300, 1700, 340)),
    Element("link", "Lose 150 Lbs Together, Win $250,000 43 minutes", (335, 548, 960, 899)),
    Element("link", "Lose 150 Lbs Together, Win $250,000 43 minutes", (980, 548, 1354, 581)),
    Element("link", "Go to channel MrBeast", (980, 618, 1020, 651)),
    Element("link", "Eat Everything In A Grocery Store, Win $1,000,000 57 minutes", (980, 918, 1472, 952)),
    Element("button", "Delete history", (20, 1000, 200, 1040)),
]


@pytest.mark.parametrize("target, expected", [
    ("subscribe", "Subscribe"),
    ("the subscribe button", "Subscribe"),
    ("eat everything in a grocery store. Win $1,000. So $1,000,000", "Eat Everything In A Grocery Store"),
    ("grocery store", "Eat Everything In A Grocery Store"),
    ("the search box", "Search"),
])
def test_find_by_what_it_says(target, expected):
    el, why = find(YOUTUBE, target)
    assert el is not None and el.name.startswith(expected), why


def test_role_words_pick_the_kind():
    assert find(YOUTUBE, "search box")[0].kind == "edit"
    assert find(YOUTUBE, "search button")[0].kind == "button"


def test_ordinals_skip_channels_and_duplicates():
    assert find(YOUTUBE, "first video")[0].name.startswith("Lose 150 Lbs")
    assert find(YOUTUBE, "second video")[0].name.startswith("Eat Everything")
    # On YouTube "the first one" means the first video, not the channel card above it.
    assert find(YOUTUBE, "the first one", videos_first=True)[0].name.startswith("Lose 150 Lbs")
    assert nth(YOUTUBE, 1).name.startswith("MrBeast Verified")  # elsewhere: the first content link


def test_numbers_refer_to_the_last_list():
    assert find(YOUTUBE, "number 2", numbered=YOUTUBE[:3])[0] is YOUTUBE[1]
    assert find(YOUTUBE, "7", numbered=YOUTUBE[:3])[0] is None


def test_nothing_matching_says_so():
    el, why = find(YOUTUBE, "buy now")
    assert el is None and "nothing called" in why


def test_risky_labels_need_a_yes():
    assert RISKY.search("Delete history") and RISKY.search("Place your order") and RISKY.search("Send")
    assert not RISKY.search("Subscribe") and not RISKY.search("Eat Everything In A Grocery Store")


def test_spoken_names_are_short():
    assert short("Eat Everything In A Grocery Store, Win $1,000,000 57 minutes") == \
        "Eat Everything In A Grocery Store, Win…"


# ---------------------------------------------------------------- invented links are refused

@pytest.fixture
def web(tmp_path):
    from mary.app_index import AppIndex
    from mary.output import Output
    from mary.tools.registry import ToolContext, ToolRegistry
    cfg = load_config()
    reg = ToolRegistry(ToolContext(cfg, AppIndex(cfg), Output()))
    opened = []
    import mary.skills.web as webmod
    webmod.os.startfile, real = opened.append, webmod.os.startfile
    yield reg, opened
    webmod.os.startfile = real


def test_made_up_video_link_is_refused(web):
    reg, opened = web
    reg.ctx.request = "check youtube for mrbeast"
    result = reg.tools["web"].fn(action="open_url", value="https://www.youtube.com/watch?v=6tGm6Jp06dA")
    assert not result.ok and "click it by its title" in result.text and opened == []


def test_links_that_were_seen_or_said_open(web):
    reg, opened = web
    reg.ctx.seen.append("… bbc.co.uk/news/world-123 …")
    assert reg.tools["web"].fn(action="open_url", value="https://www.bbc.co.uk/news/world-123").ok
    reg.ctx.request = "open github.com/anthropics"
    assert reg.tools["web"].fn(action="open_url", value="github.com/anthropics").ok
    assert reg.tools["web"].fn(action="open_url", value="youtube").ok  # a known site
    assert len(opened) == 3


def test_youtube_search_goes_to_youtube(web):
    reg, opened = web
    result = reg.tools["web"].fn(action="search", value="MrBeast", site="youtube")
    assert opened == ["https://www.youtube.com/results?search_query=MrBeast"]
    assert result.say == "I've opened a search for MrBeast on Youtube in your browser."


# ---------------------------------------------------------------- voice commands

@pytest.fixture(scope="module")
def router():
    from mary.app_index import AppIndex
    from mary.output import Output
    from mary.router.router import Router
    from mary.tools.registry import ToolContext
    cfg = load_config()
    apps = AppIndex(cfg)
    ctx = ToolContext(cfg, apps, Output())
    return needs_router(lambda: Router(cfg, apps, ctx)), ctx


@pytest.mark.parametrize("text, args", [
    ("click subscribe", {"action": "click", "target": "subscribe"}),
    ("press the play button", {"action": "click", "target": "the play button"}),
    ("scroll down", {"action": "scroll", "target": "down"}),
    ("scroll up a bit", {"action": "scroll", "target": "up a bit"}),
    ("search YouTube for MrBeast", {"action": "search", "value": "mrbeast", "site": "youtube"}),
    ("find MrBeast videos on YouTube", {"action": "search", "value": "mrbeast videos", "site": "youtube"}),
])
def test_screen_commands_route_directly(router, text, args):
    d = router[0].route(text)
    assert d.route == "direct" and d.args == args


@pytest.mark.parametrize("text", ["press enter", "select all", "click it"])
def test_keys_and_vague_clicks_go_to_the_llm(router, text):
    assert router[0].route(text).route != "direct"


def test_results_after_a_web_page_are_clicked(router):
    r, ctx = router
    ctx.last_web_at, ctx.last_files_at = time.monotonic(), 0.0
    d = r.route("play the first video")
    assert d.route == "direct" and d.tool == "screen" and d.args == {"action": "click", "target": "first video"}
    d = r.route("Pick the first one for me. Where it says eat everything in a grocery store.")
    assert d.tool == "screen" and d.args["target"] == "eat everything in a grocery store"
    ctx.last_web_at = 0.0


def test_typing_into_a_named_box(router):
    from mary.router.guards import split_clauses
    parts = split_clauses("type MrBeast into the search box and search")
    assert parts == ["type MrBeast into the search box and search"]
    d = router[0].route(parts[0])
    assert d.tool == "screen" and d.args == {"action": "type", "target": "search box", "text": "MrBeast",
                                              "submit": True}


# ---------------------------------------------------------------- the tool's fallbacks (no real screen)

class FakeInvoke:
    def __init__(self, log):
        self.log = log

    def Invoke(self):
        self.log.append("invoked")


def test_hidden_player_button_is_still_clicked(monkeypatch):
    """YouTube hides its controls while a video plays; "click pause" must still work, without the mouse."""
    import mary.skills.screen_control as sc
    from mary.app_index import AppIndex
    from mary.output import Output
    from mary.tools.registry import ToolContext

    log = []
    pause = Element("button", "Pause (k)", (40, 908, 90, 959), com=object(), hidden=True)
    monkeypatch.setattr(sc, "elements", lambda hwnd, hidden=False: [pause] if hidden else YOUTUBE[:2])
    monkeypatch.setattr(sc, "ocr_elements", lambda hwnd: [])
    monkeypatch.setattr(sc, "_window", lambda target: type("W", (), {"hwnd": 1, "title": "Video - YouTube",
                                                                     "exe": "msedge.exe"})())
    monkeypatch.setattr(sc, "click", lambda el: log.append(el.name) or "invoke")
    monkeypatch.setattr(sc.winutil, "window_title", lambda hwnd: "Video - YouTube")
    cfg = load_config()
    tool = sc.tools(ToolContext(cfg, AppIndex(cfg), Output()))[0]
    result = tool.fn(action="click", target="pause")
    assert result.ok and log == ["Pause (k)"]


def test_hidden_element_without_an_action_is_not_mouse_clicked():
    from mary.skills.screen_control import click
    with pytest.raises(RuntimeError, match="hidden"):
        click(Element("button", "Pause (k)", (0, 0, 10, 10), com=None, hidden=True))


def test_role_words_cover_explorer_buttons():
    share = Element("app bar button", "Share", (0, 0, 10, 10))
    assert find([Element("tree item", "Share drive", (0, 20, 10, 30)), share], "the share button")[0] is share
