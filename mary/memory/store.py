"""MemoryStore: the API everything else uses (the agent, the memory brick, the settings page, later the photos).
Nobody else touches the SQL or the markdown files. Methods take and return plain data, so a phone app or a hosted
service could sit behind the same calls.

Every record carries an owner and a library id (made on first run, one of each today). A record is never lost by
accident: forgetting ends it with a date, and only delete() removes one, for good (also from the index, the vectors
and the file's old pages)."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from mary.memory import clock
from mary.memory.db import Database
from mary.memory.embed import embedding_text
from mary.memory.vectors import VectorIndex

log = logging.getLogger(__name__)

KINDS = ("core", "fact", "diary", "reflection")
SPACE = "text"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def core_key(text: str) -> str:
    return "core:" + hashlib.sha1(clean(text).lower().rstrip(".").encode()).hexdigest()[:16]


def memory_id(value) -> int | None:
    """12, "12" or "#12" (how recalled lines name a memory) -> 12."""
    m = re.fullmatch(r"\s*#?\s*(\d+)\s*", str(value))
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class Memory:
    id: int
    kind: str
    text: str
    happened: datetime
    importance: float = 0.5
    source: str = ""
    ended: datetime | None = None
    ended_why: str | None = None
    superseded_by: int | None = None
    owner: str = ""
    library: str = ""
    key: str = ""
    created: datetime | None = None

    @property
    def current(self) -> bool:
        return self.ended is None

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "text": self.text, "when": _iso(self.happened),
                "importance": self.importance, "source": self.source,
                "ended": _iso(self.ended) if self.ended else None, "ended_why": self.ended_why,
                "superseded_by": self.superseded_by}


def _row(r) -> Memory:
    return Memory(r["id"], r["kind"], r["text"], _dt(r["happened"]), r["importance"], r["source"], _dt(r["ended"]),
                  r["ended_why"], r["superseded_by"], r["owner"], r["library"], r["key"], _dt(r["created"]))


class Worker:
    """The small background thread: it makes vectors for new records, writes the markdown files and runs the
    periodic jobs (a rescan of the files, the background pass). Jobs run on a kick (something changed) or on a
    timer, never two at once. flush() runs the kick jobs now, in the caller's thread (tests, and quitting)."""

    def __init__(self, background: bool):
        self.background = background
        self.on_kick: list[Callable[[], None]] = []
        self.timed: list[list] = []        # [fn, every_s, last_run]
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._busy = threading.RLock()
        self._thread: threading.Thread | None = None

    def every(self, seconds: float, fn: Callable[[], None]) -> None:
        self.timed.append([fn, seconds, time.monotonic()])

    def kick(self) -> None:
        if not self.background:
            self.flush()
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="memory-worker", daemon=True)
            self._thread.start()
        self._wake.set()

    def flush(self) -> None:
        with self._busy:
            for fn in list(self.on_kick):
                self._safely(fn)

    def _safely(self, fn) -> None:
        try:
            fn()
        except Exception:
            log.exception("memory job %s failed", getattr(fn, "__name__", fn))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=5)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._busy:
                for fn in list(self.on_kick):
                    self._safely(fn)
                now = time.monotonic()
                for job in self.timed:
                    if now - job[2] >= job[1]:
                        job[2] = now
                        self._safely(job[0])

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)


class MemoryStore:
    def __init__(self, path: Path, *, embedder=None, settings: dict | None = None, background: bool = False,
                 backups: Path | None = None):
        from mary.memory import DEFAULTS, merged

        self.path = Path(path)
        self.db = Database(self.path, backups)
        self.vectors = VectorIndex(self.db)
        self.embedder = embedder
        self.settings = merged(DEFAULTS, settings or {})
        self.owner = self.db.meta("owner")
        self.library = self.db.meta("library")
        self.listeners: list[Callable[[str, Memory], None]] = []   # ("added" | "ended" | "edited" | "deleted", memory)
        self.files = None                 # mary.memory.files.MemoryFiles, once attached
        self.worker = Worker(background)
        self.worker.on_kick.append(self.embed_pending)
        if embedder is not None and hasattr(embedder, "model"):
            self.vectors.purge_other_models(SPACE, self.vector_model)

    # ------------------------------------------------------------------ settings, life cycle

    def setting(self, dotted: str):
        node = self.settings
        for part in dotted.split("."):
            node = node[part]
        return node

    def start(self) -> None:
        """Load the meaning model on a background thread and begin the periodic jobs (MARY running, not tests)."""
        self.worker.background = True
        load = getattr(self.embedder, "load", None)
        if load:
            def _load():
                if load():
                    self.worker.kick()
            threading.Thread(target=_load, name="memory-embedder", daemon=True).start()
        self.worker.kick()

    def flush(self) -> None:
        self.worker.flush()

    def close(self) -> None:
        self.worker.stop()
        try:
            self.worker.flush()
        finally:
            self.db.close()

    @property
    def vector_model(self) -> str:
        e = self.embedder
        return getattr(e, "tag", None) or e.model

    @property
    def meaning(self) -> bool:
        """Can it search by meaning right now (the model is loaded)?"""
        return bool(self.embedder is not None and getattr(self.embedder, "ready", False))

    # ------------------------------------------------------------------ writing

    def add(self, kind: str, text: str, *, happened: datetime | None = None, importance: float = 0.5,
            source: str = "", key: str = "", ended: datetime | None = None, ended_why: str | None = None) -> Memory:
        text = clean(text)
        if not text:
            raise ValueError("nothing to remember")
        now = clock.now()
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO memory(owner, library, kind, text, happened, created, updated, importance, source, key, "
                "ended, ended_why) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.owner, self.library, kind, text, _iso(happened or now), _iso(now), _iso(now),
                 float(importance), source, key, _iso(ended) if ended else None, ended_why if ended else None))
            mid = cur.lastrowid
        m = self.get(mid)
        self._notify("added", m)
        self.worker.kick()
        return m

    def bulk_add(self, kind: str, rows: list[tuple[str, datetime, float, str]]) -> list[int]:
        """Many records in one transaction: (text, happened, importance, source). No events, and no kick: for an
        import, where the caller makes the vectors itself (or calls flush())."""
        now = _iso(clock.now())
        ids = []
        with self.db.tx() as c:
            for text, happened, importance, source in rows:
                ids.append(c.execute(
                    "INSERT INTO memory(owner, library, kind, text, happened, created, updated, importance, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (self.owner, self.library, kind, clean(text), _iso(happened), now, now, float(importance),
                     source)).lastrowid)
        return ids

    def find_duplicate(self, text: str, kinds: Iterable[str] = ("fact", "core")) -> Memory | None:
        """A current record that already says this: nearly the same words, or (when the meaning model is up) the
        same meaning."""
        from rapidfuzz import fuzz, process, utils

        text = clean(text)
        current = self.list(kinds=kinds, current=True, limit=20000)
        if not current:
            return None
        best = process.extractOne(text, [m.text for m in current], scorer=fuzz.token_sort_ratio,
                                  processor=utils.default_process)
        if best and best[1] >= 90:
            return current[best[2]]
        if self.meaning:
            q = self.embedder.embed_docs([text])[0]
            hits = self.vectors.top(SPACE, q, 1, allowed=np.array([m.id for m in current], dtype=np.int64))
            if hits and hits[0][1] >= self.setting("facts.same_meaning"):
                return self.get(hits[0][0])
        return None

    def add_fact(self, text: str, *, importance: float = 0.8, source: str = "user", replaces=None,
                 kind: str = "fact", happened: datetime | None = None) -> tuple[Memory, str]:
        """Save what was said as a fact. ("saved" | "already" | "replaced", the record): a duplicate is merged
        (its importance kept at the higher), and `replaces` (an id the caller named) ends the old fact."""
        dup = self.find_duplicate(text, kinds=(kind,) if kind != "fact" else ("fact", "core"))
        if dup is not None:
            if importance > dup.importance:
                self._update(dup.id, importance=float(importance))
            return self.get(dup.id), "already"
        old = self.get(memory_id(replaces)) if replaces not in (None, "") and memory_id(replaces) else None
        m = self.add(kind, text, importance=importance, source=source, happened=happened)
        if old is not None and old.current and old.kind in ("fact", "core") and old.id != m.id:
            self.end(old.id, "superseded", superseded_by=m.id)
            return m, "replaced"
        return m, "saved"

    def end(self, mid: int, why: str = "forgotten", superseded_by: int | None = None) -> Memory | None:
        """It stops being true (or is forgotten): kept, with the date and why, never silently lost."""
        m = self.get(mid)
        if m is None or not m.current:
            return m
        now = clock.now()
        with self.db.tx() as c:
            c.execute("UPDATE memory SET ended=?, ended_why=?, superseded_by=?, updated=? WHERE id=?",
                      (_iso(now), why, superseded_by, _iso(now), mid))
        m = self.get(mid)
        self._notify("ended", m)
        return m

    def edit(self, mid: int, text: str) -> Memory | None:
        text = clean(text)
        m = self.get(mid)
        if m is None or not text:
            return m
        with self.db.tx() as c:
            c.execute("UPDATE memory SET text=?, updated=? WHERE id=?", (text, _iso(clock.now()), mid))
        self.vectors.delete("memory", mid)   # made again from the new words
        m = self.get(mid)
        self._notify("edited", m)
        self.worker.kick()
        return m

    def _update(self, mid: int, **fields) -> None:
        allowed = {"importance", "text", "key", "source"}
        sets = ", ".join(f"{k}=?" for k in fields if k in allowed)
        with self.db.tx() as c:
            c.execute(f"UPDATE memory SET {sets}, updated=? WHERE id=?",
                      [*(v for k, v in fields.items() if k in allowed), _iso(clock.now()), mid])

    def delete(self, mid: int) -> Memory | None:
        """Gone for good: the record, its keywords and its vector, and the pages of the file that held them."""
        m = self.get(mid)
        if m is None:
            return None
        with self.db.tx() as c:
            c.execute("DELETE FROM memory WHERE id=?", (mid,))
            c.execute("UPDATE memory SET superseded_by=NULL WHERE superseded_by=?", (mid,))
        self.vectors.delete("memory", mid)
        self._scrub()
        self._notify("deleted", m)
        return m

    def _scrub(self) -> None:
        """Make the keyword index and the log file forget what was deleted (best effort while others read)."""
        c = self.db.conn()
        try:
            c.execute("INSERT INTO memory_fts(memory_fts) VALUES ('optimize')")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            log.debug("couldn't scrub the memory file now", exc_info=True)

    # ------------------------------------------------------------------ reading

    def get(self, mid: int | None) -> Memory | None:
        if mid is None:
            return None
        r = self.db.conn().execute("SELECT * FROM memory WHERE id=? AND owner=? AND library=?",
                                   (mid, self.owner, self.library)).fetchone()
        return _row(r) if r else None

    def by_key(self, kind: str, key: str, current: bool = True) -> Memory | None:
        q = "SELECT * FROM memory WHERE owner=? AND library=? AND kind=? AND key=?" + (" AND ended IS NULL" if current else "")
        r = self.db.conn().execute(q, (self.owner, self.library, kind, key)).fetchone()
        return _row(r) if r else None

    def where(self, kinds: Iterable[str] | None = None, current: bool | None = True, since: datetime | None = None,
              until: datetime | None = None, alias: str = "") -> tuple[str, list]:
        """The WHERE clause (and its arguments) for these filters; `alias` when the table is joined under a name."""
        p = f"{alias}." if alias else ""
        sql, args = f"{p}owner=? AND {p}library=?", [self.owner, self.library]
        if kinds is not None:
            kinds = list(kinds)
            sql += f" AND {p}kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if current is True:
            sql += f" AND {p}ended IS NULL"
        elif current is False:
            sql += f" AND {p}ended IS NOT NULL"
        if since:
            sql += f" AND {p}happened >= ?"
            args.append(_iso(since))
        if until:
            sql += f" AND {p}happened < ?"
            args.append(_iso(until))
        return sql, args

    def list(self, kinds: Iterable[str] | None = None, current: bool | None = True, since: datetime | None = None,
             until: datetime | None = None, limit: int = 100, offset: int = 0, newest_first: bool = True) -> list[Memory]:
        sql, args = self.where(kinds, current, since, until)
        order = "DESC" if newest_first else "ASC"
        rows = self.db.conn().execute(f"SELECT * FROM memory WHERE {sql} ORDER BY happened {order}, id {order} "
                                      "LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return [_row(r) for r in rows]

    def ids(self, kinds=None, current=True, since=None, until=None) -> np.ndarray:
        sql, args = self.where(kinds, current, since, until)
        return np.array([r[0] for r in self.db.conn().execute(f"SELECT id FROM memory WHERE {sql}", args)],
                        dtype=np.int64)

    def max_id(self) -> int:
        return self.db.conn().execute("SELECT coalesce(max(id), 0) FROM memory").fetchone()[0]

    def count(self, kinds: Iterable[str] | None = None, current: bool | None = True) -> int:
        sql, args = self.where(kinds, current)
        return self.db.conn().execute(f"SELECT count(*) FROM memory WHERE {sql}", args).fetchone()[0]

    def days(self, kind: str = "diary") -> list[tuple[date, int]]:
        sql, args = self.where((kind,), None)
        rows = self.db.conn().execute(
            f"SELECT substr(happened,1,10) d, count(*) n FROM memory WHERE {sql} GROUP BY d ORDER BY d DESC", args)
        return [(date.fromisoformat(r["d"]), r["n"]) for r in rows]

    def stats(self) -> dict:
        out = {kind: {"current": self.count((kind,), True), "ended": self.count((kind,), False)} for kind in KINDS}
        out["vectors"] = self.vectors.count(SPACE)
        out["meaning"] = self.meaning
        out["file_bytes"] = self.path.stat().st_size if self.path.exists() else 0
        return out

    # ------------------------------------------------------------------ search

    def search(self, query: str, **kw):
        """Hits for a question: see mary.memory.search.search."""
        from mary.memory.search import search

        return search(self, query, **kw)

    def resolve(self, text: str, kinds: Iterable[str] = ("core", "fact", "diary", "reflection"),
                current: bool | None = True) -> tuple[Memory | None, list[Memory]]:
        """Which memory does the user mean by `text` ("#12", or their words)? (the one, or None and the candidates
        when it's unclear or nothing fits well)."""
        mid = memory_id(text)
        if mid is not None:
            m = self.get(mid)
            return (m, []) if m and (current is None or m.current == current) and m.kind in kinds else (None, [])
        from mary.memory.search import pick

        return pick(self, text, kinds, current)

    # ------------------------------------------------------------------ vectors and the other side of core

    def embed_pending(self, limit: int = 64) -> int:
        """Make vectors for records that have none (new ones, or ones another model made)."""
        if not self.meaning:
            return 0
        done = 0
        while True:
            rows = self.db.conn().execute(
                "SELECT m.id, m.kind, m.text FROM memory m LEFT JOIN vectors v ON v.space=? AND v.ref_table='memory' "
                "AND v.ref_id=m.id WHERE v.ref_id IS NULL AND m.owner=? AND m.library=? LIMIT ?",
                (SPACE, self.owner, self.library, limit)).fetchall()
            if not rows:
                return done
            vecs = self.embedder.embed_docs([embedding_text(r["kind"], r["text"]) for r in rows])
            self.vectors.put_many(SPACE, [r["id"] for r in rows], vecs, self.vector_model)
            done += len(rows)

    def sync_core(self, facts: list[str]) -> None:
        """Keep the records of kind core in step with the facts in profile.yaml (which stays where it is)."""
        want = {core_key(f): clean(f) for f in facts if clean(f)}
        have = {m.key: m for m in self.list(kinds=("core",), current=True, limit=100000)}
        for key, text in want.items():
            if key not in have:
                self.add("core", text, importance=0.9, source="profile", key=key)
        for key, m in have.items():
            if key not in want:
                self.end(m.id, "forgotten")

    # ------------------------------------------------------------------ events

    def _notify(self, event: str, m: Memory | None) -> None:
        for listener in list(self.listeners):
            try:
                listener(event, m)
            except Exception:
                log.exception("memory listener failed")
