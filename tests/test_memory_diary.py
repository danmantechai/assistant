"""The diary and recall wired into MARY: what is written after a request, what is never written, and the lines a
request's context gets from the memory."""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from mary import memory
from mary.config import Section, load_config
from mary.memory import clock
from mary.memory.diary import Did, Diary, cut, line
from mary.memory.embed import HashEmbedder
from mary.memory.store import MemoryStore, clean
from mary.output import Output


# ---------------------------------------------------------------- the line itself


def test_a_line_says_what_was_asked_used_and_replied():
    assert line("open Spotify", [Did("open_app", True)], "Opening Spotify.") == \
        'Asked: "open Spotify". Used: open_app. Replied: "Opening Spotify.".'
    assert line("how are you", [], "Doing well, thanks.") == 'Asked: "how are you". Replied: "Doing well, thanks.".'


def test_tools_are_named_once_and_a_failure_is_said():
    got = line("find my CV", [Did("find_files", True), Did("find_files", False), Did("read_screen", True)], "Not found.")
    assert "Used: find_files (failed), read_screen." in got


def test_a_private_tool_keeps_the_question_and_the_name_but_not_the_reply():
    got = line("read my messages", [Did("messages", True, private=True)], "Lena says she is late.")
    assert got == 'Asked: "read my messages". Used: messages (private; reply not kept).'
    assert "Lena says" not in got


def test_quotes_are_softened_and_long_words_are_cut():
    got = line('say "hi" to everyone', [], 'He said "no".')
    assert "'hi'" in got and "'no'" in got and '""' not in got
    assert len(cut("word " * 100, 60)) <= 61 and cut("One. Two. Three. " * 20, 40).endswith(".")
    long = line("x" * 1000, [], "y" * 1000, request_chars=50, reply_chars=30)
    assert len(long) < 150


def test_user_words_and_reply_are_kept_in_full():
    long_req = "Here is my detailed plan: " + ("word " * 600)
    long_rep = "I have noted your entire plan: " + ("details " * 300)
    got = line(long_req, [], long_rep)
    assert clean(long_req) in got
    assert clean(long_rep) in got


def test_a_request_with_nothing_to_say_has_no_line(tmp_path):
    s = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    d = Diary(s)
    assert d.record("   ", [], "hello") is None and d.record("hello", [], "") is None
    s.close()


# ---------------------------------------------------------------- repeats


@pytest.fixture
def diary(tmp_path):
    s = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    yield Diary(s), s
    s.close()


def test_the_same_request_again_is_one_line_with_a_count(diary):
    d, s = diary
    t = datetime(2026, 11, 3, 9, 0)
    first = d.record("turn the volume up", [Did("set_volume", True)], "Done.", t)
    d.record("Turn the volume up!", [Did("set_volume", True)], "Done.", t + timedelta(minutes=1))
    last = d.record("turn the volume up", [Did("set_volume", True)], "Done.", t + timedelta(minutes=2))
    assert s.count(("diary",)) == 1 and last.id == first.id
    assert "(×3)" in s.get(first.id).text
    d.record("turn the volume up", [Did("set_volume", True)], "Done.", t + timedelta(minutes=30))   # much later
    assert s.count(("diary",)) == 2


def test_a_different_request_or_other_tools_is_a_new_line(diary):
    d, s = diary
    t = datetime(2026, 11, 3, 9, 0)
    d.record("turn the volume up", [Did("set_volume", True)], "Done.", t)
    d.record("turn the volume down", [Did("set_volume", True)], "Done.", t + timedelta(minutes=1))
    d.record("turn the volume down", [Did("media", True)], "Done.", t + timedelta(minutes=2))
    assert s.count(("diary",)) == 3


# ---------------------------------------------------------------- inside MARY


class Recorder(Output):
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


def chunk(content="", calls=None, done=False):
    return NS(message=NS(content=content, thinking=None, tool_calls=calls), done=done,
              prompt_eval_count=0, prompt_eval_duration=0, eval_count=1, eval_duration=0)


def call(tool, **args):
    return NS(function=NS(name=tool, arguments=args))


class Script:
    """A fake Ollama: each chat() plays the next scripted reply, and keeps the messages it was sent."""

    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def chat(self, **kw):
        self.sent.append(kw["messages"])
        return iter(self.replies.pop(0))


@pytest.fixture(scope="module")
def mary(tmp_path_factory):
    from mary.core import Mary

    patch = pytest.MonkeyPatch()
    patch.setattr(memory, "ENABLED", True)
    patch.setattr(memory, "FOLDER", tmp_path_factory.mktemp("mary-memory"))
    patch.setenv("MARY_USER_DIR", str(tmp_path_factory.mktemp("user")))
    cfg = load_config()
    cfg["brain"] = Section.wrap({"provider": "ollama", "mode": "new"})
    m = Mary(cfg, Recorder())
    m.memory.embedder = HashEmbedder()
    yield m
    m.memory.close()
    patch.undo()
    clock.reset()


@pytest.fixture
def fresh(mary, monkeypatch):
    mary.agent.reset()
    monkeypatch.setattr(mary.agent, "prefill_in_background", lambda: None)
    return mary


def diary_lines(mary):
    mary.memory.flush()
    return [m.text for m in mary.memory.list(kinds=("diary",), current=True, limit=1000, newest_first=False)]


def test_a_request_with_a_tool_leaves_one_line_and_never_the_tools_result(fresh, monkeypatch):
    monkeypatch.setattr(fresh.agent.brain, "client", Script(
        [chunk(calls=[call("calculate", expression="15*23")], done=True)], [chunk("That's 345.", done=True)]))
    before = len(diary_lines(fresh))
    fresh.handle("what is fifteen times twenty three")
    lines = diary_lines(fresh)
    assert len(lines) == before + 1
    assert lines[-1].startswith('Asked: "what is fifteen times twenty three". Used: calculate.')
    assert "That's 345" in lines[-1] and "15*23 =" not in lines[-1]


def test_quick_replies_and_failed_requests_leave_nothing(fresh, monkeypatch):
    before = len(diary_lines(fresh))
    fresh.handle("thanks")
    from mary.llm.agent import LLMOffline

    def offline(*a, **k):
        raise LLMOffline("no model")

    monkeypatch.setattr(fresh.agent, "run", offline)
    fresh.handle("what is the capital of France")
    assert len(diary_lines(fresh)) == before


def test_a_private_tool_keeps_its_reply_out_of_the_diary(fresh, monkeypatch):
    monkeypatch.setattr(fresh.agent.brain, "client", Script(
        [chunk(calls=[call("memory", action="list")], done=True)], [chunk("You have nothing saved yet.", done=True)]))
    fresh.handle("what do you remember about me")
    last = diary_lines(fresh)[-1]
    assert "Used: memory (private; reply not kept)." in last and "nothing saved" not in last


def test_the_diary_is_the_users_words_so_a_yes_records_what_was_agreed(fresh, monkeypatch):
    monkeypatch.setattr(fresh.agent.brain, "client", Script([chunk("Okay, done.", done=True)]))
    fresh.offer = ("Shall I open Notepad?", __import__("time").monotonic())
    fresh.handle("yes")
    assert "Yes, go ahead: Shall I open Notepad?" in diary_lines(fresh)[-1]


def test_what_is_known_about_a_name_comes_along_with_the_request(fresh, monkeypatch):
    fresh.memory.add("fact", "Marcus is my friend from university", importance=0.8)
    fresh.memory.flush()
    script = Script([chunk("Okay, I'll tell him.", done=True)])
    monkeypatch.setattr(fresh.agent.brain, "client", script)
    fresh.handle("text Marcus that I'm running late")
    sent = script.sent[0][-1]["content"]
    assert "<context>" in sent and "remembered #" in sent and "Marcus is my friend from university" in sent
    assert sent.rstrip().endswith("text Marcus that I'm running late")   # the request itself comes last


def test_an_ordinary_request_gets_no_memory_lines(fresh, monkeypatch):
    script = Script([chunk("Why did the chicken cross the road?", done=True)])
    monkeypatch.setattr(fresh.agent.brain, "client", script)
    fresh.handle("tell me a joke")
    assert "remembered #" not in script.sent[0][-1]["content"]


def test_a_broken_memory_never_breaks_a_request(fresh, monkeypatch):
    monkeypatch.setattr(fresh.memory, "search", lambda *a, **k: 1 / 0)
    script = Script([chunk("Hello there.", done=True)])
    monkeypatch.setattr(fresh.agent.brain, "client", script)
    fresh.handle("what did I say about Marcus last week")
    assert fresh.output.said or script.sent   # she still answered
    assert "remembered #" not in script.sent[0][-1]["content"]


def test_memory_switched_off_leaves_mary_as_she_was(tmp_path, monkeypatch):
    from mary.core import Mary

    monkeypatch.setattr(memory, "ENABLED", False)
    m = Mary(load_config(), Recorder())
    assert m.memory is None and m.diary is None and m.agent.memory is None
