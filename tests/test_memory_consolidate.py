"""The background pass: when it runs, what the model is shown, and what is allowed to become a fact. A fake brain only."""
import time
from datetime import datetime, timedelta

import pytest

from mary.brain import BrainOffline
from mary.memory import clock
from mary.memory.consolidate import Consolidator, grounded, said
from mary.memory.embed import HashEmbedder
from mary.memory.recall import lines
from mary.memory.store import MemoryStore

MONDAY = datetime(2026, 11, 2, 9, 0)


class FakeBrain:
    """Plays scripted JSON answers and keeps what it was asked."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def json(self, messages, schema, max_tokens=1200):
        self.asked.append((messages, schema))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def world(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b",
                        settings={"consolidate": {"enabled": True, "min_lines": 3, "reflections": False}})
    yield store
    store.close()
    clock.reset()


def said_line(store, text, reply="Okay.", at=MONDAY, used="", **kw):
    body = f'Asked: "{text}".' + (f" Used: {used}." if used else "") + (f' Replied: "{reply}".' if reply else "")
    return store.add("diary", body, happened=at, importance=0.4, source="diary", **kw)


def fill(store, *texts, at=MONDAY):
    return [said_line(store, t, at=at + timedelta(minutes=i)) for i, t in enumerate(texts)]


def fact(text, source, importance=7, replaces=None):
    return {"fact": text, "source": source, "importance": importance, "replaces": replaces}


def test_what_the_person_said_is_the_asked_part(world):
    m = said_line(world, "my sister is Ama", reply="Nice.")
    assert said(m) == "my sister is Ama"
    hand = world.add("diary", "Went for a long walk", source="diary")
    assert said(hand) == "Went for a long walk"      # a line they wrote by hand is all theirs


def test_it_waits_for_enough_new_lines_for_quiet_and_for_the_switch(world):
    brain = FakeBrain()
    c = Consolidator(world, brain)
    fill(world, "one", "two")
    assert not c.due()
    fill(world, "three")
    assert c.due()
    quiet = {"on": False}
    assert not Consolidator(world, brain, idle=lambda: quiet["on"]).due()    # someone is using the PC
    world.settings["consolidate"]["enabled"] = False
    assert not c.due()                                                        # and it is off unless asked for


def test_one_old_line_is_enough_after_a_day(world):
    c = Consolidator(world, FakeBrain())
    said_line(world, "my dog is called Biscuit", at=clock.now() - timedelta(days=2))
    assert c.due()


def test_the_model_sees_only_what_the_person_said(world):
    said_line(world, "my sister's name is Ama", reply="Lovely name, Ama it is.", used="memory")
    said_line(world, "what's the weather in Porto", reply="Sunny and 21 degrees.", used="check_weather")
    said_line(world, "read my messages from Lena", used="messages (private; reply not kept)")
    world.add("diary", 'Asked: "text Lena later". Used: messages (private; reply not kept).', source="diary")
    brain = FakeBrain({"facts": []})
    Consolidator(world, brain).run_once()
    messages, schema = brain.asked[0]
    prompt = " ".join(m["content"] for m in messages)
    assert "my sister's name is Ama" in prompt and "weather in Porto" in prompt
    assert "Lovely name" not in prompt and "Sunny and 21" not in prompt       # never her replies
    assert "Used:" not in prompt and "check_weather" not in prompt            # nor what she used
    assert "Lena" not in prompt                                               # nor a line made with a private tool
    assert schema["required"] == ["facts"]


def test_a_stated_fact_is_learned_with_its_source_and_logged(world):
    a, b, c = fill(world, "my sister's name is Ama", "play some jazz", "set a timer for ten minutes")
    brain = FakeBrain({"facts": [fact("My sister's name is Ama", a.id, 8)]})
    report = Consolidator(world, brain).run_once()
    assert report["learned"] == 1 and report["skipped"] == 0
    learned = world.list(kinds=("fact",))[0]
    assert learned.text == "My sister's name is Ama" and learned.source == f"diary:{a.id}"
    assert learned.importance == 0.8 and learned.happened == a.happened
    logged = [m for m in world.list(kinds=("diary",), limit=50) if m.source == "memory"]
    assert [m.text for m in logged] == ['Memory: learned "My sister\'s name is Ama".']


def test_a_made_up_fact_is_refused(world):
    a, b, c = fill(world, "my sister's name is Ama", "play some jazz", "what time is it")
    brain = FakeBrain({"facts": [
        fact("My brother is called Kofi", a.id),                 # a name that was never said
        fact("I love jazz music a great deal", b.id),            # words the line doesn't hold
        fact("My sister's name is Ama", 9999),                    # from a line it was never shown
        fact("My sister's name is Ama", a.id, importance=2),      # trivia
        fact("", a.id), fact("x" * 300, a.id),
        {"fact": "My sister's name is Ama", "source": a.id, "importance": "high"}]})
    report = Consolidator(world, brain).run_once()
    assert report["learned"] == 0 and report["skipped"] == 7 and world.count(("fact",)) == 0


def test_grounding_judges_words_and_names():
    assert grounded("My sister's name is Ama", "my sister's name is Ama")
    assert grounded("I live in Porto", "I just moved to Porto")           # the same place, put the person's way
    assert not grounded("I live in Faro", "I just moved to Porto")        # a name that isn't there
    assert not grounded("Marcus works at the bank", "my friend works at the bank")
    assert grounded("Marcus is my friend", "Marcus is my friend from university")
    assert not grounded("", "anything")


def test_a_fact_already_known_is_merged_not_added(world):
    known, _ = world.add_fact("My sister's name is Ama")
    a, b, c = fill(world, "my sister's name is Ama", "one", "two")
    brain = FakeBrain({"facts": [fact("My sister's name is Ama", a.id, 9)]})
    report = Consolidator(world, brain).run_once()
    assert report["merged"] == 1 and report["learned"] == 0 and world.count(("fact",)) == 1
    assert world.get(known.id).importance == 0.9                       # kept at the higher


def test_a_changed_fact_ends_the_old_one_the_model_named(world):
    old, _ = world.add_fact("I live in Lisbon")
    a, b, c = fill(world, "I just moved to Porto", "one", "two")
    brain = FakeBrain({"facts": [fact("I live in Porto", a.id, 8, replaces=old.id)]})
    report = Consolidator(world, brain).run_once()
    assert report["learned"] == 1 and report["ended"] == 1
    assert not world.get(old.id).current and world.get(old.id).ended_why == "superseded"
    assert [m.text for m in world.list(kinds=("fact",))] == ["I live in Porto"]
    assert 'replaces "I live in Lisbon"' in [m.text for m in world.list(kinds=("diary",), limit=50)
                                              if m.source == "memory"][0]
    # the model can't end a diary line, a fact already ended, or one that doesn't exist
    other, _ = world.add_fact("My dentist is Dr. Haddad")
    d, e, f = fill(world, "my doctor is Dr. Weiss", "three", "four", at=MONDAY + timedelta(hours=2))
    for target in (e.id, old.id, 9999):
        brain = FakeBrain({"facts": [fact("My doctor is Dr. Weiss", d.id, 7, replaces=target)]})
        Consolidator(world, brain).run_once()
        world.db.set_meta("consolidated_upto", "0")   # read the same lines again
    assert world.get(other.id).current and world.get(e.id).current
    assert world.count(("fact",)) == 3          # Porto, the dentist, and one Weiss (the repeats were merged)


def test_lines_are_read_once_and_the_models_own_log_lines_are_not_read_at_all(world):
    fill(world, "my sister's name is Ama", "one", "two")
    brain = FakeBrain({"facts": []})
    c = Consolidator(world, brain)
    c.run_once()
    assert not c.pending() and not c.due() and len(brain.asked) == 1
    world.add("diary", 'Memory: learned "x".', source="memory")
    assert not c.pending()
    fill(world, "three", "four", "five", at=MONDAY + timedelta(hours=1))
    assert len(c.pending()) == 3


def test_a_failed_pass_changes_nothing_and_tries_again_later(world):
    fill(world, "my sister's name is Ama", "one", "two")
    c = Consolidator(world, FakeBrain(BrainOffline("the model is away")))
    report = c.run_once()
    assert report["failed"] and world.count(("fact",)) == 0 and len(c.pending()) == 3
    assert not c.due()                                                 # not again for half an hour
    c._retry_at = 0
    assert c.due()


def test_monday_tells_it_tuesday_asks_for_it(world):
    clock.reset()
    a, b, c = fill(world, "remind me my meeting with the bank is at 3:30 on Thursday", "play some jazz", "what time is it",
                   at=clock.now() - timedelta(days=1))
    brain = FakeBrain({"facts": [fact("My meeting with the bank is on Thursday at 3:30", a.id, 8)]})
    Consolidator(world, brain).run_once()
    got = lines(world, "when is my meeting with the bank")
    assert got and "3:30" in got[0] and got[0].startswith("remembered #")
    assert lines(world, "tell me a joke") == []


def test_a_day_summary_needs_enough_to_say_and_nothing_private(world):
    world.settings["consolidate"]["reflections"] = True
    yesterday = clock.now().replace(hour=9, minute=0) - timedelta(days=1)
    fill(world, "open Spotify", "play some jazz", "set a timer for ten minutes", at=yesterday)
    two_days = yesterday - timedelta(days=1)
    fill(world, "open Word", "open Excel", "write a note", at=two_days)
    said_line(world, "read my messages", used="messages (private; reply not kept)", at=two_days + timedelta(hours=1))
    today = clock.now().replace(hour=8, minute=0)
    fill(world, "a", "b", "c", at=today - timedelta(hours=1) if today.hour > 1 else today)
    brain = FakeBrain({"facts": []}, {"summary": "A quiet day of music and a timer."})
    report = Consolidator(world, brain).run_once()
    assert report["reflections"] == 1
    r = world.list(kinds=("reflection",))
    assert [m.text for m in r] == ["A quiet day of music and a timer."]
    assert r[0].happened.date() == yesterday.date() and r[0].happened.hour == 23
    assert len(brain.asked) == 2          # the private day and today got no summary
    brain.answers.append({"facts": []})
    Consolidator(world, brain).run_once()
    assert world.count(("reflection",)) == 1


def test_tick_starts_one_pass_on_its_own_thread_and_never_two(world):
    fill(world, "my sister's name is Ama", "one", "two")
    gate = {"open": False}

    class Slow(FakeBrain):
        def json(self, *a, **k):
            while not gate["open"]:
                time.sleep(0.01)
            return super().json(*a, **k)

    brain = Slow({"facts": []})
    c = Consolidator(world, brain)
    c.tick()
    c.tick()
    time.sleep(0.1)
    assert c._thread.is_alive() and not brain.asked
    gate["open"] = True
    c._thread.join(timeout=5)
    assert len(brain.asked) == 1
