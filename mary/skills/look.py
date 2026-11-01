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


def tools(ctx: ToolContext) -> list[Tool]:
    def look(question: str, target: str | None = None, part: str | None = None) -> ToolResult:
        from mary import vision
        from mary.skills.read_screen import _target_window

        model = vision.model_for(ctx.cfg)
        if model is None:
            return ToolResult(False, "No model that can see images is set up (vision.model in config.yaml); "
                                     "read_screen can still read the text.")
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
        "Look at a window (or one area of it) with a model that can see, and answer a question about how it looks "
        "or what a picture shows.",
        {"question": {"type": "string", "description": "what to find out, e.g. 'what is in the photo?'"},
         "target": {"type": "string", "description": "app or window; omit for the one in front"},
         "part": {"type": "string", "description": "an area: 'main area', 'left panel', or words it shows"}},
        ["question"], look, private=True, acts_on="target", announce=lambda a: "Looking…")]
