"""Make sure the Ollama server is up and the model is pulled."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


class OllamaUnavailable(RuntimeError):
    pass


def reachable(host: str, timeout: float = 2) -> bool:
    try:
        return httpx.get(f"{host}/api/version", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
    return str(default) if default.exists() else None


def ensure_server(host: str, server_env: dict | None = None, wait_s: float = 20) -> bool:
    """Start `ollama serve` in the background if nothing is listening. True if we started it."""
    if reachable(host):
        return False
    exe = _ollama_exe()
    if not exe:
        raise OllamaUnavailable("Ollama isn't installed. Get it from https://ollama.com/download "
                                "(or: winget install Ollama.Ollama).")
    env = {**os.environ, **{k: str(v) for k, v in (server_env or {}).items()}}
    log.info("starting %s serve", exe)
    subprocess.Popen([exe, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if reachable(host):
            return True
        time.sleep(0.5)
    raise OllamaUnavailable("Started Ollama but it isn't answering. Try running `ollama serve` yourself.")


def ensure_model(client, model: str) -> None:
    names = {m.model for m in client.list().models}
    if model not in names and f"{model}:latest" not in names:
        raise OllamaUnavailable(f"The model '{model}' isn't downloaded yet. Run: ollama pull {model}")


def main() -> None:
    """python -m mary.llm.server: start Ollama if needed and download MARY's model (setup.bat runs this)."""
    import sys

    import ollama

    from mary.config import load_config

    cfg = load_config().llm
    try:
        ensure_server(cfg.host, dict(cfg.server_env or {}))
    except OllamaUnavailable as e:
        print(f"  {e}")
        sys.exit(1)
    client = ollama.Client(host=cfg.host)
    if cfg.model in {m.model for m in client.list().models}:
        print(f"  ok   {cfg.model}")
        return
    shown = ""
    for progress in client.pull(cfg.model, stream=True):
        done, total = progress.completed or 0, progress.total or 0
        line = f"  ...  {cfg.model} {done / total:5.0%}" if total else f"  ...  {cfg.model} {progress.status}"
        if line != shown:
            print(f"\r{line:<60}", end="", flush=True)
            shown = line
    print(f"\r{'  got  ' + cfg.model:<60}")


if __name__ == "__main__":
    main()
