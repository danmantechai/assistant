"""Skill bricks: one ability per module here, all the same shape, switched on by name in config.yaml (skills:).

A brick is a module with:
    tools(ctx)  its tools (mary.tools.registry.Tool): the only required part
    GUIDE       when to use it: its lines in the new mode's system prompt
    GROUNDING   its safety words: an action runs only when the user's own words ask for that kind of thing
    REFLEXES    instant commands (mary.reflexes.Reflex), only for the few plain ones (volume, pause, the time)
    SLOW        {tool: [waiting lines]} for tools that take a few seconds ("Let me have a look…")
Files starting with "_" are helpers, not bricks. To add an ability, copy _template.py and add its name to skills:.

The order here is the order the tools are listed to the model; it matches the old registry, so the old mode's
prompt (and its cache) didn't change when the tools moved into bricks.
"""
from __future__ import annotations

import importlib
from types import ModuleType

ALL = ["apps", "windows", "sound_screen", "media", "pc_status", "power", "web", "files", "keyboard", "screenshot",
       "notes", "timers", "powershell", "read_screen", "music", "messages", "weather", "screen_control", "memory",
       "clock", "calculate", "waiting", "look", "documents"]


def enabled(cfg) -> list[str]:
    """The bricks switched on in config.yaml (all of them if it doesn't say)."""
    names = cfg.get("skills") if isinstance(cfg, dict) else None
    return list(names or ALL)


def load(names: list[str] | None = None) -> list[ModuleType]:
    return [importlib.import_module(f"{__name__}.{name}") for name in names or ALL]


def guides(bricks: list[ModuleType]) -> list[str]:
    return [b.GUIDE for b in bricks if getattr(b, "GUIDE", "")]


def grounding(bricks: list[ModuleType]) -> list:
    return [rule for b in bricks for rule in getattr(b, "GROUNDING", [])]


def reflexes(bricks: list[ModuleType]) -> list:
    return [r for b in bricks for r in getattr(b, "REFLEXES", [])]


def slow(bricks: list[ModuleType]) -> dict[str, list[str]]:
    return {tool: lines for b in bricks for tool, lines in getattr(b, "SLOW", {}).items()}
