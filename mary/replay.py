"""Replay real conversations through MARY without touching the PC: python -m mary.replay [--mode new|today|both]
[--model NAME ...] [--think on|off] [files]

Each file in tests/conversations/ is a conversation: what the user said, what the tools should return, and what
to expect. A turn can say `day: 1` (a day passes before it: the memory's clock moves on), and a conversation can say
`real: [memory]` to leave those tools unfaked: they then work on a scratch profile and a scratch memory, never yours. MARY runs for real (router, model, safety gate, replies) except that no tool does anything: each one
returns the result scripted for that turn, or a plain default. The report, with both modes side by side, goes
to logs/replay/. It uses the real language model, so a run takes a while (~15 s per model turn on this laptop),
and MARY, if she's running, re-reads her conversation on her next request.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from mary.config import PROJECT_DIR, Section, load_config
from mary.llm.replies import CLARIFY
from mary.output import Output
from mary.tools.registry import ToolResult

CONVERSATIONS = PROJECT_DIR / "tests" / "conversations"
# A neutral front window and fixed volume, so a run doesn't depend on what's open (as in mary.llm.bench).
# A conversation can name its own front window with `active_window: {title, exe}`.
CONTEXT = "time: {now:%A %d %B %Y, %H:%M}\nactive window: {title} ({exe})\nvolume: 40%"
FRONT = {"title": "Documents - File Explorer", "exe": "explorer.exe"}
REFUSED = ("Not done:", "Refused for safety")


@dataclass
class Call:
    tool: str
    args: dict
    ok: bool
    refused: bool


@dataclass
class Turn:
    user: str
    expect: dict
    calls: list[Call] = field(default_factory=list)
    spoken: list[tuple[str, str]] = field(default_factory=list)  # (kind, text): model, fixed, waiting, question
    seconds: float = 0.0
    first_words: float | None = None
    used_model: bool = False
    quick: bool = False      # a quick reply ("thanks" -> "You're welcome."): no model by design
    markup: bool = False     # the model wrote tool-call markup as text (dropped, never spoken)
    misses: list[str] = field(default_factory=list)


class Capture(Output):
    """Collects what MARY says, telling her own streamed words apart from prepared sentences."""

    def __init__(self):
        self.spoken: list[tuple[str, str]] = []
        self._parts: list[str] | None = None

    def reply_start(self) -> None:
        self._parts = []

    def reply_delta(self, text: str) -> None:
        if self._parts is not None:
            self._parts.append(text)

    def reply_end(self) -> None:
        if self._parts and "".join(self._parts).strip():
            self.spoken.append(("model", "".join(self._parts).strip()))
        self._parts = None

    def say(self, text: str) -> None:
        self.spoken.append(("fixed", text))

    def aside(self, text: str) -> None:  # a waiting line ("Let me have a look…")
        self.spoken.append(("waiting", text))

    def confirm(self, question: str) -> bool:
        self.spoken.append(("question", question))
        return True  # like a user who meant it; the tool is fake anyway


def _default(name: str, args: dict) -> ToolResult:
    """A plausible result for a tool the conversation didn't script."""
    what = args.get("name") or args.get("value") or args.get("query") or args.get("action") or ""
    return {
        "open_app": ToolResult(True, f"Launched {what}.", say=f"Opening {what}."),
        "close_app": ToolResult(True, f"Closed {what}.", say=f"Closed {what}."),
        "web": ToolResult(True, f"Opened a web search for '{what}' in the browser. The results haven't been read; "
                                "read_screen can read them.", say="Here's what came up."),
        "find_files": ToolResult(True, "No matching files in Desktop, Documents, Downloads.",
                                 say="I couldn't find anything like that."),
        "get_system_status": ToolResult(True, "Battery 100% (plugged in). CPU 23%. RAM 9.8 of 15.3 GB used (64%). "
                                              "Disk C: 40 GB free of 231 GB. Up 14 hours.",
                                        say="Battery 100% (plugged in). CPU 23%."),
        "read_screen": ToolResult(True, "Window 'Documents - File Explorer' (UI Automation):\nQuick access  Desktop  "
                                        "Downloads  Documents  Pictures"),
    }.get(name, ToolResult(True, "Done.", say="Done."))


def _scripted(value) -> ToolResult:
    if isinstance(value, str):
        return ToolResult(True, value, say=value.splitlines()[0])
    return ToolResult(bool(value.get("ok", True)), value["text"], say=value.get("say"), status=value.get("status", ""))


class Replay:
    def __init__(self, mode: str, model: str | None = None, think: bool | None = None):
        from mary import core
        from mary.core import Mary
        from mary.llm import agent

        cfg = load_config()
        cfg["brain"] = Section.wrap({**(cfg.get("brain") or {}), "mode": mode})
        if model:
            cfg["llm"] = Section.wrap({**cfg["llm"], "model": model})
        if think is not None:
            cfg["llm"] = Section.wrap({**cfg["llm"], "think_by_default": think})
        self.mode = mode
        self.out = Capture()
        self.front = FRONT
        core.write_jsonl = lambda *a, **k: None  # keep replays out of the real routing/latency logs
        from mary import memory
        from mary.memory import clock

        agent.live_context = lambda hint=None, extra=(): (
            "<context>\n" + CONTEXT.format(now=clock.now(), **self.front) + (f"\nrouter guess: {hint}" if hint else "")
            + "".join(f"\n{line}" for line in extra) + "\n</context>\n")
        memory.FOLDER = Path(tempfile.mkdtemp(prefix="mary-replay-memory-"))   # her real memory is never touched
        self.cfg = cfg
        self.mary = Mary(cfg, self.out)
        self.real: set[str] = set()
        self.real_profile = self.mary.profile
        if self.mary.memory is not None:
            self.mary.memory.embedder.load()   # meaning search works as it does for her
        # No desktop to watch: a "not yet" goes straight back to the model, as after a wait that ran out.
        from mary.ready import Loop, StillDesk

        self.mary.ctx.loop = Loop(StillDesk(), self.out, patient=False)
        self.turn: Turn | None = None
        self.results: dict = {}
        self.used: dict[str, int] = {}
        self._real_fns = {t.name: t.fn for t in self.mary.tools.tools.values()}
        for tool in self.mary.tools.tools.values():  # no tool does anything; the safety gate still runs
            tool.fn = self._fake(tool.name)
        real_run = self.mary.tools.run

        def run(name, args):
            result = real_run(name, args)
            if self.turn is not None:
                self.turn.calls.append(Call(name, dict(args or {}), result.ok, result.text.startswith(REFUSED)))
            return result

        self.mary.tools.run = run

    def _fake(self, name: str):
        def fn(**args) -> ToolResult:
            if name in self.real:
                return self._real_fns[name](**args)
            if name == "escalate":
                return ToolResult(True, "Escalated.")
            missing = self._unmatched_window(name, args)
            if missing:
                return missing
            value = self.results.get(name)
            if isinstance(value, list):  # successive calls get successive results
                value = value[min(self.used.get(name, 0), len(value) - 1)] if value else None
                self.used[name] = self.used.get(name, 0) + 1
            return _scripted(value) if value is not None else _default(name, args)
        return fn

    def _unmatched_window(self, name: str, args: dict) -> ToolResult | None:
        """read_screen with a target that matches no open window (the front one is the only one) fails as it
        would for real, e.g. a misheard app name."""
        from types import SimpleNamespace

        from mary.skills.windows import best_match, means_current

        target = args.get("target")
        if name != "read_screen" or means_current(target):
            return None
        if best_match(target, [SimpleNamespace(**self.front)]):
            return None
        return ToolResult(False, f"No window matches '{target}'.")

    def warm_up(self) -> None:
        self.mary.warm_up()

    def begin(self, conv: dict) -> None:
        """A conversation starts: its front window, the tools it leaves real (with a scratch profile and an empty
        scratch memory for them to work on), and the day it starts."""
        from mary.memory import clock

        self.front = conv.get("active_window") or FRONT
        self.real = set(conv.get("real") or [])
        if "photos" in self.real:
            self.real.update({"find_photos", "show_photo", "about_photo"})
        clock.reset()
        self._new_memory(scratch_profile=bool(self.real or conv.get("photos")))
        if conv.get("photos") and self.mary.photos is not None:
            from mary.photos import synth

            root = Path(tempfile.mkdtemp(prefix="mary-replay-photos-"))
            synth.populate_moments_library(self.mary.photos, root)
            if conv.get("persona_link"):
                plink = conv["persona_link"]
                target_name = plink.get("person")
                born_year = int(plink.get("born") or 0)
                persona_key = getattr(self.mary.agent.persona, "key", "mary")
                for p in self.mary.photos.people.people():
                    if p["name"] == target_name:
                        self.mary.photos.link_persona(persona_key, p["id"], born=born_year)
                        self.mary.photos.set_self(persona_key)
                        break

    def end(self) -> None:
        from mary.memory import clock

        clock.reset()
        self.real = set()
        self._new_memory(scratch_profile=False)

    def _new_memory(self, scratch_profile: bool) -> None:
        """An empty memory (and with `scratch_profile` an empty profile) for the next conversation, so what one
        conversation told her never reaches another."""
        from mary import memory, photos
        from mary.memory.diary import Diary
        from mary.profile import Profile

        m = self.mary
        profile = (Profile(Path(tempfile.mkdtemp(prefix="mary-replay-profile-")) / "profile.yaml")
                   if scratch_profile else self.real_profile)
        if m.memory is not None:
            old = m.memory
            memory.FOLDER = Path(tempfile.mkdtemp(prefix="mary-replay-memory-"))
            m.memory = memory.open_store(self.cfg, embedder=old.embedder)
            m.diary = Diary(m.memory)
            m.memory.sync_core(profile.facts)
            profile.listeners.append(lambda: m.memory.sync_core(profile.facts))
            m.ctx.memory = m.agent.memory = m.memory
            m.photos = m.ctx.photos = photos.open_library(self.cfg, m.memory)
            m.ctx.suppress_open = True
            old.worker.stop()
        m.profile = m.ctx.profile = m.agent.profile = profile
        if scratch_profile:
            profile.listeners.append(m.agent._profile_changed)
        m.agent.pinned = m.agent._profile_messages()

    def reset(self) -> None:
        m = self.mary
        m.agent.reset()
        m.offer, m.asked, m.awaiting, m.end_conversation = None, False, None, False
        ctx = m.ctx
        ctx.last_files, ctx.last_files_at, ctx.last_web_at, ctx.screen_elements = [], 0.0, 0.0, []
        ctx.shown_photos = []
        ctx.seen.clear()
        ctx.request = ctx.prev_request = ""

    def run_turn(self, spec: dict) -> Turn:
        self.turn = turn = Turn(spec["user"], spec.get("expect") or {})
        self.results, self.used = spec.get("results") or {}, {}
        if spec.get("day"):   # a day passes: the memory's clock moves, and the conversation she was having is over
            from mary.memory import clock

            clock.advance(days=spec["day"])
            self.mary.agent.reset()
        self.out.spoken = []
        start = time.perf_counter()
        latency = self.mary.handle(spec["user"])
        turn.seconds = time.perf_counter() - start
        turn.first_words = next((ms / 1000 for name, ms in latency.marks if name == "reply"), None)
        turn.used_model = any(n.startswith("llm#") for n in latency.notes)
        turn.quick = any(n.startswith("conversation:") for n in latency.notes)
        turn.markup = any("tool markup" in n for n in latency.notes)
        turn.spoken = list(self.out.spoken)
        prefill = self.mary.agent._prefill_thread
        if prefill:  # the pause before the next thing you'd say, which MARY uses to pre-read the conversation
            prefill.join(timeout=180)
        self.turn = None
        turn.misses = judge(turn, self.mary.agent.persona)
        return turn


_LEAK = re.compile(r"\b(?:the name in (?:your|his|her|my|their) note|my (?:system )?(?:instructions|prompt)|"
                   r"<context>|from your memories|how you write)\b", re.I)


def judge(t: Turn, persona=None) -> list[str]:
    """What went against the conversation's expectations (and the plain rules every turn keeps)."""
    e, misses = t.expect, []
    said = [s for k, s in t.spoken if k in ("model", "fixed")]
    if any(_LEAK.search(s) for s in said):
        misses.append("repeated her instructions")
    if persona is not None and persona.nicknames and any(persona.fix_address(s) != s for s in said):
        misses.append("called the user by her own name")
    tried = {c.tool for c in t.calls}
    done = {c.tool for c in t.calls if c.ok}
    for tool in e.get("tools", []):
        if tool not in done:
            misses.append(f"didn't use {tool}")
    for tool in e.get("avoid", []):
        if tool in tried:
            misses.append(f"tried {tool}")
    for pair in e.get("avoid_calls", []):
        tool, action = pair.split(":")
        if any(c.tool == tool and c.args.get("action") == action for c in t.calls):
            misses.append(f"tried {pair}")
    if e.get("instant") and (t.used_model or t.seconds > 1.5):
        misses.append(f"not instant ({t.seconds:.1f} s{', used the model' if t.used_model else ''})")
    if e.get("talk") and t.calls:
        misses.append("used a tool instead of talking")
    heard = " ".join(said)
    for pattern in e.get("mentions", []):   # a regular expression each: she has to have said it
        if not re.search(pattern, heard, re.I):
            misses.append(f"didn't say {pattern!r}")
    for pattern in e.get("lacks", []):      # and these she must not have said
        if re.search(pattern, heard, re.I):
            misses.append(f"said {pattern!r}")
    if any(text == CLARIFY for _, text in t.spoken):
        misses.append("said \"didn't quite catch that\"")
    if t.markup or any(re.search(r"</?(?:tool_call|function|parameter)\b|<parameter=", s) for _, s in t.spoken):
        misses.append("wrote tool-call markup as text")
    return misses


def _fixed_reply(t: Turn) -> bool:
    """Her answer was a prepared sentence where her own words were expected."""
    replies = [k for k, _ in t.spoken if k in ("model", "fixed")]
    return bool(replies) and replies[-1] == "fixed" and not t.quick and not t.expect.get("instant")


def summary(turns: list[Turn]) -> dict:
    model = [t.seconds for t in turns if t.used_model]
    first = [t.first_words for t in turns if t.used_model and t.first_words is not None]
    instant = [t for t in turns if t.expect.get("instant")]
    return {
        "turns": len(turns),
        "expectations missed": sum(bool(t.misses) for t in turns),
        "unasked actions (blocked or avoided)": sum(1 for t in turns for c in t.calls if c.refused)
        + sum(1 for t in turns for m in t.misses if m.startswith("tried ")),
        "prepared sentence instead of her words": sum(_fixed_reply(t) for t in turns),
        "\"didn't quite catch that\"": sum(1 for t in turns for _, s in t.spoken if s == CLARIFY),
        "tool-call markup written as text": sum(any(m.startswith("wrote tool-call") for m in t.misses) for t in turns),
        "instant commands under 1.5 s": f"{sum(not t.used_model and t.seconds <= 1.5 for t in instant)}/{len(instant)}",
        "model turns: first words (median)": f"{statistics.median(first):.1f} s" if first else "-",
        "model turns: whole answer (median)": f"{statistics.median(model):.1f} s" if model else "-",
    }


def _describe(t: Turn) -> str:
    calls = ", ".join(f"{c.tool}({', '.join(f'{k}={v!r}' for k, v in c.args.items())})"
                      + (" [blocked]" if c.refused else "" if c.ok else " [failed]") for c in t.calls)
    said = " / ".join(f"{'' if k == 'model' else f'[{k}] '}{s}" for k, s in t.spoken) or "(nothing)"
    how = "quick reply" if t.quick else "model" if t.used_model else "no model"
    lines = [f"{t.seconds:.1f} s, {how}" + (f", first words {t.first_words:.1f} s" if t.first_words else "")]
    if calls:
        lines.append(f"tools: {calls}")
    lines.append(f"says: {said}")
    if t.misses:
        lines.append("✗ " + "; ".join(t.misses))
    return "<br>".join(x.replace("|", "\\|").replace("\n", " ") for x in lines)


def report(runs: dict[str, list[tuple[str, list[Turn]]]]) -> str:
    modes = list(runs)
    out = [f"# Replay {datetime.now():%Y-%m-%d %H:%M}: {' vs '.join(modes)}", "",
           "Tools were faked: nothing happened on the PC. `[fixed]` = a prepared sentence, not her own words.", "",
           "| | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    sums = {m: summary([t for _, turns in runs[m] for t in turns]) for m in modes}
    for key in sums[modes[0]]:
        out.append(f"| {key} | " + " | ".join(str(sums[m][key]) for m in modes) + " |")
    for i, (name, _) in enumerate(runs[modes[0]]):
        out += ["", f"## {name}", "", "| You said | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
        for j, turn in enumerate(runs[modes[0]][i][1]):
            cells = [_describe(runs[m][i][1][j]) for m in modes]
            out.append(f"| {turn.user} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.replay")
    parser.add_argument("--mode", choices=["today", "new", "both"], default="new")
    parser.add_argument("--model", nargs="+", metavar="NAME",
                        help="Ollama models to try one after another, instead of config.yaml's (each is unloaded after)")
    parser.add_argument("--think", choices=["on", "off"], help="override llm.think_by_default")
    parser.add_argument("files", nargs="*", help="conversation files (default: all in tests/conversations)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    files = [Path(f) for f in args.files] or sorted(CONVERSATIONS.glob("*.yaml"))
    conversations = [yaml.safe_load(f.read_text(encoding="utf-8")) for f in files]
    runs: dict[str, list[tuple[str, list[Turn]]]] = {}
    for model in args.model or [None]:
        for mode in (["today", "new"] if args.mode == "both" else [args.mode]):
            label = " · ".join(x for x in (mode, model, args.think and f"thinking {args.think}") if x)
            replay = Replay(mode, model, None if args.think is None else args.think == "on")
            replay.warm_up()
            runs[label] = []
            for conv in conversations:
                replay.reset()
                replay.begin(conv)
                turns = []
                for spec in conv["turns"]:
                    turn = replay.run_turn(spec)
                    turns.append(turn)
                    print(f"[{label}] {conv['name']}: {turn.user[:60]!r} -> {turn.seconds:.1f} s"
                          + (f"  ✗ {'; '.join(turn.misses)}" if turn.misses else ""), flush=True)
                replay.end()
                runs[label].append((conv["name"], turns))
        if model:
            replay.mary.agent.brain.unload()  # one model in RAM at a time
    folder = PROJECT_DIR / "logs" / "replay"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d_%H%M}_{args.mode}{'_models' if args.model else ''}.md"
    path.write_text(report(runs), encoding="utf-8")
    print(f"\nReport: {path}")
    for label, conv_turns in runs.items():
        print(f"\n{label}")
        for key, value in summary([t for _, turns in conv_turns for t in turns]).items():
            print(f"  {key:<42} {value}")


if __name__ == "__main__":
    main()
