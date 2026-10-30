"""Conversation polish: filler filtering, "no"/"thanks"/yes-to-an-offer, honest weather readings,
router hints, and the startup greeting. Everything here runs without the LLM or a browser."""
import time
from types import SimpleNamespace

import pytest

from mary.config import load_config
from mary.conversation import classify, offer_in
from mary.llm.replies import CLARIFY, ReplyFilter
from mary.output import Output
from mary.skills.weather import Weather, parse, spoken
from tests.conftest import needs_router


def _filtered(text: str) -> tuple[str, ReplyFilter]:
    f = ReplyFilter()
    out = "".join(f.feed(ch) for ch in text) + f.flush()  # streamed one character at a time
    return out.strip(), f


@pytest.mark.parametrize("reply, kept", [
    ("I found three PDFs. Would you like me to do anything else with this information?", "I found three PDFs."),
    ("I see. Let me know if there's anything else I can assist you with.", "I see."),
    ("Opening Spotify. Is there anything else?", "Opening Spotify."),
    ("Would you like me to close Notepad?", "Would you like me to close Notepad?"),  # a real question stays
    ("It's 28 degrees. Feel free to ask for the forecast.", "It's 28 degrees."),
])
def test_filler_sentences_are_dropped(reply, kept):
    assert _filtered(reply)[0] == kept


def test_filler_only_reply_is_detected():
    out, f = _filtered("I'm here to help with anything you need. How can I assist you today?")
    assert out == "" and f.only_filler


@pytest.mark.parametrize("text, pending, reply, end, accept", [
    ("No.", False, "Okay.", True, False),
    ("no thanks", True, "Okay.", True, False),
    ("Yes.", True, None, False, True),
    ("okay", True, None, False, True),        # "okay" to "Shall I…?" means yes
    ("Okay.", False, None, True, False),      # otherwise it just ends the conversation quietly
    ("thank you Mary", False, "You're welcome.", True, False),
    ("that's all", False, "Okay.", True, False),
    ("bye", False, "Bye, Sam.", True, False),
])
def test_conversation_acts(text, pending, reply, end, accept):
    act = classify(text, pending, "Sam")
    assert (act.reply, act.end, act.accept) == (reply, end, accept)


@pytest.mark.parametrize("text", ["Yes.", "No, open Spotify instead", "stop", "what's the time"])
def test_real_requests_are_not_conversation_acts(text):
    assert classify(text, offer_pending=False) is None


def test_yes_without_a_question_just_acknowledges():
    assert classify("Yes.", asked=False) == classify("yeah", asked=False) == classify("Okay.")
    assert classify("Yes.", asked=True) is None  # an answer to "Which app?": the model handles it


def test_offer_detection():
    assert offer_in("I can't see that. Would you like me to search the web for it?") == \
        "Would you like me to search the web for it?"
    assert offer_in("Opening Spotify.") is None


# ---------------------------------------------------------------- core: no LLM for these turns

class Said(Output):
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


@pytest.fixture(scope="module")
def mary():
    from mary.config import Section
    from mary.core import Mary
    cfg = load_config()
    cfg["brain"] = Section.wrap({**(cfg.get("brain") or {}), "mode": "today"})  # these test the router path
    return needs_router(lambda: Mary(cfg, Said()))


def test_no_ends_the_conversation_without_the_llm(mary, monkeypatch):
    monkeypatch.setattr(mary.agent, "run", lambda *a, **k: pytest.fail("the LLM was called"))
    mary.handle("No.", prefill=False)
    assert mary.output.said[-1] == "Okay." and mary.end_conversation


def test_yes_carries_out_marys_offer(mary, monkeypatch):
    asked = []
    monkeypatch.setattr(mary.agent, "run", lambda text, **k: asked.append(text) or "Done.")
    mary.offer = ("Would you like me to search the web for the weather?", time.monotonic())
    mary.handle("Yes.", prefill=False)
    assert asked == ["Yes, go ahead: Would you like me to search the web for the weather?"]
    assert mary.offer is None and not mary.end_conversation


def test_an_offer_is_remembered_from_the_reply(mary, monkeypatch):
    monkeypatch.setattr(mary.agent, "run", lambda text, **k: "I can't see that. Shall I read the screen?")
    mary.handle("tell me a long story about dragons", prefill=False)
    assert mary.offer[0] == "Shall I read the screen?"


def test_filler_only_answer_becomes_a_clarifying_question(mary, monkeypatch):
    chunks = [SimpleNamespace(message=SimpleNamespace(content=c, thinking=None, tool_calls=None), done=False)
              for c in ["I'm here to help. ", "How can I ", "assist you today?"]]
    chunks.append(SimpleNamespace(message=SimpleNamespace(content="", thinking=None, tool_calls=None), done=True,
                                  prompt_eval_duration=0, eval_count=3, eval_duration=0))
    monkeypatch.setattr(mary.agent.brain, "client", SimpleNamespace(chat=lambda **k: iter(chunks)))
    assert mary.agent.run("it's the current grader") == CLARIFY


def test_greeting_uses_the_name(mary):
    mary.profile.name = "Sam"
    assert ", Sam. I'm ready when you are." in mary.greeting()


# ---------------------------------------------------------------- weather, from real OCR output

@pytest.mark.parametrize("ocr, temp, feels, humidity", [
    ("Greater London, GB: Patchy rain nearby, +280 C, feels +32 0 C, humidity 75%", 28, 32, 75),
    ("London: Patchy rain nearby, +15 oc, feels +12 oc, humi dity 94%", 15, 12, 94),
    ("Paris: Light rain shower, +28°C, feels +32 •C, humidity 70%", 28, 32, 70),
    ("Oslo: Light snow, -50 C, feels -90 C, humidity 88%", -5, -9, 88),
])
def test_weather_parse(ocr, temp, feels, humidity):
    w = parse("C https://wttr.in/?format=...\n" + ocr)  # the address bar line is ignored
    assert (w.temp, w.feels, w.humidity) == (temp, feels, humidity)


def test_weather_misread_is_not_reported():
    assert parse("Lisbon: Sunny, +2800 C, feels +32 0 C, humidity 75%") is None  # 280° is not a reading
    assert parse("Tabs Online video converter | Settings") is None


def test_weather_spoken():
    w = Weather("Greater London, GB", "Patchy rain nearby", 28, 32, 75)
    assert spoken(w) == "It's 28 degrees, feeling like 32. Patchy rain nearby."
    assert spoken(w, "paris").startswith("In Paris it's 28 degrees")


# ---------------------------------------------------------------- router

@pytest.fixture(scope="module")
def router():
    from mary.app_index import AppIndex
    from mary.router.router import Router
    cfg = load_config()
    return needs_router(lambda: Router(cfg, AppIndex(cfg)))


def test_weather_routes_directly(router):
    d = router.route("what is the weather in Paris tomorrow?")
    assert d.route == "direct" and d.args == {"when": "forecast", "place": "Paris"}
    assert router.route("I mean what is the current weather.").args == {"when": "now"}


@pytest.mark.parametrize("text", ["Yes.", "No.", "What did you find?"])
def test_weak_guesses_are_not_hinted(router, text):
    d = router.route(text)
    assert d.route == "llm" and d.hint is None


def test_useful_hints_stay(router):
    assert router.route("put on Jim Reeves").hint.startswith("maybe play_music")


# ---------------------------------------------------------------- "what did you find?" after a web search

@pytest.mark.parametrize("text, about", [
    ("What did you find?", True), ("so what does it say", True), ("read it to me", True), ("any results?", True),
    ("what did you find in my downloads folder", False), ("what does it say about prices", False),
])
def test_asks_about_page(text, about):
    from mary.conversation import asks_about_page
    assert asks_about_page(text) is about


def test_page_question_reads_the_screen_first(mary, monkeypatch):
    calls = []
    monkeypatch.setattr(mary.agent, "run", lambda text, **k: calls.append(k.get("prefetch")) or "Three laptops.")
    mary.ctx.last_web_at, mary.ctx.last_files_at = time.monotonic(), 0.0
    mary.handle("What did you find?", prefill=False)
    assert calls == [("read_screen", {})]
    mary.ctx.last_files_at = time.monotonic()  # a file search since: the question is about the files
    mary.handle("What did you find?", prefill=False)
    assert calls[-1] is None


def test_one_request_in_two_clauses_runs_once(mary, monkeypatch):
    ran = []
    monkeypatch.setattr(mary, "_run_direct", lambda d, latency: ran.append((d.tool, d.args)))
    mary.handle("it's too loud in here, bring the sound down", prefill=False)
    assert len(ran) == 1


def test_so_should_i_is_a_question(router):
    assert router.route("so should I take an umbrella?").route == "llm"


def test_send_it_stays_with_typing():
    from mary.router.guards import split_clauses
    assert split_clauses("type see you at five into the message box and send it") == \
        ["type see you at five into the message box and send it"]


def test_greeting_and_context_recital_count_as_not_understood():
    out, f = _filtered("Hello, Sam. I see it is 18:41 on Monday, October 26th, with volume at full. "
                       "Is there anything you need help with today?")
    assert out == "" and f.only_filler


def test_reply_after_only_refused_actions_asks_again(mary, monkeypatch):
    """Misheard input: the model tries an unasked action, it's refused, and its "Got it." is never spoken."""
    from types import SimpleNamespace as NS

    def chunk(content="", calls=None, done=False):
        return NS(message=NS(content=content, thinking=None, tool_calls=calls), done=done,
                  prompt_eval_duration=0, eval_count=1, eval_duration=0)

    set_name = NS(function=NS(name="memory", arguments={"action": "set_name", "fact": "Sam"}))
    replies = iter([[chunk(calls=[set_name]), chunk(done=True)], [chunk("Got it, Sam."), chunk(done=True)]])
    monkeypatch.setattr(mary.agent.brain, "client", NS(chat=lambda **k: iter(next(replies))))
    mary.output.said.clear()
    spoken = []
    monkeypatch.setattr(mary.output, "reply_delta", lambda text: spoken.append(text), raising=False)
    assert mary.agent.run("It's the current grader.") == CLARIFY
    assert "Got it" not in "".join(spoken) and mary.output.said[-1] == CLARIFY


@pytest.mark.parametrize("reply, kept", [
    ("Got it, Sam. Monday 26 October 2026 at 19:04 with volume at full.", ""),
    ("The volume is at 40%.", "The volume is at 40%."),  # a real answer about the volume stays
    ("Okay. 627.", "627."),
    ("It's 19:05.", "It's 19:05."),
])
def test_acknowledgements_and_status_recital(reply, kept):
    assert _filtered(reply)[0] == kept
