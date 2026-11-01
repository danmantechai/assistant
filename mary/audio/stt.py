"""Speech-to-text with faster-whisper (CTranslate2, int8 on CPU).

Whisper is trained on subtitled video, so when it can't make out the audio it doesn't say "I don't
know": it writes what such videos say ("Thanks for watching", "Don't forget to like and subscribe").
Its default "temperature fallback" makes this worse, re-decoding unclear audio more and more freely
(measured here: wrong transcripts took 8-10 s, right ones ~3.8 s). So: one greedy-ish pass at
temperature 0, and anything unsure, empty or video-like is rejected instead of acted on.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

log = logging.getLogger(__name__)

# Heard in silence or noise; never acted on as commands. ("Okay" and "thanks" are real answers to
# "Shall I go ahead?", so answers use the short list.)
_HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching",
                   "bye", "okay", "ok", "so", "the", "uh", "um", "hmm", "mm", "i'm sorry", "hello", "oh"}
_HALLUCINATIONS_ANSWER = {"", "you", "thank you for watching", "thanks for watching"}
# Video-subtitle phrases Whisper invents for unclear audio.
_VIDEO_TALK = re.compile(r"\b(like and subscribe|subscribe|thanks? (you )?for watching|subtitles? by|captions? by|"
                         r"amara\.org|see you (in the )?next (video|time)|transcribed by|www\.|\.com/)", re.I)
# Whisper keeps only the last 223 tokens of its context prompt (faster-whisper: max_length // 2 - 1).
PROMPT_TOKENS = 223
_EXTRAS = re.compile(r"\([^)]*\)|\bv?\d+(?:\.\d+)+\b|\b(?:x64|x86|64-bit|32-bit)\b", re.I)


def spoken_app_name(name: str) -> str:
    """'Microsoft Visual Studio Code (User)' -> 'Visual Studio Code': the part people say."""
    name = re.sub(r"^Microsoft\s+(?=\w)", "", _EXTRAS.sub("", name))
    return re.sub(r"\s+", " ", name).strip(" -–")


def nothing_said(raw: str, answer: bool = False) -> str:
    """'silence' or 'video-talk' when the transcript is what speech recognition writes for no speech, else ''."""
    plain = re.sub(r"[^\w\s']", "", raw).strip().lower()
    if plain in (_HALLUCINATIONS_ANSWER if answer else _HALLUCINATIONS):
        return "silence"
    return "video-talk" if _VIDEO_TALK.search(raw) else ""


@dataclass
class Heard:
    text: str                 # what to act on ("" = nothing usable)
    raw: str                  # what the speech recognition actually wrote
    seconds: float            # time taken
    confidence: float         # mean log-probability per token (0 = certain, below -1 = guessing)
    rejected: str = ""        # why the text was thrown away: "", "silence", "unsure", "video-talk", "prompt"


class SpeechToText:
    def __init__(self, cfg, models_dir: Path, apps=None):
        from faster_whisper import WhisperModel

        self.cfg = cfg.stt
        self.model = WhisperModel(self.cfg.model, device="cpu", compute_type=self.cfg.compute_type,
                                  cpu_threads=self.cfg.cpu_threads, download_root=str(models_dir / "whisper"))
        # A short context prompt, not hotwords: hotwords made small.en "hear" them in silence
        # ("Facebook.com"); the prompt biases spelling and keeps silence empty.
        self.prompt = self.cfg.get("prompt") or None
        self.min_confidence = self.cfg.get("min_confidence", -0.9)
        # Only the fixed sentence's words: app names ("Weather", "Photos", "Mail") are everyday words too.
        self._prompt_words = {w for w in re.findall(r"[a-z]+", (self.prompt or "").lower())
                              if w not in {"hey", "mary", "open", "play", "check", "type", "this", "text"}}
        self.apps = apps if self.cfg.get("app_names", True) else None  # AppIndex: installed app names
        self.words = list(self.cfg.get("words") or [])  # names he uses that it mishears (a shop, a friend, an app)
        self._installed: tuple[int, list[tuple[str, int]]] = (-1, [])
        self.app_names: set[str] = set()

    def _tokens(self, text: str) -> int:
        return len(self.model.hf_tokenizer.encode(" " + text, add_special_tokens=False).ids)

    def names(self) -> tuple[list[str], list[tuple[str, int]]]:
        """(the names he's most likely to say: his own words, then apps with a window open;
        every installed app's name with its Whisper tokens + its ", ", cheapest first)."""
        if not self.apps:
            return list(self.words), []
        apps = list(self.apps.apps)
        if self._installed[0] != len(apps):
            names = dict.fromkeys(n for n in map(spoken_app_name, (a.name for a in apps)) if n)
            self._installed = (len(apps), sorted(((n, self._tokens(n) + 1) for n in names), key=lambda x: x[1]))
        by_exe = {a.exe: spoken_app_name(a.name) for a in apps if a.exe}
        from mary import winutil

        open_names = list(dict.fromkeys(
            by_exe.get(w.exe) or Path(w.exe).stem for w in winutil.list_windows()
            if w.exe and not winutil.is_own_console(w.hwnd)))[:12]
        return list(dict.fromkeys([*self.words, *open_names])), self._installed[1]

    def hint(self) -> str | None:
        """The context prompt: his words and the installed apps' names, then the fixed sentence, so Whisper
        spells them ("Claude Code", not "plot code"). His words and apps with a window open always go in,
        nearest the end; the rest fill what's left of the 223 tokens Whisper keeps, shortest names first."""
        likely, installed = self.names()
        if not likely and not installed:
            return self.prompt
        base = self.prompt or ""
        budget = PROMPT_TOKENS - self._tokens(", ".join(likely) + ". " + base) - 2
        chosen = []
        for name, cost in installed:
            if name in likely:
                continue
            if cost > budget:
                break
            chosen.append(name)
            budget -= cost
        names = sorted(chosen, key=str.lower) + likely
        self.app_names = {n.lower() for n in names}
        return ", ".join(names) + ". " + base

    def warm_up(self) -> None:
        """Run the noise filter and Whisper once on silence. Measured: the first request otherwise paid
        ~4 s just importing scipy for the filter, plus Whisper's own first-call setup."""
        self.hear(prepare(np.zeros(16000, np.int16))[0])

    def hear(self, audio: np.ndarray, answer: bool = False) -> Heard:
        """int16 16 kHz audio -> Heard. answer=True: a reply to a yes/no question, where "okay" counts."""
        start = time.perf_counter()
        segments, _ = self.model.transcribe(
            audio.astype(np.float32) / 32768.0, language="en", beam_size=self.cfg.beam_size,
            temperature=0.0,  # no fallback: unclear audio gets rejected, not "creatively" re-decoded
            vad_filter=False, condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=self.hint())
        segs = [s for s in segments if s.no_speech_prob < 0.8]
        raw = " ".join(s.text.strip() for s in segs).strip()
        tokens = sum(max(1, len(s.tokens)) for s in segs)
        confidence = sum(s.avg_logprob * max(1, len(s.tokens)) for s in segs) / tokens if segs else -9.0
        took = time.perf_counter() - start
        heard = Heard(raw, raw, took, confidence, nothing_said(raw, answer))
        if heard.rejected:
            pass
        elif self.prompt and len(raw) >= 0.6 * len(self.prompt) and fuzz.ratio(raw.lower(), self.prompt.lower()) > 80:
            heard.rejected = "prompt"  # Whisper sometimes just repeats its whole context prompt
        elif self.reads_app_list(raw):
            heard.rejected = "prompt"  # ...or a run of the app names in it
        elif confidence < self.min_confidence and not answer:
            heard.rejected = "unsure"
        elif confidence < -0.25 and self._prompt_words & set(re.findall(r"[a-z]+", raw.lower())):
            # Measured: in heavy noise Whisper swaps in words from its context prompt ("Play Don Williams"
            # -> "Play Spotify"). A prompt word in an unsure transcript is more likely invented than said.
            heard.rejected = "unsure"
        if heard.rejected:
            heard.text = ""
        return heard

    def reads_app_list(self, raw: str) -> bool:
        """Three or more of the hint's app names and nothing else: Whisper reading its prompt, not speech."""
        parts = [p.strip().lower() for p in re.split(r"[,.]", raw) if p.strip()]
        return len(parts) >= 3 and all(p in self.app_names for p in parts)

    def transcribe(self, audio: np.ndarray, answer: bool = False) -> tuple[str, float]:
        heard = self.hear(audio, answer)
        return heard.text, heard.seconds


def strip_wake_phrase(text: str, persona=None) -> str:
    """'Hey Mary, open Spotify' -> 'open Spotify'; 'Amen.' -> '' (only the wake phrase was said). Said after the
    hotkey, the wake phrase isn't part of the request. The persona's wake phrases (and how speech recognition
    writes them) decide; a name only counts alone where the persona says so: "Mum's birthday is…" is a request."""
    from mary import persona as personas

    return (persona or personas.load()).wake_pattern().sub("", text, count=1).strip()


def denoise(audio: np.ndarray, strength: float = 1.5) -> np.ndarray:
    """Spectral gating: learn the steady background (fan, hum, hiss) from the quietest moments of the
    recording, and turn each frequency down by how much of it is just that background."""
    from scipy.signal import istft, stft

    x = audio.astype(np.float32)
    _, _, spec = stft(x, fs=16000, nperseg=512, noverlap=384)
    mag = np.abs(spec)
    if mag.shape[1] < 8:
        return audio
    noise = np.percentile(mag, 15, axis=1, keepdims=True)  # each recording has silence before/after speech
    gain = np.clip(1.0 - strength * noise / (mag + 1e-6), 0.08, 1.0)  # keep a little, avoid "underwater" sound
    gain = np.apply_along_axis(lambda g: np.convolve(g, np.ones(3) / 3, mode="same"), 1, gain)  # smooth in time
    _, y = istft(spec * gain, fs=16000, nperseg=512, noverlap=384)
    return np.clip(y[:len(x)], -32768, 32767).astype(np.int16)


def prepare(audio: np.ndarray, strength: float = 2.5) -> tuple[np.ndarray, str]:
    """Clean up the recording for Whisper. Returns (audio, warning) where warning is "clipping",
    "quiet" or "". strength 0 skips the noise filter.

    Measured: on noisy synthetic commands the filter took 13 -> 16 of 24 exact; on the user's own
    (noisy) recording, strength 2.5 was the only setting that got every word; clean speech unchanged.
    RNNoise was tried too and made Whisper worse (artefacts), so it isn't used."""
    clipped = float(np.mean(np.abs(audio.astype(np.int32)) >= 32000))
    x = audio.astype(np.float32)
    x -= x.mean()  # some laptop mics add a DC offset
    if strength > 0:
        x = denoise(x.astype(np.int16), strength).astype(np.float32)
    peak = float(np.abs(x).max()) + 1e-6
    warning = "clipping" if clipped > 0.002 else ""
    if peak < 3000:  # very quiet: bring it up to a normal level (Whisper does worse on faint speech)
        x *= min(8.0, 16000 / peak)
        warning = warning or "quiet"
    return np.clip(x, -32768, 32767).astype(np.int16), warning
