"""Check how well MARY can hear you:

    python -m mary.audio.check --mic            speak for 6 s into MARY's own microphone path
    python -m mary.audio.check --mic 10         ...for 10 s
    python -m mary.audio.check "Recording.m4a"  analyse a recording (m4a, mp3, wav, …)

Prints loudness, clipping, background noise and signal-to-noise ratio, where speech is detected,
and what Whisper hears with and without MARY's noise filter. The audio MARY heard is saved to
data/audio_check/ so you can listen to it. Nothing leaves this PC.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from mary.config import expand_path, load_config


def load_any(path: Path) -> np.ndarray:
    """Any audio file -> 16 kHz mono int16 (decoded with PyAV, which faster-whisper already needs)."""
    import av
    from scipy.signal import resample_poly

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        rate = stream.rate
        chunks = [f.to_ndarray().astype(np.float32) for f in container.decode(audio=0)]
    x = np.concatenate([c if c.ndim == 2 else c[None, :] for c in chunks], axis=1).mean(axis=0)
    if np.abs(x).max() > 2:  # integer sample formats
        x = x / 32768.0
    if rate != 16000:
        x = resample_poly(x, 16000, rate)
    return np.clip(x * 32767, -32768, 32767).astype(np.int16)


def record_mic(seconds: float, device) -> np.ndarray:
    from mary.audio.mic import Microphone

    mic = Microphone(device)
    mic.start()
    print(f"Speak now ({seconds:.0f} s)…", flush=True)
    frames, need = [], int(seconds * 16000 / 1280)
    while len(frames) < need:
        frame = mic.read(timeout=2.0)
        if frame is not None:
            frames.append(frame)
    mic.stop()
    return np.concatenate(frames)


def report(audio: np.ndarray, cfg) -> None:
    from mary.audio.stt import SpeechToText, prepare
    from mary.audio.vad import SileroVAD
    from mary.wakeword_training.audio_io import save_wav

    x = audio.astype(np.float32) / 32768
    frames = x[: len(x) // 320 * 320].reshape(-1, 320)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    floor, loud = np.percentile(db, 10), np.percentile(db, 95)
    clipped = np.mean(np.abs(audio.astype(np.int32)) >= 32000) * 100
    print(f"\nLength {len(audio) / 16000:.1f} s · peak {20 * np.log10(np.abs(x).max() + 1e-9):.1f} dBFS · "
          f"clipped {clipped:.2f}% of samples")
    print(f"Background {floor:.0f} dBFS (a quiet room is about -50) · speech {loud:.0f} dBFS · "
          f"speech is {loud - floor:.0f} dB above the background")
    tips = []
    if clipped > 0.2:
        tips.append("Clipping: lower the mic level to ~70% (Settings > System > Sound > Input > Volume).")
    if loud - floor < 15:
        tips.append("Speech is close to the background level: get closer to the mic, or quieter surroundings.")
    if loud < -35:
        tips.append("Very quiet: raise the mic level or speak closer.")

    models = expand_path(cfg.paths.data) / "models"
    vad = SileroVAD(models / "silero" / "silero_vad.onnx")
    probs = [vad.probability(audio[i:i + 1280]) for i in range(0, len(audio) - 1279, 1280)]
    print("Speech detected (# = speech, . = not, one mark per 80 ms):")
    print("  " + "".join("#" if p >= cfg.vad.threshold else "." for p in probs))

    stt = SpeechToText(cfg, models)
    strength = cfg.stt.get("denoise_strength", 2.5)
    as_mary = prepare(audio, strength)[0]
    raw, cleaned = stt.hear(audio), stt.hear(as_mary)
    print(f"Whisper, raw audio:         {raw.raw!r}  (confidence {raw.confidence:.2f})")
    print(f"Whisper, as MARY hears it:  {cleaned.raw!r}  (confidence {cleaned.confidence:.2f}"
          f"{', would be rejected: ' + cleaned.rejected if cleaned.rejected else ''})")
    out = expand_path(cfg.paths.data) / "audio_check"
    save_wav(out / "check_raw.wav", audio)
    save_wav(out / "check_as_mary_hears_it.wav", as_mary)
    print(f"Saved both versions in {out} so you can listen.")
    for tip in tips:
        print("  ⚠ " + tip)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.audio.check")
    parser.add_argument("file", nargs="?", help="an audio file to analyse")
    parser.add_argument("--mic", nargs="?", const=6.0, type=float, help="record N seconds (default 6)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config()
    if args.mic:
        audio = record_mic(args.mic, cfg.audio.input_device)
    elif args.file:
        audio = load_any(Path(args.file))
    else:
        parser.error("give a file, or --mic")
    report(audio, cfg)


if __name__ == "__main__":
    main()
