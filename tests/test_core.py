"""The core's dictation flow ("What should I type?") with a fake keyboard, so nothing is really typed,
and the voice loop's recording logic with a fake microphone."""
import time
from types import SimpleNamespace

import numpy as np
import pytest

from mary.config import load_config
from mary.output import Output
from mary.tools.registry import ToolResult
from mary.voice import allowed_pause
from tests.conftest import needs_router


class Recorder(Output):
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


@pytest.fixture(scope="module")
def mary():
    from mary.config import Section
    from mary.core import Mary

    cfg = load_config()
    cfg["brain"] = Section.wrap({**(cfg.get("brain") or {}), "mode": "today"})  # dictation is a router intent
    m = needs_router(lambda: Mary(cfg, Recorder()))
    m.typed = []
    m.tools.tools["keyboard"].fn = lambda action, text: (m.typed.append(text) or
                                                          ToolResult(True, "typed", say="Typed it."))
    return m


def test_dictation_asks_then_types_the_next_request(mary):
    mary.handle("write the following text in it", prefill=False)
    assert mary.output.said[-1] == "What should I type?"
    mary.handle("Daniel is a good boy and then he slept.", prefill=False)  # not split, not routed
    assert mary.typed[-1] == "Daniel is a good boy and then he slept."


def test_dictation_wait_expires(mary):
    mary.handle("write the following text", prefill=False)
    mary.awaiting_until = time.monotonic() - 1  # 30 s have passed
    before = len(mary.typed)
    mary.handle("what time is it", prefill=False)
    assert len(mary.typed) == before and mary.output.said[-1].startswith("It's")


def test_dictation_cancel(mary):
    mary.handle("type the following", prefill=False)
    before = len(mary.typed)
    mary.handle("never mind", prefill=False)
    assert len(mary.typed) == before and "nothing typed" in mary.output.said[-1]


def test_allowed_pause_grows_with_speech():
    v = SimpleNamespace(end_silence_ms=1500, long_end_silence_ms=5000, short_until_s=1.2, long_after_s=2.5)
    assert allowed_pause(v, 0.8) == 1.5          # "what time is it"
    assert 4.0 < allowed_pause(v, 2.3) < 5.0     # "open notepad and write the following text"
    assert allowed_pause(v, 6.0) == 5.0          # long dictation


# ---------------------------------------------------------------- speculative transcription

class FakeMic:
    """Frames whose first sample says whether they're speech (the fake VAD reads it)."""
    active = True

    def __init__(self, pattern):  # [(is_speech, seconds), ...]
        from mary.voice import FRAME_S
        self.frames = [np.full(1280, 1000 if speech else 0, np.int16)
                       for speech, secs in pattern for _ in range(round(secs / FRAME_S))]

    def read(self, timeout=1.0):
        time.sleep(0.002)  # let the speculative thread run, as real 80 ms frames would
        return self.frames.pop(0) if self.frames else None


class FakeVad:
    def reset(self):
        pass

    def probability(self, frame):
        return 0.9 if frame[0] else 0.0


def _loop(pattern, monkeypatch):
    """A voice loop on a fake mic; loop.started lists (padded length, frames still unread) per Whisper run."""
    import mary.voice as voice
    loop = voice.VoiceLoop(load_config(), None, FakeMic(pattern), FakeVad(), None, SimpleNamespace())
    loop.started = []
    loop._transcribe = lambda audio: (f"{len(audio)} samples", None)
    real = voice._Speculation

    def spy(audio, transcribe):
        loop.started.append((len(audio), len(loop.mic.frames)))
        return real(audio, transcribe)

    monkeypatch.setattr(voice, "_Speculation", spy)
    return loop


def test_speculation_starts_in_the_final_pause_and_is_kept(monkeypatch):
    loop = _loop([(False, 0.4), (True, 1.0), (False, 3.0)], monkeypatch)
    audio = loop.record(10, speculate=True)
    assert audio is not None and loop.speculation is not None and len(loop.started) == 1
    padded, unread_then = loop.started[0]
    assert unread_then - len(loop.mic.frames) >= 10   # started 10+ frames (0.8 s) before the end
    assert abs(padded - len(audio)) <= 1280           # padded to the final recording's length
    assert loop.speculation.wait() == (f"{padded} samples", None)


def test_speaking_again_discards_the_speculation(monkeypatch):
    loop = _loop([(True, 1.0), (False, 0.7), (True, 1.0), (False, 6.0)], monkeypatch)
    audio = loop.record(10, speculate=True)
    assert len(loop.started) == 2                     # one stale run, one for the whole request
    padded = loop.started[1][0]
    assert loop.speculation.wait()[0] == f"{padded} samples"
    assert abs(padded - len(audio)) <= 1280


def test_no_speculation_unless_asked(monkeypatch):
    loop = _loop([(True, 1.0), (False, 3.0)], monkeypatch)
    assert loop.record(10) is not None
    assert loop.speculation is None and loop.started == []
