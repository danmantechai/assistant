"""The worker that looks after your folders: it finds the pictures, reads each one once, and keeps looking for new ones.

Nothing is read until you add a folder. For each new file, in order: read it (decode, thumbnail, fingerprints, what the camera
wrote), see whether it is a copy of a photo already there, work out its date and place, and make the record: the "quick facts"
(about 0.05 s a photo). Then, photo by photo and after all the quick facts, the words in the picture (Windows OCR) and, only
if the person has switched it on, the faces (mary/photos/faces.py), taking turns so neither waits for the other; then the grouping
work that follows a name being given (mary/photos/people.py).

It runs on its own thread at below-normal priority and steps aside while MARY is answering a request. Every stage picks up
where it stopped (the database says what is done), folders are scanned again at start and every few minutes (by name, size
and time: no new package watches the disk), and what it logs is counts and timings, never a file name, place or text.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import Counter
from datetime import date
from pathlib import Path

from mary.logs import write_jsonl
from mary.memory import idle
from mary.photos import clip, copies, dates, faces, read, see, sources, text
from mary.photos.library import PhotoLibrary

RETRY_MODELS_S = 20       # how long to wait before looking again for models that were not there

log = logging.getLogger(__name__)


class Ingest:
    def __init__(self, lib: PhotoLibrary, busy=lambda: False, engine=None, clip_engine=None,
                 vision_fn=None, vision_tag: str = ""):
        self.lib = lib
        self.busy = busy
        self.timings: dict[str, list[float]] = {}     # stage -> [count, seconds]
        self.state = "idle"                           # idle | scanning | reading | text | faces | people | clip | vision
        self.face_counts: Counter = Counter()
        self._engine = engine                         # the face models (loaded once, on this thread, when first needed)
        self._engine_at = 0.0
        self._clip_engine = clip_engine
        self._clip_tag = ""
        if clip_engine is not None:
            self.lib._clip = clip_engine
        self._clip_at = 0.0
        self._vision_fn = vision_fn
        self._vision_ask = vision_fn
        self._vision_tag = vision_tag
        self._moments_dirty = False
        self._flip = False
        self.last_scan = 0.0
        self._wanted = threading.Event()
        self._stop = threading.Event()
        self._scan_requested = True
        self._thread: threading.Thread | None = None
        self._busy_note = False

    def _time(self, stage: str, started: float) -> None:
        t = self.timings.setdefault(stage, [0, 0.0])
        t[0] += 1
        t[1] += time.perf_counter() - started

    def average_ms(self) -> dict[str, float]:
        return {k: round(v[1] / v[0] * 1000, 2) for k, v in self.timings.items() if v[0]}

    # ------------------------------------------------------------------ the scan

    def scan(self, folder_id: int | None = None) -> dict:
        """Look through the added folders for new, changed and missing files (a listing: nothing is opened)."""
        started = time.perf_counter()
        report = {"found": 0, "new": 0, "changed": 0, "missing": 0, "cloud": 0}
        self.state = "scanning"
        skip = (self.lib.dir, *(Path(p) for p in self.lib.setting("exclude")))
        for folder in self.lib.folders():
            if folder_id is not None and folder["id"] != folder_id:
                continue
            fid = folder["id"]
            known = self.lib.known_files(fid)
            seen, fresh = set(), []
            if os.path.isdir(folder["path"]):
                for f in sources.walk(Path(folder["path"]), folder["recursive"], tuple(Path(p) for p in skip)):
                    report["found"] += 1
                    seen.add(f.path)
                    old = known.get(f.path)
                    if old is None:
                        fresh.append(f)
                        report["cloud"] += f.online_only
                        continue
                    file_id, size, mtime, state = old
                    if state == "ignored":
                        continue
                    if size != f.size or abs(mtime - f.mtime) > 1e-3 or state == "missing" or (state == "cloud" and not f.online_only):
                        self.lib.file_changed(file_id, f.size, f.mtime, f.online_only)
                        report["changed"] += 1
                missing = [i for path, (i, _, _, state) in known.items() if path not in seen and state not in ("missing", "ignored")]
                self.lib.mark_missing(missing)
                report["missing"] += len(missing)
            if fresh:
                self.lib.add_files(fid, fresh)
                report["new"] += len(fresh)
            self.lib.stamp_scan(fid)
        self.last_scan = time.monotonic()
        self._scan_requested = False
        self._time("scan", started)
        self.state = "idle"
        return report

    def request_scan(self) -> None:
        self._scan_requested = True
        self._wanted.set()

    # ------------------------------------------------------------------ one unit of work

    def step(self) -> bool:
        """Do the next small piece of work. False when there is nothing left to do."""
        rows = self.lib.next_new(1)
        if rows:
            self.state = "reading"
            self._file(rows[0])
            self._moments_dirty = True
            return True
        engine = self._faces_engine()
        words = bool(self.lib.setting("read_text")) and text.available()
        self._flip = not self._flip
        for kind in ("text", "faces") if self._flip else ("faces", "text"):
            if kind == "faces" and engine is not None:
                rows = self.lib.people.needing(engine.tag, 1)
                if rows:
                    self.state = "faces"
                    self._faces(rows[0], engine)
                    self._moments_dirty = True
                    return True
            elif kind == "text" and words:
                rows = self.lib.needing_text(1)
                if rows:
                    self.state = "text"
                    self._text(rows[0])
                    return True
        if self.lib.people.pending() and self.lib.people.maintain():
            self.state = "people"
            self._moments_dirty = True
            return True
        clip_eng = self._clip_model()
        if clip_eng is not None:
            rows = self.lib.needing_clip(clip_eng.tag, 1)
            if rows:
                self.state = "clip"
                self._clip(rows[0], clip_eng)
                return True
        if self._moments_dirty:
            self._moments_dirty = False
            self.lib.maintain_moments()
        if self._see():
            return True
        self.state = "idle"
        return False

    def _faces_engine(self):
        """The face models, when the person has switched recognition on and they are there (else None)."""
        people = self.lib.people
        if not people.enabled():
            return None
        if self._engine is None and time.monotonic() - self._engine_at >= RETRY_MODELS_S:
            self._engine_at = time.monotonic()
            try:
                self._engine = faces.load(self.lib)
            except Exception:
                log.exception("photo worker: the face models would not load")
                self._engine = None
            if self._engine is not None:
                people.use_model(self._engine.tag)
        return self._engine

    def _clip_model(self):
        """The CLIP picture-embedding model, when switched on and present on disk (else None)."""
        if not (self.lib.settings.get("clip") or {}).get("enabled", True):
            return None
        if self._clip_engine is None and time.monotonic() - self._clip_at >= RETRY_MODELS_S:
            self._clip_at = time.monotonic()
            try:
                self._clip_engine = clip.load(self.lib)
            except Exception:
                log.exception("photo worker: the CLIP models would not load")
                self._clip_engine = None
        if self._clip_engine is not None:
            self.lib._clip = self._clip_engine
            if self._clip_tag != self._clip_engine.tag:
                self._clip_tag = self._clip_engine.tag
                self.lib.use_clip_model(self._clip_engine.tag)
        return self._clip_engine

    def vision_ready(self) -> bool:
        """Vision descriptions run only while the PC is idle, plugged into mains power, and MARY is not answering."""
        if not (self.lib.settings.get("vision") or {}).get("enabled", True):
            return False
        if self.busy() or idle.on_battery():
            return False
        idle_s = float((self.lib.settings.get("vision") or {}).get("idle_s", 300))
        return idle.input_idle_seconds() >= idle_s

    def _vision_callable(self) -> tuple:
        """Return `(ask_fn, model_tag)` if vision is enabled and allowed, else `(None, '')`."""
        vcfg = self.lib.settings.get("vision") or {}
        if not vcfg.get("enabled", True):
            return None, ""
        model_name = str(vcfg.get("model") or self._vision_tag or "").strip()
        allow_cloud = bool(vcfg.get("allow_cloud", False))
        if model_name and see.is_cloud_endpoint(model_name) and not allow_cloud:
            return None, ""
        fn = self._vision_ask or self._vision_fn
        if fn is not None:
            tag = self._vision_tag or model_name or "vision-1"
            if see.is_cloud_endpoint(tag) and not allow_cloud:
                return None, ""
            return fn, tag
        return None, ""

    def vision_status(self) -> dict:
        vcfg = self.lib.settings.get("vision") or {}
        model_name = str(vcfg.get("model") or self._vision_tag or "").strip()
        cloud = see.is_cloud_endpoint(model_name) if model_name else False
        allow_cloud = bool(vcfg.get("allow_cloud", False))
        seen = self.lib._one(
            "SELECT count(*) FROM photos WHERE owner=? AND library=? AND seen<>''",
            (self.lib.owner, self.lib.lib),
        )[0]
        total = self.lib._one(
            "SELECT count(*) FROM photos WHERE owner=? AND library=?",
            (self.lib.owner, self.lib.lib),
        )[0]
        return {
            "enabled": bool(vcfg.get("enabled", True)),
            "model": model_name,
            "cloud": cloud,
            "allow_cloud": allow_cloud,
            "cloud_blocked": bool(cloud and not allow_cloud),
            "seen": seen,
            "left": max(0, total - seen),
        }

    def _file(self, row) -> None:
        lib, path, fid = self.lib, row["path"], row["id"]
        if not os.path.exists(path):
            lib.set_file(fid, "missing", "not found where it was")
            return
        if lib.ignored(path):
            lib.set_file(fid, "ignored", "removed by you")
            return
        t = time.perf_counter()
        try:
            facts = read.read_file(Path(path))
        except read.Unreadable as e:
            lib.set_file(fid, "unreadable", str(e))
            self._time("unreadable", t)
            return
        self._time("read", t)
        if min(facts.width, facts.height) < lib.setting("min_side"):
            lib.set_file(fid, "tiny", "too small to be a photo", facts.sha, facts.width, facts.height)
            return
        if lib.ignored(sha=facts.sha):
            lib.set_file(fid, "ignored", "removed by you", facts.sha, facts.width, facts.height)
            return
        t = time.perf_counter()
        same = lib.find_same(copies.Look(facts.sha, int(facts.dhash, 16), facts.width, facts.height, facts.taken))
        self._time("copies", t)
        t = time.perf_counter()
        before = row["photo_id"]      # a file read again (it changed) may now be another photo
        if same is not None:
            pid = same
            lib.attach(same, fid, facts)
        else:
            parts = lib.folder_parts(row["root"], path)
            album, scope = lib.folder_context(row["folder_id"])
            taken = dates.decide(facts.taken, os.path.basename(path), parts, row["mtime"], date.today(), album=album,
                                 album_scope=scope)
            place = lib.gazetteer.nearest(facts.lat, facts.lon) if facts.lat is not None else None
            pid = lib.create_photo(facts=facts, taken=taken, place=place, album=row["album"], file_id=fid)
            named = (lib._folder_row(row["folder_id"]) or {"place": ""})["place"]
            if named and place is None:
                lib.set_place([pid], named)
        if before and before != pid:
            lib.drop_if_orphan(before)
        self._time("record", t)

    def _text(self, row) -> None:
        t = time.perf_counter()
        found = ""
        try:
            if row["path"]:
                found = text.read_text(Path(row["path"]))
        except read.Unreadable:
            found = ""
        except Exception:    # the engine failing on one picture must not stop the rest
            log.debug("reading text failed for one photo", exc_info=True)
        self.lib.set_text(row["id"], found)
        self._time("text", t)

    def _faces(self, row, engine) -> None:
        """The faces in one photo: found, fingerprinted if clear enough, and put in a group (never given a name)."""
        t = time.perf_counter()
        people, pid = self.lib.people, row["id"]
        try:
            path = Path(row["path"] or "")
            if not row["path"] or not path.exists():
                people.skip(pid, engine.tag)
                return
            d = read.decode(path.read_bytes(), path.suffix, self.lib.setting("faces.detect_px"))
            found = faces.analyse(d.image, engine, self.lib.setting("faces.min_px"), self.lib.setting("faces.min_score"))
        except (read.Unreadable, OSError):
            people.skip(pid, engine.tag)
            return
        except Exception:    # a model failing on one picture must not stop the rest
            log.debug("reading faces failed for one photo", exc_info=True)
            people.skip(pid, engine.tag)
            return
        self.face_counts.update(people.add(pid, found, engine.tag))
        self._time("faces", t)

    def _clip(self, row, engine) -> None:
        """Embed one photo with the CLIP image encoder and store its vector in `vectors` (`space='image'`)."""
        t = time.perf_counter()
        pid = row["id"]
        try:
            thumb = self.lib.thumb_path(pid)
            if thumb.exists():
                vec = engine.embed_image(thumb)
            else:
                path = Path(row["path"] or "")
                if not row["path"] or not path.exists():
                    self.lib.skip_clip(pid, engine.tag)
                    return
                d = read.decode(path.read_bytes(), path.suffix, read.THUMB_PX)
                vec = engine.embed_image(d.image)
            if vec is None:
                self.lib.skip_clip(pid, engine.tag)
                return
            self.lib.set_clip(pid, vec, engine.tag)
        except Exception:
            log.debug("embedding picture failed for one photo", exc_info=True)
            self.lib.skip_clip(pid, engine.tag)
            return
        self._time("clip", t)

    def _see(self, row=None, ask_fn=None, model_tag: str = "") -> bool:
        """Draw numbered face boxes `[1]`, `[2]` on one photo, ask the vision model, and save the sanitized description."""
        if row is None:
            ask_fn, model_tag = self._vision_callable()
            if ask_fn is None or not self.vision_ready():
                return False
            rows = self.lib.needing_vision(model_tag, 1)
            if not rows:
                return False
            row = rows[0]
        self.state = "vision"
        t = time.perf_counter()
        pid = row["id"]
        try:
            path = Path(row["path"] or "")
            if not row["path"] or not path.exists():
                self.lib.skip_vision(pid, model_tag)
                return True
            d = read.decode(path.read_bytes(), path.suffix, see.MAX_VISION_SIDE)
            face_rows = self.lib.people.faces_for_vision(pid)
            forbidden = [
                r["name"]
                for r in self.lib._all(
                    "SELECT name FROM people WHERE owner=? AND library=? AND name<>''",
                    (self.lib.owner, self.lib.lib),
                )
            ]
            res = see.describe_image(d.image, face_rows, ask_fn, model_tag=model_tag, forbidden_names=forbidden)
            self.lib.set_seen(pid, res)
            self.lib.maintain_moments()
        except (read.Unreadable, OSError):
            self.lib.skip_vision(pid, model_tag)
            return True
        except Exception:
            log.debug("describing photo by vision failed for one photo", exc_info=True)
            self.lib.skip_vision(pid, model_tag)
            return True
        self._time("vision", t)
        return True

    def drain(self, seconds: float | None = None) -> int:
        """Work until nothing is left (or `seconds` have passed); steps aside while MARY is answering."""
        done, stop_at = 0, time.monotonic() + seconds if seconds else None
        while not self._stop.is_set() and (stop_at is None or time.monotonic() < stop_at):
            if self.busy():
                time.sleep(0.4)
                continue
            if not self.step():
                break
            done += 1
        return done

    # ------------------------------------------------------------------ the thread

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="photos", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wanted.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wanted.set()

    def _loop(self) -> None:
        if sys.platform == "win32":    # below normal: she answers first
            try:
                import ctypes

                ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -1)
            except Exception:
                pass
        while not self._stop.is_set():
            try:
                every = self.lib.setting("rescan_s")
                if self.lib.folders() and (self._scan_requested or time.monotonic() - self.last_scan >= every):
                    self.scan()
                if self.drain():
                    self._log()
            except Exception:
                log.exception("photo worker: a round failed")
            self._wanted.wait(timeout=5)
            self._wanted.clear()

    def _log(self) -> None:
        """Counts and timings: never a name, a place or text."""
        st = self.lib.status()
        write_jsonl("photos", {"event": "caught up", "files": st["files"], "photos": st["photos"], "ms": self.average_ms(),
                               "faces": dict(self.face_counts)})

    def progress(self) -> dict:
        return {"state": self.state, **self.lib.status(), "average_ms": self.average_ms(),
                "text": bool(self.lib.setting("read_text")), "ocr": text.available(),
                "places": self.lib.gazetteer.available,
                "faces": {"on": self.lib.people.enabled(), "ready": self._engine is not None}}
