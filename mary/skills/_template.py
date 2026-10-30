"""Brick template: copy this file (say as mary/skills/greeter.py), fill it in, and switch it on (docs/skills.md:
the skills: list in your user/config.yaml, or ALL in __init__.py and config.yaml for a brick MARY ships with).
Restart MARY. Only tools() is required; leave out what you don't need.

Keep the tool's result factual (ToolResult.text): MARY's brain turns it into her own words. `say` is the
short line used when the tool runs as an instant reflex (and in the old mode).
"""
from __future__ import annotations

from mary.reflexes import Reflex
from mary.tools.registry import Tool, ToolContext, ToolResult

# When to use it: becomes a line under "Which tool for what" in her system prompt.
GUIDE = "Greeting someone by name: greet."

# Safety words: (tool, applies to these args, pattern the user's words must contain, why it was refused).
# Only for tools that change something; looking things up needs none.
GROUNDING = [
    ("greet", lambda args: True, r"\b(greet|say hello|say hi)\b", "the user didn't ask to greet anyone"),
]

# Instant commands, matched against the *whole* request. Use sparingly: anything ambiguous belongs to the brain.
REFLEXES = [
    Reflex(r"say hello to (?P<name>\w+)", "greet", lambda text: {"name": text.split()[-1]}),
]

# Tools that take a few seconds get a waiting line, said at once.
SLOW = {"greet": ["One moment."]}


def tools(ctx: ToolContext) -> list[Tool]:
    def greet(name: str) -> ToolResult:
        return ToolResult(True, f"Greeted {name}.", say=f"Hello, {name}!")

    return [Tool("greet", "Say hello to someone by name.", {"name": {"type": "string"}}, ["name"], greet)]
