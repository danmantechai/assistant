"""Any wake phrase: what training says and records for it (mary/wakeword_training/phrases.py), how MARY confirms and
strips it (mary/persona.py Wake), and the progress file training writes for the settings page."""
import json

import pytest

from mary.persona import Persona, Wake
from mary.wakeword_training.phrases import slug, spec, split


def test_the_two_original_phrases_keep_their_hand_picked_lists():
    mom = spec("mom")
    assert mom["name"] == "Hey Mom" and mom["file"] == mom["takes"] == "hey_mom"
    assert "I'll call my mum later" in mom["neg"] and spec("Hey Mom") == mom
    assert spec("mary")["pos"][0] == "Hey Mary"


def test_any_other_phrase_gets_a_generated_set():
    s = spec("hey grandma!")
    assert s["name"] == "Hey Grandma" and s["file"] == "hey_grandma"
    assert s["pos"] == ["Hey Grandma", "Hey, Grandma.", "Hey Grandma!", "hey Grandma?"]
    assert "Grandma" in s["neg"] and "Hey Tom" in s["neg"] and "Hey Grandma" not in s["neg"]
    assert "Grandma" in s["near"] and sum(n for _, n in s["styles"]) == 32
    assert "Hey... Grandma" in s["styles"][4][0]


def test_a_name_alone_phrase_never_learns_its_own_name_as_a_sound_alike():
    s = spec("Alexa")
    assert s["pos"][0] == "Alexa" and "Alexa" not in s["neg"] and "Alexa" not in s["near"]
    assert split("Okay computer") == ("Okay", "Computer") and split("Alexa") == ("", "Alexa")
    assert slug("Hey, Big Mama!") == "hey_big_mama"
    with pytest.raises(ValueError):
        spec("hey")  # a greeting with no name


def test_a_phrase_without_a_greeting_is_confirmed_and_stripped_by_its_name():
    her = Persona("t", None, wake=[Wake("Alexa", "alexa"), Wake("Hey Grandma", "hey_grandma")])
    assert her.wake[0].confirm_pattern().search("alexa")
    assert not her.wake[1].confirm_pattern().search("grandma said so")  # greeted: the name alone isn't enough
    assert her.wake[1].confirm_pattern().search("hey grandma")
    assert her.wake_pattern().sub("", "Alexa, what time is it?") == "what time is it?"
    assert her.wake_pattern().sub("", "Hey Grandma, open Notepad") == "open Notepad"
    assert her.wake_pattern().sub("", "Grandma's birthday is Friday") == "Grandma's birthday is Friday"


def test_nicknames_is_read_under_its_old_name_too(tmp_path, monkeypatch):
    from mary import persona as personas

    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    for key, field in (("old", "user_calls_her"), ("new", "nicknames")):
        folder = tmp_path / "personas" / key
        folder.mkdir(parents=True)
        (folder / "persona.yaml").write_text(f"name: Gran\n{field}: [Gran, Nana]\n", encoding="utf-8")
    personas._load.cache_clear()
    assert personas.load("old").nicknames == personas.load("new").nicknames == ["Gran", "Nana"]
    personas._load.cache_clear()


def test_training_progress_goes_to_a_file(tmp_path):
    from mary.wakeword_training.train import Status

    path = tmp_path / "status.json"
    status = Status(str(path), "Hey Grandma")
    status.span(0.2, 0.4)(5, 10)
    assert json.loads(path.read_text())["progress"] == pytest.approx(0.3)
    status.update(progress=0.302)  # under 1% more: not rewritten
    assert json.loads(path.read_text())["progress"] == pytest.approx(0.3)
    status.update("Done", 1.0, done=True, passed=True)
    data = json.loads(path.read_text())
    assert data["done"] and data["passed"] and data["stage"] == "Done" and data["phrase"] == "Hey Grandma"
    Status(None, "x").update("anything", 0.5)  # no file: nothing written, nothing fails
