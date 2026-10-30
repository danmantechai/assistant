"""RAM usage of everything MARY runs: this process, the Ollama server/runner, HUD webview."""
from __future__ import annotations

import os

import psutil

_GROUPS = {
    "ollama": ("ollama.exe", "ollama app.exe", "llama-server.exe"),
    "hud webview": ("msedgewebview2.exe", "qtwebengineprocess.exe"),
}


def _mb(n: int) -> float:
    return n / (1024 * 1024)


def memory_report() -> str:
    me = psutil.Process(os.getpid())
    my_tree = {me.pid} | {c.pid for c in me.children(recursive=True)}
    rows = [("MARY (python)", me.memory_info().rss, me.memory_full_info().uss)]
    for label, names in _GROUPS.items():
        rss = uss = 0
        for p in psutil.process_iter(["name"]):
            name = (p.info["name"] or "").lower()
            # Only count webview processes MARY started; Ollama always runs as its own tree.
            if name in names and (label == "ollama" or p.pid in my_tree):
                try:
                    rss += p.memory_info().rss
                    uss += p.memory_full_info().uss
                except psutil.Error:
                    pass
        if rss:
            rows.append((label, rss, uss))
    vm = psutil.virtual_memory()
    lines = ["  memory (working set / private):"]
    lines += [f"    {label:<16}{_mb(rss):>8.0f} MB {_mb(uss):>8.0f} MB" for label, rss, uss in rows]
    lines.append(f"    {'total':<16}{_mb(sum(r[1] for r in rows)):>8.0f} MB "
                 f"{_mb(sum(r[2] for r in rows)):>8.0f} MB")
    lines.append(f"    system: {_mb(vm.used) / 1024:.1f} of {_mb(vm.total) / 1024:.1f} GB in use "
                 f"({_mb(vm.available) / 1024:.1f} GB available)")
    return "\n".join(lines)
