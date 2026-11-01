"""The Ollama brain: a model Ollama serves on this PC, or one of Ollama's cloud models (beam:cloud), which this
PC's Ollama passes on to ollama.com once you've run `ollama signin`. Settings in config.yaml's llm: section."""
from __future__ import annotations

import re
from typing import Iterator

import httpx
import ollama

from mary.brain import Brain, BrainGarbled, BrainLost, BrainOffline, Chunk, ToolCall, parse_json
from mary.llm.server import OllamaUnavailable, ensure_model, ensure_server, is_cloud, reachable


class OllamaBrain(Brain):
    def __init__(self, llm_cfg):
        self.cfg = llm_cfg
        self.client = ollama.Client(host=llm_cfg.host)
        self.name = f"Ollama {llm_cfg.model}"
        self.local = not is_cloud(llm_cfg.model)   # confirmed by start(), from what Ollama says
        self.levels: list[str] = []                # thinking levels the model offers ("low", "medium"…), if any

    def options(self, think: bool, max_tokens: int | None = None) -> dict:
        # num_ctx/num_thread must stay constant across calls: changing them reloads the model.
        sampling = dict(self.cfg.sampling.thinking if think else self.cfg.sampling.fast)
        if max_tokens is not None:
            sampling["num_predict"] = max_tokens
        if not self.local:
            # The cloud runs it its own way. The output cap is there for this CPU's speed; a cloud model may think
            # a little even with thinking off (beam: ~150 tokens), which could leave no room for the reply.
            if max_tokens is None:
                sampling.pop("num_predict", None)
            return sampling
        return {"num_ctx": self.cfg.num_ctx, "num_thread": self.cfg.num_thread, **sampling}

    def think_value(self, think: bool) -> bool | str:
        """Ollama's think: on/off, or a level for a model that offers levels (beam's default is "max", far slower
        than MARY needs, so it gets llm.think_level)."""
        if not think or not self.levels:
            return think
        level = self.cfg.get("think_level") or "medium"
        return level if level in self.levels else self.levels[len(self.levels) // 2]

    def start(self) -> None:
        try:
            ensure_server(self.cfg.host, dict(self.cfg.server_env or {}))
            info = ensure_model(self.cfg.host, self.cfg.model)
        except OllamaUnavailable as e:
            raise BrainOffline(str(e)) from e
        self.local, self.levels = not info["cloud"], info["levels"]

    def reachable(self, timeout: float = 2.0) -> bool:
        return reachable(self.cfg.host, timeout=timeout)

    def restart(self) -> None:
        """Start Ollama again if it has gone (it reloads the model, so the next reply is slow)."""
        if reachable(self.cfg.host):
            return
        try:
            ensure_server(self.cfg.host, dict(self.cfg.server_env or {}), wait_s=30)
        except OllamaUnavailable as e:
            raise BrainOffline(str(e)) from e

    def chat(self, messages: list[dict], tools: list[dict], think: bool = False,
             max_tokens: int | None = None) -> Iterator[Chunk]:
        model = self.cfg.model
        try:
            stream = self.client.chat(model=model, messages=messages, tools=tools, think=self.think_value(think),
                                      stream=True, keep_alive=self.cfg.keep_alive if self.local else None,
                                      options=self.options(think, max_tokens))
            try:
                for part in stream:
                    msg = part.message
                    yield Chunk(text=msg.content or "", thinking=msg.thinking or "",
                                calls=[ToolCall(c.function.name, dict(c.function.arguments or {}))
                                       for c in msg.tool_calls or []],
                                done=bool(part.done), read_tokens=getattr(part, "prompt_eval_count", 0) or 0,
                                read_s=(getattr(part, "prompt_eval_duration", 0) or 0) / 1e9,
                                wrote_tokens=getattr(part, "eval_count", 0) or 0,
                                wrote_s=(getattr(part, "eval_duration", 0) or 0) / 1e9)
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()  # closing the HTTP stream also stops generation on the server
        except (ConnectionError, httpx.TransportError) as e:  # the client raises ConnectionError when it can't connect
            raise BrainLost(f"lost the connection to Ollama ({e})") from e
        except ollama.ResponseError as e:
            if e.status_code in (401, 403) and not self.local:
                raise BrainOffline("this PC isn't signed in to Ollama's cloud (run: ollama signin)") from e
            if e.status_code == 429 and not self.local:
                raise BrainOffline("Ollama's cloud limit is reached for now; try again later, or pick a model "
                                   "on this PC") from e
            if e.status_code == 404:  # the model was removed
                raise BrainOffline(f"the model {model} isn't downloaded (ollama pull {model})") from e
            if re.search(r"pars|tool", str(e), re.I):  # it wrote a tool call Ollama couldn't read
                raise BrainGarbled(f"unreadable reply from the model ({e})") from e
            if e.status_code >= 500:  # e.g. the runner crashed
                raise BrainLost(f"Ollama error {e.status_code} ({e})") from e
            raise

    def json(self, messages: list[dict], schema: dict, max_tokens: int = 1200) -> dict:
        try:
            reply = self.client.chat(model=self.cfg.model, messages=messages, format=schema, stream=False, think=False,
                                     keep_alive=self.cfg.keep_alive if self.local else None,
                                     options={**self.options(False, max_tokens), "temperature": 0})
        except (ConnectionError, httpx.TransportError) as e:
            raise BrainLost(f"lost the connection to Ollama ({e})") from e
        except ollama.ResponseError as e:
            if e.status_code in (401, 403) and not self.local:
                raise BrainOffline("this PC isn't signed in to Ollama's cloud (run: ollama signin)") from e
            if e.status_code == 429 and not self.local:
                raise BrainOffline("Ollama's cloud limit is reached for now; try again later") from e
            if e.status_code == 404:
                raise BrainOffline(f"the model {self.cfg.model} isn't downloaded") from e
            raise BrainLost(f"Ollama error {e.status_code} ({e})") from e
        return parse_json(reply.message.content or "")

    def unload(self) -> None:
        if not self.local:
            return  # nothing of it is in this PC's memory
        ollama.Client(host=self.cfg.host, timeout=5).generate(model=self.cfg.model, prompt="", keep_alive=0)
