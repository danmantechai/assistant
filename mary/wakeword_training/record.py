"""Record your voice for the "Hey Mary" and "Hey Mom" wake words (about 6 minutes):

    python -m mary.wakeword_training.record            the full session
    python -m mary.wakeword_training.record --more     add another round of "Hey Mary" takes

Synthetic voices alone teach a wake word poorly (one 2026 trainer measured ~8% recall on a real voice
from synthetic-only data vs ~90% once real recordings were added), so your recordings matter most.
Files go to data/wakeword/recordings/ and never leave this PC.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from mary.config import expand_path, load_config
from mary.wakeword_training.audio_io import RATE, save_wav, usable_take

POSITIVE_STYLES = [
    ("your normal voice", 10), ("a bit quieter, like it's late", 6), ("a bit louder, as if from the next room", 6),
    ("quickly, all in one breath", 5), ("slowly: Hey... Mary", 5), ("turned away from the laptop", 4),
    ("from 2 or 3 metres away", 4),
]
# "Hey Mom" takes: fewer styles, since the model also learns the phrase from many synthetic voices.
MOM_STYLES = [("your normal voice, the way you'd say it (Mom or Mum)", 8), ("a bit quieter", 4),
              ("a bit louder, as if from the next room", 4), ("quickly", 3), ("from 2 or 3 metres away", 3)]
NEAR_MISSES = ["Mary", "Hey", "Hey Larry", "Hey Barry", "Marry me", "Hey Siri", "Hey Maria", "Merry Christmas",
               "Hey Mark", "Very nice", "Hey man", "Okay Mary", "Hey, may I ask", "Hairy", "Hey Gary",
               "Mum", "I'll call my mum later", "Hey Tom"]
READ_ALOUD = ("Read this out in your normal voice: Yesterday I went to the market with my sister. We bought "
              "tomatoes, onions, fresh fish and a bag of rice, then stopped for tea before the rain started. "
              "In the evening we watched the news, talked about work, and planned the weekend.")


def beep() -> None:
    import sounddevice as sd

    t = np.arange(int(RATE * 0.08)) / RATE
    sd.play((np.sin(2 * np.pi * 880 * t) * np.hanning(len(t)) * 0.2).astype(np.float32), RATE)
    sd.wait()


def take(seconds: float, device) -> np.ndarray:
    import sounddevice as sd

    audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="int16", device=device)
    sd.wait()
    return audio[:, 0]


def meter(audio: np.ndarray) -> str:
    peak = int(np.abs(audio).max()) if len(audio) else 0
    bars = min(20, peak * 20 // 20000)
    return "▕" + "█" * bars + " " * (20 - bars) + "▏" + ("  too quiet, say it again" if peak < 1500 else "")


def start(style: str) -> None:
    """Wait for Enter before each way of saying it, with time to walk to the spot for the distant ones."""
    input(f"   — {style}. Press Enter to start… ")
    if "away" in style:
        print("     go there now: the beeps start in 5 seconds, say it after each one")
        time.sleep(5)


RETRY = {"no speech": "didn't hear the words, say it again", "too short": "too short, say it again",
         "cut off": "cut off: start right after the beep"}


def record_takes(prompt: str, count: int, folder, prefix: str, device, seconds: float = 3.0,
                 lead: float = 0.5, vad=None, whole: bool = True) -> None:
    """`whole`: each take must hold the entire phrase (wake-word takes); otherwise any speech will do.
    Loud background noise alone used to pass as a take, and taught the model that noise was "Hey Mary"."""
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob(f"{prefix}_*.wav")))
    done = 0
    while done < count:
        print(f"  [{done + 1}/{count}] say: {prompt}", end="  ", flush=True)
        time.sleep(lead)
        beep()
        audio = take(seconds, device)
        quiet = np.abs(audio).max() < 1500
        why = "" if quiet or vad is None else usable_take(audio, vad)[1]
        if not whole and why != "no speech":
            why = ""
        print(meter(audio) + (f"  {RETRY[why]}" if why else ""))
        if quiet or why:
            continue
        n += 1
        done += 1
        save_wav(folder / f"{prefix}_{n:03d}.wav", audio)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.wakeword_training.record")
    parser.add_argument("--more", action="store_true", help="only record another round of both phrases")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config()
    root = expand_path(cfg.paths.data) / "wakeword" / "recordings"
    device = cfg.audio.input_device
    from mary.audio.vad import SileroVAD

    vad_model = expand_path(cfg.paths.data) / "models" / "silero" / "silero_vad.onnx"
    vad = SileroVAD(vad_model) if vad_model.exists() else None  # checks each take holds the words

    print("Recording for your wake words, \"Hey Mary\" and \"Hey Mom\". Start speaking right after each beep;")
    print("if a take didn't catch the words, it asks again. Each new way of saying it waits for Enter.")
    print("Ctrl+C stops at any time; what's recorded so far is kept.\n")
    input("Press Enter when you're ready… ")
    print("\n1) \"Hey Mary\", in different ways")
    for style, count in POSITIVE_STYLES[:1] if args.more else POSITIVE_STYLES:
        start(style)
        record_takes("Hey Mary", count * (2 if args.more else 1), root / "positive", "hey_mary", device, vad=vad)
    print("\n1b) \"Hey Mom\" (or \"Hey Mum\"), in different ways")
    for style, count in MOM_STYLES[:1] if args.more else MOM_STYLES:
        start(style)
        record_takes("Hey Mom", count * (2 if args.more else 1), root / "positive", "hey_mom", device, vad=vad)
    if args.more:
        print("\nDone. Retrain both:")
        print("   python -m mary.wakeword_training.train --enable")
        print("   python -m mary.wakeword_training.train --phrase mom --enable")
        return

    print("\n2) Things that sound a bit like it, but must NOT wake MARY. Say exactly the words shown.")
    input("   Press Enter to start… ")
    for phrase in NEAR_MISSES:  # lead: time to read the phrase first
        record_takes(phrase, 1, root / "negative", "near_miss", device, lead=1.2, vad=vad, whole=False)

    print(f"\n3) {READ_ALOUD}")
    input("   Press Enter, then read for ~25 seconds… ")
    beep()
    save_wav(root / "negative" / "speech_001.wav", take(25, device))
    print("   got it.")

    print("\n4) Your room's normal sound: stay quiet, or type, or have the TV on (20 seconds)")
    input("   Press Enter to start… ")
    beep()
    save_wav(root / "negative" / "room_001.wav", take(20, device))

    positives = len(list((root / "positive").glob("*.wav")))
    print(f"\nAll done: {positives} wake-word takes. Now train both (10-20 minutes each) and switch them on:")
    print("   python -m mary.wakeword_training.train --enable")
    print("   python -m mary.wakeword_training.train --phrase mom --enable")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped; recordings so far are saved.")
