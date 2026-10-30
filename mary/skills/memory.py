"""memory: remember / forget facts about the user, set their name, list what's remembered."""
from __future__ import annotations

from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = '"Remember that…", "forget that…", "call me…": memory.'
GROUNDING = [
    ("memory", lambda a: a.get("action") == "forget",
     r"\b(forget|stop remembering|remove|delete|erase)\b", "the user didn't ask to forget anything"),
    ("memory", lambda a: a.get("action") == "set_name",
     r"\b(name|call me|called)\b", "the user didn't say what to call them"),
    ("memory", lambda a: a.get("action") == "remember",
     r"\b(remember|note|keep in mind|don'?t forget|memori[sz]e)\b", "the user didn't ask MARY to remember anything"),
]

ACTIONS = ["remember", "forget", "set_name", "list"]


def tools(ctx: ToolContext) -> list[Tool]:
    def memory(action: str, fact: str = "") -> ToolResult:
        profile = ctx.profile
        if profile is None:
            return ToolResult(False, "No profile is loaded.")
        if action == "remember":
            if not fact.strip():
                return ToolResult(False, "remember needs the fact to keep.")
            result = profile.remember(fact)
            if result == "already":
                return ToolResult(True, "That was already remembered.", say="I already knew that.")
            return ToolResult(True, f"Remembered: {fact}", say="Got it, I'll remember that.")
        if action == "forget":
            gone = profile.forget(fact)
            if gone is None:
                return ToolResult(False, f"Nothing remembered matches '{fact}'.",
                                  say="I don't have anything like that remembered.")
            return ToolResult(True, f"Forgot: {gone}", say="Done, I've forgotten that.")
        if action == "set_name":
            if not fact.strip():
                return ToolResult(False, "set_name needs the name.")
            profile.set_name(fact)
            return ToolResult(True, f"Name set to {profile.name}.", say=f"Nice to meet you, {profile.name}.")
        if action == "list":
            items = ([f"Name: {profile.name}"] if profile.name else []) + profile.facts
            return ToolResult(True, "Remembered: " + ("; ".join(items) if items else "nothing yet"))
        return ToolResult(False, f"action must be one of {ACTIONS}.")

    return [Tool(
        "memory",
        "Remember a fact or preference about the user (\"remember that I prefer Edge\"), forget one, "
        "set the name they want to be called, or list what's remembered.",
        {"action": {"type": "string", "enum": ACTIONS},
         "fact": {"type": "string", "description": "the fact, the thing to forget, or the name"}},
        ["action"], memory, speaks_for_itself=True, private=True,
        announce=lambda a: {"remember": "Remembering…", "forget": "Forgetting…", "set_name": "Noting your name…",
                            "list": "Checking what I remember…"}.get(a.get("action"), "Memory…"),
    )]
