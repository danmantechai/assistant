"""PhotoLibrary: the API for the photos in MARY's memory store. The settings page, the worker and (later) the model's tools
call this and nothing else: no SQL and no files outside this module.

A photo is one record however many files it lives in (copies, smaller versions, ones stored on their side): the files are
kept apart (photo_files) so removing a folder only takes away what that folder held. Every record carries an owner and a
library id. Originals are never touched: removing a photo or a folder removes MARY's record, its thumbnail and its words,
and a photo you removed stays removed when the folder is scanned again.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path

from mary.memory import clock
from mary.memory.store import MemoryStore
from mary.photos import copies, dates
from mary.photos.places import Gazetteer, Place

log = logging.getLogger(__name__)

TRUST = {"none": 0, "file": 1, "folder": 2, "filename": 4, "exif": 5, "album": 6, "person": 7}   # which source wins
STATES = ("new", "ok", "unreadable", "cloud", "tiny", "missing", "ignored")
FLAGS = ("undated", "guess", "check", "copies", "noplace")


class PhotoError(ValueError):
    """Something the person asked for that can't be done; the message is a sentence for them."""


def _iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def taken_of(r) -> dates.Taken:
    return dates.Taken(_dt(r["taken"]), _dt(r["taken_end"]), r["taken_precision"], r["taken_source"], r["date_flag"])


class PhotoLibrary:
    def __init__(self, store: MemoryStore, settings: dict | None = None, gazetteer: Gazetteer | None = None,
                 folder: Path | None = None):
        from mary.photos import DEFAULTS
        from mary.memory import merged

        self.store = store
        self.db = store.db
        self.settings = merged(DEFAULTS, settings or {})
        self.gazetteer = gazetteer or Gazetteer()
        self.dir = Path(folder) if folder else store.path.parent / "photos"
        self._lock = threading.RLock()
        self._hashes: copies.HashIndex | None = None
        self.gazetteer_path: Path | None = None       # where the list of places lives (or would)
        self._people = None

    @property
    def owner(self) -> str:
        return self.store.owner

    @property
    def lib(self) -> str:
        return self.store.library

    @property
    def people(self):
        """The people in these photos (mary/photos/people.py): faces, groups, names. Always there, whether or not the person has
        switched recognition on, because removing a photo must be able to remove its faces."""
        with self._lock:
            if self._people is None:
                from mary.photos.people import People

                self._people = People(self)
            return self._people

    def setting(self, dotted: str):
        node = self.settings
        for part in dotted.split("."):
            node = node[part]
        return node

    def _all(self, sql: str, args=()) -> list:
        return self.db.conn().execute(sql, args).fetchall()

    def _one(self, sql: str, args=()):
        return self.db.conn().execute(sql, args).fetchone()

    # ------------------------------------------------------------------ folders

    def _folder_row(self, fid: int):
        return self._one("SELECT * FROM photo_folders WHERE id=? AND owner=? AND library=?", (fid, self.owner, self.lib))

    def _folder_dict(self, r) -> dict:
        counts = {s: 0 for s in STATES}
        for row in self._all("SELECT state, count(*) n FROM photo_files WHERE folder_id=? GROUP BY state", (r["id"],)):
            counts[row["state"]] = row["n"]
        photos = self._one("SELECT count(DISTINCT photo_id) FROM photo_files WHERE folder_id=? AND state='ok'",
                           (r["id"],))[0]
        album = None
        if r["date_from"]:
            album = {"from": r["date_from"], "to": r["date_to"], "precision": r["date_precision"],
                     "scope": r["date_scope"], "label": dates.label(self._album_taken(r))}
        return {"id": r["id"], "path": r["path"], "label": r["label"], "recursive": bool(r["recursive"]),
                "added": r["added"], "last_scan": r["last_scan"], "files": counts, "photos": photos,
                "found": sum(counts.values()), "date": album, "place": r["place"]}

    def _album_taken(self, r) -> dates.Taken | None:
        if not r["date_from"]:
            return None
        return dates.Taken(_dt(r["date_from"]), _dt(r["date_to"]), r["date_precision"], "person")

    def folders(self) -> list[dict]:
        return [self._folder_dict(r) for r in self._all(
            "SELECT * FROM photo_folders WHERE owner=? AND library=? ORDER BY id", (self.owner, self.lib))]

    def folder(self, fid: int) -> dict | None:
        r = self._folder_row(fid)
        return self._folder_dict(r) if r else None

    def add_folder(self, path, label: str | None = None, recursive: bool = True) -> dict:
        p = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
        key = _norm(p)
        if key == _norm(self.dir) or key.startswith(_norm(self.dir) + os.sep):
            raise PhotoError("That's where MARY keeps her own thumbnails, not photos.")
        if not p or not os.path.isdir(p):
            raise PhotoError("That isn't a folder on this PC.")
        for r in self._all("SELECT * FROM photo_folders WHERE owner=? AND library=?", (self.owner, self.lib)):
            theirs = _norm(r["path"])
            if theirs == key:
                return self._folder_dict(r)
            if r["recursive"] and key.startswith(theirs + os.sep):
                raise PhotoError(f"That folder is already included in “{r['label']}”.")
        name = (label or os.path.basename(p.rstrip("\\/")) or p).strip()[:80]
        with self.db.tx() as c:
            fid = c.execute("INSERT INTO photo_folders(owner, library, path, label, recursive, added) VALUES (?,?,?,?,?,?)",
                            (self.owner, self.lib, p, name, int(recursive), _iso(clock.now()))).lastrowid
        return self.folder(fid)

    def set_folder(self, fid: int, label: str | None = None, recursive: bool | None = None) -> dict:
        if self._folder_row(fid) is None:
            raise PhotoError("That folder isn't there any more.")
        with self.db.tx() as c:
            if label is not None:
                c.execute("UPDATE photo_folders SET label=? WHERE id=?", (label.strip()[:80] or "Photos", fid))
            if recursive is not None:
                c.execute("UPDATE photo_folders SET recursive=? WHERE id=?", (int(recursive), fid))
        return self.folder(fid)

    def stamp_scan(self, fid: int) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE photo_folders SET last_scan=? WHERE id=?", (_iso(clock.now()), fid))

    def remove_folder(self, fid: int) -> dict:
        """Forget a folder: its files, and the photos that lived only there (record, thumbnail, words). Nothing on disk."""
        if self._folder_row(fid) is None:
            raise PhotoError("That folder isn't there any more.")
        photos = [r["photo_id"] for r in self._all(
            "SELECT DISTINCT photo_id FROM photo_files WHERE folder_id=? AND photo_id IS NOT NULL", (fid,))]
        gone = kept = 0
        for pid in photos:
            elsewhere = self._one("SELECT count(*) FROM photo_files WHERE photo_id=? AND folder_id<>? AND state='ok'",
                                  (pid, fid))[0]
            if elsewhere:
                kept += 1
            else:
                self._delete_photo(pid)
                gone += 1
        with self.db.tx() as c:
            c.execute("DELETE FROM photo_files WHERE folder_id=?", (fid,))
            c.execute("DELETE FROM photo_folders WHERE id=?", (fid,))
        for pid in photos:
            if self._photo_row(pid) is not None:
                self.refresh(pid)
        self.store._scrub()
        return {"photos_removed": gone, "photos_kept": kept}

    # ------------------------------------------------------------------ files (the scan and the worker use these)

    def known_files(self, fid: int) -> dict[str, tuple]:
        return {r["path"]: (r["id"], r["size"], r["mtime"], r["state"]) for r in self._all(
            "SELECT id, path, size, mtime, state FROM photo_files WHERE folder_id=?", (fid,))}

    def add_files(self, fid: int, found: list) -> None:
        now = _iso(clock.now())
        with self.db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO photo_files(owner, library, folder_id, path, size, mtime, state, reason, seen) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          [(self.owner, self.lib, fid, f.path, f.size, f.mtime,
                            "cloud" if f.online_only else "new",
                            "stored in the cloud, not on this PC yet" if f.online_only else "", now) for f in found])

    def file_changed(self, file_id: int, size: int, mtime: float, online_only: bool = False) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE photo_files SET size=?, mtime=?, state=?, reason=?, seen=? WHERE id=?",
                      (size, mtime, "cloud" if online_only else "new",
                       "stored in the cloud, not on this PC yet" if online_only else "", _iso(clock.now()), file_id))

    def mark_missing(self, ids: list[int]) -> None:
        if ids:
            with self.db.tx() as c:
                c.executemany("UPDATE photo_files SET state='missing', reason='not found where it was' WHERE id=?",
                              [(i,) for i in ids])

    def next_new(self, limit: int = 1) -> list:
        return self._all("SELECT f.*, d.path AS root, d.label AS album FROM photo_files f JOIN photo_folders d "
                         "ON d.id=f.folder_id WHERE f.state='new' AND f.owner=? AND f.library=? ORDER BY f.id LIMIT ?",
                         (self.owner, self.lib, limit))

    def set_file(self, file_id: int, state: str, reason: str = "", sha: str | None = None, width: int | None = None,
                 height: int | None = None, photo_id: int | None = None) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE photo_files SET state=?, reason=?, sha=coalesce(?, sha), width=coalesce(?, width), "
                      "height=coalesce(?, height), photo_id=? WHERE id=?",
                      (state, reason, sha, width, height, photo_id, file_id))

    def ignored(self, path: str | None = None, sha: str | None = None) -> bool:
        for kind, value in (("path", path and _norm(path)), ("sha", sha)):
            if value and self._one("SELECT 1 FROM photo_ignored WHERE owner=? AND library=? AND kind=? AND value=?",
                                   (self.owner, self.lib, kind, value)):
                return True
        return False

    # ------------------------------------------------------------------ photos: finding copies, creating, merging

    def _hash_index(self) -> copies.HashIndex:
        with self._lock:
            if self._hashes is None:
                self._hashes = copies.HashIndex([(r["id"], int(r["dhash"], 16)) for r in self._all(
                    "SELECT id, dhash FROM photos WHERE owner=? AND library=?", (self.owner, self.lib))])
            return self._hashes

    def _look(self, r) -> copies.Look:
        return copies.Look(r["sha"], int(r["dhash"], 16), r["width"], r["height"], _dt(r["exif_taken"]))

    def find_same(self, look: copies.Look) -> int | None:
        """The photo this picture is a copy of, or None. Same bytes first, then the strict look-alike rule."""
        row = self._one("SELECT photo_id FROM photo_files WHERE owner=? AND library=? AND sha=? AND state='ok' "
                        "AND photo_id IS NOT NULL LIMIT 1", (self.owner, self.lib, look.sha))
        if row:
            return row["photo_id"]
        for pid, _ in self._hash_index().within(look.dhash, copies.SAME_BITS):
            r = self._photo_row(pid)
            if r is not None and copies.same_photo(look, self._look(r)):
                return pid
        return None

    def similar(self, pid: int, limit: int = 12) -> list[dict]:
        """Photos that look like this one but were not merged: for the person to decide."""
        r = self._photo_row(pid)
        if r is None:
            return []
        mine, out = self._look(r), []
        for other, _ in self._hash_index().within(mine.dhash, copies.ALIKE_BITS):
            o = self._photo_row(other)
            if o is not None and other != pid and copies.might_be(mine, self._look(o)):
                out.append(self._photo_dict(o))
            if len(out) >= limit:
                break
        return out

    def _photo_row(self, pid: int):
        return self._one("SELECT * FROM photos WHERE id=? AND owner=? AND library=?", (pid, self.owner, self.lib))

    def _place_fields(self, place: Place | None) -> tuple:
        return (place.name, place.region, place.country, "gps") if place else ("", "", "", "")

    def create_photo(self, *, facts, taken: dates.Taken, place: Place | None, album: str, file_id: int,
                     stage: int = 1) -> int:
        now = _iso(clock.now())
        name, region, country, source = self._place_fields(place)
        with self.db.tx() as c:
            pid = c.execute(
                "INSERT INTO photos(owner, library, sha, dhash, width, height, taken, taken_end, taken_precision, "
                "taken_source, date_flag, exif_taken, lat, lon, place, region, country, place_source, album, stage, "
                "added, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.owner, self.lib, facts.sha, facts.dhash, facts.width, facts.height, _iso(taken.start),
                 _iso(taken.end), taken.precision, taken.source, taken.flag, _iso(facts.taken), facts.lat, facts.lon,
                 name, region, country, source, album, stage, now, now)).lastrowid
            c.execute("UPDATE photo_files SET photo_id=?, state='ok', reason='', sha=?, width=?, height=? WHERE id=?",
                      (pid, facts.sha, facts.width, facts.height, file_id))
        self.write_thumb(pid, facts.thumb)
        self._index(pid)
        self._hash_index().add(pid, int(facts.dhash, 16))
        return pid

    def attach(self, pid: int, file_id: int, facts) -> None:
        """This file is another copy of photo `pid`: it joins it, and the photo takes the best of both (the bigger picture
        for its thumbnail, the date, the position and the camera's time from whichever file has them)."""
        with self.db.tx() as c:
            c.execute("UPDATE photo_files SET photo_id=?, state='ok', reason='', sha=?, width=?, height=? WHERE id=?",
                      (pid, facts.sha, facts.width, facts.height, file_id))
        r = self._photo_row(pid)
        if facts.width * facts.height > r["width"] * r["height"]:
            self.write_thumb(pid, facts.thumb)
            with self.db.tx() as c:
                c.execute("UPDATE photos SET sha=?, dhash=?, width=?, height=? WHERE id=?",
                          (facts.sha, facts.dhash, facts.width, facts.height, pid))
            self._hash_index().update(pid, int(facts.dhash, 16))
        with self.db.tx() as c:
            if facts.taken and not r["exif_taken"]:
                c.execute("UPDATE photos SET exif_taken=? WHERE id=?", (_iso(facts.taken), pid))
            if facts.lat is not None and r["lat"] is None:
                c.execute("UPDATE photos SET lat=?, lon=? WHERE id=?", (facts.lat, facts.lon, pid))
        self.refresh(pid, place=True)

    def refresh(self, pid: int, place: bool = False) -> None:
        """Work the photo's date (and, with `place`, its place name) out again from all its files and what the person
        set. A date or place the person set is never replaced."""
        r = self._photo_row(pid)
        if r is None:
            return
        today = date.today()
        best = dates.Taken() if r["taken_source"] == "person" else None
        mine = taken_of(r) if best is not None else None
        if best is None:
            exif = _dt(r["exif_taken"])
            cands = []
            for f in self._all("SELECT f.path, f.mtime, d.path AS root, d.label, d.date_from, d.date_to, d.date_precision, "
                               "d.date_scope FROM photo_files f JOIN photo_folders d ON d.id=f.folder_id "
                               "WHERE f.photo_id=? AND f.state='ok'", (pid,)):
                album = dates.Taken(_dt(f["date_from"]), _dt(f["date_to"]), f["date_precision"], "person") \
                    if f["date_from"] else None
                cands.append(dates.decide(exif, Path(f["path"]).name, self.folder_parts(f["root"], f["path"]),
                                          f["mtime"], today, album=album, album_scope=f["date_scope"] or "unsure"))
            mine = max(cands, key=lambda t: TRUST.get(t.source, 0), default=dates.NONE)
        album_label = self._one("SELECT d.label FROM photo_files f JOIN photo_folders d ON d.id=f.folder_id "
                                "WHERE f.photo_id=? AND f.state='ok' ORDER BY f.width*f.height DESC, f.size DESC LIMIT 1",
                                (pid,))
        sets = {"taken": _iso(mine.start), "taken_end": _iso(mine.end), "taken_precision": mine.precision,
                "taken_source": mine.source, "date_flag": mine.flag, "album": album_label["label"] if album_label else "",
                "updated": _iso(clock.now())}
        if place and r["place_source"] != "person":
            p = self.gazetteer.nearest(r["lat"], r["lon"]) if r["lat"] is not None else None
            sets["place"], sets["region"], sets["country"], sets["place_source"] = self._place_fields(p)
        with self.db.tx() as c:
            c.execute(f"UPDATE photos SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), pid])
        self._index(pid)

    @staticmethod
    def folder_parts(root: str, path: str) -> list[str]:
        """The folder names from the added folder down to the file's own: what an album's name can say."""
        rel = os.path.relpath(os.path.dirname(path), root)
        return [os.path.basename(root.rstrip("\\/")) or root] + ([] if rel == "." else rel.split(os.sep))

    def folder_context(self, folder_id: int) -> tuple[dates.Taken | None, str]:
        r = self._folder_row(folder_id)
        return (self._album_taken(r), r["date_scope"] or "unsure") if r else (None, "unsure")

    def write_thumb(self, pid: int, data: bytes) -> None:
        target = self.thumb_path(pid)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        with self.db.tx() as c:
            c.execute("UPDATE photos SET thumb=? WHERE id=?", (target.relative_to(self.dir).as_posix(), pid))

    def thumb_path(self, pid: int) -> Path:
        return self.dir / "thumbs" / f"{pid // 1000:04}" / f"{pid:08}.jpg"

    def _index(self, pid: int) -> None:
        r = self._photo_row(pid)
        with self.db.tx() as c:
            c.execute("DELETE FROM photos_fts WHERE rowid=?", (pid,))
            if r is not None:
                c.execute("INSERT INTO photos_fts(rowid, text, caption, album, place) VALUES (?,?,?,?,?)",
                          (pid, r["text"], r["caption"], r["album"], " ".join(x for x in (r["place"], r["region"], r["country"]) if x)))

    # ------------------------------------------------------------------ the words in a photo (the slow stage)

    def needing_text(self, limit: int = 1) -> list:
        return self._all("SELECT p.id, (SELECT path FROM photo_files f WHERE f.photo_id=p.id AND f.state='ok' "
                         "ORDER BY f.width*f.height DESC LIMIT 1) AS path FROM photos p WHERE p.stage<2 AND p.owner=? "
                         "AND p.library=? ORDER BY p.id LIMIT ?", (self.owner, self.lib, limit))

    def set_text(self, pid: int, text: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE photos SET text=?, stage=2, updated=? WHERE id=?", (text, _iso(clock.now()), pid))
        self._index(pid)

    # ------------------------------------------------------------------ reading

    def _photo_dict(self, r, files: int | None = None) -> dict:
        t = taken_of(r)
        label = ", ".join(x for x in (r["place"], r["region"], r["country"]) if x)
        return {"id": r["id"], "width": r["width"], "height": r["height"], "taken": r["taken"], "taken_end": r["taken_end"],
                "precision": r["taken_precision"], "source": r["taken_source"], "guess": t.guess, "flag": r["date_flag"],
                "when": dates.label(t), "place": r["place"], "country": r["country"], "place_label": label,
                "place_source": r["place_source"], "lat": r["lat"], "lon": r["lon"], "text": r["text"],
                "caption": r["caption"], "album": r["album"], "stage": r["stage"], "added": r["added"],
                "version": r["updated"],      # changes when the thumbnail may have (for a cache)
                "files": files if files is not None else
                self._one("SELECT count(*) FROM photo_files WHERE photo_id=? AND state='ok'", (r["id"],))[0]}

    def get(self, pid: int) -> dict | None:
        r = self._photo_row(pid)
        if r is None:
            return None
        out = self._photo_dict(r)
        out["people"] = self.people.in_photo(pid)
        out["file_list"] = [{"path": f["path"], "folder": f["label"], "size": f["size"], "width": f["width"],
                             "height": f["height"], "exists": os.path.exists(f["path"])} for f in self._all(
            "SELECT f.*, d.label FROM photo_files f JOIN photo_folders d ON d.id=f.folder_id "
            "WHERE f.photo_id=? AND f.state='ok' ORDER BY f.width*f.height DESC, f.size DESC", (pid,))]
        return out

    def file_paths(self, pid: int) -> list[str]:
        return [r["path"] for r in self._all("SELECT path FROM photo_files WHERE photo_id=? AND state='ok' "
                                             "ORDER BY width*height DESC", (pid,))]

    def _where(self, year=None, month=None, place=None, folder=None, q=None, flag=None, person=None):
        where, args = ["p.owner=? AND p.library=?"], [self.owner, self.lib]
        if year:
            y = int(year)
            m = int(month) if month else None
            start = datetime(y, m or 1, 1)
            end = datetime(y + (m == 12 if m else 1), (m % 12 + 1) if m else 1, 1) if m else datetime(y + 1, 1, 1)
            where.append("p.taken < ? AND p.taken_end > ?")
            args += [_iso(end), _iso(start)]
        if place:
            where.append("(p.place = ? OR p.country = ?)")
            args += [place, place]
        if folder:
            where.append("EXISTS (SELECT 1 FROM photo_files f WHERE f.photo_id=p.id AND f.folder_id=? AND f.state='ok')")
            args.append(int(folder))
        if person:
            where.append("EXISTS (SELECT 1 FROM photo_faces f WHERE f.photo_id=p.id AND f.person_id=?)")
            args.append(int(person))
        if q:
            tokens = re.findall(r"[^\W_]+", q.lower())
            if tokens:
                match = " ".join(f'"{t}"' for t in tokens[:-1]) + (" " if len(tokens) > 1 else "") + f'"{tokens[-1]}"*'
                where.append("p.id IN (SELECT rowid FROM photos_fts WHERE photos_fts MATCH ?)")
                args.append(match)
        if flag == "undated":
            where.append("p.taken IS NULL")
        elif flag == "guess":
            where.append("p.taken_source IN ('folder','file')")
        elif flag == "check":
            where.append("p.date_flag <> ''")
        elif flag == "copies":
            where.append("(SELECT count(*) FROM photo_files f WHERE f.photo_id=p.id AND f.state='ok') > 1")
        elif flag == "noplace":
            where.append("p.place = ''")
        return " AND ".join(where), args

    def list(self, *, order: str = "newest", limit: int = 60, offset: int = 0, **filters) -> dict:
        where, args = self._where(**filters)
        by = {"newest": "p.taken IS NULL, p.taken DESC, p.id DESC", "oldest": "p.taken IS NULL, p.taken, p.id",
              "added": "p.id DESC"}.get(order, "p.id DESC")
        rows = self._all(f"SELECT p.*, (SELECT count(*) FROM photo_files f WHERE f.photo_id=p.id AND f.state='ok') AS nfiles "
                         f"FROM photos p WHERE {where} ORDER BY {by} LIMIT ? OFFSET ?", [*args, limit + 1, max(0, offset)])
        total = self._one(f"SELECT count(*) FROM photos p WHERE {where}", args)[0]
        return {"items": [self._photo_dict(r, r["nfiles"]) for r in rows[:limit]], "more": len(rows) > limit, "total": total}

    def ids(self, **filters) -> list[int]:
        where, args = self._where(**filters)
        return [r[0] for r in self._all(f"SELECT p.id FROM photos p WHERE {where}", args)]

    def facets(self, year: int | None = None) -> dict:
        """What the page can filter by: years (or the months of one), places, albums, and what needs a look."""
        base = "owner=? AND library=?"
        a = (self.owner, self.lib)
        if year:
            months = [{"month": int(r["m"]), "n": r["n"]} for r in self._all(
                "SELECT substr(taken,6,2) m, count(*) n FROM photos WHERE " + base + " AND substr(taken,1,4)=? "
                "AND taken_precision IN ('second','day','month') GROUP BY m ORDER BY m", (*a, f"{int(year):04}"))]
            return {"months": months}
        years = [{"year": int(r["y"]), "n": r["n"]} for r in self._all(
            "SELECT substr(taken,1,4) y, count(*) n FROM photos WHERE " + base + " AND taken IS NOT NULL GROUP BY y "
            "ORDER BY y DESC", a)]
        places = [{"place": r["place"], "country": r["country"], "n": r["n"]} for r in self._all(
            "SELECT place, country, count(*) n FROM photos WHERE " + base + " AND place<>'' GROUP BY place, country "
            "ORDER BY n DESC, place LIMIT 200", a)]
        count = lambda cond: self._one(f"SELECT count(*) FROM photos WHERE {base} AND {cond}", a)[0]  # noqa: E731
        return {"years": years, "places": places, "people": self.people.named_counts(), "undated": count("taken IS NULL"),
                "guess": count("taken_source IN ('folder','file')"), "check": count("date_flag<>''"),
                "noplace": count("place=''"),
                "copies": self._one("SELECT count(*) FROM (SELECT photo_id FROM photo_files WHERE owner=? AND library=? "
                                    "AND state='ok' AND photo_id IS NOT NULL GROUP BY photo_id HAVING count(*)>1)", a)[0]}

    def status(self) -> dict:
        """Counts only: what has been found, read, set aside, and what is still to do."""
        files = {s: 0 for s in STATES}
        for r in self._all("SELECT state, count(*) n FROM photo_files WHERE owner=? AND library=? GROUP BY state",
                           (self.owner, self.lib)):
            files[r["state"]] = r["n"]
        photos = self._one("SELECT count(*) FROM photos WHERE owner=? AND library=?", (self.owner, self.lib))[0]
        text_left = self._one("SELECT count(*) FROM photos WHERE owner=? AND library=? AND stage<2",
                              (self.owner, self.lib))[0]
        return {"files": files, "found": sum(files.values()), "photos": photos,
                "copies": files["ok"] - photos, "reading": files["new"], "text_left": text_left}

    # ------------------------------------------------------------------ what the person changes

    def set_taken(self, ids: list[int], taken: dates.Taken) -> int:
        with self.db.tx() as c:
            for pid in ids:
                c.execute("UPDATE photos SET taken=?, taken_end=?, taken_precision=?, taken_source='person', date_flag='', "
                          "updated=? WHERE id=? AND owner=? AND library=?",
                          (_iso(taken.start), _iso(taken.end), taken.precision, _iso(clock.now()), pid, self.owner, self.lib))
        return len(ids)

    def clear_taken(self, ids: list[int]) -> int:
        """Back to what the camera, the name or the album says."""
        with self.db.tx() as c:
            for pid in ids:
                c.execute("UPDATE photos SET taken_source='none' WHERE id=? AND taken_source='person'", (pid,))
        for pid in ids:
            self.refresh(pid)
        return len(ids)

    def set_place(self, ids: list[int], text: str) -> int:
        text = " ".join(str(text or "").split())[:120]
        if not text:
            raise PhotoError("Write the place's name.")
        with self.db.tx() as c:
            for pid in ids:
                c.execute("UPDATE photos SET place=?, region='', country='', place_source='person', updated=? "
                          "WHERE id=? AND owner=? AND library=?", (text, _iso(clock.now()), pid, self.owner, self.lib))
        for pid in ids:
            self._index(pid)
        return len(ids)

    def clear_place(self, ids: list[int]) -> int:
        with self.db.tx() as c:
            for pid in ids:
                c.execute("UPDATE photos SET place_source='' WHERE id=? AND place_source='person'", (pid,))
        for pid in ids:
            self.refresh(pid, place=True)
        return len(ids)

    def set_caption(self, pid: int, text: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE photos SET caption=?, updated=? WHERE id=? AND owner=? AND library=?",
                      (" ".join(str(text or "").split())[:500], _iso(clock.now()), pid, self.owner, self.lib))
        self._index(pid)

    def set_album(self, fid: int, taken: dates.Taken | None, scope: str = "unsure", place: str | None = None) -> dict:
        """A date and/or place for a whole album. It reaches the photos already there and the ones that arrive later;
        a camera's own date is kept unless scope is "all" (a print photographed today, for instance)."""
        if self._folder_row(fid) is None:
            raise PhotoError("That folder isn't there any more.")
        if scope not in ("unsure", "all"):
            raise PhotoError("Say which photos: the ones whose date is a guess, or all of them.")
        with self.db.tx() as c:
            if taken is not None:
                c.execute("UPDATE photo_folders SET date_from=?, date_to=?, date_precision=?, date_scope=? WHERE id=?",
                          (_iso(taken.start), _iso(taken.end), taken.precision, scope, fid))
            if place is not None:
                c.execute("UPDATE photo_folders SET place=? WHERE id=?", (" ".join(place.split())[:120], fid))
        pids = [r["photo_id"] for r in self._all("SELECT DISTINCT photo_id FROM photo_files WHERE folder_id=? AND state='ok' "
                                                 "AND photo_id IS NOT NULL", (fid,))]
        name = " ".join((place or "").split())[:120]
        for pid in pids:
            if taken is not None:
                self.refresh(pid)
            if name:
                r = self._photo_row(pid)
                if r["place_source"] != "person" and not r["place"]:
                    self.set_place([pid], name)
        return {"photos": len(pids), "folder": self.folder(fid)}

    def clear_album(self, fid: int) -> dict:
        with self.db.tx() as c:
            c.execute("UPDATE photo_folders SET date_from=NULL, date_to=NULL, date_precision=NULL, date_scope=NULL WHERE id=?",
                      (fid,))
        for r in self._all("SELECT DISTINCT photo_id FROM photo_files WHERE folder_id=? AND photo_id IS NOT NULL", (fid,)):
            self.refresh(r["photo_id"])
        return self.folder(fid)

    def merge(self, ids: list[int]) -> int:
        """The person says these are the same photo: the first one keeps all the files."""
        ids = [i for i in dict.fromkeys(ids) if self._photo_row(i) is not None]
        if len(ids) < 2:
            raise PhotoError("Pick two or more photos that are the same.")
        keep = ids[0]
        for other in ids[1:]:
            with self.db.tx() as c:
                c.execute("UPDATE photo_files SET photo_id=? WHERE photo_id=?", (keep, other))
            old = self._photo_row(other)
            if old["text"] and not self._photo_row(keep)["text"]:
                self.set_text(keep, old["text"])
            self._delete_photo(other)
        best = self._one("SELECT * FROM photo_files WHERE photo_id=? AND state='ok' ORDER BY width*height DESC, size DESC LIMIT 1",
                         (keep,))
        if best:
            with self.db.tx() as c:
                c.execute("UPDATE photos SET sha=?, width=?, height=? WHERE id=?", (best["sha"], best["width"], best["height"], keep))
        self.refresh(keep, place=True)
        return keep

    def remove(self, ids: list[int]) -> int:
        """Remove photos from MARY's records for good (record, thumbnail, words). The originals are not touched, and a rescan
        will not bring them back."""
        n = 0
        for pid in ids:
            if self._photo_row(pid) is None:
                continue
            now = _iso(clock.now())
            with self.db.tx() as c:
                for f in c.execute("SELECT path, sha FROM photo_files WHERE photo_id=?", (pid,)).fetchall():
                    for kind, value in (("path", _norm(f["path"])), ("sha", f["sha"])):
                        if value:
                            c.execute("INSERT OR IGNORE INTO photo_ignored VALUES (?,?,?,?,?)",
                                      (self.owner, self.lib, kind, value, now))
                c.execute("UPDATE photo_files SET state='ignored', reason='removed by you', photo_id=NULL WHERE photo_id=?", (pid,))
            self._delete_photo(pid)
            n += 1
        if n:
            self.store._scrub()
        return n

    def use_gazetteer(self, gazetteer: Gazetteer) -> int:
        """A (new) list of places: name every photo that has a GPS position and no place yet. Returns how many."""
        self.gazetteer = gazetteer
        named = 0
        for r in self._all("SELECT id FROM photos WHERE owner=? AND library=? AND lat IS NOT NULL AND place='' "
                           "AND place_source<>'person'", (self.owner, self.lib)):
            self.refresh(r["id"], place=True)
            named += 1
        return named

    def drop_if_orphan(self, pid: int) -> None:
        """A photo none of whose files is left (its file was replaced by another picture) goes; one that still has files
        is worked out again."""
        if self._photo_row(pid) is None:
            return
        if self._one("SELECT count(*) FROM photo_files WHERE photo_id=? AND state='ok'", (pid,))[0]:
            self.refresh(pid, place=True)
        else:
            self._delete_photo(pid)

    def _delete_photo(self, pid: int) -> None:
        r = self._photo_row(pid)
        if r is None:
            return
        self.people.forget_photo(pid)
        with self.db.tx() as c:
            c.execute("DELETE FROM photos_fts WHERE rowid=?", (pid,))
            c.execute("DELETE FROM photos WHERE id=?", (pid,))
        self.thumb_path(pid).unlink(missing_ok=True)
        self._hash_index().remove(pid)
