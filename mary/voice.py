"""The voice loop: trigger -> listen -> transcribe -> MARY -> speak, on its own thread.

    trigger (Ctrl+Space, or "Hey Mary" once trained)
      -> mic on, rising chime, HUD listening
      -> record until you're done: a 1.5 s pause ends a short request, but once you've talked for
         a few seconds pauses up to 5 s are fine (no need to rush or hold your breath).
         HOLD the hotkey to talk for as long as you like; TAP it again to finish right away.
      -> faster-whisper (started speculatively 0.5 s into your final pause, so the transcript is
         usually ready the moment the pause is over) -> Mary.handle(text): router or LLM, replies
         spoken sentence by sentence
      -> keep listening a few seconds for a follow-up, then back to waiting
"""
from __future__ import annotations

import collections
import ctypes
import logging
import threading
import time

import numpy as np

from mary.audio.mic import BLOCK, RATE, Microphone, MicUnavailable, level_of
from mary.audio.stt import prepare, strip_wake_phrase
from mary.audio.tts import chime
from mary.output import IDLE, LISTENING

log = logging.getLogger(__name__)
FRAME_S = BLOCK / RATE


def allowed_pause(v, speech_s: float) -> float:
    """Seconds of silence that end the request, given how long you've been speaking: short commands
    end quickly, but the longer you talk, the longer you may pause to think (up to 5 s)."""
    short, long = v.end_silence_ms / 1000, v.long_end_silence_ms / 1000
    if speech_s <= v.short_until_s:
        return short
    ramp = min(1.0, (speech_s - v.short_until_s) / max(0.1, v.long_after_s - v.short_until_s))
    return short + ramp * (long - short)


class _Speculation:
    """Whisper started on the audio so far while you pause. If you don't speak again before the pause
    ends the request, its transcript is the answer, and most of the STT time is already behind us."""

    def __init__(self, audio: np.ndarray, transcribe):
        self.result = None
        self.done = threading.Event()
        threading.Thread(target=self._run, args=(audio, transcribe), name="stt-speculative", daemon=True).start()

    def _run(self, audio, transcribe) -> None:
        try:
            self.result = transcribe(audio)
        except Exception:
            log.exception("speculative transcription failed")
        finally:
            self.done.set()

    def wait(self):
        self.done.wait()
        return self.result


class VoiceLoop(threading.Thread):
    def __init__(self, cfg, mary, mic: Microphone, vad, stt, voice_out, hud_out=None, console=None,
                 wakeword=None, hotkey_keys: tuple[int, int] | None = None, wake_check=None, wake_replies=None,
                 cleaner=None):
        super().__init__(name="voice", daemon=True)
        self.cfg, self.mary, self.mic, self.vad, self.stt = cfg, mary, mic, vad, stt
        self.voice_out, self.hud, self.console, self.wakeword = voice_out, hud_out, console, wakeword
        self.wake_check = wake_check      # WakeCheck: speech recognition confirms each detection
        self.wake_replies = wake_replies  # WakeReplies: "Yes, dear?" when "Hey Mom" wakes her
        self._by_voice = False            # this turn was started by the wake word (not the hotkey)
        self.cleaner = cleaner            # VoiceCleaner: the browser's noise suppression + automatic volume
        self.live = None                  # the live session record() streamed the request to
        self.hotkey_keys = hotkey_keys    # (modifier flags, virtual key), to notice hold-to-talk
        self.trigger = threading.Event()
        self.finish_now = threading.Event()
        self.typed: list[str] = []        # requests typed in the console, handled on this thread too
        self.running = True
        self.busy = False                 # inside a turn (listening, thinking or speaking)
        self.recording = False
        self._warned_clipping = False
        self._mic_warned = False          # told you the mic is gone (said once, until it's back)
        self._next_mic_try = 0.0
        self.speculation: _Speculation | None = None  # set by record() when it still matches the audio
        self._in_flight: _Speculation | None = None   # the latest one started, valid or not
        voice_out.listener = self         # spoken confirmations listen through us

    # ------------------------------------------------------------------ triggers

    def summon(self) -> None:
        """Hotkey pressed: finish the current recording, or interrupt MARY and listen, or start."""
        if self.recording:
            self.finish_now.set()
            return
        if self.voice_out.speaking:
            self.voice_out.stop()
        self._by_voice = False
        self.trigger.set()

    def submit(self, text: str) -> None:
        """A typed request; runs on the voice thread so it never overlaps a spoken one."""
        self.typed.append(text)
        self.trigger.set()

    def _speech_in(self, frames) -> bool:
        """Did the last ~1.3 s contain speech? Music, TV jingles and noise rarely pass this."""
        self.vad.reset()
        return max((self.vad.probability(f) for f in frames), default=0.0) >= self.cfg.vad.threshold

    # ------------------------------------------------------------------ microphone trouble

    def _open_mic(self) -> bool:
        """Start the mic; after a failure, re-scan devices first (one plugged in since). False if none."""
        try:
            if self.mic.broken:
                raise MicUnavailable("no audio from the microphone")
            self.mic.start()
        except MicUnavailable as e:
            if self.voice_out.speaking:  # re-scanning devices closes every stream: it would cut MARY off
                return False
            self.voice_out.speaker.close()  # closed cleanly now; it reopens on the next reply
            if not self.mic.reopen():
                log.info("microphone unavailable: %s", e)
                return False
        if self._mic_warned:
            self._mic_warned = False
            if self.console:
                self.console.status("✓ The microphone is working again.")
        return True

    def _mic_problem(self) -> None:
        """Say once that the mic is gone; the hotkey or wake-word loop keeps trying to reopen it."""
        if self._mic_warned:
            return
        self._mic_warned = True
        self.voice_out.notify("I can't hear anything from the microphone. Check that it's plugged in, "
                              "or type to me instead.")
        if self.console:
            self.console.status("⚠ Microphone unavailable. Plug one in (or check Settings › Privacy › "
                                "Microphone); I'll keep trying.")

    def _watch_mic(self) -> None:
        """Wake-word mode: the mic should always be open. Retry every few seconds while it isn't."""
        if (self.mic.active and not self.mic.broken) or time.monotonic() < self._next_mic_try:
            return
        self._next_mic_try = time.monotonic() + 5
        if not self._open_mic():
            self._mic_problem()

    # ------------------------------------------------------------------ main loop

    def _confirmed(self, heard) -> bool:
        """Whisper's second opinion on a detection (no WakeCheck: every detection counts)."""
        if not self.wake_check:
            return True
        start = time.perf_counter()
        ok, text = self.wake_check.heard(self.wakeword.last_key, np.concatenate(heard))
        log.info("wake word %s by speech recognition in %.1f s", "confirmed" if ok else "not confirmed",
                 time.perf_counter() - start)
        log.debug("wake check heard %r", text)  # room talk: kept out of the normal log
        return ok

    def run(self) -> None:
        recent = collections.deque(maxlen=16)  # the audio the wake-word model just judged
        heard = collections.deque(maxlen=round(2.5 * RATE / BLOCK))  # a little more, for Whisper's second opinion
        while self.running:
            if self.wakeword:
                self._watch_mic()  # the detector needs to hear everything
                frame = self.mic.read(timeout=0.5)
                if frame is not None and not self.voice_out.speaking:
                    recent.append(frame)
                    heard.append(frame)
                    score = self.wakeword.detect(frame)
                    if score is not None:
                        if not self._speech_in(recent):
                            log.info("wake word (%.4f) ignored: no speech in that moment", score)
                        elif self._confirmed(heard):
                            log.info("wake word (%.4f)", score)
                            self._by_voice = True
                            self.trigger.set()
            elif not self.trigger.wait(timeout=0.5):
                continue
            if self.trigger.is_set():
                self.trigger.clear()
                by_voice, self._by_voice = self._by_voice, False
                self.busy = True
                try:
                    if self.typed:
                        self.handle(self.typed.pop(0))
                    else:
                        self.conversation(by_voice)
                except Exception:
                    log.exception("voice turn failed")
                finally:
                    self.busy = False
                    self._end_turn()
                    # Pre-read the conversation into the LLM's cache only now: during follow-ups it
                    # would compete with Whisper for the CPU (Whisper took 8-10 s under contention).
                    self.mary.agent.prefill_in_background()

    # ------------------------------------------------------------------ a conversation

    def _chime(self) -> None:
        """The 'I'm listening' cue, kept out of the recording: the laptop mic hears its own speakers
        loudly (MARY's clips peaked at full scale), and a ding at the start of a clip confuses Whisper."""
        if not self.cfg.audio.get("chime", True):
            return
        speaker = self.voice_out.speaker
        speaker.play(chime(rising=True))
        speaker.wait(1.0)
        speaker.wait_quiet(tail_s=0.12, timeout=1.0)
        self.mic.drain()

    def _answer_wake(self) -> bool:
        """'Yes, dear?' (or a hum) instead of the chime, kept out of the recording the same way.
        False if no reply is ready yet (the first start renders them)."""
        reply = self.wake_replies.pick() if self.wake_replies else None
        if reply is None:
            return False
        text, audio = reply
        speaker = self.voice_out.speaker
        speaker.play(audio)
        if self.console:
            self.console.tool_log(f"MARY › {text}")
        speaker.wait(3.0)
        speaker.wait_quiet(tail_s=0.2, timeout=1.0)
        self.mic.drain()
        return True

    def conversation(self, by_voice: bool = False) -> None:
        """Listen, answer, and keep listening for follow-ups until nobody speaks for a while.
        by_voice: "Hey Mom" started it, so she answers it in words; the hotkey gets the chime."""
        if not self._open_mic():
            self._mic_problem()
            return
        live = self._open_live()  # connects while she answers, so it's ready when you start talking
        if not (by_voice and self._answer_wake()):
            self._chime()
        if self.console:
            self.console.tool_log("🎙 Listening…")
        timeout, hold = self.cfg.vad.no_speech_timeout_s, self._hotkey_held()
        misses, asked = 0, 0
        while self.running:
            self.trigger.clear()  # a hotkey press that interrupted MARY is answered by this round
            if self.hud:
                self.hud.user_text("")
                self.hud.state(LISTENING)
            started = time.perf_counter()
            audio = self.record(timeout, hold=hold, speculate=live is None, live=live or self._open_live())
            live = None  # used (it's self.live now) or cancelled: the next round opens its own
            hold = False
            if audio is None:
                if self.mic.broken:
                    self._mic_problem()
                return
            heard_at = time.perf_counter()
            spec, self.speculation = self.speculation, None
            heard = self.stt.hear_live(self.live) if self.live else None
            if heard is not None:
                warning = prepare(audio, 0.0)[1]  # only the too-loud / too-quiet check
                log.info("live transcript used (ready %.2f s after the pause ended)", time.perf_counter() - heard_at)
            elif spec and spec.wait():
                heard, warning = spec.result
                log.info("speculative transcript used (ready %.2f s after the pause ended)",
                         time.perf_counter() - heard_at)
            else:
                heard, warning = self._transcribe(audio)
            self.live = None
            if warning == "clipping" and not self._warned_clipping:
                self._warned_clipping = True
                if self.console:
                    self.console.tool_log("⚠ Your microphone is so loud it distorts, which hurts recognition. "
                                          "Lower it to ~70% (Settings › System › Sound › Input › Volume).")
            stt_wait = time.perf_counter() - heard_at  # what you waited for, not Whisper's own time
            log.info("heard %r -> %r (%.1f s audio, STT %.2f s, waited %.2f s, confidence %.2f, %s%s)", heard.raw,
                     heard.text, len(audio) / RATE, heard.seconds, stt_wait, heard.confidence,
                     heard.rejected or "accepted", f", {warning}" if warning else "")
            request = strip_wake_phrase(heard.text)
            if not request:
                misses += 1
                if heard.text:               # just "Hey Mary": she's listening, carry on
                    if self.console:
                        self.console.tool_log("(yes? I'm listening)")
                elif heard.rejected == "unsure" and asked < 2:
                    asked += 1               # something was said, but too unclear to act on
                    self.voice_out.notify("Sorry, I didn't catch that. Could you say it again?")
                    self.voice_out.wait_done()
                    self.voice_out.speaker.wait_quiet()
                    self.mic.drain()
                elif self.console:
                    self.console.tool_log(f"(ignored: {heard.rejected or 'nothing clear'})")
                if misses >= 3:              # e.g. a TV in the background: stop listening
                    return
                timeout = self.cfg.vad.no_speech_timeout_s
                continue
            misses = 0
            self.handle(request, started, heard_at, stt_wait)
            if not self.cfg.vad.followup_s or self.mary.end_conversation:  # "no", "thanks", "bye"…
                return
            timeout = self.cfg.vad.followup_s  # follow-ups need no hotkey; silence ends the conversation
            if self.console:
                self.console.tool_log(f"🎙 Still listening for {timeout:.0f} s…")

    def _transcribe(self, audio: np.ndarray):
        """(Heard, warning) for raw recorded audio: clean it up the way Whisper likes, then transcribe."""
        audio, warning = prepare(audio, self._denoise())
        return self.stt.hear(audio), warning

    def _denoise(self) -> float:
        """How hard to filter the noise before the ear hears it: not at all once the browser-style clean-up
        ran (measured: it did the filter's job, slightly better), nor for the cloud ear."""
        if self.cleaner:
            return 0.0
        return getattr(self.stt, "denoise_strength", self.cfg.stt.get("denoise_strength", 2.5))

    def _open_live(self):
        """A live hearing session (Scribe realtime), or None when the ear hears recordings only."""
        opener = getattr(self.stt, "open_live", None)
        return opener(on_partial=self._show_partial) if opener else None

    def _show_partial(self, text: str) -> None:
        if self.hud and text:
            self.hud.user_text(text)  # the words as you speak them, like on the dashboard

    def handle(self, text: str, started: float | None = None, heard_at: float | None = None,
               stt_s: float = 0.0) -> None:
        self.voice_out.first_audio_at = None
        latency = self.mary.handle(text, prefill=False)  # prefill after speaking: Kokoro needs the CPU now
        if heard_at:
            latency.stages[:0] = [("listen (you speaking)", (heard_at - started) * 1000), ("stt", stt_s * 1000)]
            deadline = time.monotonic() + 15  # the first sentence may still be synthesising
            while self.voice_out.first_audio_at is None and self.voice_out.speaking and time.monotonic() < deadline:
                time.sleep(0.02)
            if self.voice_out.first_audio_at:
                latency.marks.append(("tts first audio (after speech end)",
                                      (self.voice_out.first_audio_at - heard_at) * 1000))
        if self.console and self.mary.debug:
            print(latency.table())
        self.voice_out.wait_done()
        self.voice_out.speaker.wait_quiet()  # until the speakers have really gone quiet...
        self.mic.drain()   # ...and don't treat what the mic heard while she spoke as a new request

    def _end_turn(self) -> None:
        self.recording = False
        self.mary.awaiting = None  # an unanswered "What should I type?" ends with the conversation
        if not self.wakeword:
            self.mic.stop()  # no wake word: the mic is only on while listening
        else:
            self.wakeword.reset()  # forget audio from before the turn
        self.mic.drain()
        if self.hud:
            self.hud.state(IDLE)

    # ------------------------------------------------------------------ recording

    def _hotkey_held(self) -> bool:
        if not self.hotkey_keys:
            return False
        mods, vk = self.hotkey_keys
        key = ctypes.windll.user32.GetAsyncKeyState
        held = bool(key(vk) & 0x8000)
        for flag, mod_vk in ((0x1, 0x12), (0x2, 0x11), (0x4, 0x10), (0x8, 0x5B)):  # alt, ctrl, shift, win
            if mods & flag:
                held = held and bool(key(mod_vk) & 0x8000)
        return held

    def record(self, no_speech_timeout: float, max_s: float | None = None, hold: bool = False,
               speculate: bool = False, live=None) -> np.ndarray | None:
        """Audio from the start of speech (plus pre-roll) until you're done, or None if nothing was said.

        Done = a pause of end_silence_ms for a short request, of long_end_silence_ms once you've
        spoken long_after_s, the hotkey released (hold-to-talk), or the hotkey tapped again.
        With `speculate`, Whisper starts once a pause reaches speculate_after_ms; self.speculation is
        that run if nobody spoke again before the end (speech after it makes it stale).
        With `live`, a LiveSession, the request also streams to it from the first word; self.live is that
        session when the request is done (cancelled if nothing was said).
        The recording is cleaned (self.cleaner) as it comes in; the VAD judges the raw mic, as it was tuned.
        """
        try:
            audio = self._record(no_speech_timeout, max_s, hold, speculate, live)
        except BaseException:
            if self.live:
                self.live.cancel()
            self.live = None
            raise
        if audio is None and self.live:
            self.live.cancel()
            self.live = None
        return audio

    def _record(self, no_speech_timeout, max_s, hold, speculate, live) -> np.ndarray | None:
        self.live = live
        clean = self.cleaner.process if self.cleaner else (lambda f: f)
        v = self.cfg.vad
        max_s = max_s or v.max_utterance_s
        spec_after = (v.get("speculate_after_ms", 500) / 1000) if speculate else 0
        spec = None
        self.speculation = None
        self.vad.reset()
        self.finish_now.clear()
        self.recording = True
        preroll = collections.deque(maxlen=max(1, int(v.preroll_ms / 80)))
        captured: list[np.ndarray] = []
        speaking, silence_s, speech_s = False, 0.0, 0.0
        deadline = time.monotonic() + no_speech_timeout
        try:
            while True:
                frame = self.mic.read(timeout=1.0)
                if frame is None:
                    if not self.mic.active or self.mic.broken:  # stopped, or unplugged mid-request
                        return None
                    continue
                if self.hud:
                    self.hud.level(level_of(frame))
                prob = self.vad.probability(frame)
                cleaned = clean(frame)
                if hold:  # hold-to-talk: everything until the key comes up
                    captured.append(cleaned)
                    if self.live:
                        self.live.feed(cleaned)
                    if not self._hotkey_held():
                        return np.concatenate(captured) if len(captured) > 4 else None
                    continue
                if not speaking:
                    preroll.append(cleaned)
                    if prob >= v.threshold:
                        speaking = True
                        captured.extend(preroll)
                        if self.live:  # nothing is sent before you speak
                            self.live.feed(np.concatenate(preroll))
                        speech_s = FRAME_S
                    elif time.monotonic() > deadline or self.finish_now.is_set():
                        return None
                    continue
                captured.append(cleaned)
                if self.live:
                    self.live.feed(cleaned)
                if prob >= v.threshold * 0.7:
                    silence_s = 0.0
                    speech_s += FRAME_S
                    spec = None  # you carried on: that transcript is missing the new words
                else:
                    silence_s += FRAME_S
                    # One Whisper run at a time: a stale one still running would only slow the next.
                    idle = self._in_flight is None or self._in_flight.done.is_set()
                    if spec_after and spec is None and silence_s >= spec_after and speech_s >= 0.25 and idle:
                        # Pad with the pause heard so far, out to the length the final recording will
                        # have: the denoiser estimates the noise from the whole clip, and a shorter
                        # tail made it (and so Whisper) come out slightly differently.
                        pause = captured[-max(1, round(silence_s / FRAME_S)):]
                        missing = max(0, round((allowed_pause(v, speech_s) - silence_s) / FRAME_S))
                        audio = np.concatenate(captured + [pause[i % len(pause)] for i in range(missing)])
                        spec = self._in_flight = _Speculation(audio, self._transcribe)
                done = (silence_s >= allowed_pause(v, speech_s) or self.finish_now.is_set()
                        or len(captured) * FRAME_S >= max_s)
                if done:
                    if speech_s < 0.25:  # a cough or a click, not a request: keep waiting
                        speaking, silence_s, speech_s, spec = False, 0.0, 0.0, None
                        captured.clear()
                        if self.live:  # the cough went up too: start a clean session
                            self.live.cancel()
                            self.live = self._open_live()
                        if self.finish_now.is_set():
                            return None
                        continue
                    self.speculation = spec
                    return np.concatenate(captured)
        finally:
            self.recording = False

    def hear_answer(self) -> str:
        """For "Shall I go ahead?": listen briefly and return what was said ('' if nothing)."""
        self.mic.drain()
        if not self._open_mic():
            return ""  # no answer = no
        self._chime()
        if self.hud:
            self.hud.level(0.0)
        audio = self.record(no_speech_timeout=8, max_s=8)
        if audio is None:
            if self.console:
                self.console.tool_log("(no answer: taking that as a no)")
            return ""
        text = self.stt.hear(prepare(audio, self._denoise())[0], answer=True).text
        if self.console:
            self.console.tool_log(f"heard: {text or '(nothing clear)'}")
        return text
