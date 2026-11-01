"""The background pass: when nobody is using MARY, the brain reads the new diary lines and proposes what is worth
remembering as facts (and, if asked, one short reflection per finished day).

It is off unless switched on (memory.consolidate.enabled): with a model in the cloud, the diary lines go there.
What it may do is limited in code, not trusted to the model:
  - the model only sees what the person said (the "Asked" part of a line), never her replies, and never a line made
    with a private tool (messages, screen text)
  - it answers in JSON that fits a schema; a fact is kept only when its source is one of the lines it was shown, and
    its words and names are found in that line (it may put them in the first person, not make things up)
  - a change is a fact that names, by number, the known fact it replaces: that one is ended with a date and a link
  - a fact already known is merged, not added again
  - every change is written into the diary ("Memory: learned …"), and a failed call changes nothing and is retried later
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta

from mary.memory import clock
from mary.memory.search import STOP, _tokens, terms
from mary.memory.store import Memory, MemoryStore, clean

log = logging.getLogger(__name__)

MARKER = "consolidated_upto"
BATCH = 40
RETRY_AFTER_S = 1800

FACTS_SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {"fact": {"type": "string"}, "source": {"type": "integer"},
                       "importance": {"type": "integer"}, "replaces": {"type": ["integer", "null"]}},
        "required": ["fact", "source", "importance"]}}},
    "required": ["facts"]}
DAY_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}

SYSTEM = ("You keep a person's long-term memory. Below are things the person said to their assistant, each with a "
          "number. Write down the lasting facts about the person's life that they stated themselves: who people are to "
          "them, where they live or work, preferences, pets, health, plans with a date. Rules: only what the person "
          "said, never what you guess; skip questions, commands, small talk and anything temporary (a timer, a song, "
          "the weather); write each fact as the person would say it, in the first person (\"My sister is Ama\"); give "
          "the number of the line it came from; importance runs from 1 (trivia) to 10 (never forget). If a fact "
          "replaces one under \"Already known\" because it changed, give that fact's number in replaces. Reply with "
          "JSON only. If there is nothing, reply {\"facts\": []}.")
DAY_SYSTEM = ("Summarise one person's day with their assistant in one or two plain sentences, from the list of what "
              "they asked and what was used. Say what the day was about, not every step. Reply with JSON only: "
              "{\"summary\": \"…\"}.")

_ASKED = re.compile(r'^Asked: "(?P<text>.*?)"(?: \(×\d+\))?\.')
PRIVATE = "(private; reply not kept)"


def said(m: Memory) -> str:
    """What the person said in a diary line (a line they wrote by hand is all theirs)."""
    found = _ASKED.match(m.text)
    return found["text"] if found else m.text


def grounded(fact: str, source: str) -> bool:
    """Do the fact's words and names come from the line it says it came from?"""
    have = set(_tokens(source))
    words = [w for w, _ in terms(fact)]
    # A capitalised word that isn't filler is a name (even first in the fact): it has to be in the line.
    names = [_tokens(raw)[0] for raw in re.findall(r"[^\W_]+", fact)
             if raw[0].isupper() and _tokens(raw) and _tokens(raw)[0] not in STOP and not raw.isdigit()]
    if not words or any(n not in have for n in names):
        return False
    return sum(w in have for w in words) / len(words) >= 0.5


class Consolidator:
    def __init__(self, store: MemoryStore, brain, idle=lambda: True):
        self.store, self.brain, self.idle = store, brain, idle
        self._thread: threading.Thread | None = None
        self._retry_at = 0.0
        self.last: dict | None = None   # what the last pass did (counts only)

    # ------------------------------------------------------------------ when

    def pending(self) -> list[Memory]:
        upto = int(self.store.db.meta(MARKER, "0"))
        rows = self.store.list(kinds=("diary",), current=True, limit=BATCH * 5, newest_first=False)
        return [m for m in rows if m.id > upto and m.source == "diary" and PRIVATE not in m.text]

    def due(self) -> bool:
        if not self.store.setting("consolidate.enabled") or time.monotonic() < self._retry_at or not self.idle():
            return False
        rows = self.pending()
        if len(rows) >= self.store.setting("consolidate.min_lines"):
            return True
        return bool(rows) and clock.now() - rows[0].happened > timedelta(days=1)

    def tick(self) -> None:
        """Called every minute by the worker: starts a pass on its own thread (a model call can take a while, and the
        worker must stay free to write the files) when one is due."""
        if self._thread is not None and self._thread.is_alive():
            return
        if self.due():
            self._thread = threading.Thread(target=self.run_once, name="memory-consolidate", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------ the pass

    def run_once(self) -> dict:
        report = {"learned": 0, "merged": 0, "ended": 0, "skipped": 0, "reflections": 0, "failed": False}
        try:
            self._facts(report)
            if self.store.setting("consolidate.reflections"):
                self._reflect(report)
        except Exception as e:   # the model is down, or answered nonsense: nothing changed, try again later
            report["failed"] = True
            self._retry_at = time.monotonic() + RETRY_AFTER_S
            log.warning("memory pass failed (%s); trying again later", type(e).__name__)
        self.last = report
        log.info("memory pass: %s", {k: v for k, v in report.items()})   # counts only
        return report

    def _known(self, batch: list[Memory], limit: int = 40) -> list[Memory]:
        """The facts the model may say a new one replaces: the ones the new lines are about first, then the most
        important and the newest, up to `limit` (a change often doesn't look like what it changes: "I just moved to
        Porto" and "I live in Lisbon"). It can only name one of these."""
        seen: dict[int, Memory] = {}
        for m in batch:
            for h in self.store.search(said(m), kinds=("fact",), k=3, min_relevance=0.4, dates=False):
                seen[h.memory.id] = h.memory
        rest = sorted(self.store.list(kinds=("fact",), current=True, limit=1000), key=lambda m: (-m.importance, -m.id))
        for m in rest:
            seen.setdefault(m.id, m)
        return list(seen.values())[:limit]

    def _facts(self, report: dict) -> None:
        batch = self.pending()[:BATCH]
        if not batch:
            return
        known = self._known(batch)
        user = ("Already known:\n" + ("\n".join(f"#{m.id} {m.text}" for m in known) or "(nothing yet)") +
                "\n\nThe person said:\n" + "\n".join(f'[{m.id}] "{said(m)}"' for m in batch))
        answer = self.brain.json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], FACTS_SCHEMA)
        by_id = {m.id: m for m in batch}
        known_ids = {m.id for m in known}
        changes = []
        for item in answer.get("facts") or []:
            fact = clean(str(item.get("fact") or ""))
            source = by_id.get(item.get("source"))
            importance = item.get("importance")
            if (not fact or len(fact) > 200 or source is None or not isinstance(importance, int) or importance < 4
                    or not grounded(fact, said(source))):
                report["skipped"] += 1
                continue
            replaces = item.get("replaces") if item.get("replaces") in known_ids else None
            old = self.store.get(replaces) if replaces else None
            memory, status = self.store.add_fact(fact, importance=min(10, importance) / 10, source=f"diary:{source.id}",
                                                 replaces=replaces, happened=source.happened)
            if status == "already":
                report["merged"] += 1
                continue
            report["learned"] += 1
            line = f'Memory: learned "{fact}"'
            if status == "replaced" and old is not None:
                report["ended"] += 1
                line += f' (it replaces "{old.text}")'
            changes.append(line + ".")
        for line in changes:   # what changed is in the diary too
            self.store.add("diary", line, importance=0.3, source="memory")
        self.store.db.set_meta(MARKER, str(max(m.id for m in batch)))

    def _reflect(self, report: dict, days: int = 3) -> None:
        today = clock.now().date()
        have = {m.happened.date() for m in self.store.list(kinds=("reflection",), current=None, limit=10000)}
        by_day: dict = {}
        for m in self.store.list(kinds=("diary",), current=True, limit=5000, newest_first=False):
            if m.source == "diary" and m.happened.date() < today and m.happened.date() not in have:
                by_day.setdefault(m.happened.date(), []).append(m)
        for day, rows in sorted(by_day.items())[:days]:
            if len(rows) < 3 or any(PRIVATE in m.text for m in rows):
                continue   # too little to say, or a private moment in it: no summary
            lines = "\n".join(f"- {m.happened:%H:%M} {m.text}" for m in rows[:60])
            answer = self.brain.json([{"role": "system", "content": DAY_SYSTEM},
                                      {"role": "user", "content": lines}], DAY_SCHEMA, max_tokens=300)
            summary = clean(str(answer.get("summary") or ""))
            if summary and len(summary) <= 400:
                self.store.add("reflection", summary, happened=datetime.combine(day, datetime.min.time()) +
                               timedelta(hours=23, minutes=30), importance=0.4, source=f"diary:{day}")
                report["reflections"] += 1
