"""The diary: one line per thing that happened, written by code after every request (no model): what was asked, which
tools she used, and what she said. It is searched, never always loaded.

What goes in is decided here, in one place:
  - the user's own words, as said (cut at a length), and her reply (the first sentences, cut at a length)
  - the names of the tools she used, never what they returned: a screen, a file, a message stays out
  - when any tool used was private (messages, screen text) the reply is not kept either: it would repeat what the tool
    read out
  - the same request again soon after (volume up, volume up) is one line with a count, not many
  - quick replies ("thanks", "bye") and failed requests are not a thing that happened: no line
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from mary.memory import clock
from mary.memory.store import Memory, MemoryStore, clean

REPEAT_WITHIN = timedelta(minutes=10)


@dataclass
class Did:
    """One tool call of a request, as the registry saw it (never its result)."""
    name: str
    ok: bool
    private: bool = False


def cut(text: str, limit: int) -> str:
    text = clean(text)
    if len(text) <= limit:
        return text
    head = text[:limit]
    stop = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
    return (head[:stop + 1] if stop > limit // 2 else head.rstrip(" ,;:") + "…")


def line(request: str, did: list[Did], reply: str, request_chars: int = 240, reply_chars: int = 160,
         repeats: int = 1) -> str:
    """The diary line for one request."""
    said = request.replace('"', "'")
    count = f" (×{repeats})" if repeats > 1 else ""
    parts = [f'Asked: "{cut(said, request_chars)}"{count}.']
    if did:
        names: dict[str, bool] = {}
        for d in did:
            names[d.name] = names.get(d.name, True) and d.ok
        used = ", ".join(n + ("" if ok else " (failed)") for n, ok in names.items())
        private = any(d.private for d in did)
        parts.append(f"Used: {used}" + (" (private; reply not kept)." if private else "."))
    elif not reply:
        return parts[0]
    if reply and not any(d.private for d in did):
        parts.append(f'Replied: "{cut(reply.replace(chr(34), chr(39)), reply_chars)}".')
    return " ".join(parts)


class Diary:
    def __init__(self, store: MemoryStore):
        self.store = store
        self._last: tuple[str, int, int, datetime] | None = None   # signature, record id, repeats, when

    def record(self, request: str, did: list[Did], reply: str, when: datetime | None = None) -> Memory | None:
        """Write the line for a request that was answered (the caller skips quick replies and failures)."""
        if not clean(request) or (not did and not clean(reply)):
            return None
        s = self.store
        when = when or clock.now()
        request_chars, reply_chars = s.setting("diary.request_chars"), s.setting("diary.reply_chars")
        signature = re.sub(r"\W+", " ", request.lower()).strip() + "|" + ",".join(d.name for d in did)
        if self._last and self._last[0] == signature and when - self._last[3] <= REPEAT_WITHIN:
            _, mid, n, _ = self._last
            if s.get(mid) is not None:
                n += 1
                s.edit(mid, line(request, did, reply, request_chars, reply_chars, repeats=n))
                self._last = (signature, mid, n, when)
                return s.get(mid)
        text = line(request, did, reply, request_chars, reply_chars)
        m = s.add("diary", text, happened=when, importance=0.4 if did else 0.25, source="diary")
        self._last = (signature, m.id, 1, when)
        return m
