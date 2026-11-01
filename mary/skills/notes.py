"""Brick: notes saved to Documents/MARY Notes (create_note)."""
from __future__ import annotations

import re
from datetime import datetime

from mary.config import expand_path
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Writing something down for him: create_note."
GROUNDING = [
    ("create_note", lambda a: True, r"\b(note|notes|write (it |this |that )?down|jot|save)\b",
     "the user didn't ask for a note"),
]


def tools(ctx: ToolContext) -> list[Tool]:
    def create_note(title: str, content: str) -> ToolResult:
        folder = expand_path(ctx.cfg.paths.notes)
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(".")[:80] or "Note"
        path = folder / f"{safe}.md"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        if path.exists():
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n\n## {stamp}\n{content}\n")
            return ToolResult(True, f"Appended to existing note {path}.", say=f"Added to your {safe} note.")
        path.write_text(f"# {title}\n_{stamp}_\n\n{content}\n", encoding="utf-8")
        ctx.last_files = [str(path)]
        return ToolResult(True, f"Created note {path}.", say=f"Noted, in {safe}.")

    return [
        Tool("create_note", "Save a note to Documents/MARY Notes (appends if the title already exists).",
             {"title": {"type": "string"}, "content": {"type": "string"}},
             ["title", "content"], create_note, speaks_for_itself=True, watch=False,
             announce=lambda a: f"Saving note '{a.get('title', '')}'…"),
    ]
