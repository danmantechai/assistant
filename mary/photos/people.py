"""People: the faces found in your photos, the groups they fall into, and the names you give them.

The rule that runs through all of it: **a name is only ever one you typed**. Faces are found, fingerprinted and piled up by
resemblance (mary/photos/cluster.py holds the rules); a pile has no name until you give it one. After that, new photos of that
person are recognised, the doubtful ones are *asked* about ("Is this Ama?") instead of guessed, and everything you tell MARY (a
name, a merge of one person's young and old faces, a split of two people mixed up, "not them", "strangers") is kept and never
overruled by a later guess.

Fingerprints are biometric: they live in the same file as the rest of the memory (vectors of space "face"), on this PC, are
deleted with the person (also from the copies MARY keeps before a format change), and never reach a log or a model.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from pathlib import Path

import numpy as np

from mary.memory import clock
from mary.photos import cluster
from mary.photos.faces import Found
from mary.photos.library import PhotoError, _iso

log = logging.getLogger(__name__)

SPACE, TABLE = "face", "photo_faces"
SAME_FACE = 0.4          # two boxes this much alike (overlap over union) are the same face when a photo is read again
JOIN_SHARE = 0.75        # a group joins a named person when this share of its faces are, one by one, clearly that person
BEST_GROUPS = 300        # "same person?" looks among the biggest groups and the named people
NAME_MAX, RELATION_MAX = 80, 40


def _norm_name(text) -> str:
    return " ".join(str(text or "").split())


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / (aw * ah + bw * bh - inter)


class People:
    def __init__(self, lib):
        self.lib = lib
        self.db = lib.db
        self.vectors = lib.store.vectors
        self.rules = cluster.Rules.from_settings(lib.setting("faces"))
        self._lock = threading.RLock()
        self._gallery: cluster.Gallery | None = None
        self._queue: list[int] = []         # faces to look at again, after a name was given
        self._look_again = False
        self._dirty: set[int] = set()       # groups that changed since they were last compared with the others
        self._same: tuple | None = None
        self.found_more = {"joined": 0, "asked": 0}     # what the last "look again" came to

    # ------------------------------------------------------------------ small helpers

    @property
    def owner(self) -> str:
        return self.lib.owner

    @property
    def library(self) -> str:
        return self.lib.lib

    def _all(self, sql, args=()):
        return self.db.conn().execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        return self.db.conn().execute(sql, args).fetchone()

    def _person_row(self, pid):
        return self._one("SELECT * FROM people WHERE id=? AND owner=? AND library=?", (pid, self.owner, self.library))

    def _need(self, pid):
        r = self._person_row(pid) if isinstance(pid, int) else None
        if r is None:
            raise PhotoError("That person isn't there any more (it may have been changed on another page; reload).")
        return r

    def _states(self) -> dict[int, str]:
        return {r["id"]: r["state"] for r in self._all("SELECT id, state FROM people WHERE owner=? AND library=?",
                                                       (self.owner, self.library))}

    def crop_path(self, fid: int) -> Path:
        return self.lib.dir / "faces" / f"{fid // 1000:04}" / f"{fid:08}.jpg"

    # ------------------------------------------------------------------ the switch

    def allowed(self) -> bool:
        return bool(self.lib.setting("faces.allowed"))

    def enabled(self) -> bool:
        return self.allowed() and self.db.meta("faces") != "off"      # on unless the person deleted all face data

    def set_enabled(self, on: bool) -> bool:
        if on and not self.allowed():
            raise PhotoError("Recognising faces is switched off in config.yaml (photos.faces.allowed).")
        self.db.set_meta("faces", "on" if on else "off")
        return self.enabled()

    # ------------------------------------------------------------------ the gallery (what the rules compare with)

    def _gal(self) -> cluster.Gallery:
        with self._lock:
            if self._gallery is None:
                g = cluster.Gallery(self.rules)
                ids, mat = self.vectors.snapshot(SPACE, TABLE)
                pos = {int(i): k for k, i in enumerate(ids)}
                by: dict[int, list] = {}
                for r in self._all("SELECT id, person_id, quality, how FROM photo_faces WHERE owner=? AND library=? "
                                   "AND usable=1 AND person_id IS NOT NULL", (self.owner, self.library)):
                    k = pos.get(r["id"])
                    if k is not None:
                        by.setdefault(r["person_id"], []).append((r["id"], mat[k], r["quality"], r["how"] == "you"))
                for pid, items in by.items():
                    g.set(pid, items)
                self._gallery = g
            return self._gallery

    def _refresh(self, *persons: int) -> None:
        """Rebuild what the rules know about these groups from their faces (after faces were moved or deleted)."""
        g = self._gal()
        for pid in persons:
            items = []
            for r in self._all("SELECT id, quality, how FROM photo_faces WHERE person_id=? AND usable=1", (pid,)):
                v = self.vectors.get(SPACE, r["id"], TABLE)
                if v is not None:
                    items.append((r["id"], v, r["quality"], r["how"] == "you"))
            g.set(pid, items)

    def use_model(self, tag: str) -> int:
        """Fingerprints another model made can't be compared with this one's: they are dropped, and the photos they came from
        are read again (what the person said about them is kept: see `add`). Returns how many were dropped."""
        with self._lock:
            n = self.vectors.purge_other_models(SPACE, tag, TABLE)
            if n:
                self._gallery = None
            return n

    # ------------------------------------------------------------------ the worker's side

    def needing(self, tag: str, limit: int = 1) -> list:
        """Photos no model of this tag has read for faces yet."""
        return self._all("SELECT p.id, (SELECT path FROM photo_files f WHERE f.photo_id=p.id AND f.state='ok' "
                         "ORDER BY f.width*f.height DESC LIMIT 1) AS path FROM photos p WHERE p.owner=? AND p.library=? "
                         "AND p.faces_model<>? ORDER BY p.id LIMIT ?", (self.owner, self.library, tag, limit))

    def skip(self, photo_id: int, tag: str) -> None:
        """Nothing to read in this photo (its file is gone or unreadable): don't ask again."""
        with self.db.tx() as c:
            c.execute("UPDATE photos SET faces_model=? WHERE id=?", (tag, photo_id))

    def add(self, photo_id: int, found: list[Found], tag: str) -> dict:
        """Keep the faces found in a photo and give each a group. A photo read again keeps what the person said about it: a face
        in the same place as one they had put in a group goes back to that group."""
        stats = Counter()
        with self._lock:
            gal = self._gal()
            carried, touched = self._take_photo(photo_id)
            self._refresh(*[p for p in touched if self._person_row(p) is not None])
            states = self._states()
            used: set[int] = set()
            written: list[Path] = []
            try:
                with self.db.tx() as c:
                    now = _iso(clock.now())
                    mine = [(f, self._carried(f, carried)) for f in found]
                    for f, person in mine:             # what the person said about this picture before comes first
                        if person is not None and states.get(person) is not None:
                            used.add(person)
                    for f, person in mine:
                        fid = c.execute(
                            "INSERT INTO photo_faces(owner, library, photo_id, x, y, w, h, points, score, px, quality, usable, "
                            "created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (self.owner, self.library, photo_id, *f.box, ",".join(f"{v:.4f}" for v in f.points), f.score, f.px,
                             f.quality, int(f.usable), now)).lastrowid
                        stats["faces"] += 1
                        if not f.usable:
                            stats["unclear"] += 1
                            continue
                        path = self.crop_path(fid)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(f.crop)
                        written.append(path)
                        c.execute("UPDATE photo_faces SET crop=? WHERE id=?", (path.relative_to(self.lib.dir).as_posix(), fid))
                        self.vectors.put(SPACE, fid, f.vector, tag, TABLE)
                        if person is not None and states.get(person) is not None:
                            self._place(c, fid, person, "you", None, 0.0)
                            gal.add(person, fid, f.vector, f.quality, anchor=True)
                            stats["kept"] += 1
                            self._dirty.add(person)
                            continue
                        d = cluster.decide(gal.scores(f.vector), states, self.rules, used)
                        target = d.person
                        if target is None:
                            target = c.execute("INSERT INTO people(owner, library, created, updated) VALUES (?,?,?,?)",
                                               (self.owner, self.library, now, now)).lastrowid
                            states[target] = "unnamed"
                            stats["new"] += 1
                        self._place(c, fid, target, "auto", d.ask, d.ask_score)
                        gal.add(target, fid, f.vector, f.quality)
                        self._dirty.add(target)
                        used.add(target)
                        stats[d.reason] += 1
                        stats["asked"] += d.ask is not None
                    c.execute("UPDATE photos SET faces_model=? WHERE id=?", (tag, photo_id))
            except BaseException:
                for p in written:
                    p.unlink(missing_ok=True)
                self._gallery = None
                raise
            self._same = None
        return dict(stats)

    def _place(self, c, fid, person, how, ask, ask_score) -> None:
        c.execute("UPDATE photo_faces SET person_id=?, how=?, suggest_id=?, suggest_score=? WHERE id=?",
                  (person, how, ask, ask_score if ask else None, fid))

    @staticmethod
    def _carried(f: Found, carried: list) -> int | None:
        best, who = SAME_FACE, None
        for box, person in carried:
            o = _overlap(f.box, box)
            if o >= best:
                best, who = o, person
        return who

    def _take_photo(self, photo_id: int) -> tuple[list, set[int]]:
        """Remove a photo's faces (rows, fingerprints, pictures). Returns what the person had said about them (box, group) and
        the groups they were in."""
        rows = self._all("SELECT id, x, y, w, h, person_id, how FROM photo_faces WHERE photo_id=?", (photo_id,))
        carried = [((r["x"], r["y"], r["w"], r["h"]), r["person_id"]) for r in rows
                   if r["how"] == "you" and r["person_id"] is not None]
        touched = {r["person_id"] for r in rows if r["person_id"] is not None}
        if rows:
            self._delete_faces([r["id"] for r in rows])
        return carried, touched

    def _delete_faces(self, ids: list[int]) -> None:
        """For good: the rows, the fingerprints (in the file and in the search matrix) and the face pictures."""
        with self.db.tx() as c:
            for i in ids:
                self.vectors.delete(TABLE, i)
            c.executemany("DELETE FROM photo_faces WHERE id=?", [(i,) for i in ids])
            c.executemany("DELETE FROM people_nots WHERE kind='face' AND a=?", [(i,) for i in ids])
        for i in ids:
            self.crop_path(i).unlink(missing_ok=True)

    def forget_photo(self, photo_id: int) -> None:
        """A photo was removed from MARY's records: so are its faces."""
        with self._lock:
            carried, touched = self._take_photo(photo_id)
            if touched:
                self._after_faces_left(touched)

    def _after_faces_left(self, persons) -> None:
        """Groups that lost faces: unnamed or set-aside ones with none left go; the rules re-learn the rest."""
        with self.db.tx() as c:
            for pid in persons:
                left = c.execute("SELECT count(*) FROM photo_faces WHERE person_id=?", (pid,)).fetchone()[0]
                if not left and (self._person_row(pid) or {"state": "named"})["state"] != "named":
                    c.execute("DELETE FROM people WHERE id=?", (pid,))
                    c.execute("DELETE FROM people_nots WHERE (kind='same' AND (a=? OR b=?)) OR (kind='face' AND b=?)",
                              (pid, pid, pid))
                    c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE suggest_id=?", (pid,))
        self._refresh(*persons)
        self._dirty -= {p for p in persons if self._person_row(p) is None}
        self._same = None

    # ------------------------------------------------------------------ maintenance: looking again, tidying

    def request_look_again(self) -> None:
        """A name was given, or a group joined to a name: faces in the other groups are looked at again, a few at a time."""
        self._look_again = True

    def pending(self) -> bool:
        return bool(self._look_again or self._queue or self._dirty)

    def maintain(self, batch: int = 40) -> bool:
        """One small piece of the grouping work that follows a change: True while there is more."""
        with self._lock:
            if self._look_again:
                self._look_again = False
                self._queue = [r[0] for r in self._all(
                    "SELECT p.id FROM people p WHERE p.owner=? AND p.library=? AND p.state='unnamed' AND EXISTS "
                    "(SELECT 1 FROM photo_faces f WHERE f.person_id=p.id AND f.usable=1) ORDER BY p.id",
                    (self.owner, self.library))]
                self.found_more = {"joined": 0, "asked": 0}
            if self._queue:
                work, self._queue = self._queue[:batch], self._queue[batch:]
                self._look(work)
                return True
            if self._dirty:
                self._tidy()
                return True
            return False

    def _look(self, piles: list[int]) -> None:
        """Is a group still waiting for a name really one of the people you have named? Each of its faces is decided by the same
        rules as a new face; the group joins a named person only when most of its faces are, one by one, clearly that person
        (never by a few lucky ones), and a face that might be them is asked about. A group you have worked on is left alone."""
        gal, states = self._gal(), self._states()
        touched: set[int] = set()
        for pile in piles:
            if states.get(pile) != "unnamed":
                continue
            rows = self._all("SELECT * FROM photo_faces WHERE person_id=? AND usable=1", (pile,))
            if not rows or any(r["how"] == "you" for r in rows):
                continue
            votes: Counter = Counter()
            asks: list[tuple[int, int, float]] = []
            for r in rows:
                v = self.vectors.get(SPACE, r["id"], TABLE)
                if v is None:
                    continue
                refused = {x["b"] for x in self._all("SELECT b FROM people_nots WHERE kind='face' AND a=?", (r["id"],))}
                refused |= {x[0] for x in self._all("SELECT person_id FROM photo_faces WHERE photo_id=? AND id<>? "
                                                    "AND person_id IS NOT NULL AND person_id<>?",
                                                    (r["photo_id"], r["id"], pile))}
                d = cluster.decide(gal.scores(v, leave_out=r["id"]), states, self.rules, refused)
                if d.reason == "named":
                    votes[d.person] += 1
                    asks.append((r["id"], d.person, d.score))
                elif d.ask is not None:
                    asks.append((r["id"], d.ask, d.ask_score))
            who, k = votes.most_common(1)[0] if votes else (None, 0)
            if who is not None and k >= JOIN_SHARE * len(rows) and not self._shares_a_photo(who, pile):
                self._absorb(who, pile)
                touched.add(who)
                self.found_more["joined"] += len(rows)
                continue
            with self.db.tx() as c:
                for fid, person, score in asks:
                    if self._one("SELECT suggest_id FROM photo_faces WHERE id=?", (fid,))[0] != person:
                        c.execute("UPDATE photo_faces SET suggest_id=?, suggest_score=? WHERE id=?", (person, score, fid))
                        self.found_more["asked"] += 1
        if touched:
            self._refresh(*touched)

    def _shares_a_photo(self, a: int, b: int) -> bool:
        """Do these two groups have a face in the same photo? Then they are two people."""
        return self._one("SELECT 1 FROM photo_faces x JOIN photo_faces y ON x.photo_id=y.photo_id WHERE x.person_id=? "
                         "AND y.person_id=? LIMIT 1", (a, b)) is not None

    def _tidy(self) -> None:
        """Groups that turn out to be one person (the order the photos arrived in can leave a person in two piles) become one."""
        gal, states = self._gal(), self._states()
        dirty, self._dirty = self._dirty, set()
        unnamed = [p for p, s in states.items() if s == "unnamed" and p in gal]
        focus = [p for p in dirty if p in set(unnamed)]
        if len(unnamed) < 2 or not focus:
            return
        sizes = {r["person_id"]: r["n"] for r in self._all(
            "SELECT person_id, count(*) n FROM photo_faces WHERE owner=? AND library=? AND person_id IS NOT NULL "
            "GROUP BY person_id", (self.owner, self.library))}
        refused = self._same_nots()
        known = set(refused)
        photos: dict[int, set] = {}
        for r in self._all("SELECT f.person_id, f.photo_id FROM photo_faces f JOIN people p ON p.id=f.person_id WHERE "
                           "f.owner=? AND f.library=? AND p.state='unnamed'", (self.owner, self.library)):
            photos.setdefault(r[0], set()).add(r[1])
        merges = cluster.consolidate(gal, unnamed, sizes, self.rules, refused, focus=focus, photos=photos)
        if refused != known:            # pairs left apart because they share a photo are remembered as two people
            with self.db.tx() as c:
                for a, b in refused - known:
                    c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                              (self.owner, self.library, "same", a, b, _iso(clock.now())))
        for keep, drop in merges:
            self._absorb(keep, drop)
        if merges:
            self._refresh(*{k for k, _ in merges})
            self._same = None

    def _same_nots(self) -> set[tuple[int, int]]:
        return {(r["a"], r["b"]) for r in self._all("SELECT a, b FROM people_nots WHERE owner=? AND library=? AND kind='same'",
                                                    (self.owner, self.library))}

    def _absorb(self, keep: int, drop: int) -> None:
        """Group `drop` is now part of group `keep`: its faces, what has been asked about it and what was said of it."""
        with self.db.tx() as c:
            c.execute("UPDATE photo_faces SET person_id=? WHERE person_id=?", (keep, drop))
            c.execute("UPDATE photo_faces SET suggest_id=? WHERE suggest_id=?", (keep, drop))
            c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE suggest_id=person_id")
            for r in c.execute("SELECT kind, a, b FROM people_nots WHERE (kind='same' AND (a=? OR b=?)) OR "
                               "(kind='face' AND b=?)", (drop, drop, drop)).fetchall():
                if r["kind"] == "same":
                    a, b = sorted((keep if r["a"] == drop else r["a"], keep if r["b"] == drop else r["b"]))
                    if a != b:
                        c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                                  (self.owner, self.library, "same", a, b, _iso(clock.now())))
                else:
                    c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                              (self.owner, self.library, "face", r["a"], keep, _iso(clock.now())))
            c.execute("DELETE FROM people_nots WHERE (kind='same' AND (a=? OR b=?)) OR (kind='face' AND b=?)",
                      (drop, drop, drop))
            c.execute("DELETE FROM people WHERE id=?", (drop,))
        self._gal().drop(drop)
        self._dirty.discard(drop)
        self._dirty.add(keep)

    # ------------------------------------------------------------------ reading

    def _face(self, r) -> dict:
        return {"id": r["id"], "photo": r["photo_id"], "person": r["person_id"], "you": r["how"] == "you",
                "asked": r["suggest_id"], "v": r["created"].replace(":", "").replace("-", "")}

    def _samples(self, pid: int, n: int = 6, by: str = "quality DESC") -> list[dict]:
        return [self._face(r) for r in self._all(
            f"SELECT * FROM photo_faces WHERE person_id=? AND usable=1 ORDER BY {by}, id LIMIT ?", (pid, n))]

    def _card(self, r, faces: int, photos: int, samples: int = 6) -> dict:
        return {"id": r["id"], "name": r["name"], "relation": r["relation"], "state": r["state"], "faces": faces,
                "photos": photos, "samples": self._samples(r["id"], samples) if samples else []}

    def _counts(self, pid: int) -> tuple[int, int]:
        r = self._one("SELECT count(*), count(DISTINCT photo_id) FROM photo_faces WHERE person_id=?", (pid,))
        return r[0], r[1]

    def status(self, tag: str | None = None) -> dict:
        """Counts only."""
        q = lambda sql, *a: self._one(sql, (self.owner, self.library, *a))[0]  # noqa: E731
        faces = q("SELECT count(*) FROM photo_faces WHERE owner=? AND library=?")
        usable = q("SELECT count(*) FROM photo_faces WHERE owner=? AND library=? AND usable=1")
        left = q("SELECT count(*) FROM photos WHERE owner=? AND library=? AND faces_model<>?", tag) if tag \
            else q("SELECT count(*) FROM photos WHERE owner=? AND library=? AND faces_model=''")
        by_state = {r["state"]: r["n"] for r in self._all(
            "SELECT p.state, count(*) n FROM people p WHERE p.owner=? AND p.library=? AND EXISTS "
            "(SELECT 1 FROM photo_faces f WHERE f.person_id=p.id) GROUP BY p.state", (self.owner, self.library))}
        named = q("SELECT count(*) FROM people WHERE owner=? AND library=? AND state='named'")
        return {"faces": faces, "usable": usable, "unclear": faces - usable, "left": left,
                "named": named, "groups": by_state.get("unnamed", 0), "ignored": by_state.get("ignored", 0),
                "questions": q("SELECT count(DISTINCT suggest_id) FROM photo_faces WHERE owner=? AND library=? "
                               "AND suggest_id IS NOT NULL"),
                "asked_faces": q("SELECT count(*) FROM photo_faces WHERE owner=? AND library=? AND suggest_id IS NOT NULL"),
                "working": self.pending(), "found_more": dict(self.found_more)}

    def people(self) -> list[dict]:
        """The people you have named, the ones in most photos first."""
        rows = self._all("SELECT p.*, count(f.id) AS faces, count(DISTINCT f.photo_id) AS photos FROM people p "
                         "LEFT JOIN photo_faces f ON f.person_id=p.id WHERE p.owner=? AND p.library=? AND p.state='named' "
                         "GROUP BY p.id ORDER BY photos DESC, lower(p.name)", (self.owner, self.library))
        return [self._card(r, r["faces"], r["photos"], 4) for r in rows]

    def named_counts(self) -> list[dict]:
        """For the Photos page's filter: the people you have named and how many photos each is in."""
        return [{"id": r["id"], "name": r["name"], "photos": r["n"]} for r in self._all(
            "SELECT p.id, p.name, count(DISTINCT f.photo_id) n FROM people p JOIN photo_faces f ON f.person_id=p.id "
            "WHERE p.owner=? AND p.library=? AND p.state='named' GROUP BY p.id ORDER BY n DESC, lower(p.name)",
            (self.owner, self.library))]

    def groups(self, offset: int = 0, limit: int = 12, min_faces: int = 2) -> dict:
        """Piles of faces still waiting for a name, the biggest first."""
        where = "p.owner=? AND p.library=? AND p.state='unnamed'"
        rows = self._all(f"SELECT p.*, count(f.id) AS faces, count(DISTINCT f.photo_id) AS photos FROM people p "
                         f"JOIN photo_faces f ON f.person_id=p.id WHERE {where} GROUP BY p.id HAVING count(f.id)>=? "
                         f"ORDER BY faces DESC, p.id LIMIT ? OFFSET ?",
                         (self.owner, self.library, max(1, min_faces), limit + 1, max(0, offset)))
        total = self._one(f"SELECT count(*) FROM (SELECT p.id FROM people p JOIN photo_faces f ON f.person_id=p.id "
                          f"WHERE {where} GROUP BY p.id HAVING count(f.id)>=?)", (self.owner, self.library, max(1, min_faces)))[0]
        singles = self._one("SELECT count(*) FROM (SELECT p.id FROM people p JOIN photo_faces f ON f.person_id=p.id "
                            "WHERE p.owner=? AND p.library=? AND p.state='unnamed' GROUP BY p.id HAVING count(f.id)=1)",
                            (self.owner, self.library))[0]
        return {"items": [self._card(r, r["faces"], r["photos"]) for r in rows[:limit]], "more": len(rows) > limit,
                "total": total, "singles": singles}

    def ignored(self, limit: int = 12) -> list[dict]:
        rows = self._all("SELECT p.*, count(f.id) AS faces, count(DISTINCT f.photo_id) AS photos FROM people p JOIN "
                         "photo_faces f ON f.person_id=p.id WHERE p.owner=? AND p.library=? AND p.state='ignored' "
                         "GROUP BY p.id ORDER BY faces DESC, p.id LIMIT ?", (self.owner, self.library, limit))
        return [self._card(r, r["faces"], r["photos"], 4) for r in rows]

    def person(self, pid: int) -> dict:
        r = self._need(pid)
        faces, photos = self._counts(pid)
        return self._card(r, faces, photos)

    def faces_of(self, pid: int, offset: int = 0, limit: int = 24) -> dict:
        self._need(pid)
        rows = self._all("SELECT * FROM photo_faces WHERE person_id=? AND usable=1 ORDER BY quality DESC, id LIMIT ? OFFSET ?",
                         (pid, limit + 1, max(0, offset)))
        total = self._one("SELECT count(*) FROM photo_faces WHERE person_id=? AND usable=1", (pid,))[0]
        return {"items": [self._face(r) for r in rows[:limit]], "more": len(rows) > limit, "total": total}

    def photo_ids(self, pid: int) -> list[int]:
        return [r[0] for r in self._all("SELECT DISTINCT photo_id FROM photo_faces WHERE person_id=?", (pid,))]

    def in_photo(self, photo_id: int) -> dict | None:
        """Who is in one photo, for its dialog: the people you named, and how many faces have no name yet."""
        rows = self._all("SELECT f.id, f.usable, f.person_id, f.created, p.name, p.state FROM photo_faces f LEFT JOIN people p "
                         "ON p.id=f.person_id WHERE f.photo_id=? ORDER BY f.x", (photo_id,))
        if not rows:
            return None
        named: dict[int, str] = {}
        for r in rows:
            if r["state"] == "named":
                named.setdefault(r["person_id"], r["name"])
        return {"named": [{"id": i, "name": n} for i, n in named.items()],
                "unnamed": sum(1 for r in rows if r["usable"] and r["state"] == "unnamed"),
                "ignored": sum(1 for r in rows if r["state"] == "ignored"),
                "unclear": sum(1 for r in rows if not r["usable"]),
                "faces": [{"id": r["id"], "person": r["person_id"], "name": r["name"] if r["state"] == "named" else "",
                           "v": r["created"].replace(":", "").replace("-", "")} for r in rows if r["usable"]]}

    def questions(self, per: int = 12) -> list[dict]:
        """"Is this Ama?": faces that might be a person you named, not given to them until you say. One entry per person."""
        out = []
        for r in self._all("SELECT suggest_id, count(*) n FROM photo_faces WHERE owner=? AND library=? "
                           "AND suggest_id IS NOT NULL GROUP BY suggest_id ORDER BY n DESC, suggest_id",
                           (self.owner, self.library)):
            p = self._person_row(r["suggest_id"])
            if p is None or p["state"] != "named":
                continue
            faces = [self._face(f) for f in self._all(
                "SELECT * FROM photo_faces WHERE suggest_id=? ORDER BY suggest_score DESC, id LIMIT ?", (p["id"], per))]
            out.append({"person": {"id": p["id"], "name": p["name"], "relation": p["relation"]}, "count": r["n"],
                        "faces": faces, "more": r["n"] > len(faces)})
        return out

    def same_person(self, limit: int = 8) -> list[dict]:
        """"The same person?": pairs of groups that are alike but not alike enough to be joined for you (a child and the grown
        adult, a person in two eras). Never merged until you say."""
        with self._lock:
            key = self.db.changed
            if self._same is None or self._same[0] != key:
                self._same = (key, self._same_pairs())
            pairs = self._same[1][:limit]
            out = []
            for a, b in pairs:
                ra, rb = self._person_row(a), self._person_row(b)
                if ra is None or rb is None:
                    continue
                out.append({"a": self._card(ra, *self._counts(a), samples=4), "b": self._card(rb, *self._counts(b), samples=4)})
            return out

    def _same_pairs(self) -> list[tuple[int, int]]:
        gal, states = self._gal(), self._states()
        sizes = {r["person_id"]: r["n"] for r in self._all(
            "SELECT person_id, count(*) n FROM photo_faces WHERE owner=? AND library=? AND person_id IS NOT NULL "
            "GROUP BY person_id", (self.owner, self.library))}
        named = [p for p, s in states.items() if s == "named" and p in gal]
        big = sorted((p for p, s in states.items() if s == "unnamed" and sizes.get(p, 0) >= 2 and p in gal),
                     key=lambda p: (-sizes[p], p))[:BEST_GROUPS]
        scored = gal.pairs([*named, *big], self.rules.same)
        refused = self._same_nots()
        return [k for k, _ in sorted(scored.items(), key=lambda kv: -kv[1]) if k not in refused]

    # ------------------------------------------------------------------ what the person tells MARY

    def name(self, pid: int, name: str, relation: str | None = None) -> dict:
        """Give a group a name (or change one). The only way a name ever comes to exist."""
        with self._lock:
            row = self._need(pid)
            name = _norm_name(name)[:NAME_MAX]
            if not name:
                raise PhotoError("Write their name.")
            relation = _norm_name(relation if relation is not None else row["relation"])[:RELATION_MAX]
            twin = self._one("SELECT id FROM people WHERE owner=? AND library=? AND state='named' AND lower(name)=lower(?) "
                             "AND id<>?", (self.owner, self.library, name, pid))
            if twin is not None:
                raise PhotoError(f"You already have someone called {name}. If it is the same person, choose “Same person as "
                                 f"{name}”; if not, add something to tell them apart, like a surname.")
            with self.db.tx() as c:
                c.execute("UPDATE people SET name=?, relation=?, state='named', updated=? WHERE id=?",
                          (name, relation, _iso(clock.now()), pid))
                c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE suggest_id=person_id")
            self.request_look_again()
            self._same = None
            return self.person(pid)

    def set_relation(self, pid: int, relation: str) -> dict:
        with self._lock:
            row = self._need(pid)
            if row["state"] != "named":
                raise PhotoError("Give them a name first.")
            with self.db.tx() as c:
                c.execute("UPDATE people SET relation=?, updated=? WHERE id=?",
                          (_norm_name(relation)[:RELATION_MAX], _iso(clock.now()), pid))
            return self.person(pid)

    def unname(self, pid: int) -> dict:
        """Take a name away: the faces stay together, as a group waiting for a name."""
        with self._lock:
            row = self._need(pid)
            if row["state"] != "named":
                raise PhotoError("They don't have a name.")
            with self.db.tx() as c:
                c.execute("UPDATE people SET name='', relation='', state='unnamed', updated=? WHERE id=?",
                          (_iso(clock.now()), pid))
                c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE suggest_id=?", (pid,))
            self._dirty.add(pid)
            self._same = None
            return self.person(pid)

    def ignore(self, pid: int) -> dict:
        """Strangers, a crowd, a poster: set aside. Faces like these join the group quietly and are never asked about."""
        with self._lock:
            row = self._need(pid)
            if row["state"] == "named":
                raise PhotoError("They have a name. Take the name away first, or delete them.")
            with self.db.tx() as c:
                c.execute("UPDATE people SET state='ignored', updated=? WHERE id=?", (_iso(clock.now()), pid))
                c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE person_id=?", (pid,))
            self._dirty.discard(pid)
            self._same = None
            return self.person(pid)

    def unignore(self, pid: int) -> dict:
        with self._lock:
            row = self._need(pid)
            if row["state"] != "ignored":
                raise PhotoError("That group isn't set aside.")
            with self.db.tx() as c:
                c.execute("UPDATE people SET state='unnamed', updated=? WHERE id=?", (_iso(clock.now()), pid))
            self._dirty.add(pid)
            self._same = None
            return self.person(pid)

    def merge(self, keep: int, other: int) -> dict:
        """These two groups are one person (a child and the grown adult, say). A name beats no name; of two names, the one you
        chose to keep stays. Always your decision: no grouping rule ever joins a named person to another."""
        with self._lock:
            a, b = self._need(keep), self._need(other)
            if a["id"] == b["id"]:
                raise PhotoError("Pick two different people.")
            rank = {"named": 2, "ignored": 1, "unnamed": 0}
            stay, go = (a, b) if rank[a["state"]] >= rank[b["state"]] else (b, a)
            self._absorb(stay["id"], go["id"])
            self._refresh(stay["id"])
            if stay["state"] == "named":
                self.request_look_again()
            self._same = None
            return self.person(stay["id"])

    def split(self, pid: int, face_ids: list[int]) -> dict:
        """These faces are someone else: they become a group of their own, and are not offered back."""
        with self._lock:
            row = self._need(pid)
            ids = self._mine(face_ids, pid)
            total = self._one("SELECT count(*) FROM photo_faces WHERE person_id=?", (pid,))[0]
            if len(ids) >= total:
                raise PhotoError("That's all of them. Pick only the faces that are someone else.")
            now = _iso(clock.now())
            with self.db.tx() as c:
                new = c.execute("INSERT INTO people(owner, library, created, updated) VALUES (?,?,?,?)",
                                (self.owner, self.library, now, now)).lastrowid
                c.executemany("UPDATE photo_faces SET person_id=?, how='you', suggest_id=NULL, suggest_score=NULL WHERE id=?",
                              [(new, i) for i in ids])
                a, b = sorted((pid, new))
                c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)", (self.owner, self.library, "same", a, b, now))
                if row["state"] == "named":
                    c.executemany("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                                  [(self.owner, self.library, "face", i, pid, now) for i in ids])
            self._refresh(pid, new)
            self._same = None
            return self.person(new)

    def _mine(self, face_ids, pid: int) -> list[int]:
        if not isinstance(face_ids, list) or not face_ids or not all(isinstance(i, int) for i in face_ids):
            raise PhotoError("Pick some faces first.")
        have = {r["id"] for r in self._all(f"SELECT id FROM photo_faces WHERE person_id=? AND id IN "
                                           f"({','.join('?' * len(face_ids))})", (pid, *face_ids))}
        ids = [i for i in dict.fromkeys(face_ids) if i in have]
        if not ids:
            raise PhotoError("Those faces aren't in this group.")
        return ids

    def move(self, face_ids: list[int], pid: int) -> dict:
        """These faces are this person (the yes to "Is this Ama?", or a face that was in the wrong group). Kept as yours: no later
        guess moves them. If they were in a named person's group, they are marked "not them" for that person."""
        with self._lock:
            target = self._need(pid)
            if not isinstance(face_ids, list) or not face_ids or not all(isinstance(i, int) for i in face_ids):
                raise PhotoError("Pick some faces first.")
            rows = self._all(f"SELECT id, person_id FROM photo_faces WHERE usable=1 AND owner=? AND library=? AND id IN "
                             f"({','.join('?' * len(face_ids))})", (self.owner, self.library, *face_ids))
            if not rows:
                raise PhotoError("Those faces aren't there any more.")
            states = self._states()
            now = _iso(clock.now())
            before = {r["person_id"] for r in rows if r["person_id"] not in (None, pid)}
            with self.db.tx() as c:
                for r in rows:
                    if r["person_id"] not in (None, pid) and states.get(r["person_id"]) == "named":
                        c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                                  (self.owner, self.library, "face", r["id"], r["person_id"], now))
                    c.execute("DELETE FROM people_nots WHERE kind='face' AND a=? AND b=?", (r["id"], pid))
                    c.execute("UPDATE photo_faces SET person_id=?, how='you', suggest_id=NULL, suggest_score=NULL WHERE id=?",
                              (pid, r["id"]))
            self._refresh(pid, *before)
            self._after_faces_left(before)
            if target["state"] == "named":
                self.request_look_again()
            self._same = None
            return self.person(pid)

    def deny(self, face_ids: list[int], pid: int) -> int:
        """The no to "Is this Ama?": these faces are not them, and will not be offered to them again."""
        with self._lock:
            self._need(pid)
            if not isinstance(face_ids, list) or not face_ids or not all(isinstance(i, int) for i in face_ids):
                raise PhotoError("Pick some faces first.")
            now = _iso(clock.now())
            with self.db.tx() as c:
                for i in face_ids:
                    if c.execute("SELECT 1 FROM photo_faces WHERE id=? AND owner=? AND library=?",
                                 (i, self.owner, self.library)).fetchone():
                        c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                                  (self.owner, self.library, "face", i, pid, now))
                        c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE id=? AND suggest_id=?",
                                  (i, pid))
            return len(face_ids)

    def not_same(self, a: int, b: int) -> None:
        """"Not the same person": these two are not asked about together again, and are never joined."""
        with self._lock:
            self._need(a), self._need(b)
            x, y = sorted((a, b))
            with self.db.tx() as c:
                c.execute("INSERT OR IGNORE INTO people_nots VALUES (?,?,?,?,?,?)",
                          (self.owner, self.library, "same", x, y, _iso(clock.now())))
            self._same = None

    # ------------------------------------------------------------------ taking faces away

    def remove_person(self, pid: int) -> dict:
        """Delete a person for good: their name, every fingerprint and face picture, and their links, in the file and in the
        copies MARY keeps before a format change. The photos stay, and are not read for faces again unless you ask."""
        with self._lock:
            self._need(pid)
            ids = [r["id"] for r in self._all("SELECT id FROM photo_faces WHERE person_id=?", (pid,))]
            with self.db.tx() as c:
                self._delete_faces(ids)
                c.execute("DELETE FROM people WHERE id=?", (pid,))
                c.execute("DELETE FROM people_nots WHERE (kind='same' AND (a=? OR b=?)) OR (kind='face' AND b=?)",
                          (pid, pid, pid))
                c.execute("UPDATE photo_faces SET suggest_id=NULL, suggest_score=NULL WHERE suggest_id=?", (pid,))
            self._gal().drop(pid)
            self._dirty.discard(pid)
            self._same = None
            self.lib.store._scrub()
            self._scrub_backups(ids, [pid])
            return {"faces": len(ids)}

    def delete_all(self) -> dict:
        """Every fingerprint, face picture, group and name: gone for good, and the switch is off."""
        with self._lock:
            n = self._one("SELECT count(*) FROM photo_faces WHERE owner=? AND library=?", (self.owner, self.library))[0]
            with self.db.tx() as c:
                c.execute("DELETE FROM vectors WHERE space=? AND ref_table=?", (SPACE, TABLE))
                c.execute("DELETE FROM photo_faces WHERE owner=? AND library=?", (self.owner, self.library))
                c.execute("DELETE FROM people WHERE owner=? AND library=?", (self.owner, self.library))
                c.execute("DELETE FROM people_nots WHERE owner=? AND library=?", (self.owner, self.library))
                c.execute("UPDATE photos SET faces_model='' WHERE owner=? AND library=?", (self.owner, self.library))
            self.vectors.reload()
            self._gallery, self._queue, self._dirty, self._same, self._look_again = None, [], set(), None, False
            self.set_enabled(False)
            shutil.rmtree(self.lib.dir / "faces", ignore_errors=True)
            self.lib.store._scrub()
            self._scrub_backups(None, None)
            return {"faces": n}

    def look_again(self, photo_ids: list[int] | None = None) -> int:
        """Read these photos (all, when none are named) for faces again, for example after the person was deleted."""
        with self._lock:
            with self.db.tx() as c:
                if photo_ids is None:
                    c.execute("UPDATE photos SET faces_model='' WHERE owner=? AND library=?", (self.owner, self.library))
                    return self._one("SELECT count(*) FROM photos WHERE owner=? AND library=?", (self.owner, self.library))[0]
                c.executemany("UPDATE photos SET faces_model='' WHERE id=? AND owner=? AND library=?",
                              [(i, self.owner, self.library) for i in photo_ids])
                return len(photo_ids)

    def _scrub_backups(self, face_ids: list[int] | None, person_ids: list[int] | None) -> None:
        """The copies MARY made before a format change hold fingerprints too: take these out of them as well."""
        for path in sorted(Path(self.db.backups).glob("memory-v*.db")):
            try:
                with closing(sqlite3.connect(path)) as c:
                    c.execute("PRAGMA secure_delete=ON")
                    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='photo_faces'").fetchone():
                        continue
                    if face_ids is None:
                        for sql in ("DELETE FROM vectors WHERE space='face'", "DELETE FROM photo_faces", "DELETE FROM people",
                                    "DELETE FROM people_nots"):
                            c.execute(sql)
                    else:
                        c.executemany("DELETE FROM vectors WHERE space='face' AND ref_table='photo_faces' AND ref_id=?",
                                      [(i,) for i in face_ids])
                        c.executemany("DELETE FROM photo_faces WHERE id=?", [(i,) for i in face_ids])
                        c.executemany("DELETE FROM people WHERE id=?", [(i,) for i in person_ids or []])
                    c.commit()
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                log.debug("couldn't take face data out of one copy", exc_info=True)
