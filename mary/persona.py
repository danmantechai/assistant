"""The assistant's persona: who they are, how they talk and write, their voice, wake words and stories, in one folder.

    personas/<name>/            (or user/personas/<name>/ for a private one, which wins)
      persona.yaml   name, nicknames (what the user calls them), wake words, wake replies, greeting, voice
      soul.md        the personality: values, warmth, humour, boundaries
      speech.md      how they talk out loud, with example exchanges
      texts/*.txt    real messages they wrote, shown as examples whenever she writes (how they write is part of
                     soul.md; a texting.md from an older pack is still read but not used)
      lore/*.md      stories, sayings, family facts; each starts with "keywords: a, b, c" and is only brought
                     up when one of those words is said

`persona: <name>` in user/config.yaml picks one (default: mary). soul.md and speech.md become part of the system
prompt, which never changes while MARY runs (it's the cached part). Lore and text examples are added to a single
request's context, and only when they fit, so they cost nothing the rest of the time. The settings page edits all of
it (mary/settings).
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from mary.config import PROJECT_DIR, user_dir

log = logging.getLogger(__name__)
SHIPPED = PROJECT_DIR / "personas"
DEFAULT = "mary"
_GREETERS = r"hey|hi|hay|ok|okay"


@dataclass
class Wake:
    """One wake phrase and how speech recognition tends to write it."""
    phrase: str                                        # "Hey Mom"
    model: str                                         # "hey_mom": data/models/wakeword/hey_mom.onnx, or a built-in
    names: list[str] = field(default_factory=list)     # the name as it gets written: mom, mum, mommy…
    alone: list[str] = field(default_factory=list)     # said on its own, these count as the wake phrase too
    misheard: list[str] = field(default_factory=list)  # whole mishearings of the phrase ("amen" for "Hey Mary")
    bare: bool = False   # the name alone opening a request is the wake phrase ("Mary, open…"); off for words
                         # like "Mom" that start ordinary sentences ("Mum's birthday is on Friday")

    @property
    def greeted(self) -> bool:
        """Does the phrase open with a greeting ("Hey Mom"), or is it the name alone ("Alexa")?"""
        return bool(re.match(rf"(?i)^(?:{_GREETERS})\b", self.phrase.strip()))

    def _names(self) -> str:
        default = re.sub(rf"(?i)^(?:{_GREETERS})\W*", "", self.phrase.strip()).lower() or self.phrase.lower()
        return "|".join(map(re.escape, self.names or [default]))

    def confirm_pattern(self) -> re.Pattern:
        """What the second-opinion check must hear for a detection to count."""
        names, alone = self._names(), "|".join(map(re.escape, self.alone))
        if not self.greeted:  # "Alexa": the name is the whole phrase
            return re.compile(rf"\b(?:{names})\b", re.I)
        pattern = rf"\b(?:{_GREETERS}|a|your|yo|here|hear)\W*(?:{names})\b"
        return re.compile(pattern + (rf"|^\W*(?:{alone})\W*$" if alone else ""), re.I)

    def strip_part(self) -> str:
        names = self._names()
        greeted = rf"(?:{_GREETERS})[\s,]*(?:{names})"
        loose = rf"(?:(?:{_GREETERS})[\s,]*)?(?:{names})"
        parts = [loose if self.bare or not self.greeted else greeted]
        parts += [re.escape(m) for m in self.misheard]
        return "|".join(parts)


@dataclass
class Lore:
    name: str
    keywords: list[str]
    text: str


@dataclass
class Persona:
    key: str
    folder: Path | None
    name: str = "MARY"
    nicknames: list[str] = field(default_factory=list)  # what the user calls them ("Mom", "Nana")
    wake: list[Wake] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    morning_replies: list[str] = field(default_factory=list)
    greeting: str = "{part}, {name}. I'm ready when you are."
    greeting_no_name: str = "{part}. MARY here, ready when you are."
    voice: dict = field(default_factory=dict)
    soul: str = ""
    speech: str = ""
    texting: str = ""
    lore: list[Lore] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ the parts of her prompt

    def prompt_section(self) -> str:
        """soul.md then speech.md, for the system prompt."""
        return "\n\n".join(p for p in (self.soul, self.speech) if p)

    def extras(self, request: str, writing: bool = False, limit: int = 3) -> list[str]:
        """Lines for one request's context: lore whose keywords were said, and (when she'll write text, or always
        in text mode) a couple of her real messages as examples of how she writes."""
        lines = []
        low = request.lower()
        for entry in self.lore:
            if any(re.search(rf"\b{re.escape(k.lower())}\b", low) for k in entry.keywords):
                lines.append(f"from your memories ({entry.name}): {entry.text}")
                if len(lines) >= limit:
                    break
        if writing and self.texts:
            picks = random.sample(self.texts, min(2, len(self.texts)))
            lines.append("how you write (examples, not facts): " + " | ".join(t.replace("\n", " ") for t in picks))
        return lines

    # ------------------------------------------------------------------ names and wake words

    def fix_address(self, text: str) -> str:
        """Drop her own name used as the user's ("Got it, Mom." -> "Got it."; "Mom, I can't…" -> "I can't…").
        "Your mom's birthday" and other mentions stay."""
        if not self.nicknames:
            return text
        names = "|".join(map(re.escape, self.nicknames))
        text = re.sub(rf",\s*(?:{names})(?=\s*[.!?,;:]|\s*$)", "", text)
        text = re.sub(rf"^(\W*)(?:{names}),\s*(\w)", lambda m: m.group(1) + m.group(2).upper(), text)
        return text

    def wake_pattern(self) -> re.Pattern:
        parts = [w.strip_part() for w in self.wake] or ["(?!)"]
        return re.compile(rf"^\W*(?:{'|'.join(parts)})\b[\s,.!?]*", re.I)

    def wake_for(self, key: str) -> Wake | None:
        """The wake phrase of a detector key ('hey_mom' or a path ending in it)."""
        stem = Path(key).stem.lower()
        return next((w for w in self.wake if w.model.lower() == stem or w.model.lower() in key.lower()), None)

    def config(self) -> dict:
        """The settings this persona brings (applied over config.yaml, under your own user/config.yaml)."""
        out: dict = {}
        if self.voice:
            out["tts"] = dict(self.voice)
        if self.wake:
            paths = [_model_path(w.model) for w in self.wake]
            out["wakeword"] = {"model": paths[0], "also": paths[1:]}
        if self.replies:
            out.setdefault("wakeword", {})["replies"] = list(self.replies)
        if self.morning_replies:
            out.setdefault("wakeword", {})["morning_replies"] = list(self.morning_replies)
        return out

    def words(self) -> list[str]:
        """Names speech recognition should expect: her wake phrases and what the user calls her (not two-letter
        ones like "Ma": as key terms they'd pull ordinary sounds toward them)."""
        return list(dict.fromkeys([w.phrase for w in self.wake] + [n for n in self.nicknames if len(n) > 2]))


_BUILT_IN = {"hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy"}  # openWakeWord's own, used by name


def _model_path(model: str) -> str:
    """'hey_mom' -> the model trained on your voice (data/models/wakeword/hey_mom.onnx); a built-in name or a
    path as it is."""
    if model in _BUILT_IN or model.endswith(".onnx"):
        return model
    return f"{{project}}/data/models/wakeword/{model}.onnx"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _lore(folder: Path) -> list[Lore]:
    entries = []
    for f in sorted((folder / "lore").glob("*.md")):
        if f.name.startswith("_"):
            continue  # an example of the format (the template's), never brought up
        text = _read(f)
        m = re.match(r"(?is)^\s*keywords:\s*(.+?)\n(.*)$", text)
        if not m:
            log.warning("lore %s has no 'keywords:' first line; skipped", f.name)
            continue
        keywords = [k.strip() for k in m.group(1).split(",") if k.strip()]
        entries.append(Lore(f.stem.replace("_", " "), keywords, re.sub(r"\s+", " ", m.group(2)).strip()))
    return entries


def _texts(folder: Path) -> list[str]:
    texts = []
    for f in sorted((folder / "texts").glob("*.txt")):
        texts += [t.strip() for t in re.split(r"\n\s*\n", _read(f)) if t.strip()]
    return texts


def find(key: str) -> Path | None:
    """Your own persona of that name first (user/personas), then one that ships with MARY."""
    for root in (user_dir() / "personas", SHIPPED):
        if (root / key / "persona.yaml").exists():
            return root / key
    return None


@lru_cache(maxsize=8)
def _load(key: str, folder: Path | None) -> Persona:
    if folder is None:
        return Persona(key, None)
    data = yaml.safe_load(_read(folder / "persona.yaml")) or {}
    wake = [Wake(**w) for w in data.get("wake") or []]
    nicknames = data.get("nicknames") or data.get("user_calls_her") or []  # user_calls_her: the older name
    p = Persona(key, folder, name=data.get("name") or "MARY", nicknames=list(nicknames),
                wake=wake, replies=list(data.get("replies") or []),
                morning_replies=list(data.get("morning_replies") or []), voice=dict(data.get("voice") or {}),
                soul=_read(folder / "soul.md"), speech=_read(folder / "speech.md"),
                texting=_read(folder / "texting.md"), lore=_lore(folder), texts=_texts(folder))
    p.greeting = data.get("greeting") or p.greeting
    p.greeting_no_name = data.get("greeting_no_name") or p.greeting_no_name
    return p


def load(key: str | None = None) -> Persona:
    """The persona called `key` (default: mary). An unknown name falls back to mary, with a warning."""
    key = key or DEFAULT
    folder = find(key)
    if folder is None and key != DEFAULT:
        log.warning("persona %r not found in user/personas or personas; using %r", key, DEFAULT)
        key, folder = DEFAULT, find(DEFAULT)
    return _load(key, folder)


def of(cfg) -> Persona:
    """The persona a loaded config names."""
    return load(cfg.get("persona") if isinstance(cfg, dict) else None)


if __name__ == "__main__":  # python -m mary.persona list | new | import-whatsapp | export-card | import-card
    from mary.persona_io import main

    main()
