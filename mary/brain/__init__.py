"""The brain socket: the one place a language model plugs into MARY.

Everything else (ears, voice, skills, the safety gate) talks to a Brain and never to a particular model or
server, so a better brain can replace the local one by changing `brain.provider` in config.yaml. A brain:
  start()      makes sure it can answer (starts its server, checks its model); raises BrainOffline if not
  chat(...)    streams one reply as Chunks: text, thinking, tool calls, and token timings at the end
  reachable()  a quick check, used while MARY waits for an offline brain to come back
  restart()    after a dropped connection: bring it back, or raise BrainOffline
  unload()     free its memory when MARY quits (optional)
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

    def start(self) -> None:
        raise NotImplementedError

    def chat(self, messages: list[dict], tools: list[dict], think: bool = False,
             max_tokens: int | None = None) -> Iterator[Chunk]:
        raise NotImplementedError

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
    raise ValueError(f"unknown brain.provider '{provider}' in config.yaml")
