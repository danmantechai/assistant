"""The semantic router: match a request to an intent by meaning, and run common commands without the LLM.

    request -> guards -> embed -> cosine vs. every example -> best score per intent
            -> margin over the best intent of another group -> slots
            -> direct (run the tool) | hint (LLM, told the router's guess) | llm
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field

import numpy as np
import yaml

from mary.config import expand_path
from mary.router.embedder import Embedder, cached_embeddings
from mary.router.guards import guard_hits
from mary.router.slots import Slot, SlotFiller

log = logging.getLogger(__name__)

_NORMALIZE = re.compile(r"\b(hey|hi|ok|okay)?\s*mary\b|\b(please|kindly|can you|could you|would you|"
                        r"will you|for me)\b|[^\w\s'%.:-]", re.I)
# Intents with an implicit object: "turn it up" / "pause it" don't point back at the conversation.
# "open it" / "open the first one" right after a file search mean a result of that search (open_found).
_IMPLICIT_IT = {"volume", "brightness", "media", "lock_pc", "time_date", "system_status", "open_found"}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _NORMALIZE.sub(" ", text.lower())).strip(" .")


@dataclass
class Intent:
    name: str
    tool: str | None
    slots: str
    examples: list[str]
    group: str | None = None
    strip: list[str] = field(default_factory=list)
    llm_if: re.Pattern | None = None
    replies: list[str] = field(default_factory=list)
    direct_score: float | None = None   # per-intent override (free-text intents like web_search)
    # Commands defined by their first words ("remember …", "call me …"): the free text after them
    # dominates the embedding, so a request starting with `lead` picks the intent without the score test.
    lead: re.Pattern | None = None


@dataclass
class Decision:
    text: str
    route: str                      # "direct" | "hint" | "llm"
    intent: str | None = None
    score: float = 0.0
    margin: float = 0.0
    top: list = field(default_factory=list)     # [(intent, score)] best three
    guards: list = field(default_factory=list)
    tool: str | None = None
    args: dict = field(default_factory=dict)
    slot: Slot | None = None
    reason: str = ""
    ms: float = 0.0

    @property
    def hint(self) -> str | None:
        """Shown to the LLM in the 0.65-0.82 band. Worded as a maybe: weak matches are often wrong."""
        if self.route != "hint":
            return None
        return f"maybe {self.intent} (similarity {self.score:.2f}; ignore if it doesn't fit)"

    def as_log(self) -> dict:
        d = asdict(self)
        d["slot"] = {"ok": self.slot.ok, "why": self.slot.why} if self.slot else None
        d["score"], d["margin"], d["ms"] = round(self.score, 3), round(self.margin, 3), round(self.ms, 1)
        d["top"] = [[n, round(s, 3)] for n, s in self.top]
        return d


class Router:
    def __init__(self, cfg, apps, ctx=None):
        self.cfg = cfg.router
        path = expand_path(self.cfg.intents_file)
        source = path.read_bytes()
        spec = yaml.safe_load(source)["intents"]
        self.intents = {name: Intent(
            name=name, tool=None if d.get("tool") in (None, "none") else d["tool"], slots=d.get("slots", "none"),
            examples=d["examples"], group=d.get("group"), strip=d.get("strip") or [],
            llm_if=re.compile(d["llm_if"], re.I) if d.get("llm_if") else None, replies=d.get("replies") or [],
            direct_score=d.get("direct_score"), lead=re.compile(d["lead"], re.I) if d.get("lead") else None,
        ) for name, d in spec.items()}

        data = expand_path(cfg.paths.data)
        self.embedder = Embedder(self.cfg.model, data / "models" / "fastembed", self.cfg.threads)
        self.example_intents: list[str] = []
        examples: list[str] = []
        for intent in self.intents.values():
            for example in intent.examples:
                examples.append(normalize(example))
                self.example_intents.append(intent.name)
        self.vectors = cached_embeddings(self.embedder, examples, source, data / "router_cache.npz")
        self.names = list(self.intents)
        index = {n: i for i, n in enumerate(self.names)}
        self.example_index = np.array([index[n] for n in self.example_intents])
        self.slots = SlotFiller(cfg, apps, ctx)

    # ------------------------------------------------------------------ matching

    def scores(self, text: str) -> list[tuple[str, float]]:
        """(intent, best cosine over its examples), best first."""
        sims = self.vectors @ self.embedder.embed([normalize(text)])[0]
        best = np.full(len(self.names), -1.0)
        np.maximum.at(best, self.example_index, sims)
        order = np.argsort(-best)
        return [(self.names[i], float(best[i])) for i in order]

    def _margin(self, ranked: list[tuple[str, float]]) -> float:
        name, score = ranked[0]
        group = self.intents[name].group
        rival = next((s for n, s in ranked[1:] if group is None or self.intents[n].group != group), 0.0)
        return score - rival

    def route(self, text: str) -> Decision:
        start = time.perf_counter()
        d = self.decide(text, self.scores(text))
        d.ms = (time.perf_counter() - start) * 1000
        return d

    def decide(self, text: str, ranked: list[tuple[str, float]]) -> Decision:
        """The routing decision for already-computed scores (calibration re-decides with other thresholds)."""
        intent, score = ranked[0]
        led = self._led(text)
        if led and led != intent:
            intent, score = led, dict(ranked)[led]
        d = Decision(text, "llm", intent, score, self._margin(ranked), ranked[:3])
        d.guards = [g for g in guard_hits(text) if not (g == "reference" and intent in _IMPLICIT_IT)]
        d.route, d.reason = self._decide(d, ranked)
        return d

    def _led(self, text: str) -> str | None:
        low = normalize(text)
        return next((n for n, it in self.intents.items() if it.lead and it.lead.search(low)), None)

    def _fallback(self, d: Decision, intent: Intent) -> str:
        """Hint or plain LLM, when the router won't act itself. A weak guess is noise the small model may
        follow ("maybe media" for "Yes.", "maybe find_files" for "What did you find?"), so below
        hint_sure_score a guess is only passed on if that intent's slots fit the words."""
        if d.score < self.cfg.hint_score:
            return "llm"
        if d.score >= self.cfg.get("hint_sure_score", 0.72):
            return "hint"
        return "hint" if self.slots.extract(intent.slots, d.text, intent.strip).ok else "llm"

    def _decide(self, d: Decision, ranked: list[tuple[str, float]]) -> tuple[str, str]:
        cfg = self.cfg
        if d.guards:  # no hint: "don't open spotify" looks like close_app, which would mislead the model
            return "llm", f"guard: {', '.join(d.guards)}"
        intent = self.intents[d.intent]
        fallback = self._fallback(d, intent)
        if intent.llm_if and intent.llm_if.search(d.text):
            return fallback, "intent needs the LLM for this phrasing"
        led = self._led(d.text) == intent.name
        threshold = intent.direct_score or cfg.direct_score
        if d.score < threshold and not led:
            return fallback, f"score {d.score:.2f} < {threshold}"
        if d.margin < cfg.direct_margin and not led:
            return fallback, f"margin {d.margin:.2f} < {cfg.direct_margin} (vs {ranked[1][0]})"
        # Try the best intent's slots, then a same-group sibling that scored nearly as well
        # ("open youtube": no such app, but it is a site). A distant sibling is a different request.
        candidates = [intent] + [self.intents[n] for n, s in ranked[1:]
                                 if intent.group and self.intents[n].group == intent.group
                                 and s >= d.score - 0.08]
        reasons = []
        for cand in candidates:
            slot = self.slots.extract(cand.slots, d.text, cand.strip)
            if slot.ok:
                d.intent, d.tool, d.args, d.slot = cand.name, slot.tool or cand.tool, slot.args, slot
                why = "starts with its lead words" if led else "confident match"
                return "direct", why if cand is intent else f"slots fit sibling {cand.name}"
            reasons.append(f"{cand.name}: {slot.why}")
            d.slot = slot
        return "hint", "slots: " + "; ".join(reasons)

    # ------------------------------------------------------------------ replies

    def reply(self, d: Decision, say: str | None) -> str:
        """Spoken reply for a direct route: a template, or the tool's own words."""
        if d.slot and d.slot.reply:
            return d.slot.reply
        templates = self.intents[d.intent].replies
        if templates and d.slot:
            try:
                return random.choice(templates).format(say=say or "", **d.slot.fill).strip() or (say or "Done.")
            except (KeyError, IndexError):
                pass
        return say or "Done."
