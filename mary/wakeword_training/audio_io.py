"""WAV in/out (16 kHz mono int16) and silence trimming, shared by record/train/test."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 16000


def save_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.astype(np.int16).tobytes())


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def speech_bounds(audio: np.ndarray, vad=None, threshold: float = 0.5) -> tuple[int, int] | None:
    """(first, last) sample of speech in a take, or None if there is none.

    With a SileroVAD, speech is what it hears as speech. Without one, it's the 20 ms frames clearly above
    the take's own noise floor. (Measuring against the peak instead kept whole takes: a laptop mic's hiss
    sits only ~30 dB under the voice, so the model learned that room noise was part of "Hey Mom".)"""
    if not len(audio):
        return None
    if vad is not None:
        from mary.audio.vad import CHUNK

        vad.reset()
        probs = [vad.probability(audio[i:i + CHUNK]) for i in range(0, len(audio) - CHUNK + 1, CHUNK)]
        hits = np.flatnonzero(np.array(probs) >= threshold)
        return (int(hits[0] * CHUNK), int((hits[-1] + 1) * CHUNK)) if len(hits) else None
    x = audio.astype(np.float32)
    frame = 320
    n = len(x) // frame
    if n == 0:
        return None
    db = 20 * np.log10(np.sqrt(np.mean(x[:n * frame].reshape(n, frame) ** 2, axis=1)) + 1e-6)
    loud = np.flatnonzero((db > np.percentile(db, 10) + 10) & (db > db.max() - 38))
    return (int(loud[0] * frame), int((loud[-1] + 1) * frame)) if len(loud) else None


def trim(audio: np.ndarray, pad_s: float = 0.1, vad=None) -> np.ndarray:
    """Cut what comes before and after the speech, keeping pad_s of it on each side."""
    bounds = speech_bounds(audio, vad)
    if bounds is None:
        return audio
    pad = int(pad_s * RATE)
    return audio[max(0, bounds[0] - pad):min(len(audio), bounds[1] + pad)]


def usable_take(audio: np.ndarray, vad=None) -> tuple[np.ndarray | None, str]:
    """A recorded wake-word take trimmed to its speech, or (None, why it can't be used). A take still
    speaking as the recording ended is cut off: the model would learn half the phrase."""
    bounds = speech_bounds(audio, vad)
    if bounds is None:
        return None, "no speech"
    start, end = bounds
    if end - start < 0.25 * RATE:
        return None, "too short"
    if end > len(audio) - 0.1 * RATE:
        return None, "cut off"
    pad = int(0.1 * RATE)
    return audio[max(0, start - pad):end + pad], ""
