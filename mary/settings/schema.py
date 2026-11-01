"""The app settings the page shows, page by page: where each lives in the config, its label, a one-line explanation
and what kind of value it takes. The page draws its forms from this list, so showing one more setting is one line.

Only settings listed here can be changed from the page; anything else in config.yaml stays a file-only setting. The
assistant's own things (personality, voice, wake phrases…) aren't here: they belong to a persona (store.save_persona).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from mary import skills


@dataclass
class Field:
    path: str                   # "llm.model"
    label: str
    help: str = ""
    kind: str = "text"          # switch | number | choice | segmented | text | words | mic | skills | hotkey | model | url
    choices: list = field(default_factory=list)   # [value, label, (description)] for choice / segmented
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    advanced: bool = False      # folded away under "Advanced"

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", None, [], False) or k == "advanced"}


PAGES: dict[str, list[Field]] = {
    # The weather's place (weather.place) and how long a conversation is remembered (llm.history_reset_minutes)
    # are file-only settings: their defaults suit almost everyone. About you edits the profile (store.save_profile).
    "model": [
        Field("brain.provider", "Where it runs", kind="choice",
              choices=[["ollama", "Ollama: on this PC, or Ollama's cloud models"], ["api", "Another service"]]),
        Field("llm.model", "Ollama model", kind="model"),
        Field("brain.api.url", "Service address", "Its OpenAI-compatible address, usually ending in /v1.",
              kind="url"),
        Field("brain.api.model", "Model", "The service's name for it.", kind="model"),
        Field("llm.think_by_default", "Think before answering",
              "Better judgement on tricky requests, but replies take longer, several times longer on a CPU.",
              kind="switch"),
        Field("llm.think_level", "How hard to think", "For models that offer levels.", kind="segmented",
              choices=[["low", "Low"], ["medium", "Medium"], ["high", "High"]]),
        Field("brain.mode", "Request handling", kind="choice", advanced=True,
              choices=[["new", "Everything goes to the model (recommended)"],
                       ["today", "A router first, the model for the rest (older)"]]),
        Field("llm.unload_on_quit", "Free the model's memory on quit",
              "Off: it stays loaded, so the next start is faster.", kind="switch", advanced=True),
        Field("llm.num_thread", "CPU threads", "Usually your CPU's number of physical cores.", kind="number",
              min=1, max=64, step=1, advanced=True),
    ],
    "listening": [
        Field("wakeword.enabled", "Wake word", "Off: press the hotkey to talk.", kind="switch"),
        Field("hotkey.combo", "Hotkey", "Tap to talk, hold to talk as long as you like.", kind="hotkey"),
        Field("audio.input_device", "Microphone", kind="mic"),
        Field("audio.clean.enabled", "Reduce background noise",
              "Noise suppression, a rumble filter and automatic volume, like a browser's.", kind="switch"),
        Field("stt.provider", "Speech recognition", kind="segmented",
              choices=[["local", "On this PC", "Whisper. Free and private."],
                       ["elevenlabs", "ElevenLabs", "Scribe. Faster and more accurate; uses credits."]]),
        Field("stt.words", "Custom vocabulary",
              "Names that get misheard: apps, people, places. The assistant's own names are added for you.",
              kind="words"),
        Field("vad.followup_s", "Keep listening after a reply", "So you can follow up without the wake word.",
              kind="choice", choices=[[0, "Off"], [5, "5 seconds"], [8, "8 seconds"], [15, "15 seconds"]]),
        Field("audio.chime", "Listening chime", "A soft sound when it starts listening.", kind="switch"),
        Field("stt.model", "Speech model on this PC", kind="choice", advanced=True,
              choices=[["base.en", "base.en (faster)"], ["small.en", "small.en (more accurate)"]]),
        Field("vad.long_end_silence_ms", "Longest pause", "How long it waits for you to go on before answering.",
              kind="number", min=1500, max=10000, step=500, unit="ms", advanced=True),
    ],
    "skills": [
        Field("skills", "Skills", kind="skills"),
    ],
    # The Memory page shows what is remembered (mary/settings/memory.py); these are its settings.
    "photos": [
        Field("photos.enabled", "Look after my photos",
              "Read the folders you add (never before) and keep a small thumbnail and a record of each photo. The "
              "originals are never changed.", kind="switch"),
        Field("photos.read_text", "Read the words in photos",
              "Signs, menus, captions on scans. Read on this PC and kept only here, so you can search for them.",
              kind="switch"),
    ],
    "memory": [
        Field("memory.diary.enabled", "Keep a diary",
              "One line for each thing you ask: your words, the skills used and what was said, never what a skill read "
              "out. It stays on this PC.", kind="switch"),
        Field("memory.consolidate.enabled", "Learn from our conversations in the background",
              "When nobody is using it, the model reads what you said and writes down the lasting facts. It only sees "
              "your own words, never replies and never anything from messages or the screen.", kind="switch"),
        Field("memory.consolidate.reflections", "Write a short summary of each day",
              "A sentence or two about how each finished day went, from the diary.", kind="switch"),
    ],
}

FIELDS: dict[str, Field] = {f.path: f for page in PAGES.values() for f in page}
_SAFE_NAME = re.compile(r"^[\w.:/@+-]{1,120}$")  # a model or similar name
_URL = re.compile(r"^https?://[^\s\"'<>]{1,200}$")

# Starting points for a personality, written to the assistant like soul.md ("{name}" becomes its name). Each says
# what to do, never what not to do: a small model copies the words it reads.
PRESETS = [
    {"id": "warm", "label": "Warm friend",
     "text": "You are {name}: warm, kind and easy to talk to, like a trusted friend who happens to look after this "
             "computer. You notice how the user's day is going and it shows in small ways. You're honest and say "
             "plainly when something didn't work."},
    {"id": "pro", "label": "Calm professional",
     "text": "You are {name}: calm, capable and to the point, like an experienced assistant. You keep answers "
             "short and clear, confirm what you did in a sentence, and stay polite and steady when things go wrong."},
    {"id": "elder", "label": "Wise elder",
     "text": "You are {name}: patient and wise, with a gentle sense of humour and a lifetime of stories. You speak "
             "slowly and kindly, encourage the user, and share a little wisdom when it helps."},
    {"id": "cheerful", "label": "Cheerful",
     "text": "You are {name}: upbeat, playful and encouraging. You celebrate the user's small wins, keep things "
             "light, and still get the job done quickly."},
    {"id": "witty", "label": "Witty",
     "text": "You are {name}: quick-witted with a dry sense of humour, but always helpful first. You keep jokes "
             "short and kind, and you're straight with the user when something didn't work."},
]


def check(path: str, value):
    """The value, cleaned, if it fits the setting; else ValueError with a sentence for the page."""
    f = FIELDS.get(path)
    if f is None:
        raise ValueError(f"'{path}' can't be changed from this page.")
    if f.kind == "switch":
        if not isinstance(value, bool):
            raise ValueError(f"{f.label}: on or off.")
        return value
    if f.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{f.label}: a number.")
        if (f.min is not None and value < f.min) or (f.max is not None and value > f.max):
            raise ValueError(f"{f.label}: between {f.min:g} and {f.max:g}.")
        return int(value) if float(value).is_integer() and (f.step or 1) >= 1 else float(value)
    if f.kind in ("choice", "segmented"):
        if value not in [c[0] for c in f.choices]:
            raise ValueError(f"{f.label}: pick one of the options.")
        return value
    if f.kind == "model":
        if not isinstance(value, str) or not (_SAFE_NAME.match(value) or (value == "" and f.path != "llm.model")):
            raise ValueError(f"{f.label}: that isn't a model name.")
        return value
    if f.kind == "url":
        value = value.strip().rstrip("/") if isinstance(value, str) else None
        if value is None or (value and not _URL.match(value)):
            raise ValueError(f"{f.label}: an address starting with http:// or https://.")
        return value
    if f.kind == "hotkey":
        from mary.hotkey import parse_combo

        try:
            parse_combo(value if isinstance(value, str) else "")
        except ValueError:
            raise ValueError("Hotkey: that key can't be used. Try one like Ctrl+Space.") from None
        return value.lower()
    if f.kind == "words":
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError(f"{f.label}: up to 100 words.")
        words = [re.sub(r"\s+", " ", str(w)).strip() for w in value]
        if any(len(w) > 50 for w in words):
            raise ValueError(f"{f.label}: each at most 50 letters.")
        return list(dict.fromkeys(w for w in words if w))
    if f.kind == "skills":
        if not isinstance(value, list) or not all(isinstance(s, str) and s in skills.ALL for s in value):
            raise ValueError(f"{f.label}: unknown skill.")
        return [s for s in skills.ALL if s in value]  # in the order the tools are listed to the model
    if f.kind == "mic":
        if value is None or (isinstance(value, str) and 0 < len(value) <= 200 and "\n" not in value):
            return value
        raise ValueError(f"{f.label}: pick one from the list.")
    if not isinstance(value, str) or len(value) > 200 or "\n" in value:
        raise ValueError(f"{f.label}: one line of text.")
    return value.strip()
