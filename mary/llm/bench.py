"""Compare language models on MARY's real job: python -m mary.llm.bench [model ...]

Each model gets MARY's real system prompt, profile and tool list, then 18 requests the router can't handle
by itself: tool choices with arguments, sums and reasoning traps, and the two traps that made the 4B model
make things up (answering "what did you find?" without reading, and inventing a video link). Only the
model's first decision is scored. **No tool is ever run.**

Measured per model: correct answers, generation speed (tokens/s), prompt reading speed, time per request,
the one-time cold start (loading plus reading the ~2k-token tool prompt) and RAM. Results are saved to
logs/bench/<model>.json. Other models are unloaded first so each runs alone, so a running MARY reloads
her model on the next request.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

import ollama
import psutil

from mary.config import expand_path, load_config

# A neutral front window: an earlier run said "YouTube", and "check YouTube for…" then listed the screen.
CONTEXT = ("<context>\ntime: Monday 26 October 2026, 18:00\nactive window: Documents - File Explorer (explorer.exe)\n"
           "volume: 40%\n</context>\n")


@dataclass
class Case:
    text: str
    tool: str | None                                      # expected tool (None = answer in words)
    check: Callable[[dict], bool] = lambda a: True        # on the tool's arguments
    answer: Callable[[str], bool] = lambda t: True        # on the reply text, for tool=None
    before: list = field(default_factory=list)            # earlier turns (tool results), for the traps
    also: tuple[str, ...] = ()                            # other acceptable tools
    think: bool = False
    label: str = ""


def _has(value, *words) -> bool:
    v = str(value or "").lower()
    return all(w in v for w in words)


def _turn(user: str, tool: str, args: dict, result: str, reply: str) -> list[dict]:
    return [{"role": "user", "content": user},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool, "arguments": args}}]},
            {"role": "tool", "tool_name": tool, "content": result},
            {"role": "assistant", "content": reply}]


CASES = [
    Case("Check YouTube and see if you can get me one of MrBeast's videos.", "web",
         lambda a: a.get("action") == "search" and _has(a.get("value"), "mrbeast") and _has(a.get("site"), "youtube"),
         label="search YouTube"),
    Case("I'd like to read about the history of Ghana on Wikipedia", "web",
         lambda a: _has(a.get("value"), "ghana"), label="Wikipedia"),
    Case("can you see if I have any PDF files I downloaded this week", "find_files",
         lambda a: _has(a.get("file_type"), "pdf") and _has(a.get("modified_within"), "week"), label="find PDFs"),
    Case("it's too loud in here, bring the sound down", "system_control",
         lambda a: a.get("action") in ("volume_down", "volume_set", "set_volume"), label="volume down"),
    Case("click the button that says subscribe", "screen",
         lambda a: a.get("action") == "click" and _has(a.get("target"), "subscribe"), label="click"),
    Case("put on some music by Don Williams for me", "play_music",
         lambda a: _has(a.get("query"), "don williams"), label="play music"),
    Case("please make a note that the plumber comes on Friday", "create_note",
         lambda a: "plumber" in json.dumps(a).lower(), also=("memory",), label="note"),
    Case("how's the weather looking in Paris right now", "check_weather",
         lambda a: _has(a.get("place"), "paris"), label="weather"),
    Case("set an alarm in 25 minutes to check the rice", "set_timer",
         lambda a: "25" in json.dumps(a), label="timer"),
    Case("type 'see you at five' into the message box and send it", "screen",
         lambda a: a.get("action") == "type" and _has(a.get("text"), "see you at five"), label="type into box"),
    Case("has anybody messaged me on WhatsApp", "check_messages", label="messages"),
    Case("don't open Spotify, I changed my mind", None, answer=lambda t: True, label="negation"),
    Case("what's 15 times 23", None, answer=lambda t: "345" in t, label="arithmetic"),
    Case("which is bigger, 9.11 or 9.9?", None,
         answer=lambda t: "9.9" in t and not re.search(r"9\.11 is (bigger|larger|greater)", t), label="9.11 vs 9.9"),
    Case("If I leave at 3:40 pm and the drive takes 95 minutes, when will I arrive? Think carefully.", None,
         answer=lambda t: "5:15" in t, think=True, label="time sum (thinking)"),
    Case("so should I take an umbrella?", None,
         answer=lambda t: re.search(r"\b(yes|rain|umbrella)\b", t, re.I) and not re.search(r"\b(?!28\b|32\b|75\b)\d{2}\s*(°|degrees|%)", t),
         before=_turn("what's the weather", "check_weather", {"when": "now"},
                      "Weather in Lisbon: Patchy rain nearby, 28°C, feels like 32°C, humidity 75%.",
                      "It's 28 degrees, feeling like 32. Patchy rain nearby."), label="uses the reading"),
    Case("What did you find?", "read_screen", also=("screen",),
         before=_turn("search the web for the best budget laptops", "web",
                      {"action": "search", "value": "best budget laptops"},
                      "Opened a web search for 'best budget laptops' in the browser. The results haven't been read; "
                      "read_screen can read them.", "I've opened a search for best budget laptops in your browser."),
         label="trap: reads first"),
    Case("play the first video", "screen",
         lambda a: a.get("action") == "click" and _has(a.get("target"), "first"),
         before=_turn("search YouTube for MrBeast", "web", {"action": "search", "value": "MrBeast", "site": "youtube"},
                      "Opened a web search for 'MrBeast' in the browser. The results haven't been read; read_screen "
                      "can read them.", "I've opened a search for MrBeast on Youtube in your browser."),
         label="trap: clicks, no made-up link"),
]


def _server_ram_mb() -> int:
    total = 0
    for p in psutil.process_iter(["name", "memory_info"]):
        if p.info["name"] and p.info["name"].lower().startswith(("llama-server", "ollama_llama_server")):
            total += p.info["memory_info"].rss
    return total // 2**20


def _agent(cfg):
    from mary.app_index import AppIndex
    from mary.llm.agent import Agent
    from mary.output import Output
    from mary.profile import Profile
    from mary.tools.registry import ToolContext, ToolRegistry

    profile = Profile()
    return Agent(cfg, ToolRegistry(ToolContext(cfg, AppIndex(cfg), Output(), profile=profile)), Output(), profile)


def _chat(client, think: bool, **kw):
    """One call; a model without the thinking switch is asked again without it (and that's noted)."""
    try:
        return client.chat(think=think, **kw), ""
    except ollama.ResponseError as e:
        if "think" not in str(e).lower():
            raise
        return client.chat(**kw), "no thinking switch"


def run(model: str, cfg, agent) -> dict:
    client = ollama.Client(host=cfg.llm.host)
    for m in client.ps().models:  # each model runs alone: nothing else competes for RAM or CPU
        if m.model != model:
            client.generate(model=m.model, prompt="", keep_alive=0)
    prefix = [agent.system, *agent.pinned]
    start = time.perf_counter()
    cold, note = _chat(client, False, model=model, messages=[*prefix, {"role": "user", "content": "hi"}],
                       tools=agent.schemas, keep_alive=-1, options={**agent._options(False), "num_predict": 1})
    report = {"model": model, "cold_start_s": round(time.perf_counter() - start, 1),
              "prefix_tokens": cold.prompt_eval_count, "ram_mb": _server_ram_mb(), "note": note, "cases": []}
    for case in CASES:
        messages = [*prefix, *case.before, {"role": "user", "content": CONTEXT + case.text}]
        t0 = time.perf_counter()
        try:
            r, _ = _chat(client, case.think, model=model, messages=messages, tools=agent.schemas,
                         keep_alive=-1, options={**agent._options(case.think), "num_predict": 1024})
        except Exception as e:
            report["cases"].append({"label": case.label, "ok": False, "error": str(e)[:200]})
            continue
        took = time.perf_counter() - t0
        calls = [(c.function.name, dict(c.function.arguments or {})) for c in (r.message.tool_calls or [])]
        text = (r.message.content or "").strip()
        if case.tool is None:
            ok = not calls and bool(case.answer(text))
        else:
            ok = bool(calls) and calls[0][0] in (case.tool, *case.also) and \
                (calls[0][0] != case.tool or case.check(calls[0][1]))
        report["cases"].append({
            "label": case.label, "ok": ok, "seconds": round(took, 1),
            "calls": calls, "text": text[:300],
            "gen_tokens": r.eval_count, "gen_tps": round((r.eval_count or 0) / max(1e-9, (r.eval_duration or 0) / 1e9), 1),
            "prompt_tokens": r.prompt_eval_count,
            "prompt_tps": round((r.prompt_eval_count or 0) / max(1e-9, (r.prompt_eval_duration or 0) / 1e9), 1)})
    done = [c for c in report["cases"] if "seconds" in c]
    report["score"] = f"{sum(c['ok'] for c in report['cases'])}/{len(CASES)}"
    report["median_s"] = sorted(c["seconds"] for c in done)[len(done) // 2] if done else None
    report["gen_tps"] = round(sum(c["gen_tokens"] or 0 for c in done) /
                              max(1e-9, sum((c["gen_tokens"] or 0) / max(c["gen_tps"], 1e-9) for c in done)), 1)
    report["prompt_tps"] = round(sorted(c["prompt_tps"] for c in done)[len(done) // 2], 1) if done else None
    return report


def main() -> None:
    cfg = load_config()
    models = sys.argv[1:] or [cfg.llm.model]
    agent = _agent(cfg)
    out_dir = expand_path(cfg.paths.logs) / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for model in models:
        print(f"\n== {model}", flush=True)
        rep = run(model, cfg, agent)
        reports.append(rep)
        (out_dir / f"{model.replace(':', '_').replace('/', '_')}.json").write_text(json.dumps(rep, indent=1),
                                                                                 encoding="utf-8")
        for c in rep["cases"]:
            what = c.get("error") or (f"{c['calls'][0][0]}({json.dumps(c['calls'][0][1])[:70]})" if c.get("calls")
                                      else repr(c.get("text", ""))[:80])
            print(f"  {'✓' if c['ok'] else '✗'} {c['label']:<30} {c.get('seconds', '-'):>5} s  {what}", flush=True)
        print(f"  score {rep['score']} · {rep['gen_tps']} tok/s generating · prompt {rep['prompt_tps']} tok/s · "
              f"median {rep['median_s']} s/request · cold start {rep['cold_start_s']} s · RAM {rep['ram_mb']} MB"
              f"{' · ' + rep['note'] if rep['note'] else ''}")
    if len(reports) > 1:
        print("\nmodel                          score  tok/s  prompt tok/s  median s  cold s  RAM MB")
        for r in reports:
            print(f"{r['model']:<30} {r['score']:>5}  {r['gen_tps']:>5}  {r['prompt_tps']:>12}  {r['median_s']:>8}"
                  f"  {r['cold_start_s']:>6}  {r['ram_mb']:>6}")


if __name__ == "__main__":
    main()
