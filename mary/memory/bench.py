"""Scoring the memory on a made-up month (tests/fixtures/memory_days.yaml): python -m mary.memory.bench [--size N]

  recall@3   how often a right record is among the first three results, for words only, meaning only, and both
  gate       what goes into a request's context: no lines for ordinary commands, the right ones when a person is named
  timing     what recall costs on the way to a reply, at 1,000 / 10,000 / 50,000 records

The fixture's invented people and days are the only data: nothing here reads the real memory. Questions are split in
two: `dev` is what thresholds are set on, `held` is what is reported on."""
from __future__ import annotations

import argparse
import random
import statistics
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

from mary.config import PROJECT_DIR
from mary.memory import clock, recall, search as searching
from mary.memory.embed import embedding_text
from mary.memory.store import MemoryStore, core_key

FIXTURE = PROJECT_DIR / "tests" / "fixtures" / "memory_days.yaml"

_APPS = ["Spotify", "Notepad", "Calculator", "Word", "Excel", "Settings", "File Explorer", "Paint", "Teams"]
_CITIES = ["Porto", "Lisbon", "Braga", "Faro", "Madrid", "Seville"]
_SKIES = ["sunny", "cloudy", "windy", "light rain", "clear"]
_TOPICS = ["pasta recipes", "cinema times", "laptop deals", "bus timetable", "yoga videos", "board games",
           "weekend markets", "phone cases", "podcast apps", "bicycle lights"]


def load(path: Path = FIXTURE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _filler(rng: random.Random) -> str:
    kind = rng.randrange(8)
    app, city = rng.choice(_APPS), rng.choice(_CITIES)
    n = rng.randrange(2, 40)
    return [
        f'Asked: "open {app}". Used: open_app. Replied: "Opening {app}."',
        f'Asked: "what is the weather in {city}". Used: check_weather. Replied: "{n % 22 + 6} degrees and {rng.choice(_SKIES)} in {city}."',
        f'Asked: "set a timer for {n} minutes". Used: set_timer. Replied: "Timer set for {n} minutes."',
        f'Asked: "turn the volume {rng.choice(["up", "down"])}". Used: set_volume. Replied: "Done."',
        'Asked: "what time is it". Used: get_time. Replied: "It is ' + f'{rng.randrange(7, 22)}:{rng.randrange(10, 59)}."',
        'Asked: "take a screenshot". Used: take_screenshot. Replied: "Saved a screenshot."',
        f'Asked: "how many kilometres from {city} to {rng.choice(_CITIES)}". Used: calculate. Replied: "About {n * 7} kilometres."',
        f'Asked: "search the web for {rng.choice(_TOPICS)}". Used: web. Replied: "Here is what came up."',
    ][kind]


def build(store: MemoryStore, data: dict, filler: int = 300, seed: int = 7) -> dict[str, int]:
    """Put the made-up month into `store`; returns label -> record id."""
    at = lambda d, t="12:00": datetime.fromisoformat(f"{d} {t}")   # noqa: E731
    ids: dict[str, int] = {}
    store.sync_core([c["text"] for c in data["core"]])
    for c in data["core"]:
        ids[c["label"]] = store.by_key("core", core_key(c["text"])).id
    for f in data["facts"]:
        ids[f["label"]] = store.add("fact", f["text"], happened=at(f["found"]), importance=0.8, source="user").id
    for f in data["facts"]:
        if f.get("ended"):
            store.end(ids[f["label"]], "superseded", superseded_by=ids[f["by"]])
            store.db.conn().execute("UPDATE memory SET ended=? WHERE id=?",
                                    (at(f["ended"]).isoformat(timespec="seconds"), ids[f["label"]]))
    for r in data["reflections"]:
        ids[r["label"]] = store.add("reflection", r["text"], happened=at(r["day"], "23:30"), importance=0.4).id
    for d in data["diary"]:
        ids[d["label"]] = store.add("diary", d["text"], happened=datetime.fromisoformat(d["at"]),
                                    importance=0.4, source="diary").id
    rng, first = random.Random(seed), date(2026, 10, 5)
    today = data["today"] if isinstance(data["today"], date) else date.fromisoformat(data["today"])
    span = (today - first).days
    for _ in range(filler):
        when = datetime.combine(first + timedelta(days=rng.randrange(span)), datetime.min.time()) + \
            timedelta(minutes=rng.randrange(7 * 60, 23 * 60))
        store.add("diary", _filler(rng), happened=when, importance=0.3, source="diary")
    store.flush()
    return ids


def _now(data: dict) -> datetime:
    today = data["today"] if isinstance(data["today"], date) else date.fromisoformat(data["today"])
    return datetime.combine(today, datetime.min.time()) + timedelta(hours=12)


def answer(store: MemoryStore, q: dict, data: dict, k: int = 3):
    """What the memory finds for a question (every kind of record: the memory tool's recall)."""
    now = _now(data)
    return store.search(q["q"], k=k, now=now, cue=recall.seeks_memory(q["q"], now.date()),
                        current=None if q.get("history") else True)


def evaluate(store: MemoryStore, data: dict, ids: dict[str, int], split: str | None = None) -> dict:
    """Per question: found (a right record in the top 3), or for no-answer questions, nothing returned."""
    rows = []
    for q in data["questions"]:
        if split and q["split"] != split:
            continue
        hits = answer(store, q, data)
        got = [h.memory.id for h in hits]
        want = [ids[label] for label in q["expect"]]
        bad = [ids[label] for label in q.get("expect_not", [])]
        ok = (not got) if not want else (any(i in got for i in want) and not any(i in got for i in bad))
        rows.append({"q": q["q"], "kind": q["kind"], "split": q["split"], "ok": ok,
                     "got": [(h.memory.text[:50], round(h.relevance, 2), h.how) for h in hits]})
    return {"rows": rows, "score": sum(r["ok"] for r in rows) / max(1, len(rows))}


def by_kind(result: dict) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for r in result["rows"]:
        out.setdefault(r["kind"], [0, 0])
        out[r["kind"]][0] += r["ok"]
        out[r["kind"]][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def gate(store: MemoryStore, data: dict, ids: dict[str, int]) -> dict:
    """The lines that would go into a request's context."""
    now = _now(data)
    noisy = [(text, lines) for text in data["ordinary"] if (lines := recall.lines(store, text, now))]
    named = []
    for m in data["mentions"]:
        lines = recall.lines(store, m["q"], now)
        named.append((m["q"], any(f"#{ids[label]} " in line for line in lines for label in m["expect"])))
    # A question the context could answer: its answer is a kind of record the request's lines are drawn from
    # (the diary only when the question is about the past; core facts are already pinned, never added).
    seeking = []
    for q in data["questions"]:
        past = recall.seeks_memory(q["q"], now.date())
        drawn = ("fact", "reflection", "diary") if past else ("fact", "reflection")
        want = [ids[label] for label in q["expect"] if store.get(ids[label]).kind in drawn]
        if want and (past or recall.asks_about_you(q["q"])):
            lines = recall.lines(store, q["q"], now)
            seeking.append((q["q"], any(f"#{i} " in line for i in want for line in lines)))
    return {"ordinary": len(data["ordinary"]), "noisy": noisy, "named": named, "seeking": seeking}


def timing(store: MemoryStore, data: dict, size: int, repeats: int = 40) -> dict:
    """Milliseconds recall.lines takes for ordinary and for memory-seeking requests, with `size` records."""
    need = size - store.count(None, None)
    if need > 0:
        rng, first = random.Random(3), date(2026, 5, 1)
        rows = [(_filler(rng), datetime.combine(first + timedelta(days=rng.randrange(180)), datetime.min.time()) +
                 timedelta(minutes=rng.randrange(7 * 60, 23 * 60)), 0.3, "diary") for _ in range(need)]
        distinct = sorted({r[0] for r in rows})   # few distinct lines: embed each once and reuse
        vec = dict(zip(distinct, store.embedder.embed_docs([embedding_text("diary", t) for t in distinct])))
        ids = store.bulk_add("diary", rows)
        store.vectors.put_many("text", ids, np.vstack([vec[r[0]] for r in rows]), store.vector_model)
    now = _now(data)
    out = {}
    for name, texts in (("ordinary", data["ordinary"]), ("seeking", [q["q"] for q in data["questions"]])):
        ms = []
        for i in range(repeats):
            t = time.perf_counter()
            recall.lines(store, texts[i % len(texts)], now)
            ms.append((time.perf_counter() - t) * 1000)
        ms.sort()
        out[name] = {"p50": statistics.median(ms), "p95": ms[int(len(ms) * 0.95) - 1], "max": ms[-1]}
    return out


def fresh_store(embedder, folder: Path | None = None, settings: dict | None = None) -> MemoryStore:
    folder = folder or Path(tempfile.mkdtemp(prefix="mary-bench-"))
    return MemoryStore(folder / "memory.db", embedder=embedder, settings=settings, backups=folder / "backups")


def main() -> None:
    import os

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from mary.config import expand_path, load_config
    from mary.memory.embed import BgeEmbedder

    parser = argparse.ArgumentParser(prog="python -m mary.memory.bench")
    parser.add_argument("--sizes", type=int, nargs="*", default=[1000, 10000, 50000])
    args = parser.parse_args()
    cfg = load_config()
    embedder = BgeEmbedder(cfg.router.model, expand_path(cfg.paths.data) / "models" / "fastembed", 2)
    t = time.perf_counter()
    embedder.load()
    print(f"meaning model loaded in {time.perf_counter() - t:.1f} s")
    data = load()
    store = fresh_store(embedder)
    t = time.perf_counter()
    ids = build(store, data)
    print(f"{store.count(None, None)} records made and given vectors in {time.perf_counter() - t:.1f} s\n")
    for mode, hide in (("words only", "meaning"), ("meaning only", "words"), ("both", None)):
        original = (store.embedder, searching.keyword_relevance)
        if hide == "meaning":
            store.embedder = None
        if hide == "words":
            searching.keyword_relevance = lambda *a, **k: {}
        for split in ("dev", "held", "fresh", "fresh2"):
            r = evaluate(store, data, ids, split)
            print(f"{mode:<13}{split:<5} recall@3 {r['score']:.2f}   " +
                  "  ".join(f"{k} {a}/{b}" for k, (a, b) in by_kind(r).items()))
        store.embedder, searching.keyword_relevance = original
    g = gate(store, data, ids)
    print(f"\ngate: {len(g['noisy'])}/{g['ordinary']} ordinary commands got memory lines "
          f"{[t for t, _ in g['noisy']]}\n      named people brought their fact: {sum(ok for _, ok in g['named'])}/"
          f"{len(g['named'])}; memory-seeking questions with lines: {sum(ok for _, ok in g['seeking'])}/"
          f"{len(g['seeking'])}")
    for size in args.sizes:
        r = timing(store, data, size)
        print(f"\n{store.count(None, None):>6} records  " + "   ".join(
            f"{k}: p50 {v['p50']:.1f} ms, p95 {v['p95']:.1f} ms" for k, v in r.items()))
    clock.reset()
    store.close()


if __name__ == "__main__":
    main()
