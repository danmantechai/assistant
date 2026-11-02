"""How a photo's people and date are worded for the model, the same way everywhere (recalled moments, find_photos,
show_photo, about_photo).

Two mistakes this prevents, whatever the persona or photo:
  who   "you" in the model's context means the persona, but in a spoken reply "you" is the listener: so the persona's
        own person is named and marked as her ("Mary = you, the assistant: say 'me'"), never just "you are in this".
  when  a date is worded by where it came from. The file's own date is often only when it was copied to the PC, so it
        is given as "date unknown", never as when the photo was taken; an album-name date is an estimate.
"""
from __future__ import annotations

def when_note(when: str, source: str, flag: str = "") -> str:
    """The date as the model should read it, with how sure it may be."""
    when = (when or "").strip()
    if not when:
        out = "date unknown"
    elif source == "file":
        out = (f"date unknown (the file is dated {when}, but that may only be when it was copied to this PC: "
               "never say it as when the photo was taken)")
    elif source == "folder":
        out = f"{when}, estimated from the album's name: say 'I think'"
    else:
        out = when
    if flag == "disagree":
        out += "; the sources of this date disagree: say you're not sure when"
    return out


def moment_source(m: dict) -> str:
    """Where a moment's date comes from: a fact source when any dated photo has one, else 'folder' or 'file'."""
    if not m.get("when"):
        return "none"
    if not m.get("guess"):
        return "exif"
    sources = {p.get("source") for p in (m.get("items") or []) if p.get("taken") or p.get("when")}
    return "folder" if "folder" in sources else "file"


def self_of(lib) -> dict | None:
    """The person the active persona is modelled on, or None."""
    try:
        return lib.self_person() if hasattr(lib, "self_person") else None
    except Exception:
        return None


def is_self(person: dict, me: dict | None) -> bool:
    if not me or not isinstance(person, dict):
        return False
    if person.get("id") is not None and person.get("id") == me.get("person_id"):
        return True
    name = (me.get("name") or "").strip().lower()
    return bool(name) and (person.get("name") or "").strip().lower() == name


def who_note(people: list[dict], me: dict | None) -> str:
    """The named people, with the persona's own person marked as her."""
    names = []
    for pe in people:
        if not isinstance(pe, dict) or not pe.get("name"):
            continue
        names.append(f"{pe['name']} (= you, the assistant: say 'me')" if is_self(pe, me) else pe["name"])
    return ", ".join(names) if names else "no named people"
