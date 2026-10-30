"""Wake-word training: what counts as a usable take, and which trained models MARY loads."""
import json

import numpy as np

from mary.audio.wakeword import confirms, spoken_name, usable_models
from mary.wakeword_training.audio_io import RATE, speech_bounds, trim, usable_take
from mary.wakeword_training.train import destination, good_enough


def _take(start_s, end_s, seconds=2.2, hiss_db=-30, seed=0):
    """Room hiss with a 300 Hz "voice" from start_s to end_s; the hiss sits hiss_db under the voice,
    like the laptop mic the takes were recorded on."""
    n = int(seconds * RATE)
    voice = np.zeros(n)
    t = np.arange(int(start_s * RATE), int(end_s * RATE))
    voice[t] = np.sin(2 * np.pi * 300 * t / RATE) * 15000
    hiss = np.random.default_rng(seed).standard_normal(n) * 15000 / np.sqrt(2) * 10 ** (hiss_db / 20)
    return (voice + hiss).astype(np.int16)


def test_trimming_cuts_the_room_hiss_around_the_words():
    audio = _take(0.4, 1.0)
    start, end = speech_bounds(audio)
    assert abs(start / RATE - 0.4) < 0.03 and abs(end / RATE - 1.0) < 0.03
    assert abs(len(trim(audio)) / RATE - 0.8) < 0.05  # the words plus 0.1 s each side, not all 2.2 s


def test_unusable_takes_say_why():
    assert usable_take(_take(0.4, 1.0))[1] == ""
    assert usable_take(_take(1.5, 2.2))[1] == "cut off"  # still talking when the recording ended
    assert usable_take(_take(0.4, 0.5))[1] == "too short"
    assert usable_take(_take(0, 0))[1] == "no speech"    # just the room: used to be saved as "Hey Mary"


def test_only_trained_phrases_that_passed_load(tmp_path):
    def model(name, ok):
        onnx = tmp_path / f"{name}.onnx"
        onnx.write_bytes(b"")
        onnx.with_suffix(".json").write_text(json.dumps({"passed_on_your_voice": ok}))
        return str(onnx)

    mary, mom = model("hey_mary", False), model("hey_mom", True)
    assert usable_models([mary, mom, str(tmp_path / "missing.onnx"), "hey_jarvis"]) == [mom, "hey_jarvis"]
    assert [spoken_name(m) for m in (mom, "hey_jarvis")] == ["Hey Mom", "Hey Jarvis"]


def test_failed_training_never_replaces_a_model_that_works(tmp_path):
    out = tmp_path / "hey_mary.onnx"
    assert destination(out, ok=False) == out  # nothing there yet
    out.write_bytes(b"")
    out.with_suffix(".json").write_text(json.dumps({"passed_on_your_voice": False}))
    assert destination(out, ok=False) == out  # the old one didn't work either
    out.with_suffix(".json").write_text(json.dumps({"passed_on_your_voice": True}))
    assert destination(out, ok=False) == tmp_path / "hey_mary.failed.onnx"
    assert destination(out, ok=True) == out


def test_speech_recognition_confirms_only_the_wake_phrase():
    # How Whisper wrote the user's own "Hey Mom" takes, and what it heard for the sound-alikes he recorded.
    for heard in ("Hey, Mom.", "Hey mom", "Hey, Mum.", "Mom.", "Here Mom", "A mom"):
        assert confirms("hey_mom", heard), heard
    for heard in ("I'll call my mum later", "Mum's birthday is on Friday", "Come on.", "Hey Tom", "um", ""):
        assert not confirms("hey_mom", heard), heard
    assert confirms("hey_mary", "Amen.") and confirms("hey_mary", "Hey, Mary.")
    assert not confirms("hey_mary", "Merry Christmas")
    assert confirms("hey_jarvis", "")  # built-in wake words aren't checked


def test_a_model_that_passed_thanks_to_the_check_needs_it(tmp_path):
    onnx = tmp_path / "hey_mom.onnx"
    onnx.write_bytes(b"")
    onnx.with_suffix(".json").write_text(json.dumps({"passed_on_your_voice": True, "needs_check": True}))
    assert usable_models([str(onnx)], checked=True) == [str(onnx)]
    assert usable_models([str(onnx)], checked=False) == []
    report = {"recall_your_voice_heldout": 1.0, "false_triggers_per_hour_heldout": 17.3}  # "Hey Mom", measured
    assert good_enough(report, checked=True)[0] and not good_enough(report)[0]
    assert not good_enough({**report, "false_triggers_per_hour_heldout": 35.0}, checked=True)[0]
