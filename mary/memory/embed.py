"""Meaning for text: the small model MARY's router already uses (fastembed, bge-small, on this PC, no download), loaded
on a background thread so the first request never waits for it (measured here: 3.3 s to load, +173 MB, 14 ms per
query, 19 ms per line in a batch). While it loads, a search is keywords only.

Anything with `model`, `ready`, `embed_docs(texts)` and `embed_query(text)` can stand in for it; HashEmbedder is the
plain one the tests use (no model, no network)."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# bge's own recipe for searching: the question gets this prefix, the passages don't.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _unit(m: np.ndarray) -> np.ndarray:
    m = np.atleast_2d(np.asarray(m, dtype=np.float32))
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


class BgeEmbedder:
    """bge-small through fastembed, loaded in the background (start()) or on first use (load())."""

    def __init__(self, model: str, cache_dir: Path, threads: int = 2):
        self.model = model
        self.tag = f"{model}#text{RECIPE}"   # what the vectors are labelled with (the model and how the text was made)
        self.cache_dir, self.threads = Path(cache_dir), threads
        self._engine = None
        self._lock = threading.Lock()
        self._failed: str | None = None
        self.loaded = threading.Event()

    @property
    def ready(self) -> bool:
        return self.loaded.is_set()

    def start(self) -> None:
        threading.Thread(target=self.load, name="memory-embedder", daemon=True).start()

    def load(self) -> bool:
        with self._lock:
            if self._engine is None and self._failed is None:
                try:
                    from fastembed import TextEmbedding

                    self._engine = TextEmbedding(model_name=self.model, cache_dir=str(self.cache_dir),
                                                 threads=self.threads)
                    self.loaded.set()
                except Exception as e:  # not downloaded and offline, etc.: keywords still work
                    self._failed = str(e)
                    log.warning("meaning search is off (%s); keywords still work", e)
            return self._engine is not None

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        return _unit(np.array(list(self._engine.embed(list(texts))), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_docs([QUERY_PREFIX + text])[0]


_WORD = re.compile(r"[a-z0-9]+")

# What is embedded is the sentence, not the scaffolding around it: a diary line is `Asked: "…". Used: tool. Replied: "…".`,
# and "Asked:", "Used: create_note" and the quotes pull every line's meaning toward every other's.
_DIARY = re.compile(r'^Asked: "(?P<asked>.*?)"(?: \(×\d+\))?\.(?: Used: .*?\.)?(?: Replied: "(?P<reply>.*?)"\.?)?$')
RECIPE = 1   # bump when this changes: the vectors made the old way are then made again


def embedding_text(kind: str, text: str) -> str:
    """The words to make a record's vector from."""
    if kind == "diary":
        m = _DIARY.match(text)
        if m:
            return f"{m['asked']}. {m['reply']}" if m["reply"] else m["asked"]
    return text


class HashEmbedder:
    """Words hashed into a fixed number of slots: no model, deterministic, and 'close' means 'shares words'.
    Enough to test everything around meaning search; the real model is tested where it matters."""

    def __init__(self, dim: int = 256, model: str = "hash-256"):
        self.dim, self.model = dim, model
        self.ready = True

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for w in _WORD.findall(text.lower()):
            v[int.from_bytes(hashlib.blake2b(w.encode(), digest_size=4).digest(), "little") % self.dim] += 1.0
        return v

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        return _unit(np.array([self._one(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_docs([text])[0]
