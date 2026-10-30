"""Brick: the time and the date, as an instant answer (no tool; the brain also sees them in <context>)."""
from __future__ import annotations

import re
from datetime import datetime

from mary.reflexes import Reflex

GUIDE = "The time and date are in the <context> block: just say them."


def _answer(text: str) -> str:
    now = datetime.now()
    if re.search(r"\b(date|day|today|month|year)\b", text):
        return f"It's {now:%A}, {now.day} {now:%B %Y}."
    return f"It's {now:%I:%M %p}.".replace("It's 0", "It's ")


REFLEXES = [
    Reflex(r"what(?:'s| is) the time(?: now| right now)?|what time is it(?: now| right now)?|"
           r"(?:tell me |do you know )?(?:the )?time|do you know what time it is|"
           r"what(?:'s| is) (?:the |today's )?date(?: today)?|what(?:'s| is) today(?:'s date)?|"
           r"what day is (?:it|today)(?: today)?|which day is it|what month is it",
           answer=_answer),
]
