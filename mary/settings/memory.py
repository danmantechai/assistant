"""What the settings page's Memory page reads and changes: the diary, the facts MARY has learned, the day summaries.
It goes through the memory store's own calls (mary/memory/store.py), never its files or tables, and shows plain data.

Everything here changes the store at once (like Stories); the one switch, the background pass, is an ordinary setting.
Core facts (what you told her to remember: pinned in every request) are edited on the About you page, so they are
shown when searched but have no buttons here."""
from __future__ import annotations

import re
from datetime import date, datetime

from mary.settings.store import SettingsError

SHOWN = ("fact", "diary", "reflection")
PAGE = 50
FACT_LIMIT = 200
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def item(m) -> dict:
    return {"id": m.id, "kind": m.kind, "text": m.text, "when": f"{m.happened:%a %d %b %Y, %H:%M}",
            "day": f"{m.happened:%Y-%m-%d}", "ended": f"{m.ended:%d %b %Y}" if m.ended else None,
            "ended_why": m.ended_why, "source": m.source,
            "can_edit": m.kind in SHOWN, "importance": round(m.importance, 2)}


def need(mem):
    if mem is None:
        raise SettingsError("Memory is switched off (memory.enabled in config.yaml).")
    return mem


def brain_in_cloud(cfg) -> bool:
    """Does what MARY asks her model leave this PC? (It decides what the background pass has to warn about.)"""
    from mary.llm.server import is_cloud

    brain = cfg.get("brain") or {}
    if brain.get("provider") == "api":
        url = str((brain.get("api") or {}).get("url") or "")
        return not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:|/|$)", url, re.I)
    return is_cloud(str(cfg.llm.model))


def state(mem, cfg) -> dict:
    if mem is None:
        return {"enabled": False}
    stats = mem.stats()
    return {"enabled": True, "meaning": stats["meaning"], "bytes": stats["file_bytes"],
            "counts": {k: stats[k] for k in ("core", "fact", "diary", "reflection")},
            "learn": bool(mem.setting("consolidate.enabled")), "reflections": bool(mem.setting("consolidate.reflections")),
            "cloud": brain_in_cloud(cfg), "folder": "user/memory",
            "days": [{"day": d.isoformat(), "count": n} for d, n in mem.days("diary")[:400]]}


def listing(mem, kind: str, which: str = "current", day: str | None = None, offset: int = 0) -> dict:
    mem = need(mem)
    if kind not in SHOWN:
        raise SettingsError("That isn't a kind of memory.")
    mem.files and mem.files.sync()   # take in edits made by hand first
    current = {"current": True, "ended": False}.get(which)
    since = until = None
    if day:
        if not _DAY.match(day):
            raise SettingsError("That isn't a day.")
        since = datetime.fromisoformat(day)
        until = datetime.fromisoformat(day).replace(hour=23, minute=59, second=59)
    rows = mem.list(kinds=(kind,), current=current, since=since, until=until, limit=PAGE + 1, offset=max(0, offset),
                    newest_first=kind != "diary" or not day)
    return {"items": [item(m) for m in rows[:PAGE]], "more": len(rows) > PAGE,
            "total": mem.count((kind,), current)}


def search(mem, q: str) -> dict:
    mem = need(mem)
    q = (q or "").strip()
    if len(q) < 2:
        return {"items": []}
    from mary.memory import recall

    hits = mem.search(q, k=20, cue=True, min_relevance=0.2, current=None if recall.wants_history(q) else True)
    return {"items": [{**item(h.memory), "how": h.how, "relevance": round(h.relevance, 2)} for h in hits]}


def _one(mem, mid) -> object:
    m = need(mem).get(int(mid)) if str(mid).isdigit() else None
    if m is None:
        raise SettingsError("That memory isn't there any more (it may have been changed on another page; reload).")
    return m


def edit(mem, mid, text: str) -> dict:
    m = _one(mem, mid)
    text = " ".join(str(text or "").split())
    if m.kind not in SHOWN:
        raise SettingsError("Change that one on the About you page.")
    limit = FACT_LIMIT if m.kind == "fact" else 10000
    if not text or len(text) > limit:
        raise SettingsError("Write something short" if not text else f"Keep it under {limit} characters.")
    return item(need(mem).edit(m.id, text))


def end(mem, mid, profile=None) -> dict:
    m = _one(mem, mid)
    if m.kind not in SHOWN:
        raise SettingsError("Change that one on the About you page.")
    return item(mem.end(m.id, "forgotten"))


def remove(mem, mid, profile=None) -> dict:
    """Delete for good: gone from the store, its keywords and vector, and the markdown files."""
    m = _one(mem, mid)
    if m.kind == "core":
        raise SettingsError("Remove that one on the About you page.")
    mem.delete(m.id)
    return {"removed": m.id}


def add(mem, text: str) -> dict:
    text = " ".join(str(text or "").split())
    if not text or len(text) > FACT_LIMIT:
        raise SettingsError("Write something short" if not text else f"Keep it under {FACT_LIMIT} characters.")
    m, status = need(mem).add_fact(text, importance=0.8, source="settings page")
    return {"item": item(m), "status": status}
