"""Error recovery with fakes: Ollama offline or dropping out, a dead microphone, a stalled speaker."""
import time
from types import SimpleNamespace

import numpy as np
import pytest

import mary.brain.ollama as ollama_brain
from mary.config import load_config
from mary.llm.agent import OFFLINE_REPLY
from mary.output import Output


class Recorder(Output):
    def __init__(self):
        self.said, self.logs = [], []

    def say(self, text):
        self.said.append(text)

    def tool_log(self, line):
        self.logs.append(line)


def _chunk(content="", done=False):
    return SimpleNamespace(message=SimpleNamespace(content=content, thinking=None, tool_calls=None), done=done,
                           prompt_eval_duration=0, eval_count=1, eval_duration=0)


class FlakyClient:
    """Fails the first `failures` calls the way the ollama client does when the server is gone."""

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def chat(self, **kw):
        self.calls += 1
        if self.calls <= self.failures:
            def broken():
                raise ConnectionError("Failed to connect to Ollama")
                yield
            return broken()
        return iter([_chunk("Four."), _chunk(done=True)])


@pytest.fixture(scope="module")
def mary():
    from mary.core import Mary
    return Mary(load_config(), Recorder())


@pytest.fixture
def fresh(mary, monkeypatch):
    mary.output.said.clear()
    mary.agent.offline_reason = None
    monkeypatch.setattr(mary.agent, "_recover_loop", lambda: None)  # no background retries in tests
    yield mary
    mary.agent.offline_reason = None


def test_offline_llm_answers_at_once_and_router_still_works(fresh, monkeypatch):
    monkeypatch.setattr(ollama_brain, "reachable", lambda host, timeout=2: False)
    fresh.agent.go_offline("Ollama isn't installed.")
    start = time.perf_counter()
    fresh.handle("tell me a joke about cats", prefill=False)
    assert fresh.output.said[-1] == OFFLINE_REPLY
    assert time.perf_counter() - start < 2
    fresh.handle("what time is it", prefill=False)   # the router doesn't need the LLM
    assert fresh.output.said[-1] != OFFLINE_REPLY


def test_lost_connection_is_retried_once(fresh, monkeypatch):
    monkeypatch.setattr(ollama_brain, "reachable", lambda host, timeout=2: True)  # it came straight back
    monkeypatch.setattr(fresh.agent.brain, "client", FlakyClient(failures=1))
    fresh.handle("what is two plus two, roughly", prefill=False)
    assert fresh.agent.brain.client.calls == 2 and fresh.agent.offline_reason is None


def test_connection_that_stays_down_goes_offline(fresh, monkeypatch):
    monkeypatch.setattr(ollama_brain, "reachable", lambda host, timeout=2: True)
    monkeypatch.setattr(fresh.agent.brain, "client", FlakyClient(failures=5))
    fresh.handle("what is two plus two, roughly", prefill=False)
    assert fresh.output.said[-1] == OFFLINE_REPLY and fresh.agent.offline_reason


def test_request_during_warm_up_says_why_it_is_slow(fresh, monkeypatch):
    monkeypatch.setattr(fresh.agent.brain, "client", FlakyClient(failures=0))
    fresh.agent.warming.set()
    try:
        fresh.handle("what is two plus two, roughly", prefill=False)
    finally:
        fresh.agent.warming.clear()
    assert fresh.output.said[0].startswith("One moment") and fresh.output.said[-1] != OFFLINE_REPLY


# ---------------------------------------------------------------- audio devices

def test_dead_microphone_ends_recording_instead_of_hanging():
    from mary.audio.mic import Microphone
    from mary.voice import VoiceLoop

    mic = Microphone()
    mic._stream = object()                   # "open", but the callback stopped (unplugged)
    mic.last_frame_at = time.monotonic() - 5
    vad = SimpleNamespace(reset=lambda: None, probability=lambda f: 0.0)
    loop = VoiceLoop(load_config(), None, mic, vad, None, SimpleNamespace())
    start = time.perf_counter()
    assert loop.record(no_speech_timeout=30) is None
    assert time.perf_counter() - start < 3 and mic.broken


def test_stalled_speaker_stops_counting_as_playing():
    from mary.audio.tts import Speaker

    sp = Speaker()
    sp._stream = SimpleNamespace(stop=lambda: None, close=lambda: None)  # a sound card that went away
    sp._chunks.put(np.zeros(24000, np.float32))
    sp.busy_until = time.monotonic() - 1
    sp.last_callback = time.monotonic() - 5
    assert not sp.playing                    # wait()/wait_done() return instead of hanging for 60 s
    assert sp._stream is None                # reopened on the next play()


# ---------------------------------------------------------------- background mode

def test_autostart_shortcut_runs_mary_in_the_background(tmp_path):
    import win32com.client

    from mary import autostart

    assert not autostart.is_enabled(tmp_path)
    path = autostart.enable(tmp_path)
    assert autostart.is_enabled(tmp_path)
    link = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(path))
    assert link.TargetPath.lower().endswith("pythonw.exe") and link.Arguments == "-m mary --background"
    assert autostart.disable(tmp_path) and not autostart.is_enabled(tmp_path)


def test_single_instance():
    from mary import winutil

    name = f"MARY-test-{time.time_ns()}"
    assert winutil.single_instance(name)
    assert not winutil.single_instance(name)  # a second MARY would be turned away
