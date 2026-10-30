"""Reflexes: the few commands MARY does instantly, without asking her brain (new mode).

A reflex fires only when the *whole* request is plainly that command ("volume up", "turn it down a bit",
"pause", "what time is it"), after polite wrapping ("could you…", "please", "Mom,") is removed. Anything
more ("turn it down and open Spotify", "pause after this song") goes to the brain, whole. Bricks declare
their reflexes (Skill.REFLEXES); there are only a handful, on purpose: the old router matched requests by
resemblance and answered "how are you doing?" with a PC status report.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_POLITE = re.compile(r"^(?:(?:hey|hi|okay|ok)\s+)?(?:(?:mom|mum|mary)\s+)?(?:please\s+)?(?:(?:can|could|would|will) you\s+)?"
                     r"(?:please\s+)?(.*?)(?:\s+(?:please|for me|now|thanks|thank you))*$")


@dataclass
class Reflex:
    pattern: str                                           # must match the whole (plain) request
    tool: str | None = None                                # the tool to run; None: answer() is the reply
    args: Callable[[str], dict | None] = lambda text: {}   # the tool's arguments; None = not this reflex
    answer: Callable[[str], str] | None = None             # the reply for a reflex without a tool
    first: bool = False  # checked before the others: "stop" cancels a pending shutdown before it pauses music


def plain(text: str) -> str:
    """'Mom, could you turn the volume up, please?' -> 'turn the volume up'."""
    t = re.sub(r"[^\w\s'%]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return _POLITE.match(t).group(1).strip()


class Reflexes:
    def __init__(self, reflexes: list[Reflex]):
        ordered = [r for r in reflexes if r.first] + [r for r in reflexes if not r.first]
        self.reflexes = [(re.compile(r.pattern), r) for r in ordered]

    def match(self, text: str) -> tuple[Reflex, dict] | None:
        """The reflex for this request and its tool arguments, or None: then the brain handles it."""
        t = plain(text)
        for rx, reflex in self.reflexes:
            if rx.fullmatch(t):
                args = reflex.args(t)
                if args is not None:
                    return reflex, args
        return None
