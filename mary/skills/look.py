"""Brick: look at a window, or one area of it, with a model that can see (look), for what has to be seen rather than
read: how something looks, what a picture or photo shows, an app that exposes no text (mary/vision.py)."""
from __future__ import annotations

import io
import logging

from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("Questions about how something looks, what a picture or photo on screen shows, or describing the screen: "
         "look. For the text on screen use read_screen, which is exact.")
SLOW = {"look": ["Let me take a look.", "One moment, I'm looking."]}

log = logging.getLogger(__name__)


def window_png(win, part: str | None = None) -> bytes:
    """A picture of the window, or of one of its areas (mary/page.py), as PNG."""
    from PIL import Image

    from mary.ocr import capture_window

    pixels = capture_window(win.hwnd)
    image = Image.frombuffer("RGBA", (pixels.shape[1], pixels.shape[0]), pixels.tobytes(), "raw", "BGRA", 0, 1)
    if part:
        from mary.page import page_for

        region = page_for(win).region(part)
        if region is not None:
            image = image.crop(region.rect)
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def photo_png(lib, pid: int) -> tuple[bytes | None, list[int]]:
    """Load photo `pid` from `lib` as PNG bytes (with numbered face boxes if faces are known) so `look` can inspect it."""
    import os
    from pathlib import Path
    from PIL import Image, ImageOps
    from mary.photos import see

    paths = [p for p in lib.file_paths(pid) if os.path.exists(p)]
    thumb = lib.thumb_path(pid)
    src = Path(paths[0]) if paths else (thumb if thumb.exists() else None)
    if src is None:
        return None, []
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    faces = lib.people.faces_for_vision(pid)
    if faces:
        im = see.draw_face_boxes(im, faces)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue(), [int(f["id"]) for f in faces]


def tools(ctx: ToolContext) -> list[Tool]:
    def look(question: str, target: str | None = None, part: str | None = None,
             photo_id: int | None = None) -> ToolResult:
        from mary import vision
        from mary.skills.read_screen import _target_window

        model = vision.model_for(ctx.cfg)
        if model is None:
            return ToolResult(False, "No model that can see images is set up (vision.model in config.yaml); "
                                     "read_screen can still read the text.")
        if photo_id is not None and int(photo_id) > 0:
            lib = getattr(ctx, "photos", None) or getattr(ctx.memory, "photos", None)
            if lib is None or lib.get(int(photo_id)) is None:
                return ToolResult(False, f"Photo #{photo_id} isn't in the library.")
            pid = int(photo_id)
            png, face_ids = photo_png(lib, pid)
            if png is None:
                return ToolResult(False, f"Photo #{pid}'s file isn't on this PC right now.")
            prompt = question
            if face_ids:
                tags = ", ".join(f"[{i + 1}]" for i in range(len(face_ids)))
                prompt = (f"{question} (Refer to any numbered faces only by their box tag {tags}; "
                          "never guess a person's name.)")
            try:
                answer = vision.look(png, prompt, ctx.cfg, model)
            except Exception as e:
                log.warning("look on photo %d failed: %s", pid, e)
                return ToolResult(False, f"Couldn't look at photo #{pid}: {e}")
            from mary.photos import see

            known_names = [r["name"] for r in lib._all(
                "SELECT name FROM people WHERE owner=? AND library=? AND name<>''", (lib.owner, lib.lib))]
            clean = see.sanitize_description(answer, len(face_ids), forbidden_names=known_names)
            resolved = see.resolve_description(clean, face_ids, lib._face_names_for_photo(pid))
            m = lib.moments_store.for_photo(pid)
            if m:
                lib.touch_moment(m["id"])
            return ToolResult(True, f"Looking at photo #{pid}: {resolved}")

        win = _target_window(target)
        if win is None:
            return ToolResult(False, f"No window matches '{target}'." if target else "No window to look at.")
        try:
            answer = vision.look(window_png(win, part), question, ctx.cfg, model)
        except Exception as e:
            log.warning("look failed: %s", e)
            return ToolResult(False, f"Couldn't look at '{win.title}': {e}")
        return ToolResult(True, f"Looking at '{win.title}'{' (' + part + ')' if part else ''}: {answer}")

    return [Tool(
        "look",
        "Look at a window (or one area of it), or look at a photo from the library again for a detail its record "
        "doesn't hold, with a model that can see.",
        {"question": {"type": "string", "description": "what to find out, e.g. 'what colour is the cake?'"},
         "target": {"type": "string", "description": "app or window; omit for the one in front"},
         "part": {"type": "string", "description": "an area: 'main area', 'left panel', or words it shows"},
         "photo_id": {"type": "integer", "description": "photo #id from your photo library to look at again"}},
        ["question"], look, private=True, acts_on="target",
        watch=lambda a: not a.get("photo_id"),
        announce=lambda a: "Looking at the photo…" if a.get("photo_id") else "Looking…")]

