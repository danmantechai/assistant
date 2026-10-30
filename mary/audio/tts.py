"""Text-to-speech with Kokoro (kokoro-onnx, CPU) and a streaming speaker.

Replies arrive token by token from the LLM. VoiceOutput cuts them into sentences, synthesises each
one on a worker thread and plays it while the next is still being written, so MARY starts talking
after the first sentence instead of after the whole answer.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np

from mary.output import Output, is_yes

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
_ABBREVIATIONS = {r"\be\.g\.": "for example", r"\bi\.e\.": "that is", r"\betc\.": "et cetera",
                  r"\bvs\.?(?=\s)": "versus", r"\bapprox\.": "about", r"\bw/": "with"}
_UNITS = {"GB": "gigabytes", "MB": "megabytes", "KB": "kilobytes", "TB": "terabytes", "km/h": "kilometres an hour",
          "mph": "miles an hour", "km": "kilometres", "kg": "kilos", "ms": "milliseconds", "s": "seconds",
          "min": "minutes", "mins": "minutes", "hrs": "hours", "h": "hours"}
_FILE_KINDS = {"pdf": "PDF", "doc": "Word document", "docx": "Word document", "xls": "spreadsheet",
               "xlsx": "spreadsheet", "csv": "spreadsheet", "ppt": "presentation", "pptx": "presentation",
               "png": "picture", "jpg": "picture", "jpeg": "picture", "gif": "picture", "webp": "picture",
               "mp3": "song", "wav": "recording", "m4a": "recording", "mp4": "video", "mkv": "video",
               "mov": "video", "zip": "zip file", "exe": "program", "txt": "text file", "py": "Python file"}


def _clock(h: int, m: int, suffix: str = "") -> str:
    """19:04 -> "7 oh 4 PM"; 15:00 -> "3 o'clock PM". The voice reads "19:04" as "nineteen zero four"."""
    if not suffix and (h > 12 or h == 0):
        suffix = "PM" if h >= 12 else "AM"
    h12 = h % 12 or 12
    minutes = "o'clock" if m == 0 else f"oh {_ONES[m]}" if m < 10 else str(m)
    return f"{h12} {minutes}{' ' + suffix if suffix else ''}"


_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
          "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_DECADES = ["hundreds", "tens", "twenties", "thirties", "forties", "fifties", "sixties", "seventies", "eighties",
            "nineties"]


def _two_digits(n: int) -> str:
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])


def _decade(m: re.Match) -> str:
    """'1990s' -> 'nineteen nineties', '30s' -> 'thirties' (read as "nineteen ninety z")."""
    century, tens = m.group(1), int(m.group(2))
    if not century:
        return _DECADES[tens] if tens >= 2 else m.group(0)
    if century == "20" and tens == 0:
        return "two thousands"
    return f"{_two_digits(int(century))} {_DECADES[tens]}"


def _file_name(m: re.Match) -> str:
    """summer-promo-30s.mp4 -> "summer promo 30 second video" (it read "summerpromo thirty z dot MP four")."""
    stem, ext = m.group(1), m.group(2).lower()
    stem = re.sub(r"(?<![\d.])(\d+)s\b", r"\1 second", re.sub(r"[-_.]+", " ", stem))  # "promo-30s": 30 seconds
    return stem + " " + _FILE_KINDS.get(ext, ext.upper())


def speakable(text: str) -> str:
    """Make text sound right when spoken. Each rule is something the voice got wrong when checked by its
    phonemes: "$1,000,000" was "dollar one million", "28°C" was "degrees see", "2026-10-26" was read with
    "dash", "19:04" was "nineteen zero four", file names ran together. Also no markdown, paths become
    file names, links become their site, emoji go."""
    t = re.sub(r"(?m)^\s*(?:[-*•]|\d{1,2}[.)])\s+", "", text)                 # list bullets / numbers
    t = re.sub(r"[*_`#>|~]+(?=\S)|(?<=\S)[*_`#>|~]+", " ", t)                   # markdown emphasis, code
    t = re.sub(r"https?://(?:www\.)?([^/\s]+)\S*", r"\1", t)                     # links -> their site
    t = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)*([^\\\s]+)", r"\1", t)             # C:\...\file.pdf -> file.pdf
    t = re.sub(r"\b([\w][\w.-]*?)\.(" + "|".join(_FILE_KINDS) + r")\b", _file_name, t, flags=re.I)
    for pattern, words in _ABBREVIATIONS.items():
        t = re.sub(pattern, words, t, flags=re.I)
    t = re.sub(r"(\d{4})-(\d{2})-(\d{2})", lambda m: f"{int(m.group(3))} {_MONTHS[int(m.group(2)) - 1]} "
               f"{m.group(1)}" if 1 <= int(m.group(2)) <= 12 else m.group(0), t)                 # ISO dates
    t = re.sub(r"\b(\d{1,2}):(\d{2})(?:\s*([ap])\.?m\.?\b)?",
               lambda m: _clock(int(m.group(1)), int(m.group(2)), (m.group(3) or "").upper() + "M"
                                if m.group(3) else "") if int(m.group(1)) < 24 and int(m.group(2)) < 60
               else m.group(0), t, flags=re.I)                                                    # clock times
    t = re.sub(r"([+-]?\d+(?:\.\d+)?)\s*°\s*C\b", r"\1 degrees", t)                          # 28°C
    t = re.sub(r"([+-]?\d+(?:\.\d+)?)\s*°\s*F\b", r"\1 degrees Fahrenheit", t)
    t = t.replace("°", " degrees")
    t = re.sub(r"\$\s?(\d[\d,]*)(?:\.(\d{2}))?(\s*(?:million|billion|thousand|k)\b)?",
               lambda m: f"{m.group(1)}{m.group(3) or ''} dollars" +
               (f" {int(m.group(2))}" if m.group(2) and int(m.group(2)) else ""), t, flags=re.I)   # $4.99 -> 4 dollars 99
    t = re.sub(r"\b(1\d|20)?(\d)0s\b", _decade, t)                                              # 1990s, 30s
    t = re.sub(r"(\d)\s*[–—-]\s*(\d)", r"\1 to \2", t)                                       # 4–7 -> 4 to 7
    # 5 GB, 2.2GB, 4 s. One-letter units need a space: "30s" in a name and "the 1990s" aren't seconds.
    t = re.sub(r"\b(\d+(?:\.\d+)?)(\s?)(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
               + r")\b(?![/\w])", lambda m: f"{m.group(1)} {_UNITS[m.group(3)]}"
               if m.group(2) or m.group(3) not in ("s", "h", "ms", "min", "mins") else m.group(0), t)
    # "2.2" was read "two. two". Not version numbers ("2.5.1"), but a full stop after a decimal is fine.
    t = re.sub(r"(?<![\d.])(\d+)\.(\d+)(?!\d|\.\d)", r"\1 point \2", t)
    t = re.sub(r"(?<=\w)/(?=[A-Za-z])", " or ", t)                                               # on/off
    t = re.sub(r"(?<=\d)/(?=\d)", " of ", t)                                                    # step 1/3
    t = re.sub(r"\s*(?:->|→|=>)\s*", " to ", t)
    t = re.sub(r"(?<!\w)@(?=\w)", "at ", t).replace("~", "about ")
    t = t.replace("_", " ").replace("&", " and ").replace("%", " percent")
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]", "", t)                       # emoji
    return re.sub(r"\s+", " ", t).strip()


class TextToSpeech:
    def __init__(self, cfg, models_dir: Path):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        self.cfg = cfg.tts
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.cfg.threads
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(str(models_dir / self.cfg.model), sess_options=opts,
                                       providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(session, str(models_dir / self.cfg.voices))
        self.voice = self.cfg.voice
        self.speed = self.cfg.speed

    @property
    def voices(self) -> list[str]:
        return sorted(self.kokoro.get_voices())

    def synth(self, text: str, voice: str | None = None) -> np.ndarray:
        voice = voice or self.voice
        lang = "en-gb" if voice[:2] in ("bf", "bm") else "en-us"  # British voices sound right with en-gb
        samples, rate = self.kokoro.create(text, voice=voice, speed=self.speed, lang=lang)
        return samples.astype(np.float32)


class Speaker:
    """Plays float32 24 kHz chunks back to back; reports a loudness envelope for the HUD."""

    def __init__(self, device=None, volume: float = 1.0, on_level=None):
        self.device, self.volume, self.on_level = device, volume, on_level
        self._chunks: queue.Queue[np.ndarray] = queue.Queue()
        self._current = np.zeros(0, dtype=np.float32)
        self._stream = None
        self._lock = threading.Lock()
        self.busy_until = 0.0
        self.last_sound = 0.0   # when the sound card last actually got non-silent audio
        self.last_callback = 0.0

    def _ensure_stream(self) -> None:
        if self._stream is None:
            import sounddevice as sd

            stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     blocksize=1200, device=self.device, callback=self._callback)
            stream.start()
            self.last_callback = time.monotonic()
            self._stream = stream

    def play(self, samples: np.ndarray) -> None:
        with self._lock:
            self._ensure_stream()
            self._chunks.put(samples * self.volume)
            # Estimate when everything queued will have finished playing.
            self.busy_until = max(self.busy_until, time.monotonic()) + len(samples) / SAMPLE_RATE

    def _callback(self, outdata, frames, time_info, status) -> None:
        self.last_callback = time.monotonic()
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        while filled < frames:
            if not len(self._current):
                try:
                    self._current = self._chunks.get_nowait()
                except queue.Empty:
                    break
            n = min(frames - filled, len(self._current))
            out[filled:filled + n] = self._current[:n]
            self._current = self._current[n:]
            filled += n
        outdata[:, 0] = out
        rms = float(np.sqrt(np.mean(out ** 2)))
        if rms > 1e-4:
            self.last_sound = time.monotonic()
        if self.on_level:
            self.on_level(min(1.0, rms * 6))

    def wait_quiet(self, tail_s: float = 0.45, timeout: float = 3.0) -> None:
        """Wait until the speakers have really been silent for tail_s. Sound cards play ~0.1-0.3 s
        behind what was queued, and a mic that starts listening early hears MARY's last word, which
        Whisper turns into things like "Click the link in the description"."""
        deadline = time.monotonic() + timeout
        while time.monotonic() - self.last_sound < tail_s and time.monotonic() < deadline:
            time.sleep(0.05)

    @property
    def playing(self) -> bool:
        queued = not self._chunks.empty() or len(self._current) > 0
        if queued and self._stream is not None and time.monotonic() - self.last_callback > 1.5:
            # The sound card stopped asking for audio (headphones unplugged, device switched off).
            # Drop what's queued and reopen on the next play(), instead of everyone waiting 60 s.
            log.warning("speaker stopped responding; reopening it for the next reply")
            self.stop()
            self.close()
            return False
        return time.monotonic() < self.busy_until or queued

    def wait(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while self.playing and time.monotonic() < deadline:
            time.sleep(0.05)

    def stop(self) -> None:
        """Barge-in: drop everything queued and go quiet now."""
        while not self._chunks.empty():
            try:
                self._chunks.get_nowait()
            except queue.Empty:
                break
        self._current = np.zeros(0, dtype=np.float32)
        self.busy_until = 0.0

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log.debug("closing the speaker stream failed: %s", e)


def chime(rising: bool = True) -> np.ndarray:
    """A soft two-note cue: rising = "I'm listening", falling = "got it"."""
    notes = (660, 990) if rising else (880, 587)
    t = np.arange(int(SAMPLE_RATE * 0.07)) / SAMPLE_RATE
    parts = [np.sin(2 * np.pi * f * t) * np.hanning(len(t)) for f in notes]
    return (np.concatenate(parts) * 0.12).astype(np.float32)


class VoiceOutput(Output):
    """Speaks MARY's replies, all of them: he may not be looking at the screen. Sentences are synthesised on a
    worker thread while earlier ones play."""

    def __init__(self, tts: TextToSpeech, speaker: Speaker):
        self.tts, self.speaker = tts, speaker
        self._spoken = 0          # sentences queued for the current reply
        self.listener = None                       # set by the voice loop; answers confirm()
        self._buffer = ""
        self._sentences: queue.Queue[str | None] = queue.Queue()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self.first_audio_at: float | None = None   # for latency: when the first reply audio was queued
        self.on_problem = None                     # called once with a message when speaking fails
        self._failing = False
        threading.Thread(target=self._synth_loop, name="tts", daemon=True).start()

    # ------------------------------------------------------------------ sentence streaming

    def _queue_sentence(self, text: str) -> None:
        text = speakable(text)
        if not re.search(r"\w", text):
            return
        if not re.search(r"[,;:]$", text):  # a first clause sent early isn't a whole sentence
            self._spoken += 1
        with self._pending_lock:
            self._pending += 1
        self._sentences.put(text)

    def _synth_loop(self) -> None:
        while True:
            text = self._sentences.get()
            try:
                audio = self.tts.synth(text)
                if self.first_audio_at is None:
                    self.first_audio_at = time.perf_counter()
                self.speaker.play(audio)
                self._failing = False
            except Exception as e:  # no output device, or the voice model failed: replies stay as text
                log.exception("TTS failed for %r", text)
                if not self._failing and self.on_problem:
                    self.on_problem(f"I can't speak right now ({e}). My replies are shown as text.")
                self._failing = True
            finally:
                with self._pending_lock:
                    self._pending -= 1

    def reply_start(self) -> None:
        self._buffer = ""
        self._spoken = 0

    def reply_delta(self, text: str) -> None:
        self._buffer += text
        parts = _SENTENCE_END.split(self._buffer)
        for sentence in parts[:-1]:  # everything before the last boundary is complete
            self._queue_sentence(sentence)
        self._buffer = parts[-1]
        if self._spoken == 0 and self._sentences.empty() and self._pending == 0:
            # Nothing said yet: start with the first clause of a long first sentence rather than wait for
            # the whole sentence (measured: 4-5 s to the first sound for a long opening sentence).
            from mary.llm.replies import first_clause

            clause = first_clause(self._buffer)
            if clause:
                self._queue_sentence(clause[0])
                self._buffer = clause[1]

    def reply_end(self) -> None:
        if self._buffer.strip():
            self._queue_sentence(self._buffer)
        self._buffer = ""

    def notify(self, text: str) -> None:
        self._queue_sentence(text)

    # ------------------------------------------------------------------ control

    @property
    def speaking(self) -> bool:
        return self._pending > 0 or self.speaker.playing

    def wait_done(self, timeout: float = 60) -> None:
        """Block until everything said so far has been played (so MARY doesn't hear herself)."""
        deadline = time.monotonic() + timeout
        while self._pending > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.speaker.wait(max(0.0, deadline - time.monotonic()))

    def stop(self) -> None:
        while not self._sentences.empty():
            try:
                self._sentences.get_nowait()
                with self._pending_lock:
                    self._pending -= 1
            except queue.Empty:
                break
        self.speaker.stop()

    def confirm(self, question: str) -> bool:
        """Say the question, then listen for yes/no. Silence or anything unclear means no."""
        self._queue_sentence(question)
        self.wait_done()
        self.speaker.wait_quiet()  # don't let the mic hear the end of the question as the answer
        if self.listener is None:
            return False
        answer = self.listener.hear_answer()
        log.info("confirmation answer: %r", answer)
        return is_yes(answer)
