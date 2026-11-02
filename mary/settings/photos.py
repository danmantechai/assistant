"""What the settings page's Photos page reads and changes: your photo folders, how far the reading has got, browsing, and
fixing what MARY read. It goes through the photo library's own calls (mary/photos/library.py) and the worker's progress,
never its files or tables. Nothing a page sends is ever used as a path to open: a photo is named by its number, and only the
files MARY already has on record for it can be shown.

Every change applies at once (like the Memory page). The originals are never touched."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from mary.photos import dates, library, read, sources
from mary.settings.store import SettingsError

MAX_SELECTION = 5000
VIEW_PX = 1600          # how big a photo is shown when it is opened


def need(lib):
    if lib is None:
        raise SettingsError("Photos are switched off (photos.enabled in config.yaml).")
    return lib


def _wrap(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except library.PhotoError as e:
        raise SettingsError(str(e)) from None


def state(lib, ing) -> dict:
    from mary.photos import clip as clip_mod

    if lib is None:
        return {"enabled": False}
    if ing is not None:
        progress = ing.progress()
    else:
        st = lib.status()
        progress = {**st, "state": "idle", "average_ms": {}, "text": True, "ocr": False,
                    "places": lib.gazetteer.available,
                    "clip": {**clip_mod.models_status(lib), "left": st.get("clip_left", 0)},
                    "vision": {"on": bool(lib.settings.get("vision", {}).get("enabled", False)),
                               "allow_cloud": bool(lib.settings.get("vision", {}).get("allow_cloud", False)),
                               "left": st.get("vision_left", 0), "seen": st.get("seen", 0)},
                    "moments": st.get("moments", 0)}
    folders = lib.folders()
    return {"enabled": True, "folders": folders, "progress": progress,
            "suggestions": sources.suggestions([f["path"] for f in folders]),
            "facets": lib.facets()}


def get_places(lib, fetch=None) -> dict:
    """Fetch the list of places (about 11 MB from download.geonames.org) and name the photos that have a GPS position. Only
    ever run because the person pressed the button that says so."""
    from mary.photos import places

    lib = need(lib)
    if lib.gazetteer_path is None:
        raise SettingsError("There's nowhere to keep the list of places.")
    try:
        count = places.download(lib.gazetteer_path, fetch)
    except Exception:
        raise SettingsError("Couldn't fetch the list of places. Check the internet connection and try again.") from None
    named = lib.use_gazetteer(places.Gazetteer.open(lib.gazetteer_path))
    return {"places": count, "photos_named": named}


def get_clip(lib, ing, fetch=None) -> dict:
    """Fetch the two CLIP picture-search models (about 590 MB from huggingface.co/Qdrant). Only ever run because the person
    pressed the button that says so; nothing of theirs is sent."""
    from mary.photos import clip as clip_mod

    lib = need(lib)
    try:
        clip_mod.fetch(clip_mod.models_folder(lib), fetch)
    except Exception:
        raise SettingsError("Couldn't fetch the picture search models. Check the internet connection and try again.") from None
    if ing is not None:
        ing._clip_at = 0.0
        ing.wake()
    return {"mb": clip_mod.models_status(lib)["mb"]}


def vision_switch(lib, ing, body: dict) -> dict:
    """Switch background photo descriptions on or off, or allow a cloud vision model (with an explicit opt-in)."""
    lib = need(lib)
    vcfg = dict(lib.settings.get("vision") or {})
    if "enabled" in body:
        vcfg["enabled"] = bool(body["enabled"])
    if "allow_cloud" in body:
        vcfg["allow_cloud"] = bool(body["allow_cloud"])
    if "model" in body and isinstance(body["model"], str):
        vcfg["model"] = body["model"].strip()
    lib.settings["vision"] = vcfg
    if ing is not None:
        ing.wake()
    return {"enabled": bool(vcfg.get("enabled", False)), "allow_cloud": bool(vcfg.get("allow_cloud", False)),
            "model": str(vcfg.get("model", ""))}


def pick_folder() -> str | None:
    """A small native folder dialog, in its own process so the page's server never blocks on a window."""
    try:
        done = subprocess.run([sys.executable, "-m", "mary.settings.pickfolder"], capture_output=True, text=True,
                              timeout=600, encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        raise SettingsError("Couldn't open the folder window. Paste the folder's address instead.") from None
    path = done.stdout.strip()
    return path or None


def add_folder(lib, ing, path: str, label: str | None = None, recursive: bool = True) -> dict:
    folder = _wrap(need(lib).add_folder, path, label or None, bool(recursive))
    if ing is not None:
        ing.request_scan()
    return folder


def folder_edit(lib, fid, label=None, recursive=None) -> dict:
    return _wrap(need(lib).set_folder, int(fid), label, recursive)


def folder_remove(lib, fid) -> dict:
    return _wrap(need(lib).remove_folder, int(fid))


def scan(lib, ing) -> dict:
    need(lib)
    if ing is not None:
        ing.request_scan()
    return {"ok": True}


def parse_date(body: dict) -> dates.Taken:
    """{year, month?, day?, decade?} -> a date as exact as it was given."""
    try:
        year = int(body.get("year"))
        month = int(body["month"]) if body.get("month") not in (None, "", 0) else None
        day = int(body["day"]) if body.get("day") not in (None, "", 0) else None
    except (TypeError, ValueError):
        raise SettingsError("Give the year as a number, like 1995.") from None
    if not dates.MIN_YEAR <= year <= date.today().year:
        raise SettingsError(f"The year should be between {dates.MIN_YEAR} and {date.today().year}.")
    if day is not None and month is None:
        raise SettingsError("A day needs its month.")
    try:
        return dates.person_date(year, month, day, decade=bool(body.get("decade")))
    except ValueError:
        raise SettingsError("That isn't a real date.") from None


def album(lib, fid, body: dict) -> dict:
    lib = need(lib)
    taken = parse_date(body["date"]) if body.get("date") else None
    if body.get("clear"):
        return _wrap(lib.clear_album, int(fid))
    place = str(body["place"]) if body.get("place") not in (None, "") else None
    if taken is None and place is None:
        raise SettingsError("Give a date or a place for the album.")
    return _wrap(lib.set_album, int(fid), taken, body.get("scope") or "unsure", place)


FILTERS = ("year", "month", "place", "folder", "q", "flag", "person", "moment")


def _filters(q: dict) -> dict:
    out = {}
    for name in FILTERS:
        value = (q.get(name) or [""])[0] if isinstance(q.get(name), list) else q.get(name, "")
        if value not in ("", None):
            out[name] = value
    if "flag" in out and out["flag"] not in library.FLAGS:
        raise SettingsError("That isn't something to filter by.")
    for name in ("year", "month", "folder", "person", "moment"):
        if name in out:
            try:
                out[name] = int(out[name])
            except ValueError:
                raise SettingsError("That isn't a number.") from None
    return out


def shaped(item: dict) -> dict:
    """A photo as the page gets it: with the address of its thumbnail (served at /thumb/<id>, not under /api, because an
    <img> can't send the header the API needs; the server checks it was this page that asked)."""
    return {**item, "thumb": f"/thumb/{item['id']}?v={item['version']}", "view": f"/view/{item['id']}?v={item['version']}"}


def shaped_moment(item: dict, lib=None) -> dict:
    """A moment as the page gets it: with its cover thumbnail and, when opened, its ranked photos."""
    cid = item.get("cover_id")
    ver = 1
    if cid and lib is not None:
        r = lib._photo_row(int(cid))
        if r is not None:
            ver = r["updated"]
    out = {**item, "thumb": f"/thumb/{cid}?v={ver}" if cid else ""}
    if "items" in out:
        out["items"] = [shaped(i) for i in out["items"]]
    return out


def listing(lib, q: dict) -> dict:
    lib = need(lib)
    one = lambda name, default="": (q.get(name) or [default])[0]  # noqa: E731
    try:
        offset = max(0, int(one("offset", "0")))
    except ValueError:
        offset = 0
    out = lib.list(order=one("order", "newest"), limit=60, offset=offset, **_filters(q))
    return {**out, "items": [shaped(i) for i in out["items"]]}


def moments(lib, q: dict) -> dict:
    lib = need(lib)
    one = lambda name, default="": (q.get(name) or [default])[0]  # noqa: E731
    try:
        offset = max(0, int(one("offset", "0")))
    except ValueError:
        offset = 0
    flt = _filters(q)
    out = lib.moments(q=flt.get("q", ""), person=flt.get("person"), year=flt.get("year"),
                      place=flt.get("place", ""), limit=60, offset=offset)
    return {**out, "items": [shaped_moment(m, lib) for m in out["items"]]}


def moment(lib, mid) -> dict:
    lib = need(lib)
    got = lib.moment(int(mid))
    if got is None:
        raise SettingsError("That moment isn't there any more.")
    return shaped_moment(got, lib)


def _mid(body: dict, key: str = "id") -> int:
    v = body.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        raise SettingsError("That moment isn't there any more.")
    return v


def moment_rename(lib, body: dict) -> dict:
    lib = need(lib)
    return shaped_moment(_wrap(lib.rename_moment, _mid(body), str(body.get("title") or "")), lib)


def moment_merge(lib, body: dict) -> dict:
    lib = need(lib)
    return shaped_moment(_wrap(lib.merge_moments, _mid(body, "keep"), _mid(body, "other")), lib)


def moment_split(lib, body: dict) -> dict:
    lib = need(lib)
    return shaped_moment(_wrap(lib.split_moment, _mid(body), _ids(lib, body), str(body.get("title") or "")), lib)


def facets(lib, q: dict) -> dict:
    year = (q.get("year") or [""])[0]
    return need(lib).facets(int(year) if year.isdigit() else None)


def photo(lib, pid) -> dict:
    got = need(lib).get(int(pid))
    if got is None:
        raise SettingsError("That photo isn't there any more.")
    out = shaped(got)
    if out.get("people"):
        out["people"] = {**out["people"], "faces": [{**f, "crop": f"/face/{f['id']}?v={f['v']}"} for f in out["people"]["faces"]]}
    return out


def similar(lib, pid) -> dict:
    return {"items": [shaped(i) for i in need(lib).similar(int(pid))]}


def thumb(lib, pid) -> tuple[bytes, str, None]:
    lib = need(lib)
    path = lib.thumb_path(int(pid))
    if lib.get(int(pid)) is None or not path.exists():
        raise SettingsError("That photo isn't there any more.")
    return path.read_bytes(), "image/jpeg", None


def view(lib, pid) -> tuple[bytes, str, None]:
    """The photo big enough to look at, made from the first of its files MARY has on record that can still be read. A page never
    names a path: only a photo's number."""
    for path in need(lib).file_paths(int(pid)):
        p = Path(path)
        if not p.exists():
            continue
        try:
            return read.thumbnail(read.decode(p.read_bytes(), p.suffix, VIEW_PX).image, VIEW_PX), "image/jpeg", None
        except (read.Unreadable, OSError):
            continue
    raise SettingsError("The original isn't where it was. It may have been moved or deleted.")


def show_in_folder(path: str) -> None:
    """Explorer, with the file selected (a Windows convenience; never run on a path the page sent)."""
    subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])


def show(lib, pid) -> dict:
    paths = [p for p in need(lib).file_paths(int(pid)) if os.path.exists(p)]
    if not paths:
        raise SettingsError("The original isn't where it was. It may have been moved or deleted.")
    show_in_folder(paths[0])
    return {"ok": True}


def _ids(lib, body: dict) -> list[int]:
    if body.get("ids") is not None:
        ids = body["ids"]
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise SettingsError("Pick some photos first.")
    elif body.get("filter") is not None:
        ids = lib.ids(**_filters({k: [v] for k, v in (body["filter"] or {}).items()}))
    else:
        ids = []
    if not ids:
        raise SettingsError("Pick some photos first.")
    if len(ids) > MAX_SELECTION:
        raise SettingsError(f"That's more than {MAX_SELECTION} photos at once; narrow it down first.")
    return ids


def fix(lib, body: dict) -> dict:
    """Change the date, the place or the caption of the photos picked (or all that match a filter)."""
    lib = need(lib)
    ids = _ids(lib, body)
    done = {}
    if body.get("clear") == "date":
        done["date"] = lib.clear_taken(ids)
    elif body.get("date"):
        done["date"] = lib.set_taken(ids, parse_date(body["date"]))
    if body.get("clear") == "place":
        done["place"] = lib.clear_place(ids)
    elif body.get("place") not in (None, ""):
        done["place"] = _wrap(lib.set_place, ids, str(body["place"]))
    if "caption" in body:
        if len(ids) != 1:
            raise SettingsError("A caption is for one photo at a time.")
        lib.set_caption(ids[0], str(body["caption"] or ""))
        done["caption"] = 1
    if not done:
        raise SettingsError("Say what to change.")
    return {"changed": done}


def merge(lib, body: dict) -> dict:
    return {"photo": _wrap(need(lib).merge, _ids(lib, body))}


def remove(lib, body: dict) -> dict:
    return {"removed": need(lib).remove(_ids(lib, body))}

