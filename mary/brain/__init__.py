"""The brain socket: the one place a language model plugs into MARY.

Everything else (ears, voice, skills, the safety gate) talks to a Brain and never to a particular model or
server, so a better brain can replace the local one by changing `brain.provider` in config.yaml. A brain:
  start()      makes sure it can answer (starts its server, checks its model); raises BrainOffline if not
  chat(...)    streams one reply as Chunks: text, thinking, tool calls, and token timings at the end
  json(...)    one answer that fits a JSON schema (data for a job, not talk)
  reachable()  a quick check, used while MARY waits for an offline brain to come back
  restart()    after a dropped connection: bring it back, or raise BrainOffline
  unload()     free its memory when MARY quits (optional)
  local        True when the model runs on this PC: MARY then pre-reads the conversation between requests to keep its
               cache warm. A cloud brain skips that, since every request costs a call.

Providers: `ollama` (mary/brain/ollama.py: a model on this PC, or one of Ollama's cloud models) and `api`
(mary/brain/api.py: any service that speaks the OpenAI chat API).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


def mode(cfg) -> str:
    """brain.mode: "new" (the model is the brain; the default) or "today" (the old router path)."""
    brain = cfg.get("brain") if isinstance(cfg, dict) else None
    return (brain or {}).get("mode", "new")


class BrainOffline(RuntimeError):
    """The brain can't be reached, or its model is missing. MARY's instant commands still work."""


class BrainLost(ConnectionError):
    """The connection dropped mid-request: worth one retry after restart()."""


class BrainGarbled(RuntimeError):
    """The model's reply couldn't be understood (e.g. a tool call it wrote badly): worth one retry as it is."""


def parse_json(text: str) -> dict:
    """The JSON object a model wrote (it may wrap it in a code fence or add a word around it); BrainGarbled if none."""
    import json
    import re

    text = (text or "").strip()
    for candidate in (text, *(m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S)),
                      text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise BrainGarbled(f"the model's reply wasn't JSON: {text[:80]!r}")


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Chunk:
    text: str = ""
    thinking: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    read_tokens: int = 0     # prompt tokens the brain had to read this call (cached ones don't count)
    read_s: float = 0.0
    wrote_tokens: int = 0
    wrote_s: float = 0.0


class Brain:
    name = "brain"
    local = True

    def start(self) -> None:
        raise NotImplementedError

    def chat(self, messages: list[dict], tools: list[dict], think: bool = False,
             max_tokens: int | None = None) -> Iterator[Chunk]:
        raise NotImplementedError

    def json(self, messages: list[dict], schema: dict, max_tokens: int = 1200) -> dict:
        """One answer that must fit `schema` (no tools, no thinking, not streamed): for jobs that need data, not talk,
        like the memory's background pass. Raises BrainGarbled if what came back isn't JSON, BrainOffline or BrainLost
        as chat() does."""
        raise NotImplementedError(f"{self.name} can't answer in JSON")

    def reachable(self, timeout: float = 2.0) -> bool:
        return True

    def restart(self) -> None:
        pass

    def unload(self) -> None:
        pass


def make_brain(cfg) -> Brain:
    """The brain named in config.yaml (brain.provider)."""
    provider = (cfg.get("brain") or {}).get("provider", "ollama")
    if provider == "ollama":
        from mary.brain.ollama import OllamaBrain

        return OllamaBrain(cfg.llm)
    if provider == "api":
        from mary.brain.api import ApiBrain

        return ApiBrain((cfg.get("brain") or {}).get("api") or {}, cfg.llm)
    raise ValueError(f"unknown brain.provider '{provider}' in config.yaml")
