"""What MARY remembers about you: user/profile.yaml (your name, what you wrote about yourself on the settings page,
and facts you asked her to keep).

The agent sends the profile as a pinned first exchange right after its cached prefix, in your own
words, so a change costs one short re-read and ordinary requests cost nothing extra.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
from datetime import date
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process, utils

from mary.config import PROJECT_DIR, user_dir


def profile_path() -> Path:
    """user/profile.yaml; one kept in the project folder by an older MARY is moved there once."""
    path = user_dir() / "profile.yaml"
    old = PROJECT_DIR / "profile.yaml"
    if not path.exists() and old.exists() and "MARY_USER_DIR" not in os.environ:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(path))
    return path
_HEADER = """\
# What MARY knows about you. She adds to this when you say "remember that …" and removes things
# when you say "forget that …". You can also edit it by hand; MARY reads it when she starts.
# It stays on this PC.

"""


_STOPWORDS = {"the", "and", "that", "this", "what", "about", "said", "told", "you", "your", "for", "with",
              "are", "was", "were", "it's", "its", "have", "has", "had", "not", "but", "from", "all"}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) >= 3 and w not in _STOPWORDS}


class Profile:
    def __init__(self, path: Path | None = None):
        self.path = path or profile_path()
        self._lock = threading.Lock()
        self.listeners = []  # called after a change (the agent rebuilds its prompt)
        data = {}
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.name: str = str(data.get("name") or "").strip()
        self.about: str = str(data.get("about") or "").strip()   # in your own words (the settings page)
        self.facts: list[str] = [str(f).strip() for f in data.get("facts") or [] if str(f).strip()]
        # What she was asked to forget: kept with the date, never silently lost (older MARYs ignore this key).
        self.ended: list[dict] = [e for e in data.get("ended") or [] if isinstance(e, dict) and e.get("fact")]

    def _save(self) -> None:
        data = {"name": self.name, **({"about": self.about} if self.about else {}), "facts": self.facts,
                **({"ended": self.ended} if self.ended else {})}
        body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_HEADER + body, encoding="utf-8")
        for listener in self.listeners:
            listener()

    def remember(self, fact: str) -> str:
        fact = re.sub(r"\s+", " ", fact).strip().rstrip(".")
        fact = fact[0].upper() + fact[1:] if fact else fact
        with self._lock:
            if self.facts:
                best = process.extractOne(fact, self.facts, scorer=fuzz.token_sort_ratio,
                                           processor=utils.default_process)
                if best and best[1] >= 90:
                    return "already"
            self.facts.append(fact)
            self._save()
        return "saved"

    def forget(self, about: str) -> str | None:
        """Remove the remembered fact that best matches `about`; returns it, or None.

        Only facts sharing a real word with `about` qualify: fuzzy scores alone rate "it" as a close
        match for any fact containing "it", and "forget it" must never wipe a memory."""
        words = _content_words(about)
        with self._lock:
            candidates = [i for i, f in enumerate(self.facts) if words & _content_words(f)]
            if not candidates:
                return None
            best = process.extractOne(about, [self.facts[i] for i in candidates], scorer=fuzz.WRatio,
                                       processor=utils.default_process)
            if not best or best[1] < 60:
                return None
            self.facts.pop(candidates[best[2]])
            self.ended.append({"fact": best[0], "ended": date.today().isoformat(), "why": "forgotten"})
            self._save()
            return best[0]

    def forget_exact(self, fact: str, why: str = "forgotten") -> bool:
        """End this very fact (the memory store found which one was meant). False if it isn't there."""
        with self._lock:
            if fact not in self.facts:
                return False
            self.facts.remove(fact)
            self.ended.append({"fact": fact, "ended": date.today().isoformat(), "why": why})
            self._save()
            return True

    def erase(self, fact: str) -> bool:
        """Remove a fact and every trace of it here: the fact, and its line in what was forgotten."""
        with self._lock:
            had = fact in self.facts or any(e["fact"] == fact for e in self.ended)
            self.facts = [f for f in self.facts if f != fact]
            self.ended = [e for e in self.ended if e["fact"] != fact]
            if had:
                self._save()
            return had

    def set_name(self, name: str) -> None:
        with self._lock:
            self.name = name.strip().strip(".").title()
            self._save()

    def prompt_section(self) -> str:
        """The profile as the user's own words for the agent's pinned first exchange ("" = nothing yet)."""
        lines = []
        if self.name:
            lines.append(f"- Call me {self.name} (now and then, not in every reply).")
        lines += [f"- {fact}" for fact in self.facts]
        if not lines and not self.about:
            return ""
        out = "Background, not a request."
        if self.about:
            out += f" About me, in my own words: {self.about}\n"
        if lines:
            out += ("" if self.about else " ") + ("Things I asked you to remember about me; use them when they're "
                                                  "relevant (for example which browser or music I prefer):\n"
                                                  + "\n".join(lines))
        return out.rstrip()
