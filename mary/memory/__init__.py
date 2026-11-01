"""MARY's memory: what happened (the diary), what she has learned about you (facts), and later the moments in your
photos, in one local store.

    store = open_store(cfg)             # user/memory/memory.db (None when switched off)
    store.add_fact("My sister is Ama")  # ... and store.search("who is Ama"), store.end(12), store.delete(12)

mary/memory/store.py is the API; the rest is what it is made of: db (the file and its migrations), vectors, embed
(meaning), search, when (dates in words), files (the markdown you can read and edit), diary, recall (what goes into
each request), consolidate (the background pass).
"""
from __future__ import annotations

import copy
from pathlib import Path

from mary.config import expand_path, user_dir

ENABLED = True    # the tests switch it off (tests/conftest.py) and turn it on where they test it
FOLDER: Path | None = None   # a replay points this at a scratch folder, so it never touches the real memory

# The shipped defaults (config.yaml's memory: section explains each); a missing key falls back to these.
DEFAULTS = {
    "enabled": True,
    "model": "BAAI/bge-small-en-v1.5",
    "threads": 2,
    "recall": {"max_items": 5, "max_tokens": 300, "min_relevance": 0.4, "min_relevance_unasked": 0.8,
               "cosine_low": 0.55, "cosine_high": 0.85, "name_relevance": 0.9, "one_word_relevance": 0.6},
    "scoring": {"recency": 0.06, "importance": 0.04,
                "half_life_days": {"diary": 14, "fact": 365, "core": 365, "reflection": 30}},
    "facts": {"same_meaning": 0.90},
    "diary": {"enabled": True, "request_chars": 240, "reply_chars": 160},
    "consolidate": {"enabled": False, "idle_minutes": 10, "only_on_ac": True, "min_lines": 6, "reflections": True},
}


def merged(base: dict, mine: dict) -> dict:
    """mine over base, key by key: a copy all the way down (changing a store's settings must never change the
    defaults)."""
    out = copy.deepcopy(base)
    for key, value in (mine or {}).items():
        out[key] = merged(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict)             else copy.deepcopy(value)
    return out


def folder() -> Path:
    return FOLDER or user_dir() / "memory"


def open_store(cfg, background: bool = False, embedder="default"):
    """The memory store for this config, or None when memory is switched off. The meaning model is only created
    here, not loaded: store.start() loads it on a background thread."""
    from mary.memory.embed import BgeEmbedder
    from mary.memory.store import MemoryStore

    settings = dict((cfg.get("memory") if hasattr(cfg, "get") else None) or {})
    if not ENABLED or not merged(DEFAULTS, settings)["enabled"]:
        return None
    s = merged(DEFAULTS, settings)
    if embedder == "default":
        data = expand_path((cfg.get("paths") or {}).get("data") or "{project}/data")
        embedder = BgeEmbedder(s["model"], data / "models" / "fastembed", s["threads"])
    store = MemoryStore(folder() / "memory.db", embedder=embedder, settings=settings, background=background,
                        backups=user_dir() / "backups")
    from mary.memory.files import MemoryFiles

    MemoryFiles(store, folder())
    return store
