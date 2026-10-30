"""Preferences memory: profile.yaml, the memory tool's slots and its safety rules."""
import yaml

from mary.profile import Profile
from mary.router.slots import SlotFiller
from mary.tools.safety import ungrounded_reason


def test_remember_saves_and_dedupes(tmp_path):
    path = tmp_path / "profile.yaml"
    p = Profile(path)
    assert p.remember("I prefer Microsoft Edge") == "saved"
    assert p.remember("i prefer microsoft edge.") == "already"
    assert p.remember("my sister's name is Ama") == "saved"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["facts"] == ["I prefer Microsoft Edge", "My sister's name is Ama"]
    assert Profile(path).facts == data["facts"]  # read back on the next start


def test_forget_needs_a_shared_word(tmp_path):
    p = Profile(tmp_path / "profile.yaml")
    p.remember("I like it when music is quiet")
    p.remember("My car is a Toyota")
    assert p.forget("it") is None          # "forget it" must never wipe a memory
    assert p.forget("the weather") is None
    assert p.forget("my car") == "My car is a Toyota"
    assert p.facts == ["I like it when music is quiet"]


def test_prompt_section_and_listeners(tmp_path):
    p = Profile(tmp_path / "profile.yaml")
    assert p.prompt_section() == ""
    calls = []
    p.listeners.append(lambda: calls.append(1))
    p.set_name("sam")
    p.remember("I prefer Edge")
    section = p.prompt_section()
    assert "- Call me Sam" in section and "- I prefer Edge" in section
    assert len(calls) == 2


def _slots():
    return SlotFiller.__new__(SlotFiller)  # the memory extractors need neither config nor apps


def test_remember_slot_keeps_capitals():
    s = _slots()
    assert s.remember("Remember that I prefer Microsoft Edge", []).args == {"action": "remember",
                                                                           "fact": "I prefer Microsoft Edge"}
    assert s.remember("please remember: the spare key is in the drawer", []).args["fact"] == \
        "the spare key is in the drawer"
    assert not s.remember("remember", []).ok


def test_forget_slot_ignores_never_mind():
    s = _slots()
    assert s.forget("forget what I said about my car", []).args == {"action": "forget", "fact": "my car"}
    assert s.forget("Forget that I prefer Edge", []).args["fact"] == "I prefer Edge"
    for text in ("forget it", "forget about it", "forget that", "forget everything"):
        assert not s.forget(text, []).ok, text


def test_name_slot():
    s = _slots()
    assert s.name("my name is sam", []).args == {"action": "set_name", "fact": "sam"}
    assert s.name("you can call me Ama Serwaa", []).args["fact"] == "Ama Serwaa"
    assert not s.name("what's my name", []).ok


def test_forgetting_needs_the_users_words():
    assert ungrounded_reason("memory", {"action": "forget", "fact": "my car"}, "never mind") is not None
    assert ungrounded_reason("memory", {"action": "forget", "fact": "my car"}, "forget my car") is None
    assert ungrounded_reason("memory", {"action": "remember", "fact": "x"}, "hello") is not None
    assert ungrounded_reason("memory", {"action": "remember", "fact": "x"}, "remember x") is None
