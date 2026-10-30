"""brain.mode: new — the model hears every request whole and answers in her own words. Model replies are faked."""
from types import SimpleNamespace as NS

import pytest

from mary.config import Section, load_config
from mary.llm.replies import CLARIFY, ReplyFilter
from mary.output import Output
from mary.tools.registry import ToolResult


class Recorder(Output):
    def __init__(self):
        self.said, self.asides, self.replies, self._parts = [], [], [], None

    def say(self, text):
        self.said.append(text)

    def aside(self, text):
        self.asides.append(text)

    def reply_start(self):
        self._parts = []

    def reply_delta(self, text):
        self._parts.append(text)

    def reply_end(self):
        if self._parts:
            self.replies.append("".join(self._parts).strip())
        self._parts = None


def chunk(content="", calls=None, done=False):
    return NS(message=NS(content=content, thinking=None, tool_calls=calls), done=done,
              prompt_eval_count=0, prompt_eval_duration=0, eval_count=1, eval_duration=0)


def call(tool, **args):
    return NS(function=NS(name=tool, arguments=args))


class Script:
    """Fake Ollama client: each chat() call plays the next scripted reply."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def chat(self, **kw):
        self.calls += 1
        return iter(self.replies.pop(0))


@pytest.fixture(scope="module")
def mary():
    from mary.core import Mary

    cfg = load_config()
    cfg["brain"] = Section.wrap({"provider": "ollama", "mode": "new"})
    return Mary(cfg, Recorder())


@pytest.fixture
def fresh(mary, monkeypatch):
    out = mary.output
    out.said.clear(), out.asides.clear(), out.replies.clear()
    mary.agent.reset()
    monkeypatch.setattr(mary.agent, "prefill_in_background", lambda: None)
    return mary


def test_new_mode_has_no_router_and_her_own_prompt(mary):
    assert mary.router is None
    assert "like a mother talking with her grown son" in mary.agent.system["content"]
    assert "check_weather" in mary.agent.system["content"]  # the weather brick's guide line


def test_her_reply_is_spoken_even_after_a_refused_action(fresh, monkeypatch):
    """Asked "I'm not feeling well", the 2B tried to sleep the PC. That's refused, and her next words are hers."""
    monkeypatch.setattr(fresh.agent.brain, "client", Script(
        [chunk(calls=[call("power", action="sleep")]), chunk(done=True)],
        [chunk("Oh, I'm sorry. Rest and drink some water."), chunk(done=True)]))
    fresh.handle("I'm not feeling well. What do you think I should do?", prefill=False)
    assert fresh.output.replies == ["Oh, I'm sorry. Rest and drink some water."]
    assert CLARIFY not in fresh.output.said


def test_after_an_action_she_says_how_it_went_in_her_own_words(fresh, monkeypatch):
    monkeypatch.setattr(fresh.tools.tools["open_app"], "fn",
                        lambda name: ToolResult(True, "Launched Spotify.", say="Opening Spotify."))
    monkeypatch.setattr(fresh.agent.brain, "client", Script(
        [chunk(calls=[call("open_app", name="spotify")]), chunk(done=True)],
        [chunk("There you go, Spotify's open."), chunk(done=True)]))
    fresh.handle("open spotify for me, I want to listen to something", prefill=False)
    assert fresh.output.replies == ["There you go, Spotify's open."]
    assert "Opening Spotify." not in fresh.output.said  # the tool's prepared line isn't used
    assert fresh.output.asides == []                    # opening an app is quick: no waiting line


def test_slow_tools_get_one_waiting_line(fresh, monkeypatch):
    from mary.skills import files

    monkeypatch.setattr(fresh.tools.tools["find_files"], "fn", lambda **a: ToolResult(True, "1 pdf file(s): Invoice.pdf"))
    monkeypatch.setattr(fresh.agent.brain, "client", Script(
        [chunk(calls=[call("find_files", file_type="pdf")]), chunk(done=True)],
        [chunk("Found it: Invoice.pdf."), chunk(done=True)]))
    fresh.handle("find the pdf I downloaded", prefill=False)
    assert len(fresh.output.asides) == 1 and fresh.output.asides[0] in files.SLOW["find_files"]


def test_reflexes_never_ask_the_model(fresh, monkeypatch):
    done = []
    monkeypatch.setattr(fresh.tools.tools["system_control"], "fn",
                        lambda action, value=None: done.append((action, value)) or ToolResult(True, "ok", say="Volume's at 50%."))
    monkeypatch.setattr(fresh.agent.brain, "client", Script())  # any model call would fail: nothing scripted
    fresh.handle("Volume up.", prefill=False)
    assert done == [("volume_up", 10)] and fresh.output.said == ["Volume's at 50%."]
    assert fresh.agent.exchanges  # remembered, so "turn it back down" has context


def test_tool_markup_written_as_text_is_never_spoken():
    f = ReplyFilter(greetings_ok=True)
    out = f.feed('<parameter=context>\n{"time": "Tuesday 27 October 2026, 03:08"}\n</parameter> ')
    out += f.feed("<parameter=action> remember </parameter> I'm not feeling well. </function> </tool_call>")
    out += f.flush()
    assert out == "" and f.markup


def test_greetings_are_allowed_in_the_new_mode_only():
    new, old = ReplyFilter(greetings_ok=True), ReplyFilter()
    assert (new.feed("Hi, Sam! ") + new.flush()).strip() == "Hi, Sam!"
    assert (old.feed("Hi, Sam! ") + old.flush()).strip() == ""
    assert (new.feed("Is there anything else I can help with? ") + new.flush()).strip() == ""  # still a chatbot habit


def test_she_follows_through_when_she_says_she_will(fresh, monkeypatch):
    """The 2B said "Let me capture the screen now." and stopped: told to do it, she does."""
    done = []
    monkeypatch.setattr(fresh.tools.tools["take_screenshot"], "fn",
                        lambda: done.append(1) or ToolResult(True, "Saved screenshot to Pictures/MARY/shot.png."))
    script = Script([chunk("Let me capture the screen now."), chunk(done=True)],
                    [chunk(calls=[call("take_screenshot")]), chunk(done=True)],
                    [chunk("Done, it's in your Pictures."), chunk(done=True)])
    monkeypatch.setattr(fresh.agent.brain, "client", script)
    fresh.handle("take a screenshot", prefill=False)
    assert done == [1] and fresh.output.replies[-1] == "Done, it's in your Pictures."
    assert not any(m.get("content") == "Do it now, with the tool." for ex in fresh.agent.exchanges for m in ex)


def test_an_unreadable_tool_call_is_retried_once(fresh, monkeypatch):
    import ollama

    class Garbled(Script):
        def chat(self, **kw):
            self.calls += 1
            if self.calls == 1:
                def broken():
                    raise ollama.ResponseError("error parsing tool call", 500)
                    yield
                return broken()
            return iter(self.replies.pop(0))

    script = Garbled([chunk("Good evening, Sam."), chunk(done=True)])
    script.calls = 0
    monkeypatch.setattr(fresh.agent.brain, "client", script)
    fresh.handle("good evening mom", prefill=False)
    assert script.calls == 2 and fresh.output.replies == ["Good evening, Sam."]
    assert fresh.agent.offline_reason is None  # a garbled reply isn't a dead brain


def test_stray_think_tags_are_never_spoken():
    f = ReplyFilter(greetings_ok=True)
    assert (f.feed("You're not feeling well. </think> ") + f.feed("Rest a little.") + f.flush()).strip() == \
        "You're not feeling well. Rest a little."


@pytest.mark.parametrize("by_default", [True, False])
def test_think_by_default_switches_thinking_on_every_request(fresh, monkeypatch, by_default):
    seen = []
    script = Script([chunk("Morning, Sam."), chunk(done=True)])
    play = script.chat
    script.chat = lambda **kw: seen.append(kw["think"]) or play(**kw)
    monkeypatch.setattr(fresh.agent.brain, "client", script)
    monkeypatch.setattr(fresh.agent, "think_default", by_default)
    fresh.handle("Good morning.", prefill=False)
    assert seen == [by_default]
