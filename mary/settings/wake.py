"""Recording and training a wake word from the settings page.

The browser records each take and sends it here; it's checked and saved exactly as the command line does it
(mary/wakeword_training/record.py), so the same training uses it. Training runs as its own process at low priority
(python -m mary.wakeword_training.train --status …), writing its progress to data/wakeword/training/<phrase>.json:
that file outlives the page and MARY, so the progress bar picks up again after a reload or a restart.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from math import gcd
from pathlib import Path

import numpy as np

from mary.config import PROJECT_DIR, expand_path, load_config
from mary.settings.store import SettingsError
from mary.wakeword_training import phrases

KINDS = {"positive": ("positive", None), "near_miss": ("negative", "near_miss"), "speech": ("negative", "speech"),
         "room": ("negative", "room")}
RETRY = {"too quiet": "Too quiet. Say it a little louder.", "no speech": "Didn't catch the words. Say it again.",
         "too short": "Too short. Say the whole phrase.", "cut off": "Cut off. Start right after the beep."}
MIN_TAKES = 10
_lock = threading.Lock()
_vad = None


def _data() -> Path:
    return expand_path(load_config().paths.data)


def _spec(phrase: str) -> dict:
    try:
        return phrases.spec(phrase)
    except ValueError as e:
        raise SettingsError(str(e)) from None


def _check(audio: np.ndarray) -> str:
    """'' for a usable take, else why not (the same check as the command line's)."""
    global _vad
    from mary.wakeword_training.audio_io import usable_take

    with _lock:
        if _vad is None:
            model = _data() / "models" / "silero" / "silero_vad.onnx"
            if model.exists():
                from mary.audio.vad import SileroVAD

                _vad = SileroVAD(model)
        return usable_take(audio, _vad)[1]


def takes(spec: dict) -> int:
    return len(list((_data() / "wakeword" / "recordings" / "positive").glob(f"{spec['takes']}_*.wav")))


def plan(phrase: str) -> dict:
    """What recording this phrase involves, and what's already done."""
    from mary.settings.choices import wake_status

    s = _spec(phrase)
    negative = _data() / "wakeword" / "recordings" / "negative"
    return {"phrase": s["name"], "model": s["file"], "takes": takes(s), "min_takes": MIN_TAKES,
            "styles": [{"how": how, "count": n} for how, n in s["styles"]], "near": s["near"],
            "need_speech": not list(negative.glob("speech_*.wav")), "need_room": not list(negative.glob("room_*.wav")),
            "read_aloud": phrases.READ_ALOUD,
            "download": not (_data() / "wakeword" / "validation_set_features.npy").exists(),
            "status": wake_status(s["file"]), "training": training_status(phrase)}


def save_take(phrase: str, kind: str, pcm: bytes, rate: int) -> dict:
    """One recording from the browser (16-bit mono at `rate`): resampled to 16 kHz, checked, and saved under the
    name record.py would give it. {"ok": False, "why": …} asks for it again."""
    from scipy.signal import resample_poly

    from mary.wakeword_training.audio_io import RATE, save_wav

    if kind not in KINDS:
        raise SettingsError("Unknown kind of recording.")
    if not isinstance(rate, int) or not 8000 <= rate <= 192000:
        raise SettingsError("The recording's sample rate is off.")
    audio = np.frombuffer(pcm[:len(pcm) // 2 * 2], "<i2").astype(np.float32)
    if not len(audio) or len(audio) > rate * 40:
        raise SettingsError("That recording is empty or too long.")
    if rate != RATE:
        g = gcd(RATE, rate)
        audio = resample_poly(audio, RATE // g, rate // g)
    audio = np.clip(audio, -32768, 32767).astype(np.int16)
    quiet = int(np.abs(audio).max()) < 1500
    why = ""
    if kind != "room":
        why = "too quiet" if quiet else _check(audio)
        if kind != "positive" and why != "no speech" and why != "too quiet":
            why = ""  # sound-alikes and reading aloud only need speech in them
    if why:
        return {"ok": False, "why": RETRY.get(why, why)}
    s = _spec(phrase)
    sub, prefix = KINDS[kind]
    folder = _data() / "wakeword" / "recordings" / sub
    prefix = prefix or s["takes"]
    with _lock:
        folder.mkdir(parents=True, exist_ok=True)
        n = len(list(folder.glob(f"{prefix}_*.wav"))) + 1
        while (folder / f"{prefix}_{n:03d}.wav").exists():
            n += 1
        save_wav(folder / f"{prefix}_{n:03d}.wav", audio)
    return {"ok": True, "takes": takes(s)}


def _status_file(spec: dict) -> Path:
    return _data() / "wakeword" / "training" / f"{spec['file']}.json"


def _alive(pid) -> bool:
    import psutil

    try:
        return bool(pid) and psutil.Process(int(pid)).is_running()
    except (psutil.Error, ValueError):
        return False


def training_status(phrase: str) -> dict | None:
    """The latest training of this phrase: stage, progress 0..1, done, passed, report, error; None if never."""
    path = _status_file(_spec(phrase))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("done") and not _alive(data.get("pid")) and time.time() - data.get("updated", 0) > 20:
        data.update(done=True, error="Training stopped unexpectedly. Its log is in data/wakeword/training.")
    data["running"] = not data.get("done")
    return data


def start_training(phrase: str) -> dict:
    """Train this phrase in the background, at low priority (MARY keeps working, a little slower)."""
    s = _spec(phrase)
    folder = _data() / "wakeword" / "training"
    for other in folder.glob("*.json"):
        try:
            busy = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not busy.get("done") and _alive(busy.get("pid")):
            raise SettingsError(f"“{busy.get('phrase')}” is training. One at a time.")
    if takes(s) < MIN_TAKES:
        raise SettingsError(f"Record at least {MIN_TAKES} takes of “{s['name']}” first.")
    folder.mkdir(parents=True, exist_ok=True)
    path = _status_file(s)
    log = open(path.with_suffix(".log"), "w", encoding="utf-8")  # noqa: SIM115 - handed to the process
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "mary.wakeword_training.train", "--phrase", s["name"], "--enable", "--status", str(path)],
        cwd=PROJECT_DIR, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
    log.close()
    status = {"phrase": s["name"], "stage": "Starting", "progress": 0.0, "done": False, "pid": proc.pid,
              "started": time.time(), "updated": time.time()}
    path.write_text(json.dumps(status), encoding="utf-8")
    return {**status, "running": True}
