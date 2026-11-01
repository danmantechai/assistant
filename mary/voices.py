"""Hear MARY's possible voices and pick one.

    python -m mary.voices                   play every female voice, best-rated first
    python -m mary.voices af_bella bf_emma  play just these
    python -m mary.voices --text "..."      use your own sample line
    python -m mary.voices --set af_bella    make it her voice (saved in her persona; or use the settings page)
    python -m mary.voices --blend af_heart:0.6,bf_emma:0.4   try a mix of two voices
    python -m mary.voices --list | --devices
"""
from __future__ import annotations

import argparse
import sys

from mary.config import expand_path, load_config

# Kokoro's own quality grades (hexgrad/Kokoro-82M VOICES.md), best first.
FEMALE = [("af_heart", "A", "American, warm"), ("af_bella", "A-", "American, bright"),
          ("af_nicole", "B-", "American, soft and close"), ("bf_emma", "B-", "British, calm"),
          ("af_aoede", "C+", "American"), ("af_kore", "C+", "American"), ("af_sarah", "C+", "American"),
          ("bf_isabella", "C", "British"), ("af_nova", "C", "American"), ("af_alloy", "C", "American"),
          ("af_sky", "C-", "American"), ("bf_alice", "D", "British"), ("bf_lily", "D", "British")]
SAMPLE = "Hello, I'm MARY. Spotify is playing, and you have two new messages. Shall I go ahead?"


def set_voice(voice: str) -> None:
    """Make it her voice: saved in her persona (your private copy of it), as the settings page does."""
    from mary.settings import store

    key = store.layers().key
    store.save_persona(key, {"voice": {"voice": voice}})
    her = store.persona_info(key)
    backup = " (her backup voice: she speaks with ElevenLabs)" if her["voice"].get("provider") == "elevenlabs" else ""
    print(f"{her['name']}'s Kokoro voice is now {voice}{backup}, saved in {her['folder']}. Restart MARY to hear it.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.voices")
    parser.add_argument("voices", nargs="*")
    parser.add_argument("--text", default=SAMPLE)
    parser.add_argument("--set", metavar="VOICE")
    parser.add_argument("--blend", metavar="A:w,B:w")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--devices", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    cfg = load_config()
    from mary.audio.tts import Speaker, TextToSpeech

    tts = TextToSpeech(cfg, expand_path(cfg.paths.data) / "models")
    if args.set:
        if args.set not in tts.voices:
            sys.exit(f"Unknown voice '{args.set}'. Try --list.")
        set_voice(args.set)
        return
    if args.list:
        print(" ".join(tts.voices))
        return

    speaker = Speaker(cfg.audio.output_device, cfg.tts.volume)
    if args.blend:  # Kokoro voices are style vectors, so a weighted mix is a new voice
        import numpy as np

        mix = [(name, float(w)) for name, w in (p.split(":") for p in args.blend.split(","))]
        style = sum(tts.kokoro.get_voice_style(name) * w for name, w in mix) / sum(w for _, w in mix)
        samples, _ = tts.kokoro.create(args.text, voice=style.astype(np.float32), speed=tts.speed,
                                       lang="en-us")
        print(f"blend {args.blend}")
        speaker.play(samples)
        speaker.wait()
        return

    chosen = [(v, g, d) for v, g, d in FEMALE if not args.voices or v in args.voices]
    chosen += [(v, "?", "") for v in args.voices if v not in {c[0] for c in chosen}]
    print(f"Current voice: {cfg.tts.voice}. Playing {len(chosen)} voice(s)…\n")
    for voice, grade, desc in chosen:
        print(f"  {voice:<12} grade {grade:<3} {desc}", flush=True)
        speaker.play(tts.synth(args.text, voice))
        speaker.wait()
    print("\nPick one with:  python -m mary.voices --set <voice>")
    speaker.close()


if __name__ == "__main__":
    main()
