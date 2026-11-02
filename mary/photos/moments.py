"""Moments (Phase 4): photos grouped by time and place into events, each with a short summary such as
"June 1998 · the coast · a wedding · Grandma, Kofi".

Each moment has its time span (`start`, `end`, `precision`, `guess`), place (`place`, `country`), named people, and
photos ordered best one first (`cover_id`, `rank`). The person can rename, merge, or split moments, and their choices
(`how='you'` and `moment_nots`) are kept when new photos arrive or the library is rebuilt.
"""
from __future__ import annotations

import calendar
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from mary.memory import clock
from mary.photos import context, dates

if TYPE_CHECKING:
    from mary.photos.library import PhotoLibrary

GENERIC_FOLDERS = {
    "photos", "pictures", "images", "camera", "dcim", "100apple", "100andro", "camera roll",
    "saved pictures", "screenshots", "uploads", "backup", "backups", "shared", "misc",
    "messenger", "whatsapp", " whatsapp images", "telegram", "downloads", "desktop", "scans", "scan",
    "import", "imported", "all photos", "library",
}

EVENT_TAG_ARTICLES = {
    "wedding": "a wedding",
    "birthday": "a birthday",
    "birthday party": "a birthday",
    "birthday cake": "a birthday",
    "party": "a party",
    "christmas": "Christmas",
    "easter": "Easter",
    "graduation": "a graduation",
    "concert": "a concert",
    "festival": "a festival",
    "picnic": "a picnic",
    "barbecue": "a barbecue",
    "bbq": "a barbecue",
    "dinner": "dinner",
    "lunch": "lunch",
    "breakfast": "breakfast",
    "beach": "the beach",
    "coast": "the coast",
    "harbour": "the harbour",
    "harbor": "the harbour",
    "sunset": "sunset",
    "sunrise": "sunrise",
    "hike": "a hike",
    "camping": "camping",
    "holiday": "a holiday",
    "trip": "a trip",
    "park": "the park",
    "garden": "the garden",
    "museum": "the museum",
    "zoo": "the zoo",
    "snow": "the snow",
    "mountains": "the mountains",
    "mountain": "the mountains",
    "boat": "a boat trip",
}

PRIORITY_EVENT_TAGS = (
    "wedding", "birthday", "birthday party", "birthday cake", "christmas", "easter", "graduation",
    "concert", "festival", "party", "picnic", "barbecue", "bbq", "camping", "hike", "holiday", "trip",
    "beach", "coast", "harbour", "harbor", "mountains", "mountain", "snow", "sunset", "sunrise",
    "dinner", "lunch", "park", "garden", "museum", "zoo", "boat",
)

_PREC_ORDER = {"second": 5, "day": 4, "month": 3, "year": 2, "decade": 1, "": 0}
_DATE_STRIP = re.compile(
    r"\b(?:(?:19|20)\d0s|(?:19|20)\d{2}(?:[-_. ](?:0[1-9]|1[0-2])(?:[-_. ](?:0[1-9]|[12]\d|3[01]))?)?|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)


def _iso(d: datetime | None) -> str:
    return d.isoformat(timespec="seconds") if d else ""


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def span_label(start: datetime | None, end: datetime | None, precision: str) -> str:
    """Human time span for a moment summary ('June 1998', '12–14 August 2021', '25 December 2023')."""
    if start is None:
        return "undated"
    if precision == "decade":
        return f"the {start.year // 10 * 10}s"
    if precision == "year":
        return str(start.year)
    if precision == "month":
        return f"{calendar.month_name[start.month]} {start.year}"
    last = (end - timedelta(seconds=1)) if (end and end > start) else start
    if start.date() == last.date():
        return f"{start.day} {calendar.month_name[start.month]} {start.year}"
    if start.year == last.year and start.month == last.month:
        return f"{start.day}–{last.day} {calendar.month_name[start.month]} {start.year}"
    if start.year == last.year:
        return f"{start.day} {calendar.month_name[start.month]} – {last.day} {calendar.month_name[last.month]} {start.year}"
    return (f"{start.day} {calendar.month_name[start.month]} {start.year} – "
            f"{last.day} {calendar.month_name[last.month]} {last.year}")


def clean_folder_theme(folder_name: str) -> str:
    """Extract an event/album name from a folder name if it says more than just a date or 'Camera'."""
    raw = (folder_name or "").strip()
    if not raw or raw.lower() in GENERIC_FOLDERS:
        return ""
    # Keep "Christmas" even though dates.py also knows Christmas is December
    low = raw.lower()
    if "christmas" in low:
        rest = _DATE_STRIP.sub(" ", raw)
        rest = " ".join(rest.strip(" -_.,·").split())
        return rest or "Christmas"
    stripped = _DATE_STRIP.sub(" ", raw)
    stripped = re.sub(r"[-_.,·()/\\]+", " ", stripped)
    stripped = " ".join(stripped.split())
    if not stripped or stripped.lower() in GENERIC_FOLDERS or len(stripped) < 2:
        return ""
    low_s = stripped.lower()
    if low_s in EVENT_TAG_ARTICLES:
        return EVENT_TAG_ARTICLES[low_s]
    return stripped


def _phrase_from_captions(captions: list[str], place: str, people_names: list[str]) -> str:
    """Pick the person's own event phrase from captions when present, ignoring bare place or person names."""
    skip = {place.lower()} | {n.lower() for n in people_names}
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for cap in captions:
        c = " ".join((cap or "").split()).strip(" .,;-")
        if not c:
            continue
        low = c.lower()
        if low in skip:
            continue
        # Check if caption mentions a known event word
        for key in PRIORITY_EVENT_TAGS:
            if re.search(rf"\b{re.escape(key)}\b", low):
                phrase = EVENT_TAG_ARTICLES.get(key, key)
                counts[phrase.lower()] += 2
                original.setdefault(phrase.lower(), phrase)
                break
        else:
            if len(c.split()) <= 5:
                counts[low] += 1
                original.setdefault(low, c)
    if not counts:
        return ""
    best_low = counts.most_common(1)[0][0]
    return original[best_low]


def _phrase_from_tags(tags_list: list[str], place: str) -> str:
    """Pick a short event/scene phrase from vision tags when the person hasn't named the event."""
    place_low = (place or "").lower()
    counts: Counter[str] = Counter()
    for raw in tags_list:
        for t in (raw or "").split(","):
            tag = t.strip().lower()
            if not tag or tag == place_low:
                continue
            counts[tag] += 1
    if not counts:
        return ""
    # Prefer event tags (wedding, birthday, beach, ...) when present
    for key in PRIORITY_EVENT_TAGS:
        if counts.get(key, 0) > 0:
            phrase = EVENT_TAG_ARTICLES.get(key, key)
            if phrase.lower() != place_low and key != place_low:
                return phrase
    top_tag, _ = counts.most_common(1)[0]
    return EVENT_TAG_ARTICLES.get(top_tag, top_tag)


class Moments:
    """Moments in `lib`: grouped by time and place, with rename, merge, and split."""

    def __init__(self, lib: PhotoLibrary):
        self.lib = lib
        self.db = lib.db

    @property
    def owner(self) -> str:
        return self.lib.owner

    @property
    def library_id(self) -> str:
        return self.lib.lib

    def _all(self, sql: str, args=()) -> list:
        return self.db.conn().execute(sql, args).fetchall()

    def _one(self, sql: str, args=()):
        return self.db.conn().execute(sql, args).fetchone()

    # ---------------------------------------------------------------- scoring & building a moment from its photos

    def _photo_records(self, pids: list[int] | None = None) -> dict[int, dict]:
        where = "p.owner=? AND p.library=?"
        args: list = [self.owner, self.library_id]
        if pids is not None:
            if not pids:
                return {}
            marks = ",".join("?" for _ in pids)
            where += f" AND p.id IN ({marks})"
            args.extend(pids)
        rows = self._all(f"SELECT p.* FROM photos p WHERE {where} ORDER BY p.taken IS NULL, p.taken, p.id", args)
        out: dict[int, dict] = {}
        for r in rows:
            out[r["id"]] = dict(r)
            out[r["id"]]["subfolders"] = []
            out[r["id"]]["named_people"] = []
            out[r["id"]]["face_score"] = 0.0
        if not out:
            return out
        # Subfolder names for event grouping
        for f in self._all(
            "SELECT f.photo_id, f.path, d.path AS root, d.label FROM photo_files f JOIN photo_folders d ON d.id=f.folder_id "
            "WHERE f.owner=? AND f.library=? AND f.state='ok' AND f.photo_id IS NOT NULL",
            (self.owner, self.library_id),
        ):
            pid = f["photo_id"]
            if pid in out:
                parts = self.lib.folder_parts(f["root"], f["path"])
                for part in reversed(parts):
                    if part.strip().lower() not in GENERIC_FOLDERS:
                        out[pid]["subfolders"].append(part.strip())
                        break
        # Faces & named people per photo
        for fr in self._all(
            "SELECT f.photo_id, f.quality, f.usable, p.id AS person_id, p.name, p.state "
            "FROM photo_faces f LEFT JOIN people p ON p.id=f.person_id "
            "WHERE f.owner=? AND f.library=?",
            (self.owner, self.library_id),
        ):
            pid = fr["photo_id"]
            if pid not in out:
                continue
            q = float(fr["quality"] or 0.0) if fr["usable"] else 0.0
            if fr["state"] == "named" and fr["name"]:
                out[pid]["named_people"].append((int(fr["person_id"]), fr["name"]))
                out[pid]["face_score"] += 1.5 + q
            elif fr["usable"]:
                out[pid]["face_score"] += 0.4 * q
        return out

    @staticmethod
    def _photo_quality(p: dict) -> tuple[float, str, int]:
        """Higher is better for picking the cover photo and ordering photos inside a moment."""
        cap = 4.0 if (p.get("caption") or "").strip() else 0.0
        faces = float(p.get("face_score") or 0.0)
        sharp = 0.6 * float(p.get("sharpness") or 0.0)
        res = 0.4 * min(1.0, (int(p.get("width") or 0) * int(p.get("height") or 0)) / (1920 * 1080))
        pen = -0.5 if (p.get("date_flag") or p.get("taken_source") in ("folder", "file")) else 0.0
        score = round(cap + faces + sharp + res + pen, 6)
        # Negate id/taken in sort key so earliest taken & lowest id break ties
        return (score, p.get("taken") or "9999", -int(p["id"]))

    def _rank_photos(self, photos: list[dict]) -> list[dict]:
        return sorted(photos, key=lambda p: (-self._photo_quality(p)[0], p.get("taken") or "9999", int(p["id"])))

    @staticmethod
    def _compute_fields(ranked: list[dict], title: str = "") -> dict:
        """Compute time span, place, theme, people, and summary from a moment's ranked photo records."""
        dated = [p for p in ranked if p.get("taken")]
        if dated:
            starts = [_dt(p["taken"]) for p in dated if _dt(p["taken"])]
            ends = [_dt(p["taken_end"]) or _dt(p["taken"]) for p in dated if _dt(p["taken"])]
            start_dt = min(starts) if starts else None
            end_dt = max(ends) if ends else start_dt
            # Use the finest precision shared by the dated photos (or dominant precision)
            precs = [p["taken_precision"] for p in dated if p.get("taken_precision")]
            precision = max(precs, key=lambda pr: _PREC_ORDER.get(pr, 0)) if precs else "day"
            guess = int(all(p.get("taken_source") in ("folder", "file") for p in dated))
        else:
            start_dt = end_dt = None
            precision = ""
            guess = 1

        # Dominant place (person-set places weighted double)
        place_counts: Counter[tuple[str, str]] = Counter()
        for p in ranked:
            pl = (p.get("place") or "").strip()
            if pl:
                w = 2 if p.get("place_source") == "person" else 1
                place_counts[(pl, (p.get("country") or "").strip())] += w
        place, country = place_counts.most_common(1)[0][0] if place_counts else ("", "")

        # Named people ordered by number of photos they appear in within this moment, then by first appearance
        person_counts: Counter[int] = Counter()
        person_names: dict[int, str] = {}
        first_seen: dict[int, int] = {}
        for idx, p in enumerate(ranked):
            seen_in_photo: set[int] = set()
            for person_id, name in p.get("named_people") or ():
                if person_id not in seen_in_photo:
                    seen_in_photo.add(person_id)
                    person_counts[person_id] += 1
                    person_names[person_id] = name
                    first_seen.setdefault(person_id, idx)
        ordered_people = [
            {"id": pid, "name": person_names[pid], "photos": count}
            for pid, count in sorted(person_counts.items(), key=lambda kv: (-kv[1], first_seen[kv[0]], person_names[kv[0]].lower()))
        ]
        people_names = [x["name"] for x in ordered_people]

        # Event / theme: user's moment title > user's photo captions > folder/album theme > vision tags
        user_title = " ".join((title or "").split())
        if user_title:
            theme = user_title
        else:
            theme = _phrase_from_captions([p.get("caption") or "" for p in ranked], place, people_names)
            if not theme:
                folder_themes = [clean_folder_theme(sf) for p in ranked for sf in (p.get("subfolders") or ())]
                folder_themes = [ft for ft in folder_themes if ft and ft.lower() != place.lower()]
                if folder_themes:
                    theme = Counter(folder_themes).most_common(1)[0][0]
            if not theme:
                theme = _phrase_from_tags([p.get("tags") or "" for p in ranked], place)

        when_str = span_label(start_dt, end_dt, precision)
        people_str = ", ".join(people_names[:4])
        parts = [when_str]
        if place:
            parts.append(place)
        if theme and theme.lower() not in {when_str.lower(), place.lower()}:
            parts.append(theme)
        if people_str:
            parts.append(people_str)
        summary = " · ".join(parts)

        return {
            "title": user_title,
            "summary": summary,
            "theme": theme,
            "start": _iso(start_dt),
            "end": _iso(end_dt),
            "precision": precision,
            "guess": guess,
            "place": place,
            "country": country,
            "cover_id": int(ranked[0]["id"]) if ranked else None,
            "photos": len(ranked),
            "people": ordered_people,
        }

    # ---------------------------------------------------------------- automatic clustering

    def _nots(self) -> set[tuple[int, int]]:
        return {(int(r["a"]), int(r["b"])) for r in self._all(
            "SELECT a, b FROM moment_nots WHERE owner=? AND library=?", (self.owner, self.library_id))}

    @staticmethod
    def _blocked(cluster: list[dict], candidate_id: int, nots: set[tuple[int, int]]) -> bool:
        for p in cluster:
            a, b = (p["id"], candidate_id) if p["id"] < candidate_id else (candidate_id, p["id"])
            if (a, b) in nots:
                return True
        return False

    @staticmethod
    def _event_folder(p: dict) -> str:
        for sf in p.get("subfolders") or ():
            t = clean_folder_theme(sf)
            if t:
                return sf.lower()
        return ""

    def _can_join_fine(self, cluster: list[dict], cand: dict, gap_hours: float, nots: set[tuple[int, int]]) -> bool:
        if self._blocked(cluster, cand["id"], nots):
            return False
        last = cluster[-1]
        t_prev = _dt(last["taken"])
        t_cand = _dt(cand["taken"])
        if t_prev is None or t_cand is None:
            return False
        gap = (t_cand - t_prev).total_seconds() / 3600.0
        if gap < 0:
            gap = abs(gap)

        pl_prev = (last.get("place") or "").strip().lower()
        pl_cand = (cand.get("place") or "").strip().lower()
        f_prev = self._event_folder(last)
        f_cand = self._event_folder(cand)

        # Explicit event subfolder match within two weeks
        if f_prev and f_prev == f_cand and gap <= 24 * 14:
            if not (pl_prev and pl_cand and pl_prev != pl_cand):
                return True

        # Conflicting known places split the moment unless practically simultaneous (<1 hour)
        if pl_prev and pl_cand and pl_prev != pl_cand:
            return gap <= 1.0

        # Same time window (default 8 hours)
        if gap <= gap_hours:
            return True

        # Same known place on the same day or next day (weekend/short trip, up to 36 hours from previous photo and 72 hours total)
        first_dt = _dt(cluster[0]["taken"]) or t_prev
        total_hours = (t_cand - first_dt).total_seconds() / 3600.0
        if pl_prev and pl_prev == pl_cand and gap <= 36.0 and total_hours <= 96.0:
            return True

        return False

    def maintain(self) -> int:
        """Group photos into moments by time and place, keeping every rename, merge and split the person made.
        Returns the number of moments in the library."""
        with self.lib._lock:
            records = self._photo_records()
            if not records:
                with self.db.tx() as c:
                    c.execute("DELETE FROM moment_photos WHERE owner=? AND library=?", (self.owner, self.library_id))
                    c.execute("DELETE FROM moments WHERE owner=? AND library=?", (self.owner, self.library_id))
                return 0

            nots = self._nots()
            gap_hours = float(self.lib.settings.get("moments", {}).get("gap_hours", 8.0))
            min_photos = int(self.lib.settings.get("moments", {}).get("min_photos", 2))

            # 1. Keep moments and photo assignments that the person explicitly edited (`how='you'`)
            locked_moments: dict[int, dict] = {}
            for mr in self._all("SELECT * FROM moments WHERE owner=? AND library=? AND how='you'", (self.owner, self.library_id)):
                locked_moments[int(mr["id"])] = dict(mr)

            locked_photos: dict[int, list[int]] = {}
            assigned_pids: set[int] = set()
            for mp in self._all(
                "SELECT moment_id, photo_id FROM moment_photos WHERE owner=? AND library=? AND how='you' ORDER BY rank",
                (self.owner, self.library_id),
            ):
                mid, pid = int(mp["moment_id"]), int(mp["photo_id"])
                if pid in records and mid in locked_moments:
                    locked_photos.setdefault(mid, []).append(pid)
                    assigned_pids.add(pid)

            # Remove locked moments that no longer have any existing photos
            for mid in list(locked_moments):
                if not locked_photos.get(mid):
                    with self.db.tx() as c:
                        c.execute("DELETE FROM moment_photos WHERE moment_id=?", (mid,))
                        c.execute("DELETE FROM moments WHERE id=?", (mid,))
                    locked_moments.pop(mid, None)

            # 2. Cluster all remaining (`auto`) photos
            auto_photos = [p for pid, p in records.items() if pid not in assigned_pids]
            fine: list[dict] = []
            coarse: list[dict] = []
            for p in auto_photos:
                if p.get("taken") and p.get("taken_precision") in ("second", "day") and not p.get("date_flag"):
                    fine.append(p)
                elif p.get("taken"):
                    coarse.append(p)

            fine.sort(key=lambda p: (p["taken"], p["id"]))
            clusters: list[list[dict]] = []
            for p in fine:
                if clusters and self._can_join_fine(clusters[-1], p, gap_hours, nots):
                    clusters[-1].append(p)
                else:
                    clusters.append([p])

            # Group coarse-dated photos by (taken, precision, place or event folder), respecting `moment_nots`
            coarse.sort(key=lambda p: (p["taken"], p["id"]))
            coarse_buckets: dict[tuple, list[list[dict]]] = {}
            for p in coarse:
                key = (
                    p.get("taken") or "",
                    p.get("taken_precision") or "",
                    (p.get("place") or "").strip().lower(),
                    self._event_folder(p),
                )
                placed = False
                for group in coarse_buckets.setdefault(key, []):
                    if not self._blocked(group, p["id"], nots):
                        group.append(p)
                        placed = True
                        break
                if not placed:
                    coarse_buckets[key].append([p])
            for groups in coarse_buckets.values():
                clusters.extend(groups)

            # Keep clusters that have at least `min_photos` photos
            valid_clusters = [cl for cl in clusters if len(cl) >= min_photos]

            # 3. Match auto clusters against existing `auto` moments so IDs stay stable
            existing_auto = {
                int(r["id"]): dict(r)
                for r in self._all("SELECT * FROM moments WHERE owner=? AND library=? AND how='auto'", (self.owner, self.library_id))
            }
            existing_members: dict[int, set[int]] = {}
            for r in self._all(
                "SELECT moment_id, photo_id FROM moment_photos WHERE owner=? AND library=? AND how='auto'",
                (self.owner, self.library_id),
            ):
                mid = int(r["moment_id"])
                if mid in existing_auto:
                    existing_members.setdefault(mid, set()).add(int(r["photo_id"]))

            used_auto_ids: set[int] = set()
            now = _iso(clock.now())

            with self.db.tx() as c:
                # Refresh locked ('you') moments first
                for mid, mr in locked_moments.items():
                    pids = locked_photos.get(mid, [])
                    ranked = self._rank_photos([records[pid] for pid in pids if pid in records])
                    fields = self._compute_fields(ranked, title=mr["title"])
                    c.execute(
                        "UPDATE moments SET title=?, summary=?, theme=?, start=?, end=?, precision=?, guess=?, "
                        "place=?, country=?, cover_id=?, photos=?, updated=? WHERE id=?",
                        (fields["title"], fields["summary"], fields["theme"], fields["start"], fields["end"],
                         fields["precision"], fields["guess"], fields["place"], fields["country"],
                         fields["cover_id"], fields["photos"], now, mid),
                    )
                    c.execute("DELETE FROM moment_photos WHERE moment_id=?", (mid,))
                    c.executemany(
                        "INSERT INTO moment_photos(moment_id, photo_id, owner, library, rank, how) VALUES (?,?,?,?,?,'you')",
                        [(mid, p["id"], self.owner, self.library_id, rank) for rank, p in enumerate(ranked)],
                    )

                # Upsert auto moments
                for cl in valid_clusters:
                    ranked = self._rank_photos(cl)
                    cl_ids = {p["id"] for p in ranked}
                    best_mid = None
                    best_overlap = 0
                    for mid, prev_ids in existing_members.items():
                        if mid in used_auto_ids:
                            continue
                        ov = len(cl_ids & prev_ids)
                        if ov > best_overlap:
                            best_overlap = ov
                            best_mid = mid
                    fields = self._compute_fields(ranked, title="")
                    if best_mid is not None:
                        used_auto_ids.add(best_mid)
                        c.execute(
                            "UPDATE moments SET title='', summary=?, theme=?, start=?, end=?, precision=?, guess=?, "
                            "place=?, country=?, cover_id=?, photos=?, updated=? WHERE id=?",
                            (fields["summary"], fields["theme"], fields["start"], fields["end"], fields["precision"],
                             fields["guess"], fields["place"], fields["country"], fields["cover_id"],
                             fields["photos"], now, best_mid),
                        )
                        c.execute("DELETE FROM moment_photos WHERE moment_id=?", (best_mid,))
                        mid = best_mid
                    else:
                        mid = c.execute(
                            "INSERT INTO moments(owner, library, title, summary, theme, start, end, precision, guess, "
                            "place, country, cover_id, photos, how, created, updated) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'auto',?,?)",
                            (self.owner, self.library_id, "", fields["summary"], fields["theme"], fields["start"],
                             fields["end"], fields["precision"], fields["guess"], fields["place"], fields["country"],
                             fields["cover_id"], fields["photos"], now, now),
                        ).lastrowid
                    c.executemany(
                        "INSERT OR REPLACE INTO moment_photos(moment_id, photo_id, owner, library, rank, how) "
                        "VALUES (?,?,?,?,?,'auto')",
                        [(mid, p["id"], self.owner, self.library_id, rank) for rank, p in enumerate(ranked)],
                    )

                # Remove stale auto moments
                for mid in set(existing_auto) - used_auto_ids:
                    c.execute("DELETE FROM moment_photos WHERE moment_id=?", (mid,))
                    c.execute("DELETE FROM moments WHERE id=?", (mid,))

            return len(locked_moments) + len(valid_clusters)

    # ---------------------------------------------------------------- reading moments

    def _moment_row(self, mid: int):
        return self._one("SELECT * FROM moments WHERE id=? AND owner=? AND library=?", (mid, self.owner, self.library_id))

    def _people_in_moment(self, mid: int) -> list[dict]:
        rows = self._all(
            "SELECT p.id, p.name, count(DISTINCT mp.photo_id) AS n "
            "FROM moment_photos mp JOIN photo_faces f ON f.photo_id=mp.photo_id "
            "JOIN people p ON p.id=f.person_id "
            "WHERE mp.moment_id=? AND mp.owner=? AND mp.library=? AND p.state='named' AND p.name<>'' "
            "GROUP BY p.id, p.name ORDER BY n DESC, p.name",
            (mid, self.owner, self.library_id),
        )
        return [{"id": r["id"], "name": r["name"], "photos": r["n"]} for r in rows]

    def _moment_dict(self, r, include_photos: bool = False) -> dict:
        start_dt, end_dt = _dt(r["start"]), _dt(r["end"])
        when = span_label(start_dt, end_dt, r["precision"])
        people = self._people_in_moment(r["id"])
        keys = r.keys()
        out = {
            "id": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "theme": r["theme"],
            "when": when,
            "start": r["start"],
            "end": r["end"],
            "precision": r["precision"],
            "guess": bool(r["guess"]),
            "place": r["place"],
            "country": r["country"],
            "cover_id": r["cover_id"],
            "photos": r["photos"],
            "how": r["how"],
            "people": people,
            "mentions": int(r["mentions"] or 0) if "mentions" in keys else 0,
            "last_mentioned": (r["last_mentioned"] or "") if "last_mentioned" in keys else "",
            "updated": r["updated"],
        }
        if include_photos:
            pids = [
                mp["photo_id"]
                for mp in self._all(
                    "SELECT photo_id FROM moment_photos WHERE moment_id=? AND owner=? AND library=? ORDER BY rank",
                    (r["id"], self.owner, self.library_id),
                )
            ]
            items = []
            for pid in pids:
                pr = self.lib._photo_row(pid)
                if pr is not None:
                    items.append(self.lib._photo_dict(pr))
            out["items"] = items
        return out

    def get(self, mid: int) -> dict | None:
        r = self._moment_row(mid)
        return self._moment_dict(r, include_photos=True) if r else None

    def for_photo(self, pid: int) -> dict | None:
        mp = self._one(
            "SELECT moment_id FROM moment_photos WHERE photo_id=? AND owner=? AND library=?",
            (pid, self.owner, self.library_id),
        )
        if not mp:
            return None
        r = self._moment_row(mp["moment_id"])
        return self._moment_dict(r, include_photos=False) if r else None

    def list(self, *, q: str = "", person: int | None = None, year: int | None = None, place: str = "",
             limit: int = 60, offset: int = 0) -> dict:
        where = ["m.owner=? AND m.library=?"]
        args: list = [self.owner, self.library_id]
        if year:
            y = int(year)
            lo = _iso(datetime(y, 1, 1))
            hi = _iso(datetime(y + 1, 1, 1))
            where.append("m.start < ? AND m.end > ?")
            args += [hi, lo]
        if place:
            where.append("(m.place = ? OR m.country = ?)")
            args += [place, place]
        if person:
            where.append(
                "EXISTS (SELECT 1 FROM moment_photos mp JOIN photo_faces f ON f.photo_id=mp.photo_id "
                "WHERE mp.moment_id=m.id AND f.person_id=?)"
            )
            args.append(int(person))
        if q:
            words = [w.lower() for w in re.findall(r"[^\W_]+", q) if w]
            for w in words:
                like = f"%{w}%"
                where.append(
                    "(lower(m.summary) LIKE ? OR lower(m.title) LIKE ? OR lower(m.theme) LIKE ? OR lower(m.place) LIKE ? "
                    "OR EXISTS (SELECT 1 FROM moment_photos mp JOIN photos p ON p.id=mp.photo_id "
                    "WHERE mp.moment_id=m.id AND (lower(p.caption) LIKE ? OR lower(p.seen) LIKE ? OR lower(p.tags) LIKE ?)))"
                )
                args.extend([like] * 7)
        cond = " AND ".join(where)
        rows = self._all(
            f"SELECT m.* FROM moments m WHERE {cond} ORDER BY m.start='' , m.start DESC, m.id DESC LIMIT ? OFFSET ?",
            [*args, limit + 1, max(0, offset)],
        )
        total = self._one(f"SELECT count(*) FROM moments m WHERE {cond}", args)[0]
        return {
            "items": [self._moment_dict(r, include_photos=False) for r in rows[:limit]],
            "more": len(rows) > limit,
            "total": total,
        }

    def count(self) -> int:
        return self._one("SELECT count(*) FROM moments WHERE owner=? AND library=?", (self.owner, self.library_id))[0]

    def refresh_photo(self, pid: int) -> None:
        """Re-rank and re-summarize the moment containing `pid` (if any) after its caption, tags, or people changed."""
        mp = self._one(
            "SELECT moment_id FROM moment_photos WHERE photo_id=? AND owner=? AND library=?",
            (pid, self.owner, self.library_id),
        )
        if not mp:
            return
        mid = int(mp["moment_id"])
        r = self._moment_row(mid)
        if r is None:
            return
        pids = [
            row["photo_id"]
            for row in self._all(
                "SELECT photo_id FROM moment_photos WHERE moment_id=? AND owner=? AND library=? ORDER BY rank",
                (mid, self.owner, self.library_id),
            )
        ]
        records = self._photo_records(pids)
        ranked = self._rank_photos([records[p] for p in pids if p in records])
        if not ranked:
            return
        fields = self._compute_fields(ranked, title=r["title"])
        now = _iso(clock.now())
        how = r["how"]
        with self.db.tx() as c:
            c.execute(
                "UPDATE moments SET title=?, summary=?, theme=?, start=?, end=?, precision=?, guess=?, "
                "place=?, country=?, cover_id=?, photos=?, updated=? WHERE id=?",
                (fields["title"], fields["summary"], fields["theme"], fields["start"], fields["end"],
                 fields["precision"], fields["guess"], fields["place"], fields["country"],
                 fields["cover_id"], fields["photos"], now, mid),
            )
            c.execute("DELETE FROM moment_photos WHERE moment_id=?", (mid,))
            c.executemany(
                "INSERT OR REPLACE INTO moment_photos(moment_id, photo_id, owner, library, rank, how) "
                "VALUES (?,?,?,?,?,?)",
                [(mid, p["id"], self.owner, self.library_id, rank, how) for rank, p in enumerate(ranked)],
            )

    # ---------------------------------------------------------------- what the person changes: rename, merge, split

    def _rebuild_locked(self, mid: int, pids: list[int], title: str) -> dict:
        records = self._photo_records(pids)
        ranked = self._rank_photos([records[pid] for pid in pids if pid in records])
        fields = self._compute_fields(ranked, title=title)
        now = _iso(clock.now())
        with self.db.tx() as c:
            c.execute(
                "UPDATE moments SET title=?, summary=?, theme=?, start=?, end=?, precision=?, guess=?, "
                "place=?, country=?, cover_id=?, photos=?, how='you', updated=? WHERE id=?",
                (fields["title"], fields["summary"], fields["theme"], fields["start"], fields["end"],
                 fields["precision"], fields["guess"], fields["place"], fields["country"],
                 fields["cover_id"], fields["photos"], now, mid),
            )
            c.execute("DELETE FROM moment_photos WHERE moment_id=?", (mid,))
            c.executemany(
                "INSERT OR REPLACE INTO moment_photos(moment_id, photo_id, owner, library, rank, how) "
                "VALUES (?,?,?,?,?,'you')",
                [(mid, p["id"], self.owner, self.library_id, rank) for rank, p in enumerate(ranked)],
            )
        return self.get(mid)

    def rename(self, mid: int, title: str) -> dict:
        """Give a moment your own title (or pass '' to go back to the automatic summary)."""
        from mary.photos.library import PhotoError

        r = self._moment_row(mid)
        if r is None:
            raise PhotoError("That moment isn't there any more.")
        clean = " ".join(str(title or "").split())[:120]
        pids = [
            mp["photo_id"]
            for mp in self._all(
                "SELECT photo_id FROM moment_photos WHERE moment_id=? AND owner=? AND library=? ORDER BY rank",
                (mid, self.owner, self.library_id),
            )
        ]
        return self._rebuild_locked(mid, pids, clean)

    def merge(self, keep_id: int, other_id: int) -> dict:
        """Combine two moments into one (`keep_id`). Their photos are locked together so rescans keep them as one."""
        from mary.photos.library import PhotoError

        if int(keep_id) == int(other_id):
            raise PhotoError("Pick two different moments.")
        keep = self._moment_row(keep_id)
        other = self._moment_row(other_id)
        if keep is None or other is None:
            raise PhotoError("One of those moments isn't there any more.")
        pids = [
            mp["photo_id"]
            for mp in self._all(
                "SELECT photo_id FROM moment_photos WHERE moment_id IN (?,?) AND owner=? AND library=? ORDER BY rank",
                (keep_id, other_id, self.owner, self.library_id),
            )
        ]
        pids = list(dict.fromkeys(pids))
        title = keep["title"] or other["title"]
        keep_mentions = int(keep["mentions"] or 0) if "mentions" in keep.keys() else 0
        other_mentions = int(other["mentions"] or 0) if "mentions" in other.keys() else 0
        with self.db.tx() as c:
            # Clear any previous split barriers between these photos
            for i, a in enumerate(pids):
                for b in pids[i + 1:]:
                    lo, hi = (a, b) if a < b else (b, a)
                    c.execute(
                        "DELETE FROM moment_nots WHERE owner=? AND library=? AND a=? AND b=?",
                        (self.owner, self.library_id, lo, hi),
                    )
            c.execute("UPDATE moments SET mentions=? WHERE id=?", (keep_mentions + other_mentions, keep_id))
            c.execute("DELETE FROM moment_photos WHERE moment_id=?", (other_id,))
            c.execute("DELETE FROM moments WHERE id=?", (other_id,))
        return self._rebuild_locked(keep_id, pids, title)

    def split(self, mid: int, photo_ids: list[int], title: str = "") -> dict:
        """Take `photo_ids` out of moment `mid` into a new moment, and remember not to group them back together."""
        from mary.photos.library import PhotoError

        r = self._moment_row(mid)
        if r is None:
            raise PhotoError("That moment isn't there any more.")
        if not photo_ids:
            raise PhotoError("Pick some photos first.")
        current = [
            int(mp["photo_id"])
            for mp in self._all(
                "SELECT photo_id FROM moment_photos WHERE moment_id=? AND owner=? AND library=? ORDER BY rank",
                (mid, self.owner, self.library_id),
            )
        ]
        chosen = [int(p) for p in dict.fromkeys(photo_ids)]
        if any(p not in current for p in chosen):
            raise PhotoError("Some of those photos aren't in this moment.")
        remaining = [p for p in current if p not in set(chosen)]
        if not remaining:
            raise PhotoError("That's all of them: leave at least one photo behind, or rename the moment instead.")

        now = _iso(clock.now())
        with self.db.tx() as c:
            for a in remaining:
                for b in chosen:
                    lo, hi = (a, b) if a < b else (b, a)
                    c.execute(
                        "INSERT OR IGNORE INTO moment_nots(owner, library, a, b, added) VALUES (?,?,?,?,?)",
                        (self.owner, self.library_id, lo, hi, now),
                    )
            new_id = c.execute(
                "INSERT INTO moments(owner, library, title, summary, theme, start, end, precision, guess, "
                "place, country, cover_id, photos, how, created, updated) "
                "VALUES (?,?, '', '', '', '', '', '', 0, '', '', NULL, 0, 'you', ?, ?)",
                (self.owner, self.library_id, now, now),
            ).lastrowid

        self._rebuild_locked(mid, remaining, r["title"])
        return self._rebuild_locked(new_id, chosen, " ".join(str(title or "").split())[:120])

    # ---------------------------------------------------------------- conversational recall (Phase 5)

    def touch(self, mid: int) -> None:
        """Record that moment `mid` was mentioned or shown in conversation."""
        now = _iso(clock.now())
        with self.db.tx() as c:
            c.execute(
                "UPDATE moments SET mentions = coalesce(mentions, 0) + 1, last_mentioned = ? "
                "WHERE id=? AND owner=? AND library=?",
                (now, int(mid), self.owner, self.library_id),
            )

    def describe_for_context(self, m: dict) -> str:
        """Format a moment for conversational recall in `recall.lines()` (~60–100 words)."""
        mid = m["id"]
        if "items" not in m:
            full = self.get(mid)
            if full:
                m = full
        me = context.self_of(self.lib)
        when_str = context.when_note(m.get("when") or "", context.moment_source(m))
        place_parts = [x for x in (m.get("place"), m.get("country")) if x]
        place_str = ", ".join(place_parts) if place_parts else "place not recorded"
        theme = m.get("title") or m.get("theme") or ""
        people_str = context.who_note(m.get("people") or [], me)
        count = int(m.get("photos") or len(m.get("items") or []))

        header = f"photo moment #{mid} (when: {when_str}; place: {place_str}"
        if theme:
            header += f"; event: {theme}"
        header += f"; people in it: {people_str}; {count} photo{'s' if count != 1 else ''}"
        if m.get("cover_id"):
            header += f", best photo #{m['cover_id']}"
        header += "): "

        items = m.get("items") or []
        photo_bits: list[str] = []
        for p in items[:4]:
            pid = p["id"]
            in_p = self.lib.people.in_photo(pid) or {}
            named_list = in_p.get("named", []) if isinstance(in_p, dict) else in_p
            p_people = [pe for pe in named_list if isinstance(pe, dict) and pe.get("name")]
            what = p.get("caption") or p.get("seen") or ""
            tags = p.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            text_ocr = (p.get("text") or "").strip()
            detail_parts = []
            if what:
                detail_parts.append(what)
            elif p.get("album"):
                detail_parts.append(f"album '{p['album']}'")
            if p_people and not any(pe["name"].lower() in what.lower() for pe in p_people):
                detail_parts.append("with " + context.who_note(p_people, me))
            if tags:
                extra_tags = [t for t in tags[:4] if t.lower() not in what.lower()]
                if extra_tags:
                    detail_parts.append("tags: " + ", ".join(extra_tags))
            if text_ocr and text_ocr.lower() not in what.lower():
                detail_parts.append(f"text in photo: '{text_ocr[:40]}'")
            if p.get("guess") and not m.get("guess"):
                detail_parts.append("its own date: " + context.when_note(p.get("when") or "", p.get("source") or ""))
            desc = "; ".join(detail_parts) if detail_parts else "photo in this moment"
            photo_bits.append(f"photo #{pid} ({desc})")

        body = "; ".join(photo_bits) if photo_bits else m.get("summary") or ""
        text = header + body
        words = text.split()
        if len(words) > 105:
            text = " ".join(words[:100]) + "…"
        return text

    def recall_for_request(self, request: str, now: datetime | None = None, limit: int = 3) -> list[dict]:
        """Return the top 2–3 moments matching `request` when talking about a past moment, or `[]` when nothing
        matches well."""
        res = self.lib.search_photos(request, limit=12, now=now, for_recall=True)
        moments = res.get("moments") or []
        return moments[:limit]

