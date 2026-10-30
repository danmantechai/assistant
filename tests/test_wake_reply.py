"""Her reply to "Hey Mom" (mary/audio/wake_reply.py), with Kokoro, the speaker and the mic faked."""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from mary.audio.wake_reply import WakeReplies, parse
from mary.voice import VoiceLoop


class FakeTTS:
    voice, speed = "af_heart", 1.0

    def __init__(self):
        self.said = []
        self.kokoro = SimpleNamespace(create=self.create)

    def create(self, text, voice, speed, lang, is_phonemes):
        self.said.append((text, is_phonemes))
        return np.full(2400, 0.1, np.float32), 24000


@pytest.mark.parametrize("entry, parsed", [
    ("Yes, dear?", ("Yes, dear?", None)),
    ("Mm-hmm? /mˈhm?/", ("Mm-hmm?", "mˈhm?")),
    ("/hˈmm?/", ("hˈmm?", "hˈmm?")),
])
def test_parse(entry, parsed):
    assert parse(entry) == parsed


def test_hums_are_said_as_phonemes_and_cached(tmp_path):
    tts = FakeTTS()
    WakeReplies(tts, ["Yes, dear?", "Mm-hmm? /mˈhm?/"], cache_dir=tmp_path).render()
    assert tts.said == [("Yes, dear?", False), ("mˈhm?", True)]
    again = FakeTTS()
    replies = WakeReplies(again, ["Yes, dear?", "Mm-hmm? /mˈhm?/"], cache_dir=tmp_path)
    replies.render()
    assert again.said == [] and set(replies.audio) == {"Yes, dear?", "Mm-hmm?"}  # next start: from disk


def test_never_the_same_twice_in_a_row(tmp_path):
    replies = WakeReplies(FakeTTS(), ["Yes, dear?", "Mm-hmm? /mˈhm?/", "I'm here, love."], cache_dir=tmp_path)
    replies.render()
    said = [replies.pick()[0] for _ in range(60)]
    assert all(a != b for a, b in zip(said, said[1:])) and len(set(said)) == 3


@pytest.mark.parametrize("hour, morning", [(8, True), (15, False)])
def test_morning_replies_only_before_noon(tmp_path, hour, morning):
    replies = WakeReplies(FakeTTS(), ["Yes, dear?"], ["Morning, dear."], cache_dir=tmp_path,
                          now=lambda: datetime(2026, 10, 27, hour))
    replies.render()
    assert ("Morning, dear." in {replies.pick()[0] for _ in range(20)}) == morning


def test_nothing_rendered_yet_means_no_reply(tmp_path):
    assert WakeReplies(FakeTTS(), ["Yes, dear?"], cache_dir=tmp_path).pick() is None


def loop_with(replies):
    loop = VoiceLoop.__new__(VoiceLoop)
    played, drained = [], []
    speaker = SimpleNamespace(play=played.append, wait=lambda t: None, wait_quiet=lambda **k: None)
    loop.voice_out = SimpleNamespace(speaker=speaker, speaking=False, stop=lambda: None)
    loop.mic, loop.console, loop.wake_replies = SimpleNamespace(drain=lambda: drained.append(1)), None, replies
    loop.recording, loop.trigger = False, SimpleNamespace(set=lambda: None)
    return loop, played, drained


def test_wake_reply_plays_and_is_kept_out_of_the_recording(tmp_path):
    replies = WakeReplies(FakeTTS(), ["Yes, dear?"], cache_dir=tmp_path)
    replies.render()
    loop, played, drained = loop_with(replies)
    assert loop._answer_wake()
    assert len(played) == 1 and drained == [1]


def test_without_replies_the_chime_is_used(tmp_path):
    loop, played, _ = loop_with(None)
    assert not loop._answer_wake() and played == []


def test_the_hotkey_is_not_a_wake_word():
    loop, _, _ = loop_with(None)
    loop._by_voice = True
    loop.summon()
    assert loop._by_voice is False
