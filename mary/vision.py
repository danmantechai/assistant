"""Vision: a model that looks at a picture (a window, part of one, a photo) and answers a question about it. It's
for what has to be seen: how something looks, what a photo shows, an app that exposes no text. Text on screen is
read exactly by read_screen instead (mary/page.py).

A socket like the brain's: `look(png, question)` asks whichever model is set:
  vision.model in config.yaml, else the brain itself when it can see images, else the smallest model on this PC
  that can (Ollama says which can: "vision" in its capabilities).
A model on this PC keeps the picture here, but is slow on a CPU (tens of seconds). A cloud model (a name ending in
"cloud") is fast but sends the picture to ollama.com, so it's only used when set by name.
"""
from __future__ import annotations

import io
import logging
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)

MAX_SIDE = 1280      # pictures are scaled down to this; plenty for a model, and far fewer tokens
QUESTION_LIMIT = 500


@lru_cache(maxsize=32)
def _can_see(host: str, model: str) -> bool:
    try:
        caps = httpx.post(f"{host}/api/show", json={"model": model}, timeout=5).json().get("capabilities") or []
    except (httpx.HTTPError, ValueError):
        return False
    return "vision" in caps


def model_for(cfg) -> str | None:
    """The model that looks: set by name, else the brain if it can see, else the smallest local one that can."""
    from mary.llm.server import is_cloud

    host = cfg.llm.host
    named = str((cfg.get("vision") or {}).get("model") or "").strip()
    if named:
        return named if _can_see(host, named) else None
    brain = (cfg.get("brain") or {}).get("provider", "ollama")
    if brain == "ollama" and _can_see(host, cfg.llm.model):
        return cfg.llm.model
    try:
        listed = httpx.get(f"{host}/api/tags", timeout=4).json().get("models") or []
    except (httpx.HTTPError, ValueError):
        return None
    local = sorted((m for m in listed if not m.get("remote_host") and not is_cloud(m.get("model", ""))),
                   key=lambda m: m.get("size") or 0)
    return next((m["model"] for m in local if _can_see(host, m["model"])), None)


def shrink(png: bytes) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def look(png: bytes, question: str, cfg, model: str | None = None) -> str:
    """The model's answer to `question` about the picture."""
    import ollama

    model = model or model_for(cfg)
    if model is None:
        raise RuntimeError("no model that can see images is set up")
    reply = ollama.Client(host=cfg.llm.host, timeout=180).chat(
        model=model, think=False, options={"temperature": 0.2, "num_predict": 400},
        messages=[{"role": "user", "content": question[:QUESTION_LIMIT] + "\nAnswer in two or three short "
                   "sentences, only from what the picture shows.", "images": [shrink(png)]}])
    return (reply.message.content or "").strip()
