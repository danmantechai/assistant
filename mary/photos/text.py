"""The words printed in a photo (a sign, a menu, a shop front, a caption on a scan), read by Windows' own OCR on this PC.

It is kept as text on this PC, searched, and never written to a log. A picture with no real words in it (a texture, a face, a
landscape the engine made letters out of) keeps nothing. Measured on a made-up photo-sized picture: 0.06 s at 1600 px.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from mary.photos import read

OCR_PX = 1600
LIMIT = 2000
_WORD = re.compile(r"[^\W\d_]{3,}")
_VOWEL = re.compile(r"[aeiouyáàâäãåæéèêëíìîïóòôöõøúùûüýœ]", re.I)


@lru_cache(maxsize=1)
def available() -> bool:
    """Does Windows have an OCR language for this user (and the engine at all)?"""
    try:
        from winrt.windows.media.ocr import OcrEngine

        return read.on_fresh_thread(lambda: OcrEngine.try_create_from_user_profile_languages() is not None)
    except Exception:
        return False


def useful(text: str) -> str:
    """The text if it reads like words (at least two words with a vowel in them, or a number), else nothing."""
    text = re.sub(r"\s+", " ", text or "").strip()
    words = [w for w in _WORD.findall(text) if _VOWEL.search(w)]
    numbers = re.findall(r"\d{3,}", text)
    if len(words) < 2 and not (words and numbers):
        return ""
    return text[:LIMIT]


def read_text(path: Path) -> str:
    """The words in one picture file ('' when it has none, or OCR isn't available). Raises read.Unreadable like read_file."""
    path = Path(path)
    if not available():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        raise read.Unreadable("couldn't be opened") from None
    d = read.decode(data, path.suffix, OCR_PX)
    from mary.ocr import lines_to_text, ocr_pixels

    rgb = np.asarray(d.image.convert("RGB"))
    bgra = np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], np.full(rgb.shape[:2], 255, dtype=np.uint8)])
    try:
        return useful(lines_to_text(read.on_fresh_thread(lambda: ocr_pixels(np.ascontiguousarray(bgra)))))
    except Exception:
        return ""
