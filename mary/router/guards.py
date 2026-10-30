"""Text guards shared by the agent and the semantic router.

The router never acts on its own when a request is compound, negated, needs reasoning, or points
back at the conversation: those always go to the LLM, however confident the match looks.
"""
from __future__ import annotations

import re

_GUARDS = {
    # After split_clauses, a leftover "then"/"after that" still means a sequence the router can't do.
    "compound": re.compile(r"\b(and then|then|after that|afterwards|and also|as well as)\b", re.I),
    # "don't open spotify" embeds almost exactly like "open spotify".
    "negation": re.compile(r"\b(don'?t|do not|not|never|no longer|without|except|instead( of)?|"
                           r"never ?mind|cancel)\b", re.I),
    # "so should I take an umbrella?": a leading "so"/"and"/"ok" doesn't make it less of a question.
    "reasoning": re.compile(r"^(?:(?:so|and|ok|okay|well|but|then)[\s,]+)?"
                            r"(why|how come|how do|how can|how to|should|explain|compare|what if|what would|"
                            r"which (is|one|should)|is it (better|worth|safe))\b|\bwhat'?s the best\b|"
                            r"\b(figure out|work out|recommend|decide|analy[sz]e|summari[sz]e|pros and cons)\b",
                            re.I),
    "reference": re.compile(r"\b(again|the other one|that one|this one|same (one|thing|as before)|like before|"
                            r"like last time|as before|the previous|the last one|"
                            r"(open|close|delete|remove|move|send|show|read|rename|copy|snap) "
                            r"(it|that|them|those))\b", re.I),
}


def guard_hits(text: str) -> list[str]:
    """Names of the guards that fire for `text`."""
    return [name for name, rx in _GUARDS.items() if rx.search(text)]

# Liberal on purpose: a false positive only costs one extra model call, a miss can drop a step.
_MULTI_PART = re.compile(
    r"\b(and|then|after that|afterwards|also|plus|see if|see whether|check if|check whether|"
    r"tell me|let me know|find out|once)\b", re.IGNORECASE)


def is_multi_part(text: str) -> bool:
    """Does the request (probably) ask for more than one thing?"""
    return bool(_MULTI_PART.search(text))


_ACTION_VERBS = (
    "open|close|quit|exit|play|pause|resume|stop|skip|set|start|launch|check|see|turn|search|find|type|"
    "go|show|tell|take|make|mute|unmute|read|minimize|minimise|maximize|maximise|snap|switch|lock|"
    "create|write|look|put|move|delete|run|increase|decrease|raise|lower|send|note|remind|bring|"
    "navigate|visit|press|save|restore|focus|shut|restart|"
    # questions joined with "and": "how much battery do I have and what's the time"
    "what|what's|whats|how's|hows|how much|how many|when|where|who|is it")
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:,\s*)?(?:\band then\b|\bthen\b|\band also\b|\band\b|\bafter that\b|,)\s+"
    rf"(?=(?:please\s+|also\s+|can you\s+|could you\s+)?(?:{_ACTION_VERBS})\b)", re.IGNORECASE)


def split_clauses(text: str) -> list[str]:
    """'open my browser and open whatsapp and see if I have messages' -> three requests.

    Only splits where an action verb follows, so 'rock and roll' and 'a minute and a half' stay
    whole. A 4B model tends to stop after the first step of a compound request; doing the parts
    one by one (with the conversation carrying context, so 'open it' still works) is reliable.
    """
    parts = [p.strip(" ,.") for p in _CLAUSE_SPLIT.split(text)]
    # "OK, can you open the newest file?" is one request: a lone "OK" / "so" / "um" isn't a part.
    parts = [p for p in parts if p and not _only_filler(p)]
    # "type MrBeast into the search box and search": a lone verb finishes the part before it.
    merged: list[str] = []
    for p in parts:
        if merged and re.fullmatch(r"(?:search|submit|send|go|enter|press enter|hit enter)(?: it)?", p, re.I):
            merged[-1] = f"{merged[-1]} and {p}"
        else:
            merged.append(p)
    return merged or [text]


_FILLER_WORDS = {"ok", "okay", "so", "well", "alright", "right", "um", "umm", "uh", "uhh", "er", "erm", "hmm",
                 "oh", "yeah", "yes", "now", "please", "hey", "hi", "mary", "and", "then", "also"}


def _only_filler(part: str) -> bool:
    return all(w in _FILLER_WORDS for w in re.findall(r"[a-z]+", part.lower()))
