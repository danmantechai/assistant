"""Wake-word detection with openWakeWord (ONNX Runtime on Windows), plus a speech-recognition second opinion."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def threshold_for(model: str, configured) -> float:
    """`configured` may be "auto": use the threshold training chose (saved next to the model)."""
    if configured != "auto":
        return float(configured)
    sidecar = Path(model).with_suffix(".json")
    try:
        return float(json.loads(sidecar.read_text(encoding="utf-8"))["threshold"])
    except (OSError, ValueError, KeyError):
        return 0.5


def passed(model: str, checked: bool = True) -> bool:
    """Did training say this model works on the user's own voice? (Trained phrases load only if so.)
    checked=False: without the speech-recognition check, a model that passed only thanks to it doesn't."""
    try:
        report = json.loads(Path(model).with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(report.get("passed_on_your_voice")) and (checked or not report.get("needs_check"))


def usable_models(models: list[str], checked: bool = True) -> list[str]:
    """Built-in wake words (names like 'hey_jarvis') always; trained .onnx files once they've passed on the
    user's voice, whichever phrase it is, so "Hey Mom" works even while "Hey Mary" doesn't yet."""
    return [m for m in models if not m.endswith(".onnx") or (Path(m).exists() and passed(m, checked))]


def spoken_name(model: str) -> str:
    """'.../hey_mom.onnx' or 'hey_jarvis' -> 'Hey Mom' / 'Hey Jarvis'."""
    return Path(model).stem.replace("_", " ").title()


class WakeWord:
    def __init__(self, model: str | list[str], threshold: float | list[float] = 0.5, cooldown_s: float = 2.0):
        """`model` is a trained .onnx file (hey_mary.onnx), a built-in name like 'hey_jarvis', or a list of
        them ("Hey Mary" and "Hey Mom" are separate models), each with its own threshold."""
        from openwakeword.model import Model

        models = [model] if isinstance(model, str) else list(model)
        thresholds = threshold if isinstance(threshold, list) else [threshold] * len(models)
        for m in models:
            if m.endswith(".onnx") and not Path(m).exists():
                raise FileNotFoundError(f"wake-word model not found: {m} (train it first)")
        self.model = Model(wakeword_models=models, inference_framework="onnx")
        self.keys = list(self.model.models)  # one per model, in the order given
        self.thresholds = dict(zip(self.keys, thresholds))
        self.threshold = thresholds[0]
        self.cooldown_s = cooldown_s
        self.last_score = 0.0
        self.last_key = ""
        self._last = 0.0

    def detect(self, frame: np.ndarray) -> float | None:
        """Feed one 80 ms int16 frame; returns the score when a wake word fires, else None."""
        scores = self.model.predict(frame)
        key = max(self.keys, key=lambda k: float(scores[k]) - self.thresholds[k])  # closest to (or over) its bar
        score = float(scores[key])
        self.last_score, self.last_key = score, key
        now = time.monotonic()
        if score >= self.thresholds[key] and now - self._last > self.cooldown_s:
            self._last = now
            return score
        return None

    def reset(self) -> None:
        self.model.reset()


# What speech recognition must hear for each trained phrase (keyed by a word in the model's name). Whisper
# often writes the user's "Hey Mom" as "Mom", "Here Mom" or "A mom"; a bare "Mom" counts, since that's what
# he calls her. "I'll call my mum later" and "Mum's birthday…" don't.
_CONFIRM = {
    "mom": re.compile(r"\b(?:hey|hi|hay|a|your|yo|here|hear)\W*(?:mom|mum|mommy|mummy|mam|ma)\b"
                      r"|^\W*(?:mom|mum|mommy|mummy)\W*$", re.I),
    "mary": re.compile(r"\b(?:hey|hi|hay|ok|okay)\W*(?:mary|marie|mari|mery)\b|^\W*(?:mary|amen)\W*$", re.I),
}


def confirms(key: str, text: str) -> bool:
    """Does `text` (what Whisper heard) say the wake phrase of model `key`? Built-ins aren't checked."""
    pattern = next((p for word, p in _CONFIRM.items() if word in key.lower()), None)
    return pattern is None or bool(pattern.search(text))


class WakeCheck:
    """A second opinion on each detection: Whisper must hear the wake phrase in the last ~2.5 s.

    The trained "Hey Mom" model caught every one of the user's test takes but also fired ~17 times an hour
    on other audio. On his recordings, base.en confirmed ~20 of 22 "Hey Mom" takes and none of 41 other
    clips (sound-alikes, his reading aloud), taking ~1 s per check, which is the delay before the chime."""

    SECONDS = 2.5

    def __init__(self, model: str, models_dir: Path, cpu_threads: int = 6):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=cpu_threads,
                                  download_root=str(models_dir / "whisper"))

    def heard(self, key: str, audio: np.ndarray) -> tuple[bool, str]:
        """(confirmed, what was heard) for int16 audio ending where model `key` fired."""
        if not any(word in key.lower() for word in _CONFIRM):
            return True, ""
        segments, _ = self.model.transcribe(audio.astype(np.float32) / 32768.0, beam_size=1, language="en",
                                            vad_filter=False, without_timestamps=True,
                                            condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments)
        return confirms(key, text), text
