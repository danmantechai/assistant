"""What the settings page's People page reads and changes: whether faces are recognised at all, the groups of faces MARY has found,
the names you give them, the questions she asks ("Is this Ama?") and what you tell her (merge, split, set aside, delete). It goes
through the photo library's people calls (mary/photos/people.py), never its files or tables.

A face's picture is served at /face/<id> and only to this page (the server checks, like thumbnails), and never cached by the
browser: a deleted person must not linger in a browser's cache. Nothing a page sends is ever used as a path.
Every change applies at once. A name only ever comes from the box you typed it in."""
from __future__ import annotations

from mary.photos import faces
from mary.photos.library import PhotoError
from mary.settings.store import SettingsError

MAX_FACES = 500


def need(lib):
    if lib is None:
        raise SettingsError("Photos are switched off (photos.enabled in config.yaml).")
    return lib


def _wrap(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except PhotoError as e:
        raise SettingsError(str(e)) from None


def shape_face(f: dict) -> dict:
    """A face as the page gets it: with the address of its picture (not under /api, because an <img> can't send the header)."""
    return {**f, "crop": f"/face/{f['id']}?v={f['v']}"}


def shape_card(c: dict) -> dict:
    return {**c, "samples": [shape_face(f) for f in c.get("samples", [])]}


def models_state(lib) -> dict:
    folder = faces.models_folder(lib)
    return {"present": faces.present(folder),
            "files": [{"name": n, "mb": round(i["bytes"] / 1_000_000, 1)} for n, i in faces.MODELS.items()],
            "mb": round(sum(i["bytes"] for i in faces.MODELS.values()) / 1_000_000, 1)}


def state(lib, ing) -> dict:
    """The switch, the models, how far the reading has got, and what needs the person: questions, people, possible repeats."""
    if lib is None:
        return {"enabled": False}
    p = lib.people
    engine = getattr(ing, "_engine", None)
    st = p.status(engine.tag if engine is not None else faces.OnnxEngine.tag)
    return {"enabled": True, "allowed": p.allowed(), "on": p.enabled(), "models": models_state(lib), "status": st,
            "ready": engine is not None, "photos": lib.status()["photos"],
            "worker": getattr(ing, "state", "idle"),
            "people": [shape_card(c) for c in p.people()],
            "questions": [{**q, "faces": [shape_face(f) for f in q["faces"]]} for q in p.questions()],
            "same": [{"a": shape_card(x["a"]), "b": shape_card(x["b"])} for x in p.same_person()],
            "ignored": [shape_card(c) for c in p.ignored()]}


def groups(lib, q: dict) -> dict:
    one = lambda name, default="": (q.get(name) or [default])[0]  # noqa: E731
    try:
        offset, singles = max(0, int(one("offset", "0"))), one("singles") == "1"
    except ValueError:
        offset, singles = 0, False
    out = need(lib).people.groups(offset=offset, limit=12, min_faces=1 if singles else 2)
    return {**out, "items": [shape_card(c) for c in out["items"]]}


def faces_of(lib, pid, q: dict) -> dict:
    try:
        offset = max(0, int((q.get("offset") or ["0"])[0]))
    except ValueError:
        offset = 0
    out = _wrap(need(lib).people.faces_of, int(pid), offset, 60)
    return {**out, "items": [shape_face(f) for f in out["items"]]}


def crop(lib, fid) -> tuple[bytes, str, None]:
    lib = need(lib)
    row = lib.people._one("SELECT id FROM photo_faces WHERE id=? AND owner=? AND library=? AND usable=1",
                          (int(fid), lib.owner, lib.lib))
    path = lib.people.crop_path(int(fid))
    if row is None or not path.exists():
        raise SettingsError("That face isn't there any more.")
    return path.read_bytes(), "image/jpeg", None


def _ints(body: dict, key: str) -> list[int]:
    ids = body.get(key)
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise SettingsError("Pick some faces first.")
    if len(ids) > MAX_FACES:
        raise SettingsError(f"That's more than {MAX_FACES} faces at once; pick fewer.")
    return ids


def _id(body: dict, key: str = "id") -> int:
    v = body.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        raise SettingsError("That person isn't there any more.")
    return v


def switch(lib, ing, on) -> dict:
    out = _wrap(need(lib).people.set_enabled, bool(on))
    if ing is not None:
        ing.wake()
    return {"on": out}


def get_models(lib, ing, fetch=None) -> dict:
    """Fetch the two face models (about 39 MB from huggingface.co/opencv). Only ever run because the person pressed the button
    that says so; nothing of theirs is sent."""
    lib = need(lib)
    try:
        sizes = faces.fetch(faces.models_folder(lib), fetch)
    except Exception:
        raise SettingsError("Couldn't fetch the face models. Check the internet connection and try again.") from None
    if ing is not None:
        ing._engine_at = 0.0          # look for them now, not in a few seconds
        ing.wake()
    return {"mb": round(sum(sizes.values()) / 1_000_000, 1)}


def name(lib, ing, body: dict) -> dict:
    out = _wrap(need(lib).people.name, _id(body), body.get("name"), body.get("relation"))
    if ing is not None:
        ing.wake()
    return shape_card(out)


def relation(lib, body: dict) -> dict:
    return shape_card(_wrap(need(lib).people.set_relation, _id(body), str(body.get("relation") or "")))


def unname(lib, body: dict) -> dict:
    return shape_card(_wrap(need(lib).people.unname, _id(body)))


def ignore(lib, body: dict) -> dict:
    return shape_card(_wrap(need(lib).people.ignore, _id(body)))


def unignore(lib, ing, body: dict) -> dict:
    out = shape_card(_wrap(need(lib).people.unignore, _id(body)))
    if ing is not None:
        ing.wake()
    return out


def merge(lib, ing, body: dict) -> dict:
    out = shape_card(_wrap(need(lib).people.merge, _id(body, "keep"), _id(body, "other")))
    if ing is not None:
        ing.wake()
    return out


def split(lib, body: dict) -> dict:
    return shape_card(_wrap(need(lib).people.split, _id(body), _ints(body, "faces")))


def move(lib, ing, body: dict) -> dict:
    out = shape_card(_wrap(need(lib).people.move, _ints(body, "faces"), _id(body, "person")))
    if ing is not None:
        ing.wake()
    return out


def deny(lib, body: dict) -> dict:
    return {"denied": _wrap(need(lib).people.deny, _ints(body, "faces"), _id(body, "person"))}


def not_same(lib, body: dict) -> dict:
    _wrap(need(lib).people.not_same, _id(body, "a"), _id(body, "b"))
    return {"ok": True}


def remove(lib, body: dict) -> dict:
    return _wrap(need(lib).people.remove_person, _id(body))


def delete_all(lib) -> dict:
    return _wrap(need(lib).people.delete_all)


def look_again(lib, ing) -> dict:
    n = need(lib).people.look_again()
    if ing is not None:
        ing.wake()
    return {"photos": n}
