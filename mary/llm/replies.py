"""Reply hygiene: the chatbot habits a small model brings along, which a voice assistant shouldn't say.

The system prompt already forbids them, but a 4B model doesn't reliably follow "don't" rules, so code
removes them: filler sentences are dropped as the reply streams (before they're spoken), and a reply
that was nothing but filler means MARY didn't understand, so she asks again instead of greeting you.
"""
from __future__ import annotations

import re

CLARIFY = "Sorry, I didn't quite catch that. Could you say it again?"

# Chatbot habits: never worth saying, in either mode.
_CHATBOT = [
    r"(?:is there )?anything else(?: (?:i|you)\b.*)?",
    r"(?:just )?let me know\b.*",
    r"feel free to\b.*",
    r"if you (?:need|have|want|'d like|would like)\b.*",
    r"(?:so )?(?:how|what) (?:else )?can i (?:help|assist|do for)\b.*",
    r"(?:would|do) you (?:like|want|need) (?:me to )?(?:help with |do )?(?:anything|something) else\b.*",
    r"(?:would|do) you (?:like|want) me to do anything (?:else )?with (?:this|that|it)\b.*",
    r"i'?m (?:always )?(?:here|happy|glad) to (?:help|assist)\b.*",
    r"i'?m here (?:for you|if you need)\b.*",
    r"i'?m here to help\b.*",
    r"(?:happy|glad) to help\b.*",
    r"hope (?:this|that) helps\b.*",
    r"how can i (?:help|assist) you(?: today)?",
    r"is there anything (?:else )?(?:i can|you need|you'd like|you would like)\b.*",
    r"what can i (?:do|help) (?:for|with)\b.*",
]
# The old mode also drops these: greeting back, an empty acknowledgement, or reading out the <context> block,
# in answer to something it didn't understand ("Hello, Sam. I see it is 18:41 on Monday, with volume at
# full." for a misheard "what's the weather"). In the new mode he can greet her, and she greets him back.
_NOT_UNDERSTOOD = [
    r"(?:hello|hi|hey)(?:,? [\w']+)?",
    r"i see (?:that )?(?:it'?s|it is)\b.*\b(?:volume|\d{1,2}:\d{2}|o'?clock)\b.*",
    r"(?:got it|okay|ok|alright|understood|sure|noted)(?:,? [\w']+)?",  # an acknowledgement with nothing in it
    r".*\b\d{1,2}:\d{2}\b.*\bvolume\b.*|.*\bvolume\b.*\b\d{1,2}:\d{2}\b.*",  # reciting the time *and* the volume
]
_FILLER = re.compile(r"^(?:" + "|".join(_CHATBOT + _NOT_UNDERSTOOD) + r")\s*[.!?]*$", re.I)
_CHATBOT_ONLY = re.compile(r"^(?:" + "|".join(_CHATBOT) + r")\s*[.!?]*$", re.I)
# A tool call the model wrote out as text instead of making it ("<parameter=context> … </tool_call>"):
# measured 2 in 8 replies with an early version of the new prompt. Never spoken, nor anything after it.
_MARKUP = re.compile(r"</?(?:tool_call|function|parameter)\b|<(?:function|parameter)=", re.I)
_THINK_TAG = re.compile(r"\s*</?think>\s*", re.I)

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Where a reply's first sentence may be cut so speaking can start before the sentence is finished.
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
_EARLY_WORDS = 5   # at least this many words before the cut, so the voice gets a natural phrase
# Every filler pattern is recognisable from how the sentence starts (the status recital aside, which
# needs a clock time and is rare in a first clause): a first clause not starting like one is safe to say.
_FILLER_START = re.compile(r"^(?:is there|anything|(?:just )?let me|feel free|if you|(?:so )?(?:how|what)(?: else)? can|"
                           r"(?:would|do) you|i'?m (?:always |here|happy|glad)|happy|glad|hope|hello|hi|hey|got it|"
                           r"okay|ok|alright|understood|sure|noted|i see)\b", re.I)


def first_clause(text: str, min_words: int = _EARLY_WORDS) -> tuple[str, str] | None:
    """Split at the first comma/semicolon/colon with at least `min_words` before it ("Yes, you should take an
    umbrella, because…" -> "Yes, you should take an umbrella," + the rest), or None."""
    for m in _CLAUSE_END.finditer(text):
        if len(text[:m.start()].split()) >= min_words:
            return text[:m.start()], text[m.end():]
    return None


def is_filler(sentence: str, greetings_ok: bool = False) -> bool:
    return bool((_CHATBOT_ONLY if greetings_ok else _FILLER).match(sentence.strip().strip("\"'")))


class ReplyFilter:
    """Feed streamed text in; get back only the sentences worth saying, each once it's complete."""

    def __init__(self, greetings_ok: bool = False):
        self.greetings_ok = greetings_ok  # new mode: only chatbot habits are dropped
        self.buffer = ""
        self.kept: list[str] = []
        self.dropped: list[str] = []
        self.started = False  # part of the current sentence already went out (its first clause)
        self.markup = False   # the model wrote tool-call markup: drop it and everything after

    def _judge(self, sentence: str) -> str:
        sentence = _THINK_TAG.sub(" ", sentence).strip()  # a stray "</think>" from the model, never spoken
        if not sentence:
            return ""
        if self.markup or _MARKUP.search(sentence):
            self.markup, self.started = True, False
            self.dropped.append(sentence)
            return ""
        if self.started:  # the rest of a sentence whose start was already judged and sent
            self.started = False
            self.kept[-1] += " " + sentence
            return sentence + " "
        if is_filler(sentence, self.greetings_ok):
            self.dropped.append(sentence)
            return ""
        self.kept.append(sentence)
        return sentence + " "

    def feed(self, text: str) -> str:
        self.buffer += text
        *done, self.buffer = _SENTENCE_END.split(self.buffer)
        out = "".join(self._judge(s) for s in done)
        if not self.kept and not self.started:  # only the reply's first sentence: that's the one we wait for
            out += self._early_clause()
        return out

    def _early_clause(self) -> str:
        """The first sentence's first clause, once it's long enough and can't be filler."""
        parts = first_clause(self.buffer)
        if not parts or _FILLER_START.match(parts[0].strip()) or "<" in self.buffer:
            return ""
        clause, self.buffer = parts
        self.kept.append(clause.strip())
        self.started = True
        return clause.strip() + " "

    def flush(self) -> str:
        out, self.buffer = self._judge(self.buffer), ""
        return out.rstrip()

    @property
    def only_filler(self) -> bool:
        return bool(self.dropped) and not self.kept
