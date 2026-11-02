"""What the memory adds to a request: a few lines in the request's <context> block (after the cached prefix, so the
cache is never touched), only when something matches well.

Facts and day summaries that match the words of a request may come along with any request ("text Marcus" brings
what's known about Marcus). The diary joins only when the request is about the past (a date, "remember", "did I", "last
time", "you told me"): otherwise "what's the weather" would drag in every weather question ever asked. Anything else
the model can still look up itself with the memory tool's recall.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from mary.memory import clock, when
from mary.memory.store import MemoryStore

log = logging.getLogger(__name__)

# A request about the past: the memory verbs, past-tense question forms and "you told me".
_PAST = re.compile(r"""\b(?:remember|recall|remind\s+me\s+(?:what|when|how|who|where)|earlier|before|previous(?:ly)?|
    last\s+time|used\s+to|again|(?:i|you|we)\s+(?:told|said|mentioned|asked|wrote|noted|saved|searched|looked)|
    (?:told|asked)\s+(?:you|me)|(?:did|have|had)\s+(?:i|we|you)|what\s+(?:was|were)|when\s+(?:was|were)|
    where\s+(?:was|were)|who\s+(?:was|were)|how\s+(?:much|many|long)\s+(?:was|were)|which\s+\w+\s+(?:was|were)|
    what\s+time\s+(?:was|were)|what\s+happened)\b""", re.I | re.X)
_HISTORY = re.compile(r"\b(?:(?:used|use)\s+to|previously|formerly|before\s+(?:that|then)|back\s+then|no\s+longer|"
                      r"any\s*more|old)\b|\bbefore\s*[?.!]*$", re.I)


def seeks_memory(text: str, today=None) -> bool:
    """Is this request about the past (so the diary is worth looking at)?"""
    return bool(_PAST.search(text)) or when.past_range(text, (today or clock.now().date())) is not None


# A question about the user's own life ("where do I live", "what's my dentist called"): facts they've told her may answer
# it, so they come along at the usual bar. (The diary still needs a past.)
_ASKS = re.compile(r"^\s*(?:what|where|which|who|whom|whose|when|how|do|does|did|am|is|are|was|were|can|could|should)\b"
                   r"|\?\s*$", re.I)
_YOU = re.compile(r"\b(?:i|me|my|mine|we|our|ours)\b|\bi'(?:m|ve|d|ll)\b|\bwhat's\s+my\b", re.I)


def asks_about_you(text: str) -> bool:
    return bool(_ASKS.search(text) and _YOU.search(text))


def wants_history(text: str) -> bool:
    """Asking about what used to be true: ended facts count too."""
    return bool(_HISTORY.search(text))


_PHOTO_MOMENT_CUE = re.compile(
    r"\b(?:photos?|pictures?|shots?|albums?|moments?|remember\s+when|wedding|birthday|christmas|easter|graduation|"
    r"holiday|vacation|trip|picnic|barbecue|bbq|concert|festival|hike|camping|beach|coast|harbou?r|"
    r"summer|winter|spring|autumn|fall)\b",
    re.I,
)


def seeks_photo_moments(lib, text: str, today=None, *, cue: bool | None = None) -> bool:
    """Fast gate (<0.1 ms on unrelated requests): is `text` talking about a past moment, date, place, person, or photo?"""
    if lib is None or not text:
        return False
    vocab = lib.recall_vocab()
    if not vocab.get("has_photos"):
        return False
    today = today or clock.now().date()
    if cue is None:
        cue = seeks_memory(text, today)
    if cue or _PHOTO_MOMENT_CUE.search(text):
        return True
    from mary.photos import dates

    if dates.parse_query_when(text, today) is not None:
        return True
    self_p = lib.self_person() if hasattr(lib, "self_person") else None
    if self_p and self_p.get("born"):
        if dates.parse_age_when(text, int(self_p["born"]), today) is not None:
            return True
    low = text.lower()
    for _, _, low_name in vocab.get("people") or ():
        if low_name and low_name in low and re.search(rf"\b{re.escape(low_name)}\b", low):
            return True
    for pl in vocab.get("places") or ():
        if pl and pl in low and re.search(rf"\b{re.escape(pl)}\b", low):
            return True
    themes = vocab.get("themes")
    if themes and (themes & set(re.findall(r"[a-z]{3,}", low))):
        return True
    return False


def describe(h) -> str:
    m = h.memory
    day = m.happened.strftime("%a %d %b")
    label = {"fact": f"fact, learned {day}", "diary": f"diary, {day}", "reflection": f"summary of {day}",
             "core": "you told me"}.get(m.kind, m.kind)
    if m.ended:
        label += f"; no longer true since {m.ended:%d %b}"
    return f"remembered #{m.id} ({label}): {m.text}"


def lines(store: MemoryStore, request: str, now: datetime | None = None) -> list[str]:
    """The context lines for one request ([] when nothing matches well). Never raises: memory must not break a
    request."""
    try:
        now = now or clock.now()
        cue = seeks_memory(request, now.date())
        kinds = ["fact", "reflection", "diary"] if cue else ["fact", "reflection"]
        # Asked about the past or about the user's own life: the usual bar. Otherwise (a command, a chat) only strong
        # evidence comes along: a name the memory knows, or something that means the same.
        bar = store.setting("recall.min_relevance" if cue or asks_about_you(request) else "recall.min_relevance_unasked")
        hits = store.search(request, kinds=kinds, current=None if wants_history(request) else True,
                            k=store.setting("recall.max_items"), cue=cue, now=now, min_relevance=bar)
    except Exception:
        log.exception("memory recall failed")
        return []
    out, budget = [], store.setting("recall.max_tokens") * 3.8   # characters
    for h in hits:
        line = describe(h)
        if out and sum(map(len, out)) + len(line) > budget:
            break
        out.append(line[:int(budget)])

    lib = getattr(store, "photos", None)
    if lib is not None:
        try:
            if seeks_photo_moments(lib, request, now.date(), cue=cue):
                moments = lib.moments_store.recall_for_request(request, now=now, limit=3)
                for m in moments[:3]:
                    out.append(lib.moments_store.describe_for_context(m))
        except Exception:
            log.exception("photo moment recall failed")
    return out

