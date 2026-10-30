"""Try the wake word live: python -m mary.wakeword_training.test_model [--minutes 10] [--model path]

Shows a live score meter. Say "Hey Mary" a few times (it should fire), then leave it running while
you talk, type and watch TV (it shouldn't). Ctrl+C prints the count of detections.
"""
from __future__ import annotations

import argparse
import sys
import time

from mary.config import expand_path, load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.wakeword_training.test_model")
    parser.add_argument("--model", help="default: wakeword.model from config.yaml")
    parser.add_argument("--threshold", type=float, help="default: the one training chose")
    parser.add_argument("--minutes", type=float, default=0, help="stop after this long (0 = until Ctrl+C)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config()

    from mary.audio.mic import Microphone
    from mary.audio.wakeword import WakeWord, threshold_for

    model = args.model or (str(expand_path(cfg.wakeword.model)) if cfg.wakeword.model.endswith(".onnx")
                           else cfg.wakeword.model)
    threshold = args.threshold or threshold_for(model, cfg.wakeword.threshold)
    ww = WakeWord(model, threshold, cooldown_s=1.5)
    mic = Microphone(cfg.audio.input_device)
    mic.start()
    print(f"Listening with {model} (threshold {threshold:.2f}). Ctrl+C to stop.\n")
    fired, start, peak = 0, time.monotonic(), 0.0
    try:
        while not args.minutes or time.monotonic() - start < args.minutes * 60:
            frame = mic.read()
            if frame is None:
                continue
            hit = ww.detect(frame)
            peak = max(peak * 0.9, ww.last_score)  # decaying peak, so short spikes stay visible
            if hit is not None:
                fired += 1
                print(f"\r  ✓ detected ({hit:.2f})  [{fired} so far]" + " " * 30)
            print(f"\r  {'█' * int(peak * 30):<30} {peak:.2f}", end="", flush=True)
    except KeyboardInterrupt:
        pass
    mic.stop()
    minutes = (time.monotonic() - start) / 60
    print(f"\n\n{fired} detection(s) in {minutes:.1f} minutes.")
    print("If it misses you: record more (python -m mary.wakeword_training.record --more) and retrain.")
    print("If it fires on its own: raise wakeword.threshold in config.yaml (or --threshold here).")


if __name__ == "__main__":
    main()
