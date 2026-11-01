"""A brain from any service that speaks the OpenAI chat API (POST {url}/chat/completions): OpenAI, OpenRouter, Groq,
Together, a server on this PC such as LM Studio or llama.cpp, or Ollama itself (http://127.0.0.1:11434/v1).

config.yaml: brain.provider: api, with brain.api.url and brain.api.model. The key is LLM_API_KEY in .env (the
settings page writes it there). Everything MARY sends the model goes to that service.

MARY's messages are Ollama-shaped; here tool calls get the IDs the OpenAI format pairs results with, and arguments
travel as JSON text. Thinking is reasoning_effort ("none" when off, else llm.think_level). Sampling and reasoning
settings are extras: a service that refuses one gets the request again without them, for good.
"""
from __future__ import annotations

import itertools
import json
import os
import time
from typing import Iterator
from urllib.parse import urlparse

import httpx

from mary.brain import Brain, BrainGarbled, BrainLost, BrainOffline, Chunk, ToolCall, parse_json

KEY = "LLM_API_KEY"
_EXTRAS = ("temperature", "top_p", "max_tokens", "reasoning_effort", "stream_options")


def api_key() -> str:
    """LLM_API_KEY, from the environment or MARY's .env (only that one value is read)."""
    from dotenv import dotenv_values

    from mary.config import PROJECT_DIR

    env = PROJECT_DIR / ".env"
    return (os.environ.get(KEY) or (dotenv_values(env).get(KEY) if env.exists() else "") or "").strip()


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def to_openai(messages: list[dict]) -> list[dict]:
    """MARY's messages as the OpenAI format wants them: each tool call with an ID, each result pointing back to it."""
    out, pending, ids = [], [], itertools.count(1)
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function") or {}
                call_id = f"call_{next(ids)}"
                pending.append((call_id, fn.get("name", "")))
                args = fn.get("arguments")
                calls.append({"id": call_id, "type": "function", "function": {
                    "name": fn.get("name", ""), "arguments": args if isinstance(args, str) else json.dumps(args or {})}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            name = m.get("tool_name", "")
            match = next((p for p in pending if p[1] == name), pending[0] if pending else (f"call_{next(ids)}", name))
            if match in pending:
                pending.remove(match)
            out.append({"role": "tool", "tool_call_id": match[0], "content": str(m.get("content", ""))})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


class ApiBrain(Brain):
    def __init__(self, api_cfg: dict, llm_cfg, key: str | None = None):
        self.url = str(api_cfg.get("url") or "").rstrip("/")
        self.model = str(api_cfg.get("model") or "")
        self.cfg = llm_cfg
        self.key = api_key() if key is None else key
        self.name = f"{urlparse(self.url).hostname or 'API'} {self.model}"
        self.local = is_local(self.url)   # a server on this PC keeps a cache worth pre-filling
        self.plain = False                # the service refused an extra setting: send none from now on
        self.http = httpx.Client(timeout=httpx.Timeout(120, connect=10))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _refused(self, status: int, detail: str) -> Exception:
        if status in (401, 403):
            return BrainOffline(f"the service refused the API key ({detail})")
        if status == 404:
            return BrainOffline(f"the service doesn't know the model '{self.model}', or the address is wrong")
        if status == 429:
            return BrainOffline("the service's rate limit is reached; try again in a moment")
        if status >= 500:
            return BrainLost(f"the service had an error {status} ({detail})")
        return BrainOffline(f"the service refused the request ({detail})")

    def start(self) -> None:
        if not self.url or not self.model:
            raise BrainOffline("choose the service's address and model (settings › Model)")
        if not self.local and not self.key:
            raise BrainOffline("add the service's API key (settings › Model)")
        try:
            r = self.http.get(f"{self.url}/models", headers=self._headers(), timeout=10)
        except httpx.HTTPError as e:
            raise BrainOffline(f"can't reach {self.url} ({e})") from e
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            raise self._refused(r.status_code, r.text[:200])

    def reachable(self, timeout: float = 2.0) -> bool:
        try:
            return self.http.get(f"{self.url}/models", headers=self._headers(), timeout=timeout).status_code < 500
        except httpx.HTTPError:
            return False

    def restart(self) -> None:
        if not self.reachable(timeout=10):
            raise BrainOffline(f"can't reach {self.url}")

    def body(self, messages: list[dict], tools: list[dict], think: bool, max_tokens: int | None) -> dict:
        body = {"model": self.model, "messages": to_openai(messages), "stream": True}
        if tools:
            body["tools"] = tools
        if self.plain:
            return body
        sampling = self.cfg.sampling.thinking if think else self.cfg.sampling.fast
        # The output cap is for a CPU's speed: a remote reasoning model needs room to think before it replies.
        body.update({"temperature": sampling.get("temperature"), "top_p": sampling.get("top_p"),
                     "max_tokens": max_tokens or (sampling.get("num_predict") if self.local else None),
                     "stream_options": {"include_usage": True}})
        # Thinking off has to be said: reasoning models think by default (Ollama's qwen3.5 here spent all 512 tokens
        # thinking without it). A service that doesn't take reasoning_effort gets the request again without it.
        body["reasoning_effort"] = (self.cfg.get("think_level") or "medium") if think else "none"
        return {k: v for k, v in body.items() if v is not None}

    def json(self, messages: list[dict], schema: dict, max_tokens: int = 1200) -> dict:
        body = {"model": self.model, "messages": to_openai(messages), "stream": False, "max_tokens": max_tokens,
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "answer", "schema": schema, "strict": True}}}
        for attempt in (1, 2):
            try:
                r = self.http.post(f"{self.url}/chat/completions", json=body, headers=self._headers())
            except httpx.TransportError as e:
                raise BrainLost(f"lost the connection to {self.url} ({e})") from e
            if r.status_code == 400 and attempt == 1:   # a service that takes no schema: plain JSON mode
                body["response_format"] = {"type": "json_object"}
                body.pop("max_tokens", None)
                continue
            if r.status_code >= 400:
                raise self._refused(r.status_code, r.text[:300])
            break
        try:
            return parse_json(r.json()["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, ValueError) as e:
            raise BrainGarbled(f"unreadable reply from the service ({e})") from e

    def chat(self, messages: list[dict], tools: list[dict], think: bool = False,
             max_tokens: int | None = None) -> Iterator[Chunk]:
        for attempt in (1, 2):
            body = self.body(messages, tools, think, max_tokens)
            try:
                with self.http.stream("POST", f"{self.url}/chat/completions", json=body,
                                      headers=self._headers()) as r:
                    if r.status_code >= 400:
                        detail = r.read().decode(errors="replace")[:300]
                        if r.status_code == 400 and attempt == 1 and not self.plain and \
                                any(x in detail for x in _EXTRAS):
                            self.plain = True  # e.g. a reasoning model that takes no temperature
                            continue
                        raise self._refused(r.status_code, detail)
                    yield from self._read(r)
                    return
            except httpx.TransportError as e:
                raise BrainLost(f"lost the connection to {self.url} ({e})") from e

    def _read(self, r: httpx.Response) -> Iterator[Chunk]:
        """The server-sent events of one streamed reply."""
        start, first, usage = time.perf_counter(), None, {}
        calls: dict[int, dict] = {}
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError as e:
                raise BrainGarbled(f"unreadable reply from the service ({data[:80]})") from e
            if event.get("error"):
                raise BrainOffline(f"the service stopped with an error ({str(event['error'])[:200]})")
            usage = event.get("usage") or usage
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content") or ""
                thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "args": ""})
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        slot["args"] = json.dumps(args)
                    elif args:
                        slot["args"] += args
                if text or thinking:
                    first = first or time.perf_counter()
                    yield Chunk(text=text, thinking=thinking)
        parsed = []
        for slot in calls.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except ValueError as e:
                raise BrainGarbled(f"the model wrote a tool call that isn't JSON ({slot['args'][:80]})") from e
            parsed.append(ToolCall(slot["name"], args if isinstance(args, dict) else {}))
        end = time.perf_counter()
        first = first or end
        yield Chunk(calls=parsed, done=True, read_tokens=usage.get("prompt_tokens", 0) or 0, read_s=first - start,
                    wrote_tokens=usage.get("completion_tokens", 0) or 0, wrote_s=end - first)
