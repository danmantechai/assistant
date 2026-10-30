"""The orchestrator: one request in, actions + reply out. Front ends (text REPL, HUD, voice)
call Mary.handle(); everything they show or say arrives through the Output they passed in.

brain.mode: new (the model is the brain)
    request -> quick reply ("thanks", "no", "bye")
            -> reflex: the whole request is a plain instant command ("volume up", "pause", "what time is it")
            -> otherwise the brain gets the whole request and answers in her own words, using tools as needed

brain.mode: today (the old way, kept for comparison)
    request -> split into clauses -> per clause: semantic router
        direct -> run the tool, speak its reply (no LLM)
        hint / llm -> agent, with the router's guess when it had one
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import nullcontext

from mary import conversation
from mary.app_index import AppIndex
from mary.brain import mode as brain_mode
from mary.latency import Latency
from mary.llm.agent import OFFLINE_REPLY, Agent, LLMOffline
from mary.logs import write_jsonl
from mary.output import ERROR, IDLE, Output
from mary.reflexes import Reflexes
from mary.router.guards import split_clauses
from mary.tools.registry import ToolContext, ToolRegistry

log = logging.getLogger(__name__)


class Mary:
    def __init__(self, cfg, output: Output, debug: bool = False):
        self.cfg = cfg
        self.output = output
        self.debug = debug
        self.apps = AppIndex(cfg)
        if self.apps.loaded_from_cache:
            self.apps.refresh_in_background()  # pick up newly installed apps
        from mary.profile import Profile

        self.profile = Profile()
        self.mode = brain_mode(cfg)
        self.ctx = ToolContext(cfg, self.apps, output, profile=self.profile)
        self.tools = ToolRegistry(self.ctx)
        self.agent = Agent(cfg, self.tools, output, self.profile)
        from mary import skills

        self.reflexes = Reflexes(skills.reflexes(self.tools.bricks))
        self.router = None
        self.router_load_s = 0.0
        self.awaiting: str | None = None  # "dictation": the next request is text to type
        self.awaiting_until = 0.0         # ...but only within 30 s; later requests are commands again
        self.offer: tuple[str, float] | None = None  # MARY's last yes/no question, e.g. "Shall I open it?"
        self.asked = False                # MARY's last reply ended with a question ("Which app?")
        self.end_conversation = False     # set by "no" / "thanks" / "bye": stop listening for follow-ups
        if cfg.router.enabled and self.mode == "today":  # the new mode doesn't use it (nor load its model)
            from mary.router.router import Router

            start = time.perf_counter()
            self.router = Router(cfg, self.apps, self.ctx)
            self.router_load_s = time.perf_counter() - start

    def greeting(self) -> str:
        """What MARY says when she's ready: by time of day, with your name if she knows it."""
        hour = time.localtime().tm_hour
        part = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        name = self.profile.name
        return f"{part}, {name}. I'm ready when you are." if name else f"{part}. MARY here, ready when you are."

    def warm_up(self) -> float | None:
        """Seconds the model took to load, or None if it's unavailable: MARY then starts in router-only
        mode (simple commands still work) and keeps trying to reach Ollama in the background."""
        try:
            return self.agent.warm_up()
        except Exception as e:  # OllamaUnavailable, or Ollama refusing the request
            self.agent.go_offline(str(e))
            return None

    def handle(self, text: str, think: bool = False, prefill: bool = True) -> Latency:
        """prefill=False: the caller starts the background cache prefill itself, later (voice mode
        does it after MARY has finished speaking, so the two don't fight over the CPU)."""
        latency = Latency(text)
        self.output.user_text(text)
        self.end_conversation = False
        if self.awaiting and time.monotonic() > self.awaiting_until:
            self.awaiting = None
        if self.awaiting == "dictation":  # the answer to "What should I type?" is typed as said
            self.awaiting = None
            try:
                self._type_dictation(text, latency)
            finally:
                self.output.state(IDLE)
            return latency
        offer, self.offer = self.offer, None
        offer = offer[0] if offer and time.monotonic() - offer[1] < 60 else None
        act = conversation.classify(text, offer_pending=bool(offer), name=self.profile.name, asked=self.asked)
        self.asked = False
        if act and not act.accept:  # "no", "thanks", "okay", "bye": no LLM, and the conversation is over
            self.end_conversation = act.end
            if act.reply:
                self.output.say(act.reply)
                self.agent.remember_direct(text, None, {}, "", act.reply)
            latency.notes.append(f"conversation: {act.reply or '(ends quietly)'}")
            self.output.state(IDLE)
            write_jsonl("latency", latency.as_dict())
            return latency
        if act and act.accept:  # "yes" to MARY's own question: the model does it, told exactly what
            latency.notes.append(f"yes to: {offer}")
            text = f"Yes, go ahead: {offer}"
        if conversation.asks_about_page(text) and self._just_browsed():
            latency.notes.append("about the page MARY opened: reading the screen first")
            time.sleep(max(0.0, 3.0 - (time.monotonic() - self.ctx.last_web_at)))  # a fresh page is still loading
            return self._answer(text, latency, prefetch=("read_screen", {}), prefill=prefill)
        # Deep-thinking requests stay whole (the model plans them); others run part by part.
        thinking = think or self.agent.wants_thinking(text)
        clauses = [text] if thinking or (act and act.accept) or self.mode == "new" else split_clauses(text)
        if len(clauses) > 1:
            latency.notes.append(f"split into {len(clauses)} parts: {clauses}")
        try:
            if self.mode == "new":
                self._new_turn(text, latency, think, plain=not (thinking or (act and act.accept)))
                clauses = []
            last_direct = None
            for clause in clauses:
                routed = self.router and not thinking and not (act and act.accept)
                decision = self.route(clause, latency) if routed else None
                if decision and decision.route == "direct":
                    # "it's too loud in here, bring the sound down" is two clauses but one request: don't
                    # turn the volume down twice.
                    if (decision.tool, decision.args) == last_direct:
                        latency.notes.append(f"skipped a repeat: {clause!r}")
                        continue
                    last_direct = (decision.tool, decision.args)
                    self._run_direct(decision, latency)
                else:
                    reply = self.agent.run(clause, latency=latency, think=think,
                                           hint=decision.hint if decision else None)
                    question = conversation.offer_in(reply)
                    self.offer = (question, time.monotonic()) if question else None
                    self.asked = reply.rstrip().endswith("?")
        except KeyboardInterrupt:
            self.output.reply_end()
            self.output.tool_log("✗ Stopped")
        except LLMOffline as e:
            log.warning("request needed the LLM, which is offline: %s", e)
            self.output.say(OFFLINE_REPLY)
        except Exception as e:
            log.exception("request failed: %s", text)
            self.output.state(ERROR)
            self.output.say(f"Sorry, something went wrong: {e}")
        finally:
            self.output.state(IDLE)
        write_jsonl("latency", latency.as_dict())
        if prefill:
            self.agent.prefill_in_background()  # so the next LLM request doesn't re-read this one
        return latency

    def _new_turn(self, text: str, latency: Latency, think: bool, plain: bool) -> None:
        """New mode: a reflex if the whole request is a plain instant command, else the brain, whole."""
        hit = self.reflexes.match(text) if plain else None
        if hit:
            self._run_reflex(text, *hit, latency)
            return
        reply = self.agent.run(text, latency=latency, think=think)
        question = conversation.offer_in(reply)
        self.offer = (question, time.monotonic()) if question else None
        self.asked = reply.rstrip().endswith("?")

    def _run_reflex(self, text: str, reflex, args: dict, latency: Latency) -> None:
        self.ctx.prev_request, self.ctx.request = self.ctx.request, text  # the safety gate still checks it
        latency.notes.append(f"reflex: {reflex.tool or 'answer'} {args}")
        result_text = ""
        if reflex.tool:
            with latency.stage(f"tool:{reflex.tool}"):
                result = self.tools.run(reflex.tool, args)
            result_text = result.text
            reply = (result.say or result.text) if result.ok else (result.say or f"I couldn't do that. {result.text}")
        else:
            reply = reflex.answer(text)
        latency.mark("reply")
        self.output.say(reply)
        self.agent.remember_direct(text, reflex.tool, args, result_text, reply)  # "turn it back up" works

    def _just_browsed(self) -> bool:
        """MARY opened a web page in the last 10 minutes, and nothing else since (a file search after it
        makes "what did you find?" about the files)."""
        web = self.ctx.last_web_at
        return bool(web) and time.monotonic() - web < 600 and web > self.ctx.last_files_at

    def _answer(self, text: str, latency: Latency, prefetch, prefill: bool) -> Latency:
        """Straight to the model, with a tool result already in front of it."""
        try:
            reply = self.agent.run(text, latency=latency, prefetch=prefetch)
            question = conversation.offer_in(reply)
            self.offer = (question, time.monotonic()) if question else None
            self.asked = reply.rstrip().endswith("?")
        except LLMOffline as e:
            log.warning("request needed the LLM, which is offline: %s", e)
            self.output.say(OFFLINE_REPLY)
        except Exception as e:
            log.exception("request failed: %s", text)
            self.output.state(ERROR)
            self.output.say(f"Sorry, something went wrong: {e}")
        finally:
            self.output.state(IDLE)
        write_jsonl("latency", latency.as_dict())
        if prefill:
            self.agent.prefill_in_background()
        return latency

    def route(self, clause: str, latency: Latency | None = None):
        with latency.stage("router") if latency else nullcontext():
            decision = self.router.route(clause)
        write_jsonl("routing", decision.as_log())
        if latency:
            latency.notes.append(f"router: {decision.route} → {decision.intent} {decision.score:.2f} "
                                 f"(margin {decision.margin:.2f}): {decision.reason}")
        return decision

    def _run_direct(self, d, latency: Latency) -> None:
        """Run a confidently routed request without the LLM."""
        self.ctx.prev_request, self.ctx.request = self.ctx.request, d.text
        result_text = ""
        if d.slot and d.slot.reply:  # the slot step already knows the answer ("Calculator isn't open.")
            reply = d.slot.reply
        elif d.tool:
            with latency.stage(f"tool:{d.tool}"):
                result = self.tools.run(d.tool, d.args)
            result_text = result.text
            if not result.ok:
                reply = result.say or f"I couldn't do that. {result.text}"
            elif d.intent == "system_status" and d.slot.fill.get("part"):
                part = d.slot.fill["part"]  # "how's my battery" -> just the battery sentence
                reply = next((s.strip() + "." for s in result.text.split(". ") if s.startswith(part)),
                             result.say)
            else:
                reply = self.router.reply(d, result.say)
        else:
            reply = self.router.reply(d, None)
        latency.mark("reply")
        self.output.say(reply)
        self.agent.remember_direct(d.text, d.tool, d.args, result_text, reply)
        if d.slot and d.slot.fill.get("await"):
            self.awaiting = d.slot.fill["await"]
            self.awaiting_until = time.monotonic() + 30

    def _type_dictation(self, text: str, latency: Latency) -> None:
        if re.fullmatch(r"\W*(cancel|never ?mind|stop|forget it|nothing)\W*", text, re.I):
            self.output.say("Okay, nothing typed.")
            return
        self.ctx.prev_request, self.ctx.request = self.ctx.request, f"type {text}"
        with latency.stage("tool:keyboard"):
            result = self.tools.run("keyboard", {"action": "type", "text": text})
        reply = result.say if result.ok else (result.say or f"I couldn't type it. {result.text}")
        latency.mark("reply")
        self.output.say(reply)
        self.agent.remember_direct(f"type {text}", "keyboard", {"action": "type", "text": text}, result.text, reply)
