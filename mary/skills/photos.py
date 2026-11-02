"""photos: find photos and moments by person, place, date, moment or what's shown; show a photo or moment on screen;
and read the full who/where/when/what record of a photo or moment.

Context lines marked "photo moment #n" are recalled automatically by `mary/memory/recall.py` when the user talks about a
past moment. The tools here let the model search deeper, open a photo on screen, or inspect its full record."""
from __future__ import annotations

import logging
import os

from mary.photos import context
from mary.tools.registry import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

GUIDE = (
    'Questions about the user\'s photos, pictures, albums, or past moments ("Do you remember the wedding in June 1998?", '
    '"Show me a photo from last Christmas", "Find pictures of Grandma at the beach", "Do we have photos from 1985?", '
    '"Remember your 60th birthday?", "When you were young"): '
    'use any "photo moment #n" lines in <context> first, or call find_photos, about_photo, or show_photo. '
    'In photo records, a name marked "(= you, the assistant)" is you: speak of that person as "me" and of the photo as '
    'your own memory. The user is in a photo only when the record names them; never say "you" are in it otherwise. '
    'When the user asks to see, show, open, or pull up a photo or moment, call show_photo (after find_photos if you '
    'don\'t have its #id yet) rather than only describing it. '
    'For a visual detail a photo\'s record doesn\'t hold, call look with photo_id. '
    'Honesty: answer strictly from the records, never say #id numbers out loud, say a date only as the record words it '
    '("I think" for an estimate; nothing for "date unknown"), and say "I don\'t have photos from then" rather than '
    'inventing a memory when no photos match.'
)

GROUNDING = [
    (
        "show_photo",
        lambda a: True,
        r"\b(show|see|open|pull up|bring up|display|look at|view|put up|let'?s see)\b",
        "the user didn't ask to show or open a photo",
    ),
]


def _open_photo_file(path: str) -> None:
    """Open `path` in the default photo viewer on Windows."""
    if hasattr(os, "startfile"):
        os.startfile(path)


def _named_in(photo: dict) -> list[dict]:
    raw = photo.get("people")
    if isinstance(raw, dict):
        items = raw.get("named") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    return [pe for pe in items if isinstance(pe, dict) and pe.get("name")]


def _format_photo_brief(p: dict, me: dict | None = None) -> str:
    pid = p["id"]
    when_str = context.when_note(p.get("when") or "", p.get("source") or "", p.get("flag") or "")
    place_str = p.get("place_label") or p.get("place") or "place not recorded"
    who_str = context.who_note(_named_in(p), me)
    what_parts = []
    if p.get("caption"):
        what_parts.append(f"caption: \"{p['caption']}\"")
    if p.get("album"):
        what_parts.append(f"album: \"{p['album']}\"")
    if p.get("seen") and p.get("seen") != p.get("caption"):
        what_parts.append(f"seen: {p['seen']}")
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if tags:
        what_parts.append("tags: " + ", ".join(tags[:6]))
    if p.get("text"):
        what_parts.append(f"text in photo: \"{p['text'][:60]}\"")
    what_str = "; ".join(what_parts) if what_parts else "no description yet"
    moment_info = ""
    if p.get("moment") and p["moment"].get("id"):
        moment_info = f" (in moment #{p['moment']['id']}: {p['moment'].get('summary') or ''})"
    return f"photo #{pid}{moment_info}: when={when_str}; where={place_str}; who={who_str}; {what_str}"


def tools(ctx: ToolContext) -> list[Tool]:
    def lib():
        return getattr(ctx, "photos", None) or getattr(ctx.memory, "photos", None)

    def find_photos(
        query: str = "",
        person: str = "",
        place: str = "",
        when: str = "",
        moment_id: int = 0,
    ) -> ToolResult:
        library = lib()
        if library is None:
            return ToolResult(False, "The photo library isn't switched on.")
        mid = int(moment_id) if moment_id and int(moment_id) > 0 else None
        res = library.search_photos(
            query=query,
            person=person or None,
            place=place,
            when=when,
            moment_id=mid,
            limit=8,
        )
        moments = res.get("moments") or []
        photos = res.get("photos") or []
        if not moments and not photos:
            when_lbl = res.get("when_label") or when
            if when_lbl:
                return ToolResult(
                    True,
                    f"No photos found from {when_lbl}. Tell the user honestly that you don't have photos from then; "
                    "never invent a memory.",
                    say=f"I don't have any photos from {when_lbl}.",
                )
            reason = res.get("empty_reason") or "nothing in the library matched"
            return ToolResult(
                True,
                f"No photos found ({reason}). Tell the user honestly that you don't have photos matching that; "
                "never invent a memory.",
                say="I don't have any photos matching that.",
            )

        if moments:
            library.touch_moment(moments[0]["id"])

        lines: list[str] = []
        if moments:
            lines.append("Matching moments:")
            for m in moments[:4]:
                lines.append("  - " + library.moments_store.describe_for_context(m))
        if photos:
            lines.append("Top matching photos:")
            for p in photos[:6]:
                lines.append("  - " + _format_photo_brief(p, context.self_of(library)))
        return ToolResult(True, "\n".join(lines))

    def show_photo(photo_id: int = 0, moment_id: int = 0) -> ToolResult:
        library = lib()
        if library is None:
            return ToolResult(False, "The photo library isn't switched on.")
        pid = int(photo_id or 0)
        mid = int(moment_id or 0)
        moment_obj = None
        if pid <= 0 and mid > 0:
            moment_obj = library.moment(mid)
            if moment_obj is None:
                return ToolResult(False, f"Moment #{mid} isn't in the library.")
            pid = int(moment_obj.get("cover_id") or (moment_obj["items"][0]["id"] if moment_obj.get("items") else 0))
        if pid <= 0:
            return ToolResult(False, "Pass photo_id or moment_id to show.")

        photo = library.get(pid)
        if photo is None:
            return ToolResult(False, f"Photo #{pid} isn't in the library.")
        if moment_obj is None and photo.get("moment"):
            moment_obj = photo["moment"]
        if moment_obj and moment_obj.get("id"):
            library.touch_moment(int(moment_obj["id"]))

        paths = [p for p in library.file_paths(pid) if os.path.exists(p)]
        thumb = library.thumb_path(pid)
        target_path = paths[0] if paths else (str(thumb) if thumb.exists() else "")
        if not target_path:
            return ToolResult(False, f"Photo #{pid}'s file isn't on this PC right now.")

        ctx.shown_photos.append(pid)
        if not getattr(ctx, "suppress_open", False):
            try:
                _open_photo_file(target_path)
            except Exception as e:
                log.debug("opening photo file skipped: %s", e)

        brief = _format_photo_brief(photo, context.self_of(library))
        return ToolResult(
            True,
            f"Opened on screen: {brief}",
            say="Here's that photo.",
        )

    def about_photo(photo_id: int = 0, moment_id: int = 0) -> ToolResult:
        library = lib()
        if library is None:
            return ToolResult(False, "The photo library isn't switched on.")
        pid = int(photo_id or 0)
        mid = int(moment_id or 0)
        if pid > 0:
            photo = library.get(pid)
            if photo is None:
                return ToolResult(False, f"Photo #{pid} isn't in the library.")
            if photo.get("moment") and photo["moment"].get("id"):
                library.touch_moment(int(photo["moment"]["id"]))
            src = photo.get("source") or "none"
            when_str = context.when_note(photo.get("when") or "", src, photo.get("flag") or "")
            if photo.get("when") and src not in ("file", "folder", "none"):
                when_str += f" (from {src})"
            place_str = photo.get("place_label") or photo.get("place") or "not recorded"
            if photo.get("place_source"):
                place_str += f" (from {photo['place_source']})"
            who_str = context.who_note(_named_in(photo), context.self_of(library))
            tags = photo.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            moment_str = photo["moment"]["summary"] if photo.get("moment") else "none"
            details = [
                f"Photo #{pid}:",
                f"  When: {when_str}",
                f"  Where: {place_str}",
                f"  Who: {who_str}",
                f"  Caption (user's words): {photo.get('caption') or 'none'}",
                f"  Album: {photo.get('album') or 'none'}",
                f"  Vision description: {photo.get('seen') or 'none'}",
                f"  Tags: {', '.join(tags) if tags else 'none'}",
                f"  Text in photo (OCR): {photo.get('text') or 'none'}",
                f"  Moment: {moment_str}",
            ]
            return ToolResult(True, "\n".join(details))

        if mid > 0:
            m = library.moment(mid)
            if m is None:
                return ToolResult(False, f"Moment #{mid} isn't in the library.")
            library.touch_moment(mid)
            return ToolResult(True, library.moments_store.describe_for_context(m))

        return ToolResult(False, "Pass photo_id or moment_id.")

    return [
        Tool(
            "find_photos",
            "Find photos and moments in the user's photo collection by person, place, date/time ('June 1998', "
            "'last Christmas', '1985'), moment, or what the photo shows ('pink car', 'wedding cake', 'beach at sunset').",
            {
                "query": {"type": "string", "description": "what to look for: scene, event, words, person, place, or date"},
                "person": {"type": "string", "description": "name of a person in the photo, e.g. 'Grandma'"},
                "place": {"type": "string", "description": "place or country name, e.g. 'Brighton' or 'France'"},
                "when": {"type": "string", "description": "date or time span, e.g. 'June 1998', 'last Christmas', '1985'"},
                "moment_id": {"type": "integer", "description": "specific moment #id to list photos from"},
            },
            [],
            find_photos,
            private=True,
            watch=False,
            announce=lambda a: "Looking through your photos…",
        ),
        Tool(
            "show_photo",
            "Open a photo or a moment's best photo on screen for the user to see. Call only when they ask to see, "
            "show, open, or pull up a photo or moment.",
            {
                "photo_id": {"type": "integer", "description": "the #id of the photo to open on screen"},
                "moment_id": {"type": "integer", "description": "the #id of a moment to open its cover photo on screen"},
            },
            [],
            show_photo,
            private=True,
            watch=False,
            announce=lambda a: "Opening the photo…",
        ),
        Tool(
            "about_photo",
            "Read the full record of a photo or moment: who is in it, where and when it was taken (and whether the "
            "date is an estimate), the user's caption, album name, vision description, tags, and text in the photo.",
            {
                "photo_id": {"type": "integer", "description": "the #id of the photo"},
                "moment_id": {"type": "integer", "description": "the #id of the moment"},
            },
            [],
            about_photo,
            private=True,
            watch=False,
            announce=lambda a: "Checking the photo details…",
        ),
    ]
