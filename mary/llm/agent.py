"""The agent loop: the brain (mary.brain, Qwen via Ollama today) + native tool calling + on-demand thinking.

Prompt layout, chosen so Ollama can reuse its KV cache between requests:
    [system prompt + tool schemas]  static: identical on every call
    [conversation so far]           append-only, trimmed only every few turns
    [<context>…</context> + request] the only part that is new each turn

Thinking is on for every request when llm.think_by_default is set (slower: she reasons before she acts).
Otherwise it switches on when the user says "think carefully" etc., or when the model itself calls
`escalate`; the request then continues with think=True, keeping any tool work already done.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time

from mary import skills
from mary.brain import BrainGarbled, BrainLost, BrainOffline, make_brain
from mary.brain import mode as brain_mode
from mary.latency import Latency
from mary.llm.context import live_context
from mary.llm.prompts import SYSTEM_PROMPT, new_prompt
from mary.llm.replies import CLARIFY, ReplyFilter
from mary.output import DEEP_THINKING, THINKING, Output
from mary.router.guards import is_multi_part
from mary.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# "Let me take a screenshot now." with no tool call: she said she'd act and didn't (new mode follows through).
_PROMISE = re.compile(r"\b(?:let me|i'?ll|i will|i'?m going to)\b[^.?!]*\b(?:do (?:that|it)|check|take|capture|open|"
                      r"search|look|find|remember|note|play|read|type|set|start|save|put|show|get)\b", re.I)

OFFLINE_REPLY = ("I can't reach my language model right now, so I can only do simple things, like opening apps, "
                 "music, volume or the time.")


class LLMOffline(RuntimeError):
    """The brain can't be reached (or its model is missing); simple commands still work."""


class Agent:
    def __init__(self, cfg, tools: ToolRegistry, output: Output, profile=None):
        self.cfg = cfg.llm
        self.tools = tools
        self.output = output
        self.brain = make_brain(cfg)
        self.schemas = tools.schemas()
        # New mode: her personality prompt with each brick's guide, waiting lines for slow tools, and every
        # reply in her own words. The old mode keeps its prompt and its tools' prepared sentences.
        self.new_mode = brain_mode(cfg) == "new"
        self.think_default = bool(self.cfg.get("think_by_default"))
        bricks = getattr(tools, "bricks", [])
        self.slow = skills.slow(bricks) if self.new_mode else {}
        prompt = new_prompt(skills.guides(bricks)) if self.new_mode else SYSTEM_PROMPT
        self.system = {"role": "system", "content": prompt}
        self.profile = profile
        self.pinned = self._profile_messages()
        self.exchanges: list[list[dict]] = []   # each = the messages of one request/response
        self.last_activity = time.monotonic()
        self._prefill_wanted = threading.Event()
        self._prefill_thread: threading.Thread | None = None
        self.offline_reason: str | None = None  # set while Ollama is unreachable; a thread keeps retrying
        self.warming = threading.Event()        # set while the model loads (~2 min after a reboot)
        self._recovery: threading.Thread | None = None
        if profile is not None:
            profile.listeners.append(self._profile_changed)

    def _profile_messages(self) -> list[dict]:
        """What the user asked MARY to remember, as a pinned first exchange after the static prefix.

        Not part of the system prompt: Ollama renders the tool schemas after the system text, so a
        changed fact there means re-reading the whole ~3.5k-token prefix. Here a change only re-reads this
        exchange and the conversation after it. There is always one (even with nothing to remember): the
        warm-up ends on its "Noted.", the point every request then continues from (see warm_up)."""
        section = self.profile.prompt_section() if self.profile else ""
        section = section or "Background, not a request: there's nothing to remember about me yet."
        return [{"role": "user", "content": section}, {"role": "assistant", "content": "Noted."}]

    def _profile_changed(self) -> None:
        self.pinned = self._profile_messages()
        self.prefill_in_background()

    # ------------------------------------------------------------------ setup

    def prefill_in_background(self) -> None:
        """Pre-load the conversation so far into Ollama's KV cache while nobody is waiting.

        Requests the router handles add history the model hasn't read yet; at ~30 ms per token on
        this CPU, nine quick commands meant 20 s of re-reading on the next LLM request. A one-token
        call ending on the last remembered reply makes Ollama process that history now, so the next real
        request only reads its own words. A real request arriving meanwhile just queues behind it.

        It must end exactly there, not on a placeholder question: Qwen3.5 (part recurrent) can only resume
        from the end of an earlier prompt. Measured: a request after a prompt that ended on a dummy "hi"
        re-read all ~3,500 tokens (5.9 s); after one that ended on the last reply, 0.5 s.
        """
        if self.offline_reason:
            return
        self._prefill_wanted.set()
        if self._prefill_thread is None or not self._prefill_thread.is_alive():
            self._prefill_thread = threading.Thread(target=self._prefill_loop, name="llm-prefill", daemon=True)
            self._prefill_thread.start()

    def _prefill_loop(self) -> None:
        while self._prefill_wanted.is_set():
            self._prefill_wanted.clear()
            # Ends on a reply (see above), with the same history the next request will send.
            messages = [self.system, *self.pinned, *self._history(short=self.think_default)]
            try:
                for _ in self.brain.chat(messages, self.schemas, think=False, max_tokens=1):
                    pass
            except Exception as e:  # the brain is down etc.: the next real request will report it
                log.debug("background prefill failed: %s", e)

    # ------------------------------------------------------------------ availability

    def go_offline(self, reason: str) -> None:
        """Ollama is unreachable or the model is missing: answer LLM requests with OFFLINE_REPLY at once
        (the router keeps working) and retry in the background until it's back."""
        if self.offline_reason is None:
            log.warning("language model offline: %s", reason)
        self.offline_reason = reason
        if self._recovery is None or not self._recovery.is_alive():
            self._recovery = threading.Thread(target=self._recover_loop, name="llm-recovery", daemon=True)
            self._recovery.start()

    def _recover_loop(self) -> None:
        delay = 15.0
        while self.offline_reason:
            time.sleep(delay)
            delay = min(120.0, delay * 2)
            try:
                self.warm_up()
            except Exception as e:
                log.debug("language model still offline: %s", e)
                continue
            self.offline_reason = None
            log.info("language model is back")
            self.output.status("✓ My language model is back.")

    def _restart(self) -> None:
        """The connection dropped mid-session: bring the brain back (a local one reloads its model, so
        the next reply is slow). Raises LLMOffline if that doesn't work."""
        if self.brain.reachable():
            return
        self.output.status("▸ My language model stopped; restarting it…")
        try:
            self.brain.restart()
        except BrainOffline as e:
            self.go_offline(str(e))
            raise LLMOffline(str(e)) from e

    def warm_up(self) -> float:
        """Load the model and pre-fill the static prefix. Returns seconds taken.

        Measured: ~1 s when Ollama still has the model loaded, ~110 s cold (after a reboot, or after
        MARY unloaded it on quit), almost all of it reading the ~2k-token tool prompt on this CPU."""
        self.warming.set()
        try:
            self.brain.start()
            start = time.perf_counter()
            # Ends on the pinned "Noted.": every request starts with exactly this, so it continues from here
            # instead of re-reading it all (a dummy "hi" here cost 5.9 s on the first request, and again
            # after every conversation reset).
            messages = [self.system, *self.pinned]
            for _ in self.brain.chat(messages, self.schemas, think=False, max_tokens=1):
                pass
            return time.perf_counter() - start
        finally:
            self.warming.clear()

    # ------------------------------------------------------------------ memory

    def reset(self) -> None:
        self.exchanges.clear()

    def _history(self, short: bool = False) -> list[dict]:
        """Remembered messages. `short` keeps only the last N exchanges, leaving room in the context
        window for a long thinking trace."""
        if time.monotonic() - self.last_activity > self.cfg.history_reset_minutes * 60:
            self.exchanges.clear()
        exchanges = self.exchanges[-self.cfg.history_turns:] if short else self.exchanges
        return [m for ex in exchanges for m in ex]

    def _remember(self, turn: list[dict]) -> None:
        # Keep the tool calls (without them the model learns from its own history to answer in plain
        # text instead of acting), but shorten what tools returned: a screen read or file list would
        # otherwise ride along in every later prompt at ~30 ms of CPU per token.
        limit = self.cfg.history_tool_chars
        self.exchanges.append([
            {**m, "content": m["content"][:limit] + "…"} if m["role"] == "tool" and len(m["content"]) > limit
            else m for m in turn])
        # Cached history is nearly free; re-reading it is not (~30 ms/token). So let it grow to a token
        # budget and only then trim to the last N exchanges, instead of trimming every few turns.
        size = sum(len(str(m.get("content", ""))) + len(str(m.get("tool_calls", "")))
                   for ex in self.exchanges for m in ex) / 3.5
        if size > self.cfg.history_max_tokens:
            self.exchanges = self.exchanges[-self.cfg.history_turns:]
        self.last_activity = time.monotonic()

    def remember_direct(self, text: str, tool: str | None, args: dict, result: str, reply: str) -> None:
        """Record a request the router handled without the LLM, so later turns ("do that again",
        "turn it down more") still have the context. Shaped exactly like a normal tool turn."""
        if time.monotonic() - self.last_activity > self.cfg.history_reset_minutes * 60:
            self.exchanges.clear()
        turn: list[dict] = [{"role": "user", "content": text}]
        if tool:
            turn += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool,
                                                                                         "arguments": args}}]},
                     {"role": "tool", "tool_name": tool, "content": result}]
        turn.append({"role": "assistant", "content": reply})
        self._remember(turn)

    # ------------------------------------------------------------------ main loop

    def wants_thinking(self, text: str) -> bool:
        low = text.lower()
        return any(trigger in low for trigger in self.cfg.think_triggers)

    def run(self, text: str, latency: Latency | None = None, hint: str | None = None,
            think: bool = False, prefetch: tuple[str, dict] | None = None) -> str:
        """prefetch: a tool to run *before* the model's first call, its result put in front of the model
        (e.g. read_screen for "what did you find?" after a web search: told to read first, the 4B model
        answered "I didn't find any results" without looking)."""
        latency = latency or Latency()
        if self.offline_reason and not self.brain.reachable(timeout=0.5):
            raise LLMOffline(self.offline_reason)
        if self.warming.is_set():  # the request queues behind the warm-up in Ollama: say why it's slow
            self.output.say("One moment, my language model is still starting up.")
        think = think or self.think_default or self.wants_thinking(text)
        history = self._history(short=think)
        ctx = self.tools.ctx  # tools check destructive calls against what the user actually said
        ctx.prev_request, ctx.request = (ctx.request if history else ""), text
        turn: list[dict] = [{"role": "user", "content": live_context(hint) + text}]
        self.output.state(DEEP_THINKING if think else THINKING)
        reply, fallback = "", "Done."
        tools_ran = False  # with no tool and nothing but filler to say, MARY didn't understand
        refused_only = False  # every tool the model tried was refused as unasked: it didn't understand either
        if prefetch:
            name, args = prefetch
            with latency.stage(f"tool:{name}"):
                result = self.tools.run(name, args)
            limit = self.cfg.tool_result_chars
            turn += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name,
                                                                                       "arguments": args}}]},
                     {"role": "tool", "tool_name": name, "content": result.text[:limit]}]
            tools_ran = True

        answered = False  # an action tool already told the user how it went
        multi_part = is_multi_part(text)  # then the follow-up call may still have work to do
        waited = False    # a waiting line was said ("Let me have a look…")
        refusals = 0      # actions the safety gate refused as unasked
        nudged, nudge = False, None  # told to do what she said she'd do (new mode); not remembered

        for step in range(1, self.cfg.max_iterations + 1):
            # After a self-confirming action the next call only looks for further steps: single-part
            # requests stop at the first word of reply text, multi-part ones give a tool call a few
            # tokens to appear first.
            quiet = (8 if multi_part else 0) if answered else None
            with latency.stage(f"llm#{step}{' (think)' if think else ''}"):
                content, calls = self._chat([self.system, *self.pinned, *history, *turn], think, latency,
                                            step=step, quiet=quiet, hold=refused_only and not self.new_mode)

            if self.new_mode and calls and refusals >= 3:
                # It keeps reaching for actions nobody asked for (the 2B once tried to type its answer into the
                # window eight times): it didn't understand, so ask rather than loop for a minute.
                latency.notes.append(f"stopped after {refusals} unasked actions")
                reply = CLARIFY
                self.output.say(reply)
                turn.append({"role": "assistant", "content": reply})
                break

            if not calls:
                reply = fallback if answered and not content.strip() else content.strip()
                if self.new_mode:
                    if (not nudged and not tools_ran and reply and _PROMISE.search(reply)
                            and not reply.endswith("?") and step < self.cfg.max_iterations):
                        # She said she'd do it ("Let me capture the screen now.") but called nothing, which the 2B
                        # often does. Her words were spoken; now she does it.
                        nudged = True
                        latency.notes.append(f"llm#{step}: said she'd do it without doing it; nudged")
                        nudge = {"role": "user", "content": "Do it now, with the tool."}
                        turn += [{"role": "assistant", "content": reply}, nudge]
                        continue
                    # Her reply is spoken as she wrote it, even when an action she tried was refused (asked "I'm
                    # not feeling well", the 2B tried to sleep the PC, then asked a caring question). Only silence
                    # gets the generic line.
                    if not reply:
                        reply = CLARIFY if refused_only or not tools_ran else fallback
                        self.output.say(reply)
                    turn.append({"role": "assistant", "content": reply})
                    break
                if refused_only:
                    # e.g. "Got it, Sam." after trying to save a name nobody gave: a mishearing. That
                    # reply was held back, not spoken.
                    if content.strip():
                        latency.notes.append(f"held back {content.strip()[:60]!r}: every action was unasked")
                    reply = CLARIFY
                    self.output.say(reply)
                if not reply:
                    # After a tool: the model went quiet, so use the tool's own short reply. With no tool,
                    # an empty or filler-only reply ("How can I assist you today?") means the request
                    # made no sense to it, usually a mishearing: ask again rather than greet.
                    reply = fallback if tools_ran else CLARIFY
                    self.output.say(reply)
                turn.append({"role": "assistant", "content": reply})
                break

            if not think and any(c.name == "escalate" for c in calls):
                reason = next(c.args.get("reason", "") for c in calls if c.name == "escalate")
                log.info("escalating: %s", reason)
                self.output.tool_log("▸ Thinking it through…")
                self.output.state(DEEP_THINKING)
                think = True
                continue  # drop this reply; redo the step with thinking, keeping earlier tool results

            turn.append({"role": "assistant", "content": content,
                         "tool_calls": [{"function": {"name": c.name, "arguments": c.args}} for c in calls]})
            says, all_actions = [], True
            for call in calls:
                name, args = call.name, dict(call.args)
                if name == "escalate":
                    text_out = "You are already reasoning in depth; carry on."
                else:
                    if self.slow.get(name) and not waited and not content.strip():
                        waited = True  # once per request, and not if she already said what she's doing
                        self.output.aside(random.choice(self.slow[name]))
                    with latency.stage(f"tool:{name}"):
                        result = self.tools.run(name, args)
                    unasked = not result.ok and result.text.startswith("Not done:")
                    refused_only = unasked and (refused_only or not tools_ran)
                    refusals += unasked
                    tools_ran = True
                    text_out = result.text
                    if not unasked:  # "Not done: …" is for the model, never something to say
                        fallback = result.say or result.text
                    tool = self.tools.tools.get(name)
                    if result.ok and result.say and tool and tool.speaks_for_itself and not self.new_mode:
                        says.append(result.say)
                    else:
                        all_actions = False
                    self.output.state(DEEP_THINKING if think else THINKING)
                limit = self.cfg.tool_result_chars
                turn.append({"role": "tool", "tool_name": name,
                             "content": text_out if len(text_out) <= limit else text_out[:limit] + "…"})
            # Simple actions confirm themselves ("Opening Spotify.") the moment they finish. The next
            # model call then only checks whether more steps are needed, and is cut short if not.
            answered = bool(says) and all_actions
            if answered:
                fallback = " ".join(says)
                latency.mark("reply")
                self.output.say(fallback)
                if not multi_part:  # compound requests arrive pre-split, so a single action is the job
                    reply = fallback
                    turn.append({"role": "assistant", "content": reply})
                    break
        else:
            reply = "That took more steps than I'm allowed, so I stopped. Could you break it into smaller asks?"
            self.output.say(reply)
            turn.append({"role": "assistant", "content": reply})

        self._remember([m for m in turn if m is not nudge])
        return reply

    def _chat(self, messages: list[dict], think: bool, latency: Latency, step: int = 1,
              quiet: int | None = None, hold: bool = False):
        """One model call, retried once if the brain dropped out (restarting it if it has gone). Tool
        results gathered so far are part of `messages`, so nothing already done is done again."""
        for attempt in (1, 2):
            try:
                return self._chat_once(messages, think, latency, step, quiet, hold)
            except BrainLost as e:
                if attempt == 2:
                    self.go_offline(str(e))
                    raise LLMOffline(str(e)) from e
                log.warning("%s; retrying", e)
                time.sleep(1)
                self._restart()
            except BrainOffline as e:  # e.g. the model was removed
                self.go_offline(str(e))
                raise LLMOffline(str(e)) from e
            except BrainGarbled as e:  # a badly written tool call: sampled again it's usually fine
                log.warning("%s; %s", e, "retrying" if attempt == 1 else "giving up")
                latency.notes.append(f"llm#{step}: unreadable reply{', retried' if attempt == 1 else ''}")
                if attempt == 2:
                    return "", []

    def _chat_once(self, messages: list[dict], think: bool, latency: Latency, step: int, quiet: int | None,
                   hold: bool = False):
        """One streamed model call; returns (content, tool_calls).

        Reply text streams to the output as it arrives. With `quiet` set, the user has already
        heard the outcome, so the call only looks for further tool calls: once more than `quiet`
        chunks of reply text arrive without one, it means "no more steps" and the call ends early
        instead of generating a redundant reply.
        """
        stream = self.brain.chat(messages, self.schemas, think=think)
        parts, calls, speaking, text_chunks = [], [], False, 0
        sentences = ReplyFilter(greetings_ok=self.new_mode)  # filler sentences are dropped before they're spoken

        def emit(text: str) -> None:
            nonlocal speaking
            if not text:
                return
            if hold:  # collected for the caller to judge, not spoken
                parts.append(text)
                return
            if not speaking:
                latency.mark("reply")
                self.output.reply_start()
                speaking = True
            parts.append(text)
            self.output.reply_delta(text)

        try:
            for chunk in stream:
                if chunk.thinking:
                    latency.mark("llm_first_token")
                    self.output.thinking_delta(chunk.thinking)
                if chunk.text:
                    latency.mark("llm_first_token")
                    if quiet is not None:
                        text_chunks += bool(chunk.text.strip())
                        if text_chunks > quiet and not calls:
                            latency.notes.append(f"llm#{step}: cut short, outcome already spoken")
                            return "", []
                        continue
                    emit(sentences.feed(chunk.text))
                calls.extend(chunk.calls)
                if chunk.done:
                    latency.notes.append(f"llm#{step}: prompt {chunk.read_s:.1f} s, {chunk.read_tokens} tok read, "
                                         f"{chunk.wrote_tokens} tok generated in {chunk.wrote_s:.1f} s")
            emit(sentences.flush())
            if sentences.markup:
                latency.notes.append(f"llm#{step}: dropped tool markup written as text")
            elif sentences.dropped:
                latency.notes.append(f"llm#{step}: dropped filler {sentences.dropped}")
        finally:
            if speaking:
                self.output.reply_end()
            stream.close()  # stops generation on the server when the reply is cut short
        return "".join(parts).strip(), calls
