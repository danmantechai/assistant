"""Train a wake word: python -m mary.wakeword_training.train [--phrase "Hey Grandma"] [--synthetic-only] [--enable]

Any phrase can be trained (the settings page records and trains it in the browser); mary and mom name the two
this started with.

"Hey Mary" and "Hey Mom" ("Hey Mum" too, as it's said in Ghana and Britain) are two separate models, run
side by side. Measured on synthetic voices: one model for both phrases woke 48.6 times an hour on other
audio against 1.4 for "Hey Mary" alone, because the short, common "Hey Mom" blurred what it listened for.

How it works, all on this PC, no PyTorch:
  1. Positives: your recordings (data/wakeword/recordings/positive) plus Kokoro saying "Hey Mary" in
     ~28 English voices at three speeds. Each clip is augmented: noise, room echo, volume.
  2. Negatives: openWakeWord's 11-hour validation feature set (speech, music, noise), Kokoro saying
     sound-alikes ("Mary", "Hey Larry", "Merry Christmas"…), and your own near-miss/room recordings.
  3. openWakeWord's bundled melspectrogram + embedding models turn every 2 s clip into the same
     16x96 features its own wake words use; a small neural net learns "Hey Mary" vs everything else.
  4. The net is exported as an ONNX model in openWakeWord's format, with a threshold chosen to keep
     false triggers on held-out audio under ~0.5 per hour.
Synthetic speech is cached in data/wakeword/voices, so retraining after recording more is quick.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import zlib
from pathlib import Path

import numpy as np

from mary.config import expand_path, load_config
from mary.wakeword_training.audio_io import RATE, load_wav, trim, usable_take
from mary.wakeword_training.phrases import OTHER_NEG, spec

CLIP_S = 2.0
# What each phrase is and its sound-alikes: mary/wakeword_training/phrases.py (any phrase, not just these two).
# openWakeWord's 11 hours of other audio as features: the negatives every model trains and is tested against.
VALIDATION_URL = ("https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
                  "validation_set_features.npy")
NEG_CLIP_S = 3.0  # sound-alikes sit inside 3 s clips, and every 16-frame window of them is a negative
SPEEDS = (0.85, 1.0, 1.15)


# ----------------------------------------------------------------------------- synthetic speech

def kokoro_clips(phrases: list[str], voices: list[str], speeds, cache: Path, tts,
                 progress=None) -> list[tuple[str, np.ndarray]]:
    """(voice, int16 16 kHz audio) per phrase/voice/speed; cached as .npy files. progress(done, total)."""
    from scipy.signal import resample_poly

    cache.mkdir(parents=True, exist_ok=True)
    out = []
    todo = [(p, v, s) for p in phrases for v in voices for s in speeds]
    for i, (phrase, voice, speed) in enumerate(todo, 1):
        # crc32, not hash(): Python salts str hashes per run, so the old names never matched again.
        f = cache / f"{voice}_{speed}_{zlib.crc32(phrase.encode()) % 10**8}.npy"
        if f.exists():
            audio = np.load(f)
        else:
            lang = "en-gb" if voice[:2] in ("bf", "bm") else "en-us"
            samples, _ = tts.kokoro.create(phrase, voice=voice, speed=speed, lang=lang)
            audio = trim((resample_poly(samples, 2, 3) * 32767).clip(-32768, 32767).astype(np.int16))
            np.save(f, audio)
        out.append((voice, audio))
        if progress:
            progress(i, len(todo))
        if len(todo) > 20 and (i % 25 == 0 or i == len(todo)):
            print(f"\r    {i}/{len(todo)} clips", end="", flush=True)
    if len(todo) > 20:
        print()
    return out


# ----------------------------------------------------------------------------- augmentation

def place(audio: np.ndarray, rng, end_gap=(0.05, 0.35), seconds: float = CLIP_S) -> np.ndarray:
    """Put a short utterance into a clip so it ends shortly before the clip does (like streaming)."""
    n = int(seconds * RATE)
    clip = np.zeros(n, dtype=np.float32)
    a = audio[-n:].astype(np.float32)
    end = n - int(rng.uniform(*end_gap) * RATE)
    start = max(0, end - len(a))
    clip[start:start + len(a[-(end - start):])] = a[-(end - start):]
    return clip


def augment(clip: np.ndarray, rng, noises: list[np.ndarray]) -> np.ndarray:
    x = clip.copy()
    if rng.random() < 0.5:  # a little room: exponentially decaying noise as an impulse response
        rt60 = rng.uniform(0.15, 0.6)
        t = np.arange(int(rt60 * RATE)) / RATE
        ir = rng.standard_normal(len(t)) * np.exp(-6.9 * t / rt60)
        ir[0] = 1.0
        x = np.convolve(x, ir / np.abs(ir).sum() * 3, mode="full")[:len(x)]
    peak = np.abs(x).max() + 1e-6
    x = x / peak * rng.uniform(2000, 26000)  # anywhere from quiet to loud
    if rng.random() < 0.8:
        snr_db = rng.uniform(3, 25)
        if noises and rng.random() < 0.7:
            src = noises[rng.integers(len(noises))]
            start = rng.integers(0, max(1, len(src) - len(x)))
            noise = np.resize(src[start:start + len(x)].astype(np.float32), len(x))
        else:
            noise = np.cumsum(rng.standard_normal(len(x))) * 0.02 + rng.standard_normal(len(x))  # pinkish
        sig_p = np.mean(x ** 2) + 1e-6
        noise_p = np.mean(noise ** 2) + 1e-6
        x = x + noise * np.sqrt(sig_p / (noise_p * 10 ** (snr_db / 10)))
    return np.clip(x, -32768, 32767).astype(np.int16)


def windows(audio: np.ndarray, hop_s: float = 0.5) -> list[np.ndarray]:
    n, hop = int(CLIP_S * RATE), int(hop_s * RATE)
    return [audio[i:i + n] for i in range(0, max(1, len(audio) - n + 1), hop) if len(audio[i:i + n]) == n]


# ----------------------------------------------------------------------------- features / model

def features(clips: list[np.ndarray], af, every_window: bool = False) -> np.ndarray:
    """openWakeWord embeddings, (N, 16, 96): the last 16 frames of each clip, or with every_window
    all 16-frame windows (what live detection sees, one per 80 ms) of every clip."""
    if not clips:
        return np.zeros((0, 16, 96), dtype=np.float32)
    out = []
    for i in range(0, len(clips), 256):
        emb = af.embed_clips(np.stack(clips[i:i + 256]).astype(np.int16), batch_size=64)
        if every_window:
            win = np.lib.stride_tricks.sliding_window_view(emb, (16, 96), axis=(1, 2))[:, :, 0]
            out.append(win.reshape(-1, 16, 96))
        else:
            out.append(emb[:, -16:, :])
    return np.concatenate(out).astype(np.float32)


def stream_peak(clips: list[np.ndarray], af, score) -> np.ndarray:
    """Highest score over all windows of each clip: how it behaves when heard live."""
    if not clips:
        return np.zeros(0)
    emb = af.embed_clips(np.stack(clips).astype(np.int16), batch_size=64)
    win = np.lib.stride_tricks.sliding_window_view(emb, (16, 96), axis=(1, 2))[:, :, 0]
    return score(win.reshape(-1, 16, 96)).reshape(len(clips), -1).max(axis=1)


def export_onnx(mlp, path: Path) -> None:
    """The sklearn net as ONNX in openWakeWord's shape: input [batch, 16, 96] -> output [batch, 1]."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    nodes, inits, prev = [helper.make_node("Flatten", ["input"], ["h0"], axis=1)], [], "h0"
    for i, (w, b) in enumerate(zip(mlp.coefs_, mlp.intercepts_)):
        inits += [numpy_helper.from_array(w.astype(np.float32), f"W{i}"),
                  numpy_helper.from_array(b.astype(np.float32), f"B{i}")]
        nodes.append(helper.make_node("Gemm", [prev, f"W{i}", f"B{i}"], [f"z{i}"]))
        last = i == len(mlp.coefs_) - 1
        nodes.append(helper.make_node("Sigmoid" if last else "Relu", [f"z{i}"], ["output" if last else f"h{i + 1}"]))
        prev = f"h{i + 1}"
    graph = helper.make_graph(nodes, "hey_mary",
                              [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["batch", 16, 96])],
                              [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["batch", 1])], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="mary")
    model.ir_version = 8
    onnx.checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(path))


class Status:
    """How training is going, for the settings page: a small JSON file rewritten as it goes (no file: print only).
    It outlives the page and MARY herself, so a reload or a restart picks the progress up again."""

    def __init__(self, path: str | None, phrase: str):
        self.path = Path(path) if path else None
        self.data = {"phrase": phrase, "stage": "Starting", "progress": 0.0, "done": False, "pid": os.getpid(),
                     "started": time.time()}
        self._written = -1.0
        self.update("Starting")

    def update(self, stage: str | None = None, progress: float | None = None, **more) -> None:
        if stage:
            self.data["stage"] = stage
        if progress is not None:
            self.data["progress"] = round(max(self.data["progress"], min(1.0, progress)), 3)
        self.data.update(more, updated=time.time())
        if self.path is None or (not more and not stage and self.data["progress"] - self._written < 0.01):
            return
        self._written = self.data["progress"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)

    def span(self, start: float, end: float):
        """A progress callback for one stage: done/total mapped onto start..end."""
        return lambda done, total: self.update(progress=start + (end - start) * done / max(1, total))


def ensure_validation_features(path: Path, status: Status) -> None:
    """openWakeWord's validation features (185 MB, from Hugging Face), downloaded the first time a model is trained."""
    if path.exists():
        return
    print("Downloading openWakeWord's background-audio features (185 MB, only the first time)…", flush=True)
    part = path.with_suffix(".part")
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(VALIDATION_URL, timeout=60) as answer, open(part, "wb") as out:  # noqa: S310
        total, done = int(answer.headers.get("Content-Length") or 184_836_608), 0
        while chunk := answer.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            status.update(f"Downloading training data, only the first time ({done >> 20} of {total >> 20} MB)",
                          0.2 * done / total)
    os.replace(part, path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.wakeword_training.train")
    parser.add_argument("--synthetic-only", action="store_true", help="train without your recordings")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--phrase", default="mary", help='the wake phrase, e.g. "Hey Grandma" (or mary / mom)')
    parser.add_argument("--enable", action="store_true",
                        help="switch the wake word on in user/config.yaml if the model passes on your voice")
    parser.add_argument("--status", help="also write progress to this JSON file (the settings page reads it)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        phrase = spec(args.phrase)
    except ValueError as e:
        sys.exit(str(e))
    status = Status(args.status, phrase["name"])
    try:
        run(args, phrase, status)
    except BaseException as e:  # Ctrl+C too: the page shouldn't wait forever
        status.update("Stopped", done=True, error=str(e) or type(e).__name__)
        raise


def run(args, phrase: dict, status: Status) -> None:
    rng = np.random.default_rng(args.seed)
    cfg = load_config()
    data = expand_path(cfg.paths.data)
    root, models = data / "wakeword", data / "models"
    started = time.perf_counter()
    ensure_validation_features(root / "validation_set_features.npy", status)
    status.update("Getting ready", 0.2)

    from openwakeword.utils import AudioFeatures

    from mary.audio.tts import TextToSpeech

    tts = TextToSpeech(cfg, models)
    af = AudioFeatures(inference_framework="onnx")
    voices = [v for v in tts.voices if v[:2] in ("af", "am", "bf", "bm")]
    held_voices = set(voices[::5])  # every 5th voice is only used for testing
    print(f"Kokoro voices: {len(voices)} ({len(held_voices)} held out for testing)")

    # ---- your recordings
    from mary.audio.vad import SileroVAD

    vad_model = models / "silero" / "silero_vad.onnx"
    vad = SileroVAD(vad_model) if vad_model.exists() else None  # finds the words in each take
    rec = root / "recordings"
    takes = sorted((rec / "positive").glob(f"{phrase['takes']}_*.wav"))
    user_pos, skipped = [], {}
    for f in [] if args.synthetic_only else takes:
        audio, why = usable_take(load_wav(f), vad)  # a take of room noise taught the model noise
        if audio is None:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            user_pos.append(audio)
    user_neg_long = [] if args.synthetic_only else [load_wav(f) for f in sorted((rec / "negative").glob("*.wav"))]
    note = f" ({', '.join(f'{n} {why}' for why, n in skipped.items())}: skipped)" if skipped else ""
    print(f"Your recordings: {len(user_pos)} usable \"{phrase['name']}\" takes{note}, "
          f"{len(user_neg_long)} negative files")
    if user_pos:
        print(f"  the phrase lasts {np.median([len(a) for a in user_pos]) / RATE:.2f} s (median) once trimmed")
    if not user_pos and not args.synthetic_only:
        print("  (none yet: record on the settings page (Wake word), or with "
              f"`python -m mary.wakeword_training.record --phrase \"{phrase['name']}\"`)")
    # Background noise mixed into the clips: your room's sound only. The read-aloud recording is speech, and
    # mixed under "Hey Mary" it taught the model that any talking is the wake word (40-104 false wake-ups/h).
    noises = [] if args.synthetic_only else [load_wav(f) for f in sorted((rec / "negative").glob("room_*.wav"))]

    print(f"Synthesising \"{phrase['name']}\"…")
    status.update(f"Saying “{phrase['name']}” in many voices", 0.22)
    pos_synth = kokoro_clips(phrase["pos"], voices, SPEEDS, root / "voices", tts, status.span(0.22, 0.42))
    print("Synthesising sound-alikes…")
    status.update("Saying the sound-alikes", 0.42)
    neg_synth = kokoro_clips(phrase["neg"], voices, (1.0,), root / "voices", tts, status.span(0.42, 0.55))
    for i, p in enumerate(OTHER_NEG, 1):
        neg_synth += kokoro_clips([p], list(rng.choice(voices, size=6, replace=False)), (1.0,), root / "voices", tts)
        status.update(progress=0.55 + 0.05 * i / len(OTHER_NEG))

    # ---- build clips
    train_pos, test_pos_synth = [], []
    for voice, audio in pos_synth:
        target = test_pos_synth if voice in held_voices else train_pos
        for _ in range(1 if voice in held_voices else 3):
            target.append(augment(place(audio, rng), rng, noises))
    rng.shuffle(user_pos)
    n_test_user = len(user_pos) // 5
    test_pos_user = [augment(place(a, rng), rng, noises) for a in user_pos[:n_test_user]]
    for a in user_pos[n_test_user:]:
        train_pos += [augment(place(a, rng), rng, noises) for _ in range(12)]  # your voice weighs most
    def neg_clip(a):
        return augment(place(a, rng, (0.2, 1.5), seconds=NEG_CLIP_S), rng, noises)

    train_neg_clips = [neg_clip(a) for v, a in neg_synth if v not in held_voices for _ in range(2)]
    test_near = [neg_clip(a) for v, a in neg_synth if v in held_voices]
    train_neg_long = []
    for a in user_neg_long:
        if len(a) <= RATE * 3:   # a near-miss take
            train_neg_clips += [neg_clip(trim(a, vad=vad)) for _ in range(6)]
        else:                    # read-aloud speech / room sound
            train_neg_long += windows(a)
    print(f"Clips: {len(train_pos)} positive, {len(train_neg_clips)} sound-alike + {len(train_neg_long)} "
          "room/speech negatives (+ validation features)")

    print("Computing features…")
    status.update("Listening to every clip", 0.6)
    x_pos = features(train_pos, af)
    x_neg = np.concatenate([features(train_neg_clips, af, every_window=True), features(train_neg_long, af)])
    x_test_synth, x_test_user = features(test_pos_synth, af), features(test_pos_user, af)
    # openWakeWord's validation set is one long stream of 80 ms embedding frames (speech, music, noise).
    # The first 80% trains (16-frame windows every 0.64 s); the last 20% is replayed frame by frame
    # like live audio to count false triggers.
    val = np.load(root / "validation_set_features.npy").astype(np.float32)
    cut = int(len(val) * 0.8)
    view = np.lib.stride_tricks.sliding_window_view
    x_val_train = view(val[:cut], (16, 96))[::8, 0]
    x_val_stream = view(val[cut:], (16, 96))[:, 0]
    hours = len(x_val_stream) * 0.08 / 3600

    # ---- train
    from sklearn.neural_network import MLPClassifier

    negatives = np.concatenate([x_neg, x_val_train])
    reps = max(1, int(len(negatives) * 0.25 / max(1, len(x_pos))))  # oversample positives to ~20%
    x = np.concatenate([np.repeat(x_pos, reps, axis=0), negatives]).reshape(-1, 16 * 96)
    y = np.concatenate([np.ones(len(x_pos) * reps), np.zeros(len(negatives))])
    print(f"Training on {len(x)} windows ({len(x_pos)}x{reps} positive)…")
    status.update("Training", 0.7)
    mlp = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-4, batch_size=256, learning_rate_init=1e-3,
                        max_iter=40, random_state=args.seed, verbose=False)
    mlp.fit(x, y)

    # Hard-negative mining: replay the training part of the stream every 160 ms, collect the windows
    # that fooled the first model, and train again with them counted several times.
    dense = view(val[:cut], (16, 96))[::2, 0]
    mined = []
    for i in range(0, len(dense), 20000):
        chunk = dense[i:i + 20000]
        s = mlp.predict_proba(chunk.reshape(-1, 16 * 96))[:, 1]
        mined.append(chunk[s >= 0.3])
    mined = np.concatenate(mined) if mined else np.zeros((0, 16, 96), np.float32)
    print(f"Round 2: {len(mined)} hard negatives found, retraining…")
    status.update("Training again on what fooled it", 0.85)
    if len(mined):
        hard = np.repeat(mined, 4, axis=0).reshape(-1, 16 * 96)
        x = np.concatenate([x, hard])
        y = np.concatenate([y, np.zeros(len(hard))])
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-4, batch_size=256, learning_rate_init=1e-3,
                            max_iter=40, random_state=args.seed, verbose=False)
        mlp.fit(x, y)

    # ---- choose the threshold and report
    def scores(feats):
        return mlp.predict_proba(feats.reshape(-1, 16 * 96))[:, 1] if len(feats) else np.zeros(0)

    def triggers(stream_scores, t, cooldown_frames=25):
        """Detections when replayed as a live stream (one window per 80 ms, 2 s cooldown)."""
        count, last = 0, -cooldown_frames
        for i in np.flatnonzero(stream_scores >= t):
            if i - last >= cooldown_frames:
                count, last = count + 1, i
        return count

    status.update("Testing it", 0.95)
    s_val = np.concatenate([scores(x_val_stream[i:i + 20000]) for i in range(0, len(x_val_stream), 20000)])
    s_synth, s_user = scores(x_test_synth), scores(x_test_user)
    s_near = stream_peak(test_near, af, scores)  # held-out voices, judged like live audio
    # Scores bunch up near 1, so the search goes on past 0.99 in finer steps (0.9925 … 0.9999); stopping at
    # 0.98 left every model trained on real takes at a fallback of 0.95, waking 28-57 times an hour.
    grid = np.concatenate([np.arange(0.5, 0.99, 0.01), 1 - np.logspace(-2, -4, 17)[1:]])
    # With MARY's speech-recognition check, a false wake-up costs ~1 s of CPU and is dropped, so the
    # threshold can stay lower (more of your takes caught) for up to 20 of them an hour; without it, 0.5.
    checked = bool(cfg.wakeword.get("verify"))
    candidates = [t for t in grid if triggers(s_val, t) / hours <= (20.0 if checked else 0.5)]
    threshold = float(candidates[0]) if candidates else float(grid[-1])
    print("\nThreshold trade-off (your held-out takes caught, false wake-ups per hour on other audio):")
    for t in (0.9, 0.95, 0.99, 0.995, 0.999, 0.9999):
        caught = f"{(s_user >= t).mean():.0%}" if len(s_user) else "-"
        print(f"  {t:<7} {caught:>5}  {triggers(s_val, t) / hours:6.1f}/h")
    report = {
        "threshold": round(threshold, 6),  # the grid near 1 steps in the 5th decimal
        "recall_your_voice_heldout": round(float((s_user >= threshold).mean()), 3) if len(s_user) else None,
        "recall_synthetic_voices_heldout": round(float((s_synth >= threshold).mean()), 3),
        "false_triggers_per_hour_heldout": round(triggers(s_val, threshold) / hours, 2),
        "heldout_audio_hours": round(hours, 1),
        "sound_alikes_triggered": f"{int((s_near >= threshold).sum())}/{len(s_near)}",
        "your_recordings": len(user_pos), "trained_s": round(time.perf_counter() - started),
    }
    ok, why = good_enough(report, checked)
    report["passed_on_your_voice"] = ok  # MARY only loads a phrase that passed
    report["needs_check"] = ok and not good_enough(report)[0]  # passed only thanks to wakeword.verify
    out = destination(models / "wakeword" / f"{phrase['file']}.onnx", ok)
    export_onnx(mlp, out)
    out.with_suffix(".json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\nResult:")
    for k, v in report.items():
        print(f"  {k:<34} {v}")
    print(f"\nSaved {out}")
    if out.stem.endswith(".failed"):
        print("  (your previous model, which passed, stays in use)")
    print(f"Try it live:  python -m mary.wakeword_training.test_model --model {out}")
    status.update("Done", 1.0, done=True, passed=ok, why=why, report=report)
    if not ok:
        print(f"\n✗ Not switched on: {why}. Record more takes (the settings page, or "
              f"python -m mary.wakeword_training.record --phrase \"{phrase['name']}\") and train again.")
    elif args.enable:
        set_enabled(True)
        print(f"\n✓ Switched on in user/config.yaml. Restart MARY, then say \"{phrase['name']}\".")
    else:
        print("\n✓ Passed. Switch it on: train again with --enable, or on the settings page (Wake word).")


def destination(out: Path, ok: bool) -> Path:
    """Where a new model goes: never over one that passed on the user's voice, unless it passed too."""
    from mary.audio.wakeword import passed

    if not ok and out.exists() and passed(str(out)):
        return out.with_name(f"{out.stem}.failed.onnx")
    return out


def good_enough(report: dict, checked: bool = False) -> tuple[bool, str]:
    """Wakes on most of your held-out takes, rarely on anything else. `checked`: MARY confirms each
    detection with speech recognition (wakeword.verify), which drops false wake-ups for ~1 s of CPU each,
    so up to 30 an hour is fine; without that check, more than one an hour is too many."""
    recall, fa = report.get("recall_your_voice_heldout"), report.get("false_triggers_per_hour_heldout")
    limit = 30.0 if checked else 1.0
    if recall is None:
        return False, "it has never heard your voice"
    if recall < 0.8:
        return False, f"it caught only {recall:.0%} of your test takes"
    if fa is not None and fa > limit:
        return False, f"it woke {fa} times an hour on other audio"
    return True, ""


def set_enabled(on: bool) -> None:
    """wakeword.enabled in user/config.yaml (yours; the shipped config.yaml stays as it is)."""
    from mary.settings import store

    store.save_settings({"wakeword.enabled": on})


if __name__ == "__main__":
    main()
