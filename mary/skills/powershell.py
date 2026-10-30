"""run_powershell: always confirmed, and blocklisted patterns are refused outright."""
from __future__ import annotations

import subprocess

from mary.tools.registry import Tool, ToolContext, ToolResult
from mary.tools.safety import powershell_block_reason

GUIDE = "Anything else on the PC that needs a command: run_powershell (it shows him the command and asks first)."


def tools(ctx: ToolContext) -> list[Tool]:
    safety = ctx.cfg.safety
    limit = ctx.cfg.llm.tool_result_chars

    def run_powershell(command: str) -> ToolResult:
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=safety.powershell_timeout_s,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Timed out after {safety.powershell_timeout_s} s.")
        out = proc.stdout.decode("utf-8", "replace").strip()
        err = proc.stderr.decode("utf-8", "replace").strip()
        text = f"exit code {proc.returncode}\n{out}" + (f"\nerrors:\n{err}" if err else "")
        if len(text) > limit:
            text = text[:limit] + "\n…(truncated)"
        return ToolResult(proc.returncode == 0, text)

    return [Tool(
        "run_powershell",
        "Run a Windows PowerShell command and return its output. Only for things no other tool "
        "can do. Deleting files, registry edits and security changes are refused.",
        {"command": {"type": "string", "description": "a single PowerShell command or pipeline"}},
        ["command"], run_powershell, tier=2,
        announce=lambda a: "Running PowerShell…",
        confirm_text=lambda a: f"run this PowerShell command: {a.get('command', '')}",
        precheck=lambda a: powershell_block_reason(a.get("command", ""), safety.powershell_blocklist),
    )]
