"""The cloud ear (mary/audio/cloud_stt.py) with ElevenLabs and the local Whisper faked: nothing is sent anywhere."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from mary.audio import cloud_stt
from mary.audio.cloud_stt import ScribeEar, keyterms, make_ear
from mary.audio.stt import Heard
from mary.config import Section

AUDIO = np.zeros(16000, np.int16)


def cfg(**scribe):
    return Section.wrap({"stt": {"provider": "elevenlabs", "denoise_strength": 2.5,
                                 "elevenlabs": {"model": "scribe_v2", "timeout_s": 4, "retry_after_s": 30, **scribe}}})


def words(*pairs):
    return [NS(text=t, type="word", logprob=p) for t, p in pairs]


class FakeScribe:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.speech_to_text = NS(convert=self.convert)

    def convert(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeLocal:
    def __init__(self):
        self.heard = []

    def names(self):
        return ["Bolt Food", "Claude"], [("Paint", 2), ("Calculator", 2)]

    def hear(self, audio, answer=False):
        self.heard.append(audio)
        return Heard("local words", "local words", 3.0, -0.1)

    def warm_up(self):
        pass


def ear(*replies, **scribe):
    local = FakeLocal()
    return ScribeEar(cfg(**scribe), local, client=FakeScribe(*replies)), local


def test_scribe_hears_it_with_his_words_as_key_terms():
    e, local = ear(NS(text="Order from Bolt Food.", words=words(("Order", -0.01), ("Bolt", -0.2), ("Food", -0.05))))
    heard = e.hear(AUDIO)
    assert heard.text == "Order from Bolt Food." and not heard.rejected and local.heard == []
    call = e.client.calls[0]
    assert call["keyterms"] == ["Bolt Food", "Claude", "Paint", "Calculator"]  # his words and open apps first
    assert call["file_format"] == "pcm_s16le_16" and call["file"][1] == AUDIO.tobytes()
    assert call["no_verbatim"] is True and call["request_options"]["timeout_in_seconds"] == 4
    assert heard.confidence == pytest.approx(-0.0867, abs=1e-3)


def test_answers_keep_fillers():
    e, _ = ear(NS(text="Mm-hmm.", words=words(("Mm-hmm.", -0.1))))
    assert e.hear(AUDIO, answer=True).text == "Mm-hmm."
    assert e.client.calls[0]["no_verbatim"] is False


@pytest.mark.parametrize("text, why", [("", "silence"), ("Thank you.", "silence"),
                                       ("Don't forget to like and subscribe!", "video-talk")])
def test_nothing_said_is_still_ignored(text, why):
    e, _ = ear(NS(text=text, words=[]))
    heard = e.hear(AUDIO)
    assert heard.rejected == why and heard.text == ""


def test_unsure_only_once_a_limit_is_set():
    e, _ = ear(NS(text="Open spot if I.", words=words(("Open", -1.5), ("spot", -2.5))), min_confidence=-1.0)
    assert e.hear(AUDIO).rejected == "unsure"
    e, _ = ear(NS(text="Open spot if I.", words=words(("Open", -1.5), ("spot", -2.5))))
    assert e.hear(AUDIO).text == "Open spot if I."  # null until measured on his voice


def test_offline_falls_back_to_local_whisper_and_skips_the_cloud_for_a_while(monkeypatch):
    told = []
    e, local = ear(ConnectionError("no internet"), NS(text="Back online.", words=[]))
    e.on_problem = told.append
    assert e.hear(AUDIO).text == "local words" and len(local.heard) == 1
    assert e.hear(AUDIO).text == "local words" and len(e.client.calls) == 1  # skipped: no second 4 s wait
    assert len(told) == 1
    monkeypatch.setattr(cloud_stt.time, "monotonic", lambda: e.skip_until + 1)
    assert e.hear(AUDIO).text == "Back online."  # tried again after retry_after_s


def test_fallback_gets_the_noise_filter_the_local_whisper_was_tuned_with(monkeypatch):
    filtered = []
    monkeypatch.setattr(cloud_stt, "prepare", lambda audio, strength: (filtered.append(strength) or audio, ""))
    e, _ = ear(TimeoutError("slow"))
    assert e.denoise_strength == 0.0  # the cloud gets the recording as it is
    e.hear(AUDIO)
    assert filtered == [2.5]


def test_key_terms_respect_scribes_limits():
    long = "A Name With Far Too Many Words In It"
    terms = keyterms(["Bolt Food", long, "x" * 51], [(f"App{i}", 2) for i in range(150)])
    assert len(terms) == 100 and terms[0] == "Bolt Food" and long not in terms and "x" * 51 not in terms


def test_no_key_means_local_whisper(monkeypatch):
    told = []
    monkeypatch.setattr(cloud_stt, "SpeechToText", lambda c, m, apps=None: "local ear")
    monkeypatch.setattr(cloud_stt, "load_key", lambda: False)
    assert make_ear(cfg(), "models", on_problem=told.append) == "local ear" and told


def test_provider_local_never_loads_the_key(monkeypatch):
    monkeypatch.setattr(cloud_stt, "SpeechToText", lambda c, m, apps=None: "local ear")
    monkeypatch.setattr(cloud_stt, "load_key", lambda: pytest.fail("key loaded"))
    local_cfg = Section.wrap({"stt": {"provider": "local"}})
    assert make_ear(local_cfg, "models") == "local ear"
