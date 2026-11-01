"""run_powershell: the terminal as MARY's general-purpose tool, for anything a command can do.

Commands that only look (reading, listing, measuring) run at once; anything that changes something shows the command
and asks first. PowerShell's own parser decides which is which (mary/terminal.py), and blocklisted patterns are
refused outright. The output comes back trimmed and structured: objects as short tables.
"""
from __future__ import annotations

from mary import terminal
from mary.tools.registry import Tool, ToolContext, ToolResult
from mary.tools.safety import powershell_block_reason

GUIDE = ("Anything a command can do on this PC (folder sizes, finding, listing or renaming files, installed apps, "
         "settings, network, processes, disks): run_powershell, rather than clicking through windows. Commands that "
         "only look run at once; anything that changes something is shown to the user and asked first.\n"
         "Screen tools (clicking, reading the screen) are only for what has no other way in: an app or page with no "
         "command for it, or something that has to be seen.")


def tools(ctx: ToolContext) -> list[Tool]:
    safety = ctx.cfg.safety
    limit = ctx.cfg.llm.tool_result_chars

    def run_powershell(command: str) -> ToolResult:
        ran = terminal.run(command, safety.powershell_timeout_s, limit)
        return ToolResult(ran.ok, ran.text)

    def changes(args: dict) -> bool:
        return not terminal.looks_only(args.get("command", ""))

    return [Tool(
        "run_powershell",
        "Run a Windows PowerShell command or pipeline and get its output back as a short table. Use it for anything "
        "a command can do. Commands that only read, list or measure run at once; anything that changes something is "
        "shown to the user and asked first. Deleting files, registry edits and security changes are refused.",
        {"command": {"type": "string", "description": "a single PowerShell command or pipeline"}},
        ["command"], run_powershell, tier=lambda a: 2 if changes(a) else 1,
        announce=lambda a: "Running PowerShell…",
        confirm_text=lambda a: f"run this PowerShell command: {a.get('command', '')}",
        precheck=lambda a: powershell_block_reason(a.get("command", ""), safety.powershell_blocklist),
        watch=changes,  # a command that only looks touches no window; one that changes something may open one
    )]
