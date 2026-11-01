"""Recording and training a wake word from the settings page (mary/settings/wake.py), on a scratch data folder."""
import json
import time

import numpy as np
import pytest

from mary import persona as personas
from mary.settings import store, wake


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text(f"paths:\n  data: '{(tmp_path / 'data').as_posix()}'\n", encoding="utf-8")
    personas._load.cache_clear()
    monkeypatch.setattr(wake, "_vad", None)
    yield tmp_path / "data"
    personas._load.cache_clear()


def take(rate=48000, tone_s=0.8, amplitude=9000):
    """3 s at the browser's rate: quiet, a voice-like tone, quiet (16-bit mono, as the page sends it)."""
    t = np.arange(int(tone_s * rate)) / rate
    tone = (np.sin(2 * np.pi * 220 * t) * np.sin(2 * np.pi * 3 * t) ** 2 * amplitude).astype(np.int16)
    gap = np.zeros(int(0.5 * rate), np.int16)
    return np.concatenate([gap, tone, np.zeros(int(3 * rate) - len(gap) - len(tone), np.int16)]).tobytes()


def test_a_take_is_resampled_checked_and_saved_like_the_command_line(data):
    out = wake.save_take("Hey Testy", "positive", take(), 48000)
    assert out == {"ok": True, "takes": 1}
    saved = data / "wakeword" / "recordings" / "positive" / "hey_testy_001.wav"
    from mary.wakeword_training.audio_io import load_wav

    audio = load_wav(saved)
    assert len(audio) == 3 * 16000  # resampled to 16 kHz
    assert wake.save_take("Hey Testy", "positive", take(), 48000)["takes"] == 2


def test_silence_and_cut_off_takes_are_asked_for_again(data):
    assert wake.save_take("Hey Testy", "positive", bytes(48000 * 6), 48000) == {"ok": False,
                                                                                "why": wake.RETRY["too quiet"]}
    late = np.concatenate([np.zeros(int(2.6 * 48000), np.int16),
                           (np.sin(np.arange(int(0.4 * 48000)) / 8) * 9000).astype(np.int16)]).tobytes()
    assert not wake.save_take("Hey Testy", "positive", late, 48000)["ok"]
    assert not (data / "wakeword" / "recordings" / "positive").exists() or \
        not list((data / "wakeword" / "recordings" / "positive").glob("*.wav"))


def test_sound_alikes_and_room_sound_go_with_the_shared_negatives(data):
    assert wake.save_take("Hey Testy", "near_miss", take(), 48000)["ok"]
    assert wake.save_take("Hey Testy", "room", bytes(16000 * 2 * 4), 16000)["ok"]  # a quiet room is fine
    negative = data / "wakeword" / "recordings" / "negative"
    assert sorted(f.name for f in negative.glob("*.wav")) == ["near_miss_001.wav", "room_001.wav"]
    plan = wake.plan("hey testy")
    assert plan["phrase"] == "Hey Testy" and plan["takes"] == 0 and not plan["need_room"] and plan["need_speech"]
    assert plan["download"] and plan["status"] == "needs training" and plan["training"] is None
    assert sum(s["count"] for s in plan["styles"]) == 32


def test_a_trained_phrase_shows_as_ready_once_it_passed(data):
    models = data / "models" / "wakeword"
    models.mkdir(parents=True)
    (models / "hey_testy.onnx").write_bytes(b"model")
    (models / "hey_testy.json").write_text('{"passed_on_your_voice": false}', encoding="utf-8")
    assert wake.plan("Hey Testy")["status"] == "needs more takes"
    (models / "hey_testy.json").write_text('{"passed_on_your_voice": true}', encoding="utf-8")
    assert wake.plan("Hey Testy")["status"] == "ready"


def test_bad_recordings_are_refused(data):
    for kind, rate in (("nonsense", 48000), ("positive", 1000), ("positive", "48000")):
        with pytest.raises(store.SettingsError):
            wake.save_take("Hey Testy", kind, take(), rate)
    with pytest.raises(store.SettingsError):
        wake.save_take("hey", "positive", take(), 48000)  # no name in the phrase


def test_training_starts_in_the_background_only_with_enough_takes(data, monkeypatch):
    with pytest.raises(store.SettingsError):
        wake.start_training("Hey Testy")
    for _ in range(wake.MIN_TAKES):
        wake.save_take("Hey Testy", "positive", take(), 48000)
    started = []

    class FakeProcess:
        pid = 4242

        def __init__(self, args, **kw):
            started.append((args, kw))

    monkeypatch.setattr(wake.subprocess, "Popen", FakeProcess)
    status = wake.start_training("hey testy")
    args, kw = started[0]
    assert args[1:] == ["-m", "mary.wakeword_training.train", "--phrase", "Hey Testy", "--enable", "--status",
                        str(data / "wakeword" / "training" / "hey_testy.json")]
    assert kw["creationflags"] & wake.subprocess.BELOW_NORMAL_PRIORITY_CLASS
    assert status["running"] and status["pid"] == 4242


def test_training_status_notices_a_training_that_died(data, monkeypatch):
    folder = data / "wakeword" / "training"
    folder.mkdir(parents=True)
    old = time.time() - 60
    (folder / "hey_testy.json").write_text(json.dumps({"phrase": "Hey Testy", "progress": 0.4, "done": False,
                                                       "pid": 999999, "updated": old}), encoding="utf-8")
    status = wake.training_status("Hey Testy")
    assert status["done"] and not status["running"] and "stopped" in status["error"]
    (folder / "hey_testy.json").write_text(json.dumps({"phrase": "Hey Testy", "progress": 1, "done": True,
                                                       "passed": True}), encoding="utf-8")
    assert wake.training_status("Hey Testy")["passed"]
