"""Small conversation turns the LLM shouldn't spend ~10 s on: "no", "thanks", "okay", "bye", and a yes or
no to a question MARY herself just asked ("Would you like me to search for it?").

Measured before: "Yes." and "No." each took ~11 s through the LLM, and "No." got back filler ("I see.
Let me know if there's anything else…"). These end the follow-up listening at once instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from mary.output import is_yes

_NO = re.compile(r"^(no|nope|nah|no thanks?|no thank you|not now|not really|no need|no that's all)$")
_DONE = re.compile(r"^(that'?s (all|it|fine|ok|okay|great|good|enough)|nothing( else)?|i'?m (good|fine|ok|okay|done)|"
                   r"all good|never ?mind|forget it|we'?re done)$")
_BYE = re.compile(r"^(bye|bye bye|goodbye|good bye|see you( later)?|good night|night)$")
_THANKS = re.compile(r"^((ok|okay|great|perfect|cool|alright)\s+)?(thanks|thank you|thank you (so|very) much|"
                     r"thanks (a lot|so much)|cheers)$")
_ACK = re.compile(r"^(ok|okay|alright|all right|cool|great|good|nice|perfect|got it|i see|fine|awesome|"
                  r"ok cool|okay cool|ok great|okay great)$")
_OFFER = re.compile(r"\b(would you like|do you want|shall i|should i|want me to|do you need me to)\b[^.!?]*\?$", re.I)


@dataclass
class Act:
    reply: str | None       # what to say (None = nothing: the conversation just ends)
    end: bool = True        # stop listening for follow-ups
    accept: bool = False    # yes to MARY's pending offer: carry it out


def _plain(text: str) -> str:
    t = re.sub(r"[^\w\s']", " ", text.lower())
    t = re.sub(r"\b(mary|please|hey)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def classify(text: str, offer_pending: bool = False, name: str = "", asked: bool = True) -> Act | None:
    """The act for a short conversational turn, or None for a real request. `asked`: MARY's last reply
    was a question, so "yes" answers it (and goes to the model); otherwise "yes" just acknowledges."""
    t = _plain(text)
    if not t or len(t.split()) > 5:
        return None
    if offer_pending and is_yes(t):
        return Act(None, end=False, accept=True)
    if not asked and re.fullmatch(r"(yes|yeah|yep|yup|sure|right|exactly|correct)( (it is|thanks?|thank you))?", t):
        return Act(None)
    if _NO.match(t) or _DONE.match(t):
        return Act("Okay.")
    if _THANKS.match(t):
        return Act("You're welcome.")
    if _BYE.match(t):
        night = "night" in t
        return Act(("Good night" if night else "Bye") + (f", {name}." if name else "."))
    if _ACK.match(t):
        return Act(None)
    return None


_ABOUT_PAGE = re.compile(r"^(?:so |and |ok(?:ay)? )?(?:what did you find|what have you found|what does it say|"
                         r"what do(?:es)? (?:they|it|the results?|the page) say|what are the results|"
                         r"what(?:'s| is) (?:on )?(?:it|there|the page|on the page)|tell me what (?:it says|you found)|"
                         r"read (?:it|that|them|the results?|the page)(?: (?:to|for) me| out)?|any (?:results|luck)|"
                         r"(?:did you find|have you found) (?:anything|something|it))"
                         r"(?: (?:there|online|for me|on (?:it|the page|google|the web)))?$")  # nothing more specific


def asks_about_page(text: str) -> bool:
    """'What did you find?' right after a web search: the answer is on the page, which the model hasn't seen."""
    return bool(_ABOUT_PAGE.match(_plain(text)))


def offer_in(reply: str) -> str | None:
    """MARY's closing yes/no offer ("Would you like me to open it?"), if her reply ends with one."""
    last = re.split(r"(?<=[.!?])\s+", reply.strip())[-1] if reply.strip() else ""
    return last if _OFFER.search(last) else None
