"""The safety gate's rules: intent grounding and the PowerShell blocklist.

Grounding: an action only runs when the user's own words ask for that kind of thing. A small model sometimes
"helps" with a tool nobody asked for (asked to check WhatsApp, it tried to force-quit Edge; given a misheard
sentence, it set the volume to 100%). Each skill brick declares its own words (GROUNDING); the registry
collects them. PowerShell has none: it always shows the command and asks. Tier rules (what needs a "Shall I
go ahead?") live next to each tool (Tool.tier); the registry asks.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Callable

Rule = tuple[str, Callable[[dict], bool], str, str]  # (tool, applies to these args, words needed, why refused)

_FORCE_WORDS = r"\b(force|kill|terminate)\b"


def ground_args(tool: str, args: dict, user_words: str) -> dict:
    """Quietly drop escalations nobody asked for: an unrequested `force` becomes a normal close."""
    if tool == "close_app" and args.get("force") and not re.search(_FORCE_WORDS, user_words, re.IGNORECASE):
        return {k: v for k, v in args.items() if k != "force"}
    return args


@lru_cache(maxsize=1)
def _all_rules() -> tuple[Rule, ...]:
    from mary import skills

    return tuple(skills.grounding(skills.load()))


def ungrounded_reason(tool: str, args: dict, user_words: str, rules: list[Rule] | None = None) -> str | None:
    """Why this call wasn't asked for, or None if the user's words support it. `rules`: the switched-on
    bricks' GROUNDING (default: every brick's)."""
    for name, applies, pattern, reason in rules if rules is not None else _all_rules():
        if name == tool and applies(args) and not re.search(pattern, user_words, re.IGNORECASE):
            return reason
    return None


@lru_cache(maxsize=4)
def _compiled(patterns: tuple[str, ...]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def powershell_block_reason(command: str, patterns: list[str]) -> str | None:
    """The offending text if `command` matches any blocklisted pattern, else None.

    Matching is deliberately broad (aliases, .NET calls, encoded commands) and errs on the
    side of refusing: a refused harmless command costs a retry, a missed one could cost data.
    """
    padded = f" {command} "  # lets patterns like '\s-enc\s' match at the edges
    for rx in _compiled(tuple(patterns)):
        m = rx.search(padded)
        if m:
            return m.group(0).strip()
    return None
