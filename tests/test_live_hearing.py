"""Live hearing (mary/audio/live_stt.py), the browser-style clean-up (mary/audio/clean.py) and the recording loop
feeding them. ElevenLabs is faked: nothing is sent anywhere."""
import time
from types import SimpleNamespace

import numpy as np
import pytest

from mary.audio.clean import VoiceCleaner, make_cleaner
from mary.audio.live_stt import LiveLoop, LiveSession
from mary.config import Section, load_config
from tests.test_core import FakeMic, FakeVad


class FakeConnection:
    """The SDK's realtime connection: on commit it 'transcribes' what it was sent."""

    def __init__(self, text="Open Notepad.", fail_on_commit=False):
        self.handlers, self.sent, self.closed, self.text, self.fail = {}, [], False, text, fail_on_commit

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def emit(self, event, data):
        for fn in self.handlers.get(event, []):
            fn(data)

    async def send(self, data):
        self.sent.append(data["audio_base_64"])
        self.emit("partial_transcript", {"text": "Open"})

    async def commit(self):
        if self.fail:
            self.emit("error", {"message_type": "transcriber_error"})
        else:
            self.emit("committed_transcript", {"text": self.text})

    async def close(self):
        self.closed = True


@pytest.fixture(scope="module")
def live_loop():
    return LiveLoop()


def session(live_loop, conn, partials=None):
    async def connect():
        return conn
    return LiveSession(live_loop, connect, partials.append if partials is not None else None)


def test_audio_streams_and_the_final_text_comes_on_commit(live_loop):
    conn, partials = FakeConnection(), []
    s = session(live_loop, conn, partials)
    s.feed(np.ones(1280, np.int16))
    s.feed(np.ones(1280, np.int16))
    heard = s.finish(2)
    assert heard.text == "Open Notepad." and not heard.rejected
    assert len(conn.sent) == 2 and partials == ["Open", "Open"] and conn.closed


def test_a_failed_session_returns_none_so_the_recording_is_heard_instead(live_loop):
    s = session(live_loop, FakeConnection(fail_on_commit=True))
    s.feed(np.ones(1280, np.int16))
    assert s.finish(2) is None and s.error


def test_a_connection_that_never_opens_fails_quietly(live_loop):
    async def connect():
        raise OSError("no internet")
    s = LiveSession(live_loop, connect)
    s.feed(np.ones(1280, np.int16))
    assert s.finish(2) is None


def test_nothing_sent_means_nothing_heard(live_loop):
    conn = FakeConnection()
    heard = session(live_loop, conn).finish(2)
    assert heard.text == "" and heard.rejected == "silence" and conn.sent == []


def test_cancel_closes_without_a_transcript(live_loop):
    conn = FakeConnection()
    s = session(live_loop, conn)
    s.feed(np.ones(1280, np.int16))
    s.cancel()
    deadline = time.monotonic() + 2
    while not conn.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert conn.closed and not s.texts


# ------------------------------------------------------------------ the recording loop


class FakeLive:
    def __init__(self):
        self.fed, self.cancelled = [], False

    def feed(self, audio):
        self.fed.append(len(audio))

    def cancel(self):
        self.cancelled = True


def loop_on(pattern):
    import mary.voice as voice
    loop = voice.VoiceLoop(load_config(), None, FakeMic(pattern), FakeVad(), None, SimpleNamespace())
    loop.opened = []
    loop._open_live = lambda: loop.opened.append(FakeLive()) or loop.opened[-1]
    return loop


def test_nothing_is_sent_before_you_speak_then_everything_is():
    loop = loop_on([(False, 1.0), (True, 1.0), (False, 3.0)])
    live = FakeLive()
    audio = loop.record(10, live=live)
    assert loop.live is live and not live.cancelled
    preroll = int(loop.cfg.vad.preroll_ms / 80)
    assert live.fed[0] == preroll * 1280             # the pre-roll, once speech starts
    assert sum(live.fed) == len(audio)               # then every frame of the request


def test_a_cough_starts_a_fresh_session():
    loop = loop_on([(True, 0.08), (False, 2.0), (True, 1.0), (False, 3.0)])
    first = FakeLive()
    loop.record(10, live=first)
    assert first.cancelled and loop.live is loop.opened[0] and not loop.opened[0].cancelled


def test_no_speech_cancels_the_session():
    loop = loop_on([(False, 3.0)])
    live = FakeLive()
    assert loop.record(0.01, live=live) is None and live.cancelled and loop.live is None


def test_the_recording_is_cleaned_but_the_vad_hears_the_raw_mic():
    loop = loop_on([(True, 1.0), (False, 3.0)])
    loop.cleaner = SimpleNamespace(process=lambda f: f * 0)  # "cleaned" frames are silent; raw ones are speech
    audio = loop.record(10)
    assert audio is not None and not audio.any()  # it still knew you were speaking


# ------------------------------------------------------------------ the clean-up


def test_cleaner_keeps_the_length_of_80_ms_blocks_and_carries_odd_bits():
    cleaner = VoiceCleaner()
    rng = np.random.default_rng(0)
    block = (rng.normal(0, 3000, 1280)).astype(np.int16)
    assert len(cleaner.process(block)) == 1280
    assert len(cleaner.process(block[:1000])) == 960   # 40 samples wait for the next call
    assert len(cleaner.process(block[:120])) == 160


def test_cleaner_can_be_switched_off():
    off = Section.wrap({"audio": {"clean": {"enabled": False}}})
    assert make_cleaner(off) is None


def test_ear_opens_no_live_session_without_a_live_model():
    from mary.audio.cloud_stt import ScribeEar
    from tests.test_cloud_stt import FakeLocal, FakeScribe, cfg

    ear = ScribeEar(cfg(), FakeLocal(), client=FakeScribe())
    assert ear.open_live() is None


def test_a_failed_live_session_pauses_live_hearing():
    from mary.audio.cloud_stt import ScribeEar
    from tests.test_cloud_stt import FakeLocal, FakeScribe, cfg

    ear = ScribeEar(cfg(live_model="scribe_v3_live"), FakeLocal(), client=FakeScribe())
    assert ear.hear_live(SimpleNamespace(finish=lambda timeout: None)) is None
    assert ear.open_live() is None  # batch Scribe and the local Whisper until retry_after_s

