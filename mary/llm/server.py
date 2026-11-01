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


def is_cloud(model: str) -> bool:
    """Ollama's cloud models are named so (beam:cloud, gpt-oss:120b-cloud)."""
    return model.endswith((":cloud", "-cloud"))


def ensure_model(host: str, model: str) -> dict:
    """The model is added to this Ollama: where it runs and the thinking levels it offers.
    {"cloud": bool, "levels": ["low", "medium", …] or []}; levels are empty for a plain on/off thinker."""
    try:
        listed = httpx.get(f"{host}/api/tags", timeout=10).json().get("models") or []
    except (httpx.HTTPError, ValueError) as e:
        raise OllamaUnavailable(f"Ollama didn't answer ({e})") from e
    found = next((m for m in listed if m.get("model") in (model, f"{model}:latest")), None)
    if found is None:
        raise OllamaUnavailable(f"The model '{model}' isn't downloaded yet. Run: ollama pull {model}")
    try:
        thinking = httpx.post(f"{host}/api/show", json={"model": model}, timeout=10).json().get("thinking") or {}
    except (httpx.HTTPError, ValueError):
        thinking = {}
    return {"cloud": bool(found.get("remote_host")) or is_cloud(model),
            "levels": [v for v in thinking.get("values") or [] if isinstance(v, str)]}


def main() -> None:
    """python -m mary.llm.server: start Ollama if needed and download MARY's model (setup.bat runs this)."""
    import sys

    import ollama

    from mary.config import load_config

    full = load_config()
    if (full.get("brain") or {}).get("provider", "ollama") != "ollama":
        print("  ok   the brain is a service (brain.provider), nothing to download")
        return
    cfg = full.llm
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
