"""Sentence embeddings for the router: fastembed (ONNX Runtime, no PyTorch) + an on-disk cache."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class Embedder:
    def __init__(self, model: str, cache_dir: Path, threads: int = 2):
        from fastembed import TextEmbedding

        self.model_name = model
        self.model = TextEmbedding(model_name=model, cache_dir=str(cache_dir), threads=threads)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Unit-length vectors, one row per text, so a dot product is the cosine similarity."""
        vectors = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def cached_embeddings(embedder: Embedder, texts: list[str], source: bytes, cache_file: Path) -> np.ndarray:
    """Embed `texts`, reusing cache_file while `source` (intents.yaml) and the model are unchanged."""
    key = hashlib.sha256(source + embedder.model_name.encode()).hexdigest()
    try:
        cached = np.load(cache_file)
        if str(cached["key"]) == key and cached["vectors"].shape[0] == len(texts):
            return cached["vectors"]
    except (OSError, KeyError, ValueError):
        pass
    log.info("embedding %d router examples (intents changed or no cache)", len(texts))
    vectors = embedder.embed(texts)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache_file, key=key, vectors=vectors)
    return vectors
