"""Tool registry: schemas for the LLM, safety tiers, confirmation, execution and logging. Every call runs inside
the feedback loop (mary/ready.py): what it works on is ready first, what it changed has settled after, a "not yet"
is tried again while that can help, and the result ends with what's really on screen."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field, replace
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
    # done | not_yet | failed (from ok when left out). not_yet = what the tool needs isn't there yet (a search box,
    # a result, any text): the feedback loop (mary/ready.py) waits and calls it again. Only for a call that did
    # nothing yet, or that is safe to make again.
    status: str = ""
    waits_for: str | None = None   # what this action started, for the loop to wait for: an app ("spotify.exe",
                                   # "Notepad") or "front" (whatever comes to the front: a web page, a file)
    check: Callable[[], bool] | None = None   # work that doesn't show on screen (a download): is it done yet?

    def __post_init__(self) -> None:
        if not self.status:
            self.status = "done" if self.ok else "failed"


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
    last_launch: tuple[int, float] | None = None  # (hwnd, when its window appeared) after an app was started
    loop: Any = None          # the feedback loop around every tool call (mary/ready.py); made by the registry
    profile: Any = None       # Profile: what the user asked MARY to remember
    memory: Any = None        # MemoryStore: the diary, the facts she has learned, and the photos
    photos: Any = None        # PhotoLibrary: the user's photo collection and moments
    shown_photos: list[int] = field(default_factory=list)  # photo IDs opened by show_photo in this session
    did: list = field(default_factory=list)   # this request's tool calls (name, ok, private), for the diary: never results
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
    # The feedback loop (mary/ready.py) watches the screen around every tool unless watch is off: for tools that
    # never touch a window (the time, volume, timers, memory), or per call (a command that only looks). acts_on: the
    # window an action works on, which has to be ready first: "front", or the name of the argument that names it
    # ("window", "target").
    watch: bool | Callable[[dict], bool] = True
    acts_on: str | None = None

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
        from mary import ready, skills
        from mary.config import expand_path

        self.ctx = ctx
        if ctx.loop is None:
            logs = (ctx.cfg.get("paths") or {}).get("logs")
            ctx.loop = ready.Loop(output=ctx.output, log_path=expand_path(logs) / "ready.jsonl" if logs else None)
        self._request: str | None = None
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
            ["reason"], fn=lambda reason="": ToolResult(True, "Escalated."), watch=False)

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
            result = self._act(tool, args)
            self.ctx.did.append((name, result.ok, tool.private))
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

    # ------------------------------------------------------------------ the feedback loop (mary/ready.py)

    _TRIES = 8

    def _act(self, tool: Tool, args: dict) -> ToolResult:
        """Run a tool inside the loop: its window ready first; then what it changed settled; "not yet" tried
        again while that can still help; and the result ending with what's really on screen."""
        loop = self.ctx.loop
        if loop is None or not (tool.watch(args) if callable(tool.watch) else tool.watch):
            return tool.fn(**args)
        if self.ctx.request != self._request:  # a new request: a new waiting budget
            self._request = self.ctx.request
            loop.new_request()
        loop.new_call()
        started = loop.desk.now()
        if tool.acts_on:
            loop.before(loop.window(None if tool.acts_on == "front" else args.get(tool.acts_on)))
        steady_since: float | None = None   # when the app a "not yet" waits for was first ready and steady
        shown = None                        # the most telling thing seen over all the tries
        for attempt in range(1, self._TRIES + 1):
            before = loop.snapshot()
            result = tool.fn(**args)
            seen = loop.after(before, result.waits_for)
            shown = seen if seen.state != "none" or shown is None else shown
            if seen.appeared and seen.window:
                self.ctx.last_launch = (seen.window.hwnd, time.monotonic())  # typing waits until it's really ready
            if result.check is not None and result.status == "done":
                finished, took = loop.until(result.check, loop.limit_for(""))
                if not finished:
                    result.text += f" It isn't finished yet after {took:.0f} s; it's still going."
            if seen.state == "ready" and not seen.waited and steady_since is None:
                steady_since = loop.desk.now()
            if result.status != "not_yet" or not self._worth_another_try(loop, result, seen, started, steady_since):
                break
        return self._report(result, shown, loop.desk.now() - started)

    def _worth_another_try(self, loop, result: ToolResult, seen, started: float, steady_since: float | None) -> bool:
        """Try a "not yet" again only while that can help: the app it started is ready now (within its usual time,
        and for a few seconds once it's steady, as an app may describe its page a moment after drawing it), or the
        screen was still changing. A steady screen without it, for longer: it isn't coming."""
        from mary.ready import MIN_LIMIT_S

        if not loop.patient or loop.left(1.0) <= 0:
            return False
        if result.waits_for:
            app = "" if result.waits_for == "front" else result.waits_for
            now = loop.desk.now()
            if steady_since is not None and now - steady_since >= MIN_LIMIT_S:
                return False
            return seen.state == "ready" and now - started < loop.limit_for(app) + MIN_LIMIT_S
        front = loop.window(None)
        if front is None:
            return False
        settling = loop.before(front, fresh=True)  # a look now; if it's still changing, wait until it settles
        return settling.state == "ready" and (settling.waited or seen.waited)

    @staticmethod
    def _report(result: ToolResult, seen, took: float) -> ToolResult:
        line = seen.line()
        if result.status == "not_yet":
            waited = f" Still not there after {took:.0f} s." if took >= 1 else ""
            return replace(result, ok=False, text=f"{result.text}{waited} {line} If it's still starting, call wait, "
                                                  f"then try the same step again.".replace("  ", " ").strip())
        return replace(result, text=f"{result.text} {line}".strip()) if line else result
