"""Picture embeddings (Phase 4): searching by what a photo shows ("pink car", "beach at sunset") and finding visually
similar photos, using a CPU CLIP image–text model through fastembed (`Qdrant/clip-ViT-B-32-vision` and
`Qdrant/clip-ViT-B-32-text`, 512 floats, unit length).

Vectors live in the shared `vectors` table under `space='image'`, `ref_table='photos'`, beside each photo record.
The models are never downloaded behind your back: `available()` checks the local disk (`data/models/fastembed`), and
`fetch()` downloads them only when asked. Tests use a deterministic offline stand-in (`synth.StandInClip`).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from mary.config import PROJECT_DIR

log = logging.getLogger(__name__)

ENABLED = True   # switched off in tests/conftest.py unless testing CLIP, just like memory.ENABLED and photos.ENABLED
IMAGE_MODEL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"
DIM = 512
TAG = "clip-vit-b-32-v1"
EXPECTED_MB = 590.0
MODEL_DIR = Path(os.environ.get("FASTEMBED_CACHE_PATH") or (PROJECT_DIR / "data" / "models" / "fastembed"))

_DIRS = (
    ("clip-ViT-B-32-vision", "models--Qdrant--clip-ViT-B-32-vision", 340_000_000),
    ("clip-ViT-B-32-text", "models--Qdrant--clip-ViT-B-32-text", 250_000_000),
)


def unit(v: np.ndarray) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(a))
    return (a / n) if n > 1e-12 else a


def _dir_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _has_onnx(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(f.suffix == ".onnx" and f.stat().st_size > 1_000_000 for f in path.rglob("*.onnx"))


def models_folder(lib) -> Path:
    from mary.config import expand_path

    cfg = (lib.settings.get("clip") if hasattr(lib, "settings") else None) or {}
    raw = cfg.get("models") or os.environ.get("FASTEMBED_CACHE_PATH") or "{project}/data/models/fastembed"
    return Path(expand_path(raw))


def _resolve_folder(folder=MODEL_DIR) -> Path:
    if hasattr(folder, "settings") or hasattr(folder, "store"):
        return models_folder(folder)
    return Path(folder)


def models_status(folder=MODEL_DIR) -> dict:
    """Whether the CLIP image and text ONNX models are on disk in `folder`."""
    folder = _resolve_folder(folder)
    files = []
    total = 0
    ready = True
    for label, dirname, approx in _DIRS:
        sub = folder / dirname
        ok = _has_onnx(sub)
        size = _dir_bytes(sub) if ok else approx
        total += size
        ready = ready and ok
        files.append({"name": f"{label}.onnx", "bytes": size, "present": ok})
    return {"ready": ready, "mb": round(total / 1_000_000, 1), "files": files, "folder": str(folder)}


def available(folder=MODEL_DIR) -> bool:
    return models_status(folder)["ready"]


def fetch(folder=MODEL_DIR, get=None) -> dict:
    """Download the CLIP image and text models into `folder`. Called only when the person asks."""
    folder = _resolve_folder(folder)
    folder.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from fastembed import ImageEmbedding, TextEmbedding

    ImageEmbedding(IMAGE_MODEL, cache_dir=str(folder))
    TextEmbedding(TEXT_MODEL, cache_dir=str(folder))
    st = models_status(folder)
    if not st["ready"]:
        raise OSError("The picture search models did not finish downloading.")
    return st


_CACHE_LOCK = threading.Lock()
_IMG_SESSIONS: dict[tuple[str, int], object] = {}
_TXT_SESSIONS: dict[tuple[str, int], object] = {}


def load(lib) -> "Engine | None":
    """The real CLIP engine for `lib`, or None when switched off or the model files are not on disk yet."""
    cfg = (lib.settings.get("clip") if hasattr(lib, "settings") else None) or {}
    if not ENABLED or not cfg.get("enabled", True):
        return None
    folder = models_folder(lib)
    return Engine(folder, int(cfg.get("threads", 2))) if available(folder) else None


class Engine:
    """CPU CLIP image and text encoder backed by fastembed ONNX Runtime."""

    tag = TAG
    dim = DIM

    def __init__(self, folder: Path = MODEL_DIR, threads: int = 2):
        self.folder = Path(folder)
        self.threads = max(1, int(threads))
        self._lock = threading.Lock()

    def available(self) -> bool:
        return available(self.folder)

    def _key(self) -> tuple[str, int]:
        return (os.path.normcase(os.path.abspath(str(self.folder))), self.threads)

    def _ensure_image(self):
        if not self.available():
            return None
        key = self._key()
        with _CACHE_LOCK:
            model = _IMG_SESSIONS.get(key)
            if model is None:
                from fastembed import ImageEmbedding

                model = ImageEmbedding(IMAGE_MODEL, cache_dir=str(self.folder), threads=self.threads, local_files_only=True)
                _IMG_SESSIONS[key] = model
            return model

    def _ensure_text(self):
        if not self.available():
            return None
        key = self._key()
        with _CACHE_LOCK:
            model = _TXT_SESSIONS.get(key)
            if model is None:
                from fastembed import TextEmbedding

                model = TextEmbedding(TEXT_MODEL, cache_dir=str(self.folder), threads=self.threads, local_files_only=True)
                _TXT_SESSIONS[key] = model
            return model

    def embed_image(self, source: Image.Image | Path | str) -> np.ndarray | None:
        with self._lock:
            model = self._ensure_image()
            if model is None:
                return None
            item = source if isinstance(source, Image.Image) else str(source)
            vec = next(iter(model.embed([item])), None)
            return unit(vec) if vec is not None else None

    def embed_text(self, query: str) -> np.ndarray | None:
        q = " ".join(str(query or "").split())
        if not q:
            return None
        with self._lock:
            model = self._ensure_text()
            if model is None:
                return None
            vec = next(iter(model.embed([q])), None)
            return unit(vec) if vec is not None else None
