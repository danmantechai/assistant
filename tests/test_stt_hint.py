"""The app names in Whisper's context prompt (mary/audio/stt.py), without loading Whisper."""
from types import SimpleNamespace

import pytest

from mary.audio.stt import PROMPT_TOKENS, SpeechToText, spoken_app_name

BASE = "Hey Mary, open Notepad. Play Spotify. Check WhatsApp. Type this text."


class Words:
    """A stand-in tokenizer: one token per word or comma."""

    def encode(self, text, add_special_tokens=True):
        return SimpleNamespace(ids=text.replace(",", " ,").split())


def make(apps, app_names=True, words=()) -> SpeechToText:
    s = SpeechToText.__new__(SpeechToText)
    s.prompt, s.model = BASE, SimpleNamespace(hf_tokenizer=Words())
    s.apps = SimpleNamespace(apps=[SimpleNamespace(name=n, exe=e) for n, e in apps]) if app_names else None
    s._installed, s.app_names, s.words = (-1, []), set(), list(words)
    return s


@pytest.fixture
def windows(monkeypatch):
    shown = []
    monkeypatch.setattr("mary.winutil.list_windows", lambda: shown)
    monkeypatch.setattr("mary.winutil.is_own_console", lambda hwnd: hwnd == 0)
    return shown


@pytest.mark.parametrize("name, said", [
    ("Microsoft Visual Studio Code (User)", "Visual Studio Code"),
    ("Microsoft Edge", "Edge"),
    ("Python 3.12.4 (64-bit)", "Python"),
    ("Claude", "Claude"),
])
def test_spoken_app_name(name, said):
    assert spoken_app_name(name) == said


def test_hint_puts_open_apps_last_and_ends_with_the_fixed_sentence(windows):
    s = make([("Claude", "claude.exe"), ("Calculator", None), ("Paint", None)])
    windows += [SimpleNamespace(hwnd=1, exe="claude.exe"), SimpleNamespace(hwnd=0, exe="windowsterminal.exe")]
    assert s.hint() == "Calculator, Paint, Claude. " + BASE  # MARY's own console isn't listed


def test_open_app_missing_from_the_index_uses_its_exe(windows):
    s = make([("Paint", None)])
    windows.append(SimpleNamespace(hwnd=1, exe="WhatsApp.exe"))
    assert s.hint().startswith("Paint, WhatsApp. ")


def test_hint_fits_what_whisper_keeps_and_drops_the_longest_names_first(windows):
    s = make([(f"App{i}", None) for i in range(300)] + [("A Very Long Application Name Here", None)])
    hint = s.hint()
    assert len(Words().encode(" " + hint).ids) <= PROMPT_TOKENS
    assert "Long Application" not in hint and hint.endswith(BASE)


def test_switched_off_keeps_the_fixed_sentence(windows):
    assert make([("Claude", None)], app_names=False).hint() == BASE


def test_a_list_of_app_names_alone_is_whisper_reading_its_prompt(windows):
    s = make([("Claude", None), ("Paint", None), ("Calculator", None)])
    s.hint()
    assert s.reads_app_list("Claude, Paint, Calculator.")
    assert not s.reads_app_list("Open Claude, Paint and Calculator.")
    assert not s.reads_app_list("Claude.")


def test_his_words_go_in_even_with_app_names_off(windows):
    assert make([], app_names=False, words=["Bolt Food"]).hint() == "Bolt Food. " + BASE
