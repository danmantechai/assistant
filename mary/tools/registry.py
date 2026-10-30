"""Tool registry: schemas for the LLM, safety tiers, confirmation, execution and logging."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from mary.brain import mode as brain_mode
from mary.output import CONFIRMING, Output
from mary.tools.safety import ground_args, ungrounded_reason

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    ok: bool
    text: str                 # what the LLM reads back
    say: str | None = None    # short spoken reply when the router runs the tool directly


@dataclass
class ToolContext:
    """Shared state handed to every tool."""
    cfg: Any
    apps: Any                 # AppIndex
    output: Output
    last_files: list[str] = field(default_factory=list)   # results of the last find_files
    last_files_at: float = 0.0                              # when (monotonic), for "open the newest one"
    last_web_at: float = 0.0                                # when MARY last opened a web page or search
    screen_elements: list = field(default_factory=list)    # the last numbered list, for "click number 3"
    seen: deque = field(default_factory=lambda: deque(maxlen=12))  # recent tool output: a link must come from here
    timers: list = field(default_factory=list)
    last_launch: tuple[int, float] | None = None  # (hwnd, when its window appeared) from open_app
    profile: Any = None       # Profile: what the user asked MARY to remember
    request: str = ""         # the user's current words, for intent grounding
    prev_request: str = ""    # the turn before (answers like "Spotify" to "which app?")


@dataclass
class Tool:
    name: str
    description: str
    properties: dict          # JSON-schema properties
    required: list[str]
    fn: Callable[..., ToolResult]
    tier: int | Callable[[dict], int] = 1                   # 2 = ask "Shall I go ahead?" first
    announce: Callable[[dict], str] | None = None          # "Launching Spotify…"
    confirm_text: Callable[[dict], str] | None = None      # "close Notepad"
    precheck: Callable[[dict], str | None] | None = None   # refusal reason, checked before asking
    # Action tools whose `say` fully answers the user ("Opening Spotify."). The agent speaks it
    # right away instead of waiting for the model to phrase a confirmation (a few seconds on CPU).
    speaks_for_itself: bool = False
    private: bool = False     # keep results (e.g. message names) out of the log file
    # New mode: ask only with a real reason and say it ("Notepad may have unsaved work. Close it anyway?"),
    # and a tier of its own where a step can be undone instead (a moved file can be moved back).
    ask: Callable[[dict], str] | None = None
    new_tier: int | Callable[[dict], int] | None = None

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.properties, "required": self.required}}}

    def tier_for(self, args: dict, new_mode: bool = False) -> int:
        tier = self.new_tier if new_mode and self.new_tier is not None else self.tier
        return tier(args) if callable(tier) else tier


class ToolRegistry:
    """The brick slot: loads the skill bricks switched on in config.yaml and runs their tools through the
    safety gate (grounding in the user's words, then a confirmation for risky steps)."""

    def __init__(self, ctx: ToolContext):
        from mary import skills

        self.ctx = ctx
        self.new_mode = brain_mode(ctx.cfg) == "new"
        self.bricks = skills.load(skills.enabled(ctx.cfg))
        self.grounding = skills.grounding(self.bricks)
        self.tools: dict[str, Tool] = {}
        for brick in self.bricks:
            for tool in brick.tools(ctx) if hasattr(brick, "tools") else []:
                self.tools[tool.name] = tool
        # escalate is handled by the agent loop itself; the schema just lets the model ask for it.
        self.tools["escalate"] = Tool(
            "escalate",
            "Ask for deeper step-by-step reasoning before acting. Use for complex, multi-step, "
            "ambiguous or risky requests, troubleshooting, or anything needing careful planning.",
            {"reason": {"type": "string", "description": "why deeper thinking is needed"}},
            ["reason"], fn=lambda reason="": ToolResult(True, "Escalated."))

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def run(self, name: str, args: dict | None) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool '{name}'. Available: {', '.join(self.tools)}")
        args = {k: v for k, v in (args or {}).items() if k in tool.properties and v not in (None, "")}
        out = self.ctx.output
        try:
            refusal = tool.precheck(args) if tool.precheck else None
            if refusal:
                out.tool_log(f"✗ Refused: {refusal}")
                return ToolResult(False, f"Refused for safety: {refusal}. Do not retry it another way.",
                                  say="I'm not allowed to do that one, for safety.")
            words = f"{self.ctx.prev_request} {self.ctx.request}"
            args = ground_args(name, args, words)
            unasked = ungrounded_reason(name, args, words, self.grounding)
            if unasked:
                log.warning("blocked ungrounded %s %s for request %r", name, args, self.ctx.request)
                out.tool_log(f"✗ Skipped {name}: {unasked}")
                # New mode: steer her back to talking; the 2B otherwise tried one unasked tool after another.
                then = ("Don't try another tool for this; answer him in words." if self.new_mode
                        else "Only do what the user asked.")
                return ToolResult(False, f"Not done: {unasked}. {then}")
            if tool.tier_for(args, self.new_mode) >= 2:
                what = tool.confirm_text(args) if tool.confirm_text else f"run {name}"
                question = (tool.ask(args) if self.new_mode and tool.ask
                            else f"I'm about to {what}. {self.ctx.cfg.safety.confirm_phrase}")
                out.state(CONFIRMING)
                if not out.confirm(question):
                    out.tool_log("✗ Cancelled")
                    return ToolResult(False, "The user said no, so nothing was done. Acknowledge briefly.",
                                      say="Okay, I've left it alone.")
            if tool.announce:
                out.tool_log(f"▸ {tool.announce(args)}")
            start = time.perf_counter()
            result = tool.fn(**args)
            log.info("tool %s %s -> ok=%s %.0fms: %s", name, args, result.ok,
                     (time.perf_counter() - start) * 1000, "<private>" if tool.private else result.text[:200])
            self.ctx.seen.append(result.text)
        except TypeError as e:  # wrong/missing arguments from the model
            result = ToolResult(False, f"Bad arguments for {name}: {e}")
        except Exception as e:
            log.exception("tool %s failed", name)
            result = ToolResult(False, f"{name} failed: {e}")
        out.tool_log("✓ Done" if result.ok else f"✗ {result.text[:120]}")
        return result
