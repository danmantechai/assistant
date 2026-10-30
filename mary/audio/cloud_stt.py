"""Hearing in the cloud: ElevenLabs Scribe v2 (stt.provider: elevenlabs), with the local Whisper as the fallback.

Only what you say after the wake word goes out; "Hey Mom" and its double-check stay on this PC. The recording
is sent as raw 16 kHz PCM (Scribe's low-latency format) with up to 100 key terms: your own words, then the apps
with a window open, then the installed ones. When Scribe doesn't answer in time, errors, or there's no key,
the local Whisper hears it instead, and after a failure the cloud is skipped for a while, so being offline
costs one wait rather than one per request.

The key comes from .env (ELEVENLABS_API_KEY, written there by the ElevenLabs CLI); it's never in the code.
"""
from __future__ import annotations

import logging
import time

import numpy as np

from mary.audio.stt import Heard, SpeechToText, nothing_said, prepare
from mary.config import PROJECT_DIR

log = logging.getLogger(__name__)
KEYTERMS = 100  # Scribe's limit; each term at most 50 characters and 5 words


def load_key() -> bool:
    """Put .env's ELEVENLABS_API_KEY into the environment. True if a key is there."""
    import os

    from dotenv import load_dotenv

    load_dotenv(PROJECT_DIR / ".env")
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def keyterms(likely: list[str], installed: list[tuple[str, int]], limit: int = KEYTERMS,
             max_chars: int = 50) -> list[str]:
    """Batch Scribe takes 100 terms of up to 50 characters; the realtime model 50 of up to 20."""
    terms = [t for t in dict.fromkeys([*likely, *(n for n, _ in installed)])
             if t and len(t) <= max_chars and len(t.split()) <= 5]
    return terms[:limit]


class ScribeEar:
    """Same face as SpeechToText (hear, transcribe, warm_up), so the voice loop doesn't know which it has."""

    def __init__(self, cfg, local: SpeechToText, client=None, on_problem=None):
        from elevenlabs import ElevenLabs

        self.cfg, self.local, self.on_problem = cfg.stt.elevenlabs, local, on_problem
        self.client = client or ElevenLabs()
        # The recording as it is: the noise filter was tuned for the small local Whisper. The fallback filters,
        # unless the browser-style clean-up already ran (mary/audio/clean.py), which does its job.
        cleaned = (cfg.get("audio") or {}).get("clean", {}).get("enabled", False)
        self.denoise_strength = cfg.stt.get("denoise_strength", 2.5) if self.cfg.get("denoise") else 0.0
        self.local_strength = 0.0 if cleaned else cfg.stt.get("denoise_strength", 2.5)
        self.min_confidence = self.cfg.get("min_confidence")  # mean word log-probability; None = not used
        self.skip_until = 0.0
        self.live_skip_until = 0.0
        self._live_loop = None
        self._told = False
        live = self.cfg.get("live_model")
        self.name = f"ElevenLabs {live} live" if live else f"ElevenLabs {self.cfg.get('model', 'scribe_v2')}"

    def warm_up(self) -> None:
        self.local.warm_up()

    def open_live(self, on_partial=None):
        """A LiveSession streaming to Scribe's realtime model, or None (no live_model, or it failed lately)."""
        model = self.cfg.get("live_model")
        if not model or time.monotonic() < max(self.skip_until, self.live_skip_until):
            return None
        from elevenlabs import AudioFormat, CommitStrategy, RealtimeAudioOptions

        from mary.audio.live_stt import LiveLoop, LiveSession

        if self._live_loop is None:
            self._live_loop = LiveLoop()
        likely, installed = self.local.names()
        options = RealtimeAudioOptions(
            model_id=model, audio_format=AudioFormat.PCM_16000, sample_rate=16000,
            commit_strategy=CommitStrategy.MANUAL, language_code="en", no_verbatim=True,
            keyterms=keyterms(likely, installed, limit=50, max_chars=20),
            # Ignores the room's background talk and noise: yours is loud (about 12 dB under your voice).
            filter_background_audio=bool(self.cfg.get("filter_background", True)))
        timeout = self.cfg.get("timeout_s", 4)

        async def connect():
            import asyncio

            return await asyncio.wait_for(self.client.speech_to_text.realtime.connect(options), timeout)

        return LiveSession(self._live_loop, connect, on_partial)

    def hear_live(self, session) -> Heard | None:
        """The live session's final text, or None (then the recording is heard as usual)."""
        heard = session.finish(self.cfg.get("timeout_s", 4))
        if heard is None:
            self.live_skip_until = time.monotonic() + self.cfg.get("retry_after_s", 30)
        elif heard.rejected:
            heard.text = ""
        return heard

    def hear(self, audio: np.ndarray, answer: bool = False) -> Heard:
        if time.monotonic() >= self.skip_until:
            try:
                heard = self._scribe(audio, answer)
                self._told = False
                return heard
            except Exception as e:  # offline, timed out, out of credit, rate-limited, a bad key...
                self.skip_until = time.monotonic() + self.cfg.get("retry_after_s", 30)
                log.warning("Scribe failed (%s: %s); local Whisper hears it", type(e).__name__, e)
                if self.on_problem and not self._told:
                    self._told = True
                    self.on_problem("Can't reach ElevenLabs, so I'm using my own ears for now.")
        if self.denoise_strength != self.local_strength:
            audio = prepare(audio, self.local_strength)[0]
        return self.local.hear(audio, answer)

    def _scribe(self, audio: np.ndarray, answer: bool) -> Heard:
        likely, installed = self.local.names()
        start = time.perf_counter()
        result = self.client.speech_to_text.convert(
            model_id=self.cfg.get("model", "scribe_v2"),
            file=("speech.pcm", audio.astype(np.int16).tobytes()),
            file_format="pcm_s16le_16", language_code="eng", tag_audio_events=False,
            # Fillers and false starts dropped from requests, but kept in answers: "Mm-hmm" is a yes.
            no_verbatim=not answer, temperature=0.0, seed=0, keyterms=keyterms(likely, installed) or None,
            request_options={"timeout_in_seconds": self.cfg.get("timeout_s", 4), "max_retries": 0})
        took = time.perf_counter() - start
        raw = (result.text or "").strip()
        probs = [w.logprob for w in result.words or [] if w.type == "word" and w.logprob is not None]
        confidence = sum(probs) / len(probs) if probs else (0.0 if raw else -9.0)
        heard = Heard(raw, raw, took, confidence, nothing_said(raw, answer))
        if not heard.rejected and self.min_confidence is not None and confidence < self.min_confidence \
                and not answer:
            heard.rejected = "unsure"
        if heard.rejected:
            heard.text = ""
        return heard

    def transcribe(self, audio: np.ndarray, answer: bool = False) -> tuple[str, float]:
        heard = self.hear(audio, answer)
        return heard.text, heard.seconds


def make_ear(cfg, models_dir, apps=None, on_problem=None):
    """The ear config.yaml asks for: Scribe in front of the local Whisper, or the local Whisper alone."""
    local = SpeechToText(cfg, models_dir, apps=apps)
    if cfg.stt.get("provider", "local") != "elevenlabs":
        return local
    if not load_key():
        log.warning("stt.provider is elevenlabs but there's no ELEVENLABS_API_KEY in .env; using local Whisper")
        if on_problem:
            on_problem("No ElevenLabs key in .env (run: npx -y @elevenlabs/cli@latest onboard init), so I'm "
                       "using my own ears.")
        return local
    return ScribeEar(cfg, local, on_problem=on_problem)
