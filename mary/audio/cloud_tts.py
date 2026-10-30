"""MARY's voice from ElevenLabs (tts.provider: elevenlabs): the voice in tts.elevenlabs.voice_id, with Kokoro as
the fallback when ElevenLabs can't be reached, is slow, or the month's credits have run out.

Each sentence is fetched as raw 24 kHz PCM, the format the speaker already plays, so nothing else in the voice
changes. Everything she says goes to ElevenLabs, WhatsApp messages she reads out included.
"""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger(__name__)
_LONG_S = 6 * 3600  # out of credits: don't try again for a while (they reset monthly)


class ElevenLabsVoice:
    def __init__(self, cfg, fallback, client=None, on_problem=None):
        from elevenlabs import ElevenLabs

        self.cfg = cfg.tts
        self.el = cfg.tts.elevenlabs
        self.fallback, self.on_problem = fallback, on_problem
        self.client = client or ElevenLabs()
        self.voice, self.speed = self.el.voice_id, self.el.get("model", "eleven_v4_turbo")  # the wake-reply cache key
        self.name = f"ElevenLabs {self.speed}"
        self.skip_until = 0.0
        self._told = False

    def synth_strict(self, text: str) -> np.ndarray:
        """float32 24 kHz audio of the text in her voice; raises if ElevenLabs fails (nothing falls back)."""
        chunks = self.client.text_to_speech.convert(
            voice_id=self.voice, text=text, model_id=self.speed, output_format="pcm_24000",
            request_options={"timeout_in_seconds": self.el.get("timeout_s", 6), "max_retries": 0})
        pcm = b"".join(chunks)
        return np.frombuffer(pcm[:len(pcm) // 2 * 2], np.int16).astype(np.float32) / 32768.0

    def synth(self, text: str, voice: str | None = None) -> np.ndarray:
        if voice is None and time.monotonic() >= self.skip_until:
            try:
                audio = self.synth_strict(text)
                self._told = False
                return audio
            except Exception as e:
                out_of_credit = "quota" in str(e).lower() or "credits" in str(e).lower()
                self.skip_until = time.monotonic() + (_LONG_S if out_of_credit else self.el.get("retry_after_s", 30))
                log.warning("ElevenLabs voice failed (%s: %s); Kokoro speaks", type(e).__name__, str(e)[:200])
                if self.on_problem and not self._told:
                    self._told = True
                    self.on_problem("ElevenLabs credits are used up, so I'm using my own voice for now."
                                    if out_of_credit else "Can't reach ElevenLabs, so I'm using my own voice for now.")
        return self.fallback.synth(text, voice)


def make_voice(cfg, kokoro, on_problem=None):
    """The voice config.yaml asks for: ElevenLabs in front of Kokoro, or Kokoro alone."""
    from mary.audio.cloud_stt import load_key

    el = cfg.tts.get("elevenlabs") or {}
    if cfg.tts.get("provider", "kokoro") != "elevenlabs" or not el.get("voice_id"):
        return kokoro
    if not load_key():
        if on_problem:
            on_problem("No ElevenLabs key in .env, so I'm using my own voice.")
        return kokoro
    return ElevenLabsVoice(cfg, kokoro, on_problem=on_problem)


def credits_left(client=None) -> tuple[int, int, float] | None:
    """(credits used, the month's credits, when they reset as a unix time), or None if it can't tell."""
    try:
        from elevenlabs import ElevenLabs

        s = (client or ElevenLabs()).user.subscription.get()
        return s.character_count, s.character_limit, s.next_character_count_reset_unix
    except Exception as e:
        log.info("couldn't read the ElevenLabs credit balance: %s", e)
        return None
