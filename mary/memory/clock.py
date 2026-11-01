"""The one clock the memory reads, so tests and replays can live several days in a few seconds."""
from __future__ import annotations

from datetime import datetime, timedelta

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now() + _offset


def advance(days: float = 0, hours: float = 0, minutes: float = 0) -> None:
    """Move this clock forward (a replay's "next day")."""
    global _offset
    _offset += timedelta(days=days, hours=hours, minutes=minutes)


def reset() -> None:
    global _offset
    _offset = timedelta(0)
