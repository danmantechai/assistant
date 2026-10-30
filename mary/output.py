"""Output sinks: everything MARY shows or says goes through one interface.

Phase 1 has the console. The HUD (Phase 3) and streaming TTS (Phase 4) implement the
same methods, and MultiOutput fans out to several at once, so the brain never cares
where its words end up.
"""
from __future__ import annotations

import sys
import threading

# HUD states (see the HUD spec): idle is hidden, the rest are visible styles.
IDLE, LISTENING, THINKING, DEEP_THINKING, SPEAKING, CONFIRMING, ERROR = (
    "idle", "listening", "thinking", "deep_thinking", "speaking", "confirming", "error")

YES_WORDS = {"y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go", "go ahead", "do it",
             "please", "please do", "proceed", "confirm", "affirmative", "absolutely", "of course"}


def is_yes(answer: str) -> bool:
    a = answer.strip().lower().rstrip(".!")
    return a in YES_WORDS or a.startswith(("yes", "yeah", "go ahead", "do it", "sure"))


class Output:
    """Base sink; every method is optional to override."""

    def state(self, name: str) -> None: ...
    def user_text(self, text: str) -> None: ...
    def reply_start(self) -> None: ...
    def reply_delta(self, text: str) -> None: ...
    def reply_end(self) -> None: ...
    def thinking_delta(self, text: str) -> None: ...
    def tool_log(self, line: str) -> None: ...
    def status(self, text: str) -> None: ...          # about MARY herself (mic, model, speaker), not a request
    def notify(self, text: str) -> None: ...
    def asking(self, question: str) -> None: ...      # show a question another sink is asking
    def level(self, value: float) -> None: ...        # live audio level 0..1 (mic or speech)
    def hide_overlay(self) -> None: ...               # get out of the way of a screen capture
    def show_overlay(self) -> None: ...

    def confirm(self, question: str) -> bool:
        return False  # a sink that cannot ask never approves anything

    def say(self, text: str) -> None:
        self.reply_start()
        self.reply_delta(text)
        self.reply_end()

    def aside(self, text: str) -> None:
        """A short line while she works ("Let me have a look…"): said at once, not part of the answer."""
        self.say(text)


class MultiOutput(Output):
    """Fan out to several sinks. The first sink that can ask handles confirm()."""

    def __init__(self, *sinks: Output):
        self.sinks = list(sinks)

    def _each(self, method: str, *args) -> None:
        for sink in self.sinks:
            getattr(sink, method)(*args)

    def state(self, name): self._each("state", name)
    def user_text(self, text): self._each("user_text", text)
    def reply_start(self): self._each("reply_start")
    def reply_delta(self, text): self._each("reply_delta", text)
    def reply_end(self): self._each("reply_end")
    def thinking_delta(self, text): self._each("thinking_delta", text)
    def tool_log(self, line): self._each("tool_log", line)
    def status(self, text): self._each("status", text)
    def notify(self, text): self._each("notify", text)
    def asking(self, question): self._each("asking", question)
    def level(self, value): self._each("level", value)
    def hide_overlay(self): self._each("hide_overlay")
    def show_overlay(self): self._each("show_overlay")
    def aside(self, text): self._each("aside", text)

    def confirm(self, question: str) -> bool:
        for sink in self.sinks[1:]:  # e.g. the HUD shows the question while the console asks it
            sink.asking(question)
        return self.sinks[0].confirm(question)


class ConsoleOutput(Output):
    """Text-mode terminal output with a little colour."""

    CYAN, AMBER, DIM, RED, GREEN, RESET = "\033[96m", "\033[93m", "\033[90m", "\033[91m", "\033[92m", "\033[0m"

    def __init__(self, debug: bool = False, name: str = "MARY", echo_user: bool = False):
        self.debug = debug
        self.name = name
        self.echo_user = echo_user  # voice mode: print what MARY heard (in text mode you typed it)
        self._lock = threading.Lock()
        self._in_reply = False
        self._in_thinking = False

    def _write(self, text: str) -> None:
        # A console that can't show a character, or no console at all (pythonw), must never fail a request.
        with self._lock:
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except UnicodeEncodeError:
                encoding = getattr(sys.stdout, "encoding", None) or "ascii"
                sys.stdout.write(text.encode(encoding, "replace").decode(encoding))
            except (AttributeError, OSError, ValueError):
                pass

    def state(self, name: str) -> None:
        if name == DEEP_THINKING:
            self._write(f"{self.AMBER}  … thinking it through{self.RESET}\n")
        elif name == ERROR:
            self._write(f"{self.RED}  ! something went wrong{self.RESET}\n")

    def user_text(self, text: str) -> None:
        if self.echo_user and text:
            self._write(f"You (voice) › {text}\n")

    def asking(self, question: str) -> None:
        self._write(f"{self.AMBER}{self.name} › {question} (answer out loud){self.RESET}\n")

    def reply_start(self) -> None:
        self._end_thinking()
        self._in_reply = True
        self._write(f"{self.CYAN}{self.name} ›{self.RESET} ")

    def reply_delta(self, text: str) -> None:
        self._write(text)

    def reply_end(self) -> None:
        if self._in_reply:
            self._write("\n")
        self._in_reply = False

    def thinking_delta(self, text: str) -> None:
        if not self.debug:
            return
        if not self._in_thinking:
            self._in_thinking = True
            self._write(f"{self.DIM}  [thinking] ")
        self._write(text.replace("\n", " "))

    def _end_thinking(self) -> None:
        if self._in_thinking:
            self._in_thinking = False
            self._write(f"{self.RESET}\n")

    def tool_log(self, line: str) -> None:
        self._end_thinking()
        colour = self.GREEN if line.startswith("✓") else self.RED if line.startswith("✗") else self.DIM
        self._write(f"  {colour}{line}{self.RESET}\n")

    def status(self, text: str) -> None:
        self._end_thinking()
        self._write(f"  {self.GREEN if text.startswith('✓') else self.AMBER}{text}{self.RESET}\n")

    def notify(self, text: str) -> None:
        self._write(f"\n{self.AMBER}⏰ {text}{self.RESET}\nYou › ")

    def confirm(self, question: str) -> bool:
        self._end_thinking()
        self._write(f"{self.AMBER}{self.name} › {question} [y/n]{self.RESET} ")
        try:
            return is_yes(input())
        except EOFError:
            return False
