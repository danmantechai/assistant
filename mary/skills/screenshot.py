"""Brick: a screenshot of all screens, saved to Pictures/MARY (take_screenshot)."""
from __future__ import annotations

from datetime import datetime

from mary.config import expand_path
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "A screenshot, or a picture of the screen: take_screenshot."
GROUNDING = [
    ("take_screenshot", lambda a: True,
     r"\b(screenshot|screen ?shot|capture|snap|picture of (the|my|this) screen|print screen)\b",
     "the user didn't ask for a screenshot"),
]


def tools(ctx: ToolContext) -> list[Tool]:
    def take_screenshot() -> ToolResult:
        from PIL import ImageGrab

        folder = expand_path(ctx.cfg.paths.screenshots)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Screenshot {datetime.now():%Y-%m-%d %H-%M-%S}.png"
        ctx.output.hide_overlay()  # keep the HUD out of the picture
        try:
            ImageGrab.grab(all_screens=True).save(path)
        finally:
            ctx.output.show_overlay()
        ctx.last_files = [str(path)]
        return ToolResult(True, f"Saved screenshot to {path}.", say="Got it. It's in Pictures, MARY folder.")

    return [
        Tool("take_screenshot", "Capture all screens to Pictures/MARY.", {}, [], take_screenshot,
             speaks_for_itself=True, announce=lambda a: "Taking a screenshot…"),
    ]
