"""Fetch the voice models into data/models (python -m mary.audio.download). Safe to re-run."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

from mary.config import expand_path, load_config

FILES = {
    "kokoro/kokoro-v1.0.onnx":  # full precision: much faster than the int8 file on this CPU
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
    "kokoro/voices-v1.0.bin":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
    "silero/silero_vad.onnx":
        "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
}


def missing(cfg) -> list[str]:
    """Model files voice mode needs that aren't downloaded yet (empty = all there)."""
    models = expand_path(cfg.paths.data) / "models"
    gone = [rel for rel in FILES if not (models / rel).is_file()]
    whisper = models / "whisper" / f"models--Systran--faster-whisper-{cfg.stt.model}"
    if not whisper.is_dir():
        gone.append(f"whisper/{cfg.stt.model}")
    return gone


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ok   {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  ...  {dest.name} {done / total:5.0%}", end="", flush=True)
    tmp.replace(dest)
    print(f"\r  got  {dest.name} ({done / 2**20:.0f} MB)")


def main() -> None:
    cfg = load_config()
    models = expand_path(cfg.paths.data) / "models"
    for rel, url in FILES.items():
        fetch(url, models / rel)

    print("  ...  faster-whisper", cfg.stt.model)
    from faster_whisper import WhisperModel

    WhisperModel(cfg.stt.model, device="cpu", compute_type=cfg.stt.compute_type,
                 download_root=str(models / "whisper"))
    print("  ok   faster-whisper")

    print("  ...  openWakeWord base models")
    import openwakeword.utils

    openwakeword.utils.download_models()
    print("  ok   openWakeWord")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
