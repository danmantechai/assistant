"""The memory as plain Markdown you can read and edit in Notepad, kept in step with the store:

    user/memory/facts.md            what MARY knows, Current and Ended         "- text · 2026-10-12"
    user/memory/diary/2026-10-30.md one file per day, written by code           "- 09:14 text"
    user/memory/reflections.md      a short summary per finished day            "## 2026-10-30" and a paragraph

MARY writes them as she saves. A file whose content is no longer what she last wrote was edited by hand, and the edit
is imported by line: a new line is a new memory, a line taken out ends that memory ("removed by hand", never deleted
behind your back), and one line changed for another is an edit (the old one kept as ended, linked to the new). Delete
memory.db and the files rebuild it (importance and vectors are made again). Delete a file and she writes it again from
what she has: to forget something, say so, or end it on the settings page.

Lines are matched by their words, so nothing here needs ids. Every file starts with <!-- mary-memory 1 -->, the
format's number.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from mary.memory import clock
from mary.memory.store import Memory, MemoryStore, clean

log = logging.getLogger(__name__)

FORMAT = "<!-- mary-memory 1 -->"
SCAN_EVERY_S = 300

_FACT = re.compile(r"^-\s+(?P<text>.+?)(?:\s+·\s+(?P<found>\d{4}-\d{2}-\d{2})(?:\s+→\s+(?P<ended>\d{4}-\d{2}-\d{2}))?"
                   r"(?:\s+\((?P<why>[^)]*)\))?)?\s*$")
_DIARY = re.compile(r"^-\s+(?:(?P<time>\d{1,2}:\d{2})\s+)?(?P<text>.+?)\s*$")
_DAY = re.compile(r"^##\s+(?P<day>\d{4}-\d{2}-\d{2})\s*$")


def norm(text: str) -> str:
    return clean(text).lower().rstrip(".")


@dataclass
class Item:
    text: str
    when: datetime
    ended: datetime | None = None
    why: str | None = None


def _day(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------- reading a file


def parse_facts(text: str, now: datetime) -> tuple[list[Item], list[Item]]:
    """(current, ended) from facts.md; anything that isn't a "- " line under a section is commentary."""
    current: list[Item] = []
    ended: list[Item] = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("##"):
            section = "ended" if "ended" in line.lower() else "current" if "current" in line.lower() else None
            continue
        m = _FACT.match(line) if section else None
        if not m:
            continue
        found = datetime.combine(_day(m["found"]), datetime.min.time()) + timedelta(hours=12) if m["found"] else now
        if section == "current":
            current.append(Item(clean(m["text"]), found))
        else:
            end = datetime.combine(_day(m["ended"]), datetime.min.time()) + timedelta(hours=12) if m["ended"] else now
            ended.append(Item(clean(m["text"]), found, end, m["why"] or "removed by hand"))
    return current, ended


def parse_diary(text: str, day: date) -> list[Item]:
    items = []
    for raw in text.splitlines():
        m = _DIARY.match(raw.strip())
        if m and not raw.strip().startswith("#"):
            h, mi = (int(x) for x in (m["time"] or "12:00").split(":"))
            items.append(Item(clean(m["text"]), datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=mi)))
    return items


def parse_reflections(text: str) -> list[Item]:
    items, day, parts = [], None, []

    def close():
        if day and parts:
            items.append(Item(clean(" ".join(parts)), datetime.combine(day, datetime.min.time()) + timedelta(hours=23, minutes=30)))

    for raw in text.splitlines():
        m = _DAY.match(raw.strip())
        if m:
            close()
            day, parts = _day(m["day"]), []
        elif day and raw.strip() and not raw.strip().startswith(("#", "<!--")):
            parts.append(raw.strip())
    close()
    return items


# ---------------------------------------------------------------- writing a file


def render_facts(current: list[Memory], ended: list[Memory]) -> str:
    out = [FORMAT, "# What MARY knows about you", "",
           "Edit this file freely: add a line to teach her something, take one out to end it, change one to correct it.",
           "MARY reads your changes within a few minutes (or when she starts). Ended lines stay here, with their dates.",
           "", "## Current"]
    out += [f"- {m.text} · {m.happened:%Y-%m-%d}" for m in current]
    out += ["", "## Ended"]
    for m in ended:
        why = f" ({m.ended_why})" if m.ended_why else ""
        out.append(f"- {m.text} · {m.happened:%Y-%m-%d} → {m.ended:%Y-%m-%d}{why}")
    return "\n".join(out) + "\n"


def render_diary(day: date, rows: list[Memory]) -> str:
    return "\n".join([FORMAT, f"# {day:%A %d %B %Y}", ""] + [f"- {m.happened:%H:%M} {m.text}" for m in rows]) + "\n"


def render_reflections(rows: list[Memory]) -> str:
    out = [FORMAT, "# How the days went", ""]
    for m in rows:
        out += [f"## {m.happened:%Y-%m-%d}", m.text, ""]
    return "\n".join(out)


# ---------------------------------------------------------------- the files of one store


class MemoryFiles:
    def __init__(self, store: MemoryStore, folder: Path):
        self.store, self.folder = store, Path(folder)
        self.dirty: set[str] = set()
        self._lock = threading.RLock()
        self._active = False   # sync() adds records, which kick the worker, which would call sync() again
        store.files = self
        store.listeners.append(self._on_change)
        store.worker.on_kick.append(self.sync)
        store.worker.every(SCAN_EVERY_S, self.sync)

    # ------------------------------------------------------------------ names

    def path(self, name: str) -> Path:
        if name == "facts":
            return self.folder / "facts.md"
        if name == "reflections":
            return self.folder / "reflections.md"
        return self.folder / "diary" / f"{name.split(':', 1)[1]}.md"

    def _on_change(self, event: str, m: Memory | None) -> None:
        if m is None:
            return
        name = {"fact": "facts", "reflection": "reflections"}.get(m.kind) or \
            (f"diary:{m.happened:%Y-%m-%d}" if m.kind == "diary" else None)
        if name:
            self.dirty.add(name)
            self.store.worker.kick()

    # ------------------------------------------------------------------ the one entry point the worker calls

    def sync(self) -> None:
        """Take in hand edits, then write what changed in the store. Never raises."""
        with self._lock:
            if self._active:
                return
            self._active = True
            try:
                self.rescan()
                self.flush()
            except Exception:
                log.exception("memory files: sync failed")
            finally:
                self._active = False

    def flush(self) -> None:
        for name in sorted(self.dirty):
            if self._write(name):
                self.dirty.discard(name)

    def _write(self, name: str) -> bool:
        store = self.store
        upto = store.max_id()   # what the file will hold is at most this: later records are new to it
        if name == "facts":
            current = store.list(kinds=("fact",), current=True, limit=100000, newest_first=False)
            ended = sorted(store.list(kinds=("fact",), current=False, limit=100000), key=lambda m: m.ended)
            text = render_facts(current, ended)
        elif name == "reflections":
            rows = store.list(kinds=("reflection",), current=True, limit=100000, newest_first=False)
            text = render_reflections(rows)
        else:
            day = _day(name.split(":", 1)[1])
            rows = store.list(kinds=("diary",), current=True, since=datetime.combine(day, datetime.min.time()),
                              until=datetime.combine(day + timedelta(days=1), datetime.min.time()), limit=100000,
                              newest_first=False)
            if not rows and not self.path(name).exists():
                return True
            text = render_diary(day, rows)
        path = self.path(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".md.tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError as e:   # open in another program, say: try again at the next sync
            log.warning("memory files: couldn't write %s (%s)", path.name, e)
            return False
        self._record(path, text.encode("utf-8"), upto)
        return True

    def _key(self, path: Path) -> str:
        return path.name if path.parent == self.folder else f"diary/{path.name}"

    def _record(self, path: Path, data: bytes, upto: int) -> None:
        st = path.stat()
        with self.store.db.tx() as c:
            c.execute("INSERT INTO sources VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET sha=excluded.sha, "
                      "mtime=excluded.mtime, size=excluded.size, exported_id=excluded.exported_id",
                      (self._key(path), hashlib.sha256(data).hexdigest(), st.st_mtime, st.st_size, upto))

    # ------------------------------------------------------------------ hand edits

    def _known(self) -> list[tuple[str, Path]]:
        names = [("facts", self.folder / "facts.md"), ("reflections", self.folder / "reflections.md")]
        diary = self.folder / "diary"
        if diary.is_dir():
            names += [(f"diary:{p.stem}", p) for p in sorted(diary.glob("????-??-??.md"))]
        known = {n for n, _ in names}
        names += [(f"diary:{day}", self.path(f"diary:{day}")) for day, _ in self.store.days("diary")
                  if f"diary:{day}" not in known]
        return names

    def rescan(self) -> int:
        """Import every file that was changed by hand; returns how many were."""
        imported = 0
        for name, path in self._known():
            row = self.store.db.conn().execute("SELECT * FROM sources WHERE path=?", (self._key(path),)).fetchone()
            if not path.exists():   # not written yet, or taken away: written (again) from the store
                self.dirty.add(name)
                continue
            st = path.stat()
            if row is not None and abs(st.st_mtime - row["mtime"]) < 1e-6 and st.st_size == row["size"]:
                continue
            data = path.read_bytes()
            if row is not None and hashlib.sha256(data).hexdigest() == row["sha"]:
                continue   # touched, not edited
            self._import(name, data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n"),
                         row["exported_id"] if row else None)
            self.dirty.add(name)   # written back normalised (and its hash recorded)
            imported += 1
        return imported

    def _import(self, name: str, text: str, upto: int | None) -> None:
        store, now = self.store, clock.now()
        if name == "facts":
            current, ended = parse_facts(text, now)
            self._reconcile("fact", current, store.list(kinds=("fact",), current=True, limit=100000), upto, now,
                            ended_items=ended)
        elif name == "reflections":
            self._reconcile("reflection", parse_reflections(text),
                            store.list(kinds=("reflection",), current=True, limit=100000), upto, now)
        else:
            day = _day(name.split(":", 1)[1])
            start = datetime.combine(day, datetime.min.time())
            self._reconcile("diary", parse_diary(text, day),
                            store.list(kinds=("diary",), current=True, since=start, until=start + timedelta(days=1),
                                       limit=100000), upto, now)
        log.info("memory files: took in hand edits to %s", name)

    def _reconcile(self, kind: str, items: list[Item], rows: list[Memory], upto: int | None, now: datetime,
                   ended_items: list[Item] = ()) -> None:
        """Make the store say what the file says. Records made after the file was last written (an id above `upto`)
        aren't missing from it, just not there yet."""
        from rapidfuzz import fuzz

        store = self.store
        have = {}
        for m in rows:
            have.setdefault(norm(m.text), []).append(m)
        added: list[Item] = []
        for item in items:
            bucket = have.get(norm(item.text))
            if bucket:
                bucket.pop(0)
            else:
                added.append(item)
        removed = [m for bucket in have.values() for m in bucket if upto is None or m.id <= upto]
        in_ended_section = {norm(i.text) for i in ended_items}
        # a line moved to the Ended section is ended by hand
        for m in list(removed):
            if norm(m.text) in in_ended_section:
                store.end(m.id, "ended by hand")
                removed.remove(m)
        # one line changed for another: an edit (old kept, ended, linked to the new)
        pairs = []
        for item in added:
            best = max(removed, key=lambda m: fuzz.ratio(norm(m.text), norm(item.text)), default=None)
            if best and fuzz.ratio(norm(best.text), norm(item.text)) >= 55:
                pairs.append((item, best))
                removed.remove(best)
        edited = {id(i) for i, _ in pairs}
        for item, old in pairs:
            new = store.add(kind, item.text, happened=item.when, importance=old.importance, source="hand edit")
            store.end(old.id, "edited by hand", superseded_by=new.id)
        for item in added:
            if id(item) not in edited:
                store.add(kind, item.text, happened=item.when, importance=0.8 if kind == "fact" else 0.4,
                          source="hand edit")
        for m in removed:
            store.end(m.id, "removed by hand")
        known_ended = {norm(m.text) for m in store.list(kinds=(kind,), current=False, limit=100000)}
        for item in ended_items:   # the Ended section: history that survives a rebuild
            if norm(item.text) not in known_ended and norm(item.text) not in {norm(i.text) for i in items}:
                store.add(kind, item.text, happened=item.when, importance=0.8, source="file",
                          ended=item.ended, ended_why=item.why)
