"""Per-request stage timing: wake -> STT -> router -> LLM first token -> tool -> TTS first audio."""
from __future__ import annotations

import time
from contextlib import contextmanager


class Latency:
    def __init__(self, label: str = ""):
        self.label = label
        self.t0 = time.perf_counter()
        self.stages: list[tuple[str, float]] = []   # (name, duration ms)
        self.marks: list[tuple[str, float]] = []    # (name, ms since start)
        self.notes: list[str] = []                   # free-form detail, e.g. LLM token counts

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages.append((name, (time.perf_counter() - start) * 1000))

    def mark(self, name: str) -> None:
        """Record a point in time (e.g. the first LLM token), once per name."""
        if not any(n == name for n, _ in self.marks):
            self.marks.append((name, (time.perf_counter() - self.t0) * 1000))

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def as_dict(self) -> dict:
        return {"label": self.label, "total_ms": round(self.total_ms),
                "stages": [[n, round(ms)] for n, ms in self.stages],
                "marks": [[n, round(ms)] for n, ms in self.marks], "notes": self.notes}

    def table(self) -> str:
        rows = [f"    {n:<24}{ms:>8.0f} ms" for n, ms in self.stages]
        rows += [f"    @{n:<23}{ms:>8.0f} ms" for n, ms in self.marks]
        rows.append(f"    {'total':<24}{self.total_ms:>8.0f} ms")
        rows += [f"    · {note}" for note in self.notes]
        return "  latency:\n" + "\n".join(rows)
