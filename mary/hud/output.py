"""HudOutput: the Output sink that drives the HUD. Safe to call from any thread (it only emits Signals)."""
from __future__ import annotations

import json
import time

from mary.output import CONFIRMING, IDLE, SPEAKING, Output


class HudOutput(Output):
    def __init__(self, window):
        self.w = window

    def _js(self, fn: str, *args) -> None:
        self.w.js.emit(f"hud.{fn}({', '.join(json.dumps(a) for a in args)})")

    def state(self, name: str) -> None:
        if getattr(self.w, "director", None) is not None:
            self.w.director.set_state(name)
        if name == IDLE:
            # The request is done: calm cyan look, then fade out after hud.hide_after_s.
            self._js("state", "listening")
            self.w.command.emit("idle")
        else:
            self.w.command.emit("activity")
            self._js("state", name)

    def speech_audio(self, audio, sample_rate: int = 24000, starts_at: float | None = None) -> None:
        if getattr(self.w, "director", None) is not None:
            self.w.director.feed_audio(audio, sample_rate=sample_rate, starts_at=starts_at)

    def speech_stopped(self) -> None:
        if getattr(self.w, "director", None) is not None:
            self.w.director.stop_speech()

    def user_text(self, text: str) -> None:
        self.w.command.emit("activity")
        self._js("user", text)

    def reply_start(self) -> None:
        self.w.command.emit("activity")  # a reply is coming: stay up even if an earlier "idle" started the timer
        self._js("state", SPEAKING)
        self._js("replyStart")

    def reply_delta(self, text: str) -> None:
        self._js("replyDelta", text)

    def reply_end(self) -> None:
        self._js("replyEnd")

    def tool_log(self, line: str) -> None:
        self._js("log", line)

    def status(self, text: str) -> None:
        self._js("log", text)

    def notify(self, text: str) -> None:
        self.w.command.emit("activity")
        self._js("notify", text)
        self.w.command.emit("idle")

    def asking(self, question: str) -> None:
        self._js("state", CONFIRMING)
        self._js("ask", question)

    def level(self, value: float) -> None:
        self.w.level.emit(float(value))

    def hide_overlay(self) -> None:
        self.w.command.emit("hide_now")
        time.sleep(0.25)  # let the main thread hide the window before the caller captures the screen

    def show_overlay(self) -> None:
        self.w.command.emit("show_now")
