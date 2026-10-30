"""What MARY says when "Hey Mom" wakes her: "Yes, dear?", a hum, "I'm here, love." One short reply at random,
never the same twice in a row, rendered by Kokoro once and cached on disk so it plays the moment she wakes.

A reply is text, or text followed by /phonemes/ for sounds Kokoro can't spell: it reads "Mm-hmm" letter by
letter ("em em hum"), so "Mm-hmm? /mˈhm?/" says the phonemes and shows the text.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
_PHONEMES = re.compile(r"^(.*?)\s*/([^/]+)/\s*$")


def parse(entry: str) -> tuple[str, str | None]:
    """'Mm-hmm? /mˈhm?/' -> ('Mm-hmm?', 'mˈhm?'); 'Yes, dear?' -> ('Yes, dear?', None)."""
    m = _PHONEMES.match(entry)
    return (m.group(1) or m.group(2), m.group(2)) if m else (entry.strip(), None)


class WakeReplies:
    def __init__(self, tts, replies: list[str], morning: list[str] = (), cache_dir: Path | None = None,
                 now=datetime.now):
        self.tts, self.cache_dir, self.now = tts, cache_dir, now
        self.replies = [parse(r) for r in replies if r.strip()]
        self.morning = [parse(r) for r in morning if r.strip()]
        self.audio: dict[str, np.ndarray] = {}
        self.last: str | None = None
        self.ready = threading.Event()

    def render_in_background(self) -> None:
        threading.Thread(target=self.render, name="wake-replies", daemon=True).start()

    def render(self) -> None:
        for text, phonemes in self.replies + self.morning:
            try:
                self.audio[text] = self._load_or_synth(text, phonemes)
            except Exception:
                log.exception("couldn't render the wake reply %r", text)
        self.ready.set()

    def _load_or_synth(self, text: str, phonemes: str | None) -> np.ndarray:
        said = phonemes or text
        key = f"{self.tts.voice}_{self.tts.speed}_{zlib.crc32(f'{said}|{bool(phonemes)}'.encode()) % 10**8}.npy"
        path = self.cache_dir / key if self.cache_dir else None
        if path and path.exists():
            return np.load(path)
        if hasattr(self.tts, "kokoro"):  # Kokoro: hums from their phonemes
            lang = "en-gb" if self.tts.voice[:2] in ("bf", "bm") else "en-us"
            samples, _ = self.tts.kokoro.create(said, voice=self.tts.voice, speed=self.tts.speed, lang=lang,
                                                is_phonemes=bool(phonemes))
            audio = samples.astype(np.float32)
        else:  # a cloud voice says the text as written ("Mm-hmm?"); a failure raises, so it's never cached
            audio = self.tts.synth_strict(text)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, audio)
        return audio

    def pick(self) -> tuple[str, np.ndarray] | None:
        """A reply that's ready to play, not the one she said last time; None until any is rendered."""
        pool = self.replies + (self.morning if self.now().hour < 12 else [])
        ready = [text for text, _ in pool if text in self.audio]
        choices = [t for t in ready if t != self.last] or ready
        if not choices:
            return None
        self.last = random.choice(choices)
        return self.last, self.audio[self.last]
