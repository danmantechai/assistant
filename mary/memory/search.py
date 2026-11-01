"""Searching the memory: filters from code (kind, dates, still true), keywords (exact names, numbers, rare words) and
meaning (the small model), merged into one relevance between 0 and 1, then ranked like the Generative Agents paper does:
relevance first, a little recency and importance to break ties. Nothing below `min_relevance` is returned, so
"nothing matches" is a real answer.

Keywords are scored by how much of the question's weight they cover (each word weighs its rarity, as in BM25): a rare
name that matches covers nearly all of it, and a question about "Marcel" does not match a page about "Marcus". Meaning
is the cosine between the question and the record, stretched from the range where related text sits to 0-1.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from mary.memory import clock, when
from mary.memory.store import Memory, MemoryStore, clean

STOP = set("""a about above after again all also am an and any are aren't as at be because been before being below
between both but by can can't could couldn't did didn't do does doesn't doing don't down during each few for from
further get got had has hasn't have haven't having he her here hers herself him himself his how i i'd i'll i'm i've if
in into is isn't it it's its itself just let's like me more most mustn't my myself no nor not of off on once only or
other ought our ours ourselves out over own please same say said she should shouldn't so some such tell than that
that's the their theirs them themselves then there there's these they they'd they're they've this those through to too
under until up us very was wasn't we we'd we're we've were weren't what what's when where which while who whom why
will with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves remember recall mention
mentioned told ask asked know knew tell much many long kind sort thing things something anything one ones lot lots
bit way ways name names happened happen happening else everything s t d ll m re ve""".split())


def _tokens(text: str) -> list[str]:
    """Words as the keyword index sees them: lower case, no accents, split at anything that isn't a letter or digit."""
    flat = unicodedata.normalize("NFKD", text.lower())
    return re.findall(r"[^\W_]+", "".join(ch for ch in flat if not unicodedata.combining(ch)))


def terms(text: str) -> list[tuple[str, bool]]:
    """The words worth matching exactly, as (word, proper): lower case, no accents, no filler. `proper` marks a name
    (written with a capital and not just starting the sentence): a name the memory doesn't know counts against a match,
    while another word it doesn't know is just talk."""
    out: dict[str, bool] = {}
    flat = [(m.group(), m.start()) for m in re.finditer(r"[^\W_]+", text)]
    for k, (raw, at) in enumerate(flat):
        word = _tokens(raw)
        word = word[0] if word else ""
        if not word or word in STOP or not (len(word) >= 2 or word.isdigit()):
            continue
        proper = (raw[0].isupper() and not raw.isdigit() and (k > 0 or len(flat) == 1)
                  and word not in when.WEEKDAYS and word not in when.MONTHS)   # a capital on a day or month isn't a name
        out[word] = out.get(word, False) or proper
    return list(out.items())


@dataclass
class Hit:
    memory: Memory
    relevance: float
    score: float
    how: str        # words | meaning | words+meaning | dates


def _idf(n: int, df: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def keyword_relevance(store: MemoryStore, words: list[tuple[str, bool]], kinds, current, since=None, until=None,
                      limit: int = 100) -> dict[int, float]:
    """id -> how well a record's words answer the question's (0-1): the share of the question's weight they cover
    (each word weighs its rarity), or, when the question names someone, how many of those names the record has."""
    if not words:
        return {}
    c = store.db.conn()
    n = max(1, c.execute("SELECT count(*) FROM memory WHERE owner=? AND library=?",
                         (store.owner, store.library)).fetchone()[0])
    names = [w for w, _ in words]
    marks = ",".join("?" * len(names))
    df = {r["term"]: r["doc"] for r in c.execute(f"SELECT term, doc FROM memory_terms WHERE term IN ({marks})", names)}
    if not df:
        return {}
    # A word the memory has never seen is talk, unless it is a name: then nothing that lacks it can answer.
    weight = {w: _idf(n, df.get(w, 0)) for w, proper in words if w in df or proper}
    names_weight = {w: x for w, x in weight.items() if dict(words)[w]}
    total, name_total = sum(weight.values()), sum(names_weight.values())
    sql, args = store.where(kinds, current, since, until, alias="m")
    match = " OR ".join(f'"{w}"' for w in weight if w in df)
    # FTS5 only finds the candidates (best BM25 first). (BM25 itself gives a word found in half the records no
    # weight at all, so a small memory would score nothing.)
    rows = c.execute(f"SELECT m.id AS id, m.text AS text FROM memory_fts f JOIN memory m ON m.id=f.rowid "
                     f"WHERE memory_fts MATCH ? AND {sql} ORDER BY f.rank LIMIT ?", [match, *args, limit]).fetchall()
    floor, out = store.setting("recall.name_relevance"), {}
    for r in rows:
        seen = set(_tokens(r["text"]))
        matched = [w for w in weight if w in seen]
        covered = sum(weight[w] for w in matched) / total
        if len(matched) < 2:   # one ordinary word in common ("music", "name") proves nothing on its own
            covered = min(covered, store.setting("recall.one_word_relevance"))
        # every name in the question found: the floor, and what else the record shares lifts it toward 1
        named = (sum(x for w, x in names_weight.items() if w in seen) / name_total * (floor + (1 - floor) * covered)
                 if name_total else 0.0)
        out[r["id"]] = min(1.0, max(covered, named))
    return out


def meaning_relevance(store: MemoryStore, question: str, allowed: np.ndarray, limit: int = 50) -> dict[int, float]:
    if not store.meaning or not len(allowed):
        return {}
    lo, hi = store.setting("recall.cosine_low"), store.setting("recall.cosine_high")
    q = store.embedder.embed_query(question)
    return {i: min(1.0, max(0.0, (cos - lo) / (hi - lo))) for i, cos in store.vectors.top("text", q, limit, allowed)}


def search(store: MemoryStore, query: str, *, kinds=None, since: datetime | None = None, until: datetime | None = None,
           current: bool | None = True, k: int = 5, min_relevance: float | None = None,
           now: datetime | None = None, dates: bool = True, cue: bool = False) -> list[Hit]:
    """The best records for a question. A past date in the words ("last Tuesday") narrows it to those days; a
    question that is only a date lists that stretch of the memory, most important first. `cue`: the sentence is
    about the past (lets "on Friday" or "in May" count)."""
    now = now or clock.now()
    kinds = list(kinds) if kinds else None
    threshold = store.setting("recall.min_relevance") if min_relevance is None else min_relevance
    question = clean(query)
    found = when.past_range(question, now.date(), cue=cue) if dates else None
    words = terms(when.strip(question, found))
    if found:   # what was said or learned on the days the words named (a fact's day is when it was learned)
        since = datetime.combine(found.start, datetime.min.time())
        until = datetime.combine(found.end, datetime.min.time()) + timedelta(days=1)
    allowed = store.ids(kinds, current, since, until)
    if not words and found:   # only a date: that stretch of the memory
        hits = [Hit(m, 1.0, _rank(store, m, 1.0, now), "dates") for m in _load(store, allowed)]
        return sorted(hits, key=lambda h: -h.score)[:k]
    if not words:
        return []
    kw = keyword_relevance(store, words, kinds, current, since, until)
    mv = meaning_relevance(store, when.strip(question, found), allowed)
    ids = list({*kw, *mv})
    memories = {m.id: m for m in _load(store, np.array(ids, dtype=np.int64))}
    hits = []
    for i in ids:
        a, b = kw.get(i, 0.0), mv.get(i, 0.0)
        relevance = max(a, b)
        if relevance >= threshold and i in memories:
            how = "words+meaning" if a and b else "words" if a else "meaning"
            hits.append(Hit(memories[i], relevance, _rank(store, memories[i], relevance, now), how))
    return sorted(hits, key=lambda h: -h.score)[:k]


def _load(store: MemoryStore, ids: np.ndarray) -> list[Memory]:
    out = []
    for chunk in range(0, len(ids), 500):
        part = [int(i) for i in ids[chunk:chunk + 500]]
        rows = store.db.conn().execute(
            f"SELECT * FROM memory WHERE id IN ({','.join('?' * len(part))})", part).fetchall()
        from mary.memory.store import _row

        out += [_row(r) for r in rows]
    return out


def _rank(store: MemoryStore, m: Memory, relevance: float, now: datetime) -> float:
    """Generative Agents' recipe: relevance + recency + importance, relevance leading."""
    half = store.setting("scoring.half_life_days").get(m.kind, 30)
    age = max(0.0, (now - m.happened).total_seconds() / 86400)
    recency = 0.5 ** (age / half)
    return relevance + store.setting("scoring.recency") * recency + store.setting("scoring.importance") * m.importance


def pick(store: MemoryStore, text: str, kinds, current: bool | None, margin: float = 0.12,
         min_relevance: float = 0.6) -> tuple[Memory | None, list[Memory]]:
    """Which one does `text` mean? The one that stands out; with no clear winner, the candidates to ask about."""
    hits = search(store, text, kinds=kinds, current=current, k=4, min_relevance=min_relevance, dates=False)
    if not hits:
        return None, []
    if len(hits) > 1 and hits[0].relevance - hits[1].relevance < margin and hits[0].memory.text != hits[1].memory.text:
        close = [h.memory for h in hits if hits[0].relevance - h.relevance < margin]
        return None, close
    return hits[0].memory, []
