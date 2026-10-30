"""Slot extraction: turn a matched request into tool arguments, or say why it can't.

Each extractor gets the request text (and the intent's strip patterns) and returns a Slot. An
unresolved slot sends the request to the LLM instead, with the router's guess as a hint.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from mary.parsers import parse_duration, parse_level

# Words that carry no meaning for slot values ("could you please open spotify for me").
_FILLER = re.compile(r"\b(hey|hi|ok|okay)?\s*mary\b|\b(please|kindly|can you|could you|would you|will you|"
                     r"i want you to|i'd like you to|go ahead and|quickly|right now|now|real quick|for me)\b", re.I)


@dataclass
class Slot:
    ok: bool
    args: dict = field(default_factory=dict)
    fill: dict = field(default_factory=dict)   # values for reply templates
    why: str = ""                              # reason when not ok
    reply: str | None = None                   # tool-less intents answer directly
    tool: str | None = None                    # run this tool instead of the intent's own


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'#.]", " ", text)).strip(" .")


def remainder(text: str, strip: list[str]) -> str:
    """The request minus filler and the intent's own phrasing ('open', 'fire up', ...)."""
    out = _FILLER.sub(" ", text.lower())
    for pattern in strip:
        out = re.sub(pattern, " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" .,!?'\"")


class SlotFiller:
    """Holds what the extractors need: the app index, known websites, open windows."""

    def __init__(self, cfg, apps, ctx=None):
        self.cfg = cfg
        self.apps = apps
        self.ctx = ctx  # the tool context: "open the newest one" needs the last file search's results
        self.sites = {k.lower(): v for k, v in (cfg.web.sites or {}).items()}
        self.extractors: dict[str, Callable[[str, list[str]], Slot]] = {
            "none": lambda text, strip: Slot(True),
            "lock": lambda text, strip: Slot(True, {"action": "lock"}),
            "app": self.app, "site": self.site, "running_app": self.running_app, "window": self.window,
            "volume": self.volume, "brightness": self.brightness, "media": self.media,
            "music_query": self.music_query, "time_date": self.time_date, "status": self.status,
            "search_query": self.search_query, "timer": self.timer, "files": self.files,
            "found_file": self.found_file, "folder": self.folder, "weather": self.weather,
            "click": self.click, "scroll": self.scroll,
            "dictation": self.dictation, "remember": self.remember, "forget": self.forget, "name": self.name,
        }

    def extract(self, kind: str, text: str, strip: list[str]) -> Slot:
        return self.extractors[kind](text, strip)

    # ------------------------------------------------------------------ apps, sites, windows

    def app(self, text: str, strip: list[str]) -> Slot:
        name = remainder(text, strip)
        if not name:
            return Slot(False, why="no app name")
        match = self.apps.match(name)
        if match.app is None:
            return Slot(False, why=f"no confident app match for '{name}' (candidates: {match.candidates[:3]})")
        return Slot(True, {"name": name}, {"app": match.app.name})

    def site(self, text: str, strip: list[str]) -> Slot:
        name = remainder(text, strip)
        key = re.sub(r"\s+(web|dot com)$", "", name)
        if key in self.sites or re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", name):
            return Slot(True, {"action": "open_url", "value": key if key in self.sites else name}, {"site": key})
        return Slot(False, why=f"'{name}' isn't a known site or a domain")

    def running_app(self, text: str, strip: list[str]) -> Slot:
        from mary.skills.apps import _running_targets
        from mary.tools.registry import ToolContext

        name = remainder(text, strip)
        if not name:
            return Slot(False, why="no app name")
        label, windows = _running_targets(ToolContext(self.cfg, self.apps, None), name)
        if not windows:
            installed = self.apps.match(name).app
            if installed:  # nothing to do, and no need to ask the LLM to find that out
                return Slot(True, reply=f"{installed.name} isn't open.")
            return Slot(False, why=f"nothing open matches '{name}'")
        return Slot(True, {"name": name}, {"app": label})

    _WINDOW_ACTIONS = [
        ("restore", r"\b(restore|un-?maximi[sz]e|normal size)\b"),
        ("snap_left", r"\bleft\b"),
        ("snap_right", r"\bright\b"),
        ("minimize", r"\b(minimi[sz]e|hide)\b"),
        ("maximize", r"\b(maximi[sz]e|full ?screen|bigger)\b"),
        ("focus", r"\b(switch to|go to|focus|to the front|show me|bring up)\b"),
    ]
    _WINDOW_WORDS = (r"\b(minimi[sz]e|maximi[sz]e|un-?maximi[sz]e|restore|hide|snap|put|move|make|switch|go|"
                     r"focus|show|bring|full ?screen|bigger|normal size|left|right|half|side|of the screen|"
                     r"screen|window|this|it|the|to|on|in|me|front|my|up)\b")

    def window(self, text: str, strip: list[str]) -> Slot:
        from mary import winutil
        from mary.skills.windows import find_window

        low = text.lower()
        action = next((a for a, rx in self._WINDOW_ACTIONS if re.search(rx, low)), None)
        if action is None:
            return Slot(False, why="no window action")
        target = remainder(re.sub(self._WINDOW_WORDS, " ", _FILLER.sub(" ", low)), [])
        if not target:
            return Slot(True, {"action": action}, {"target": "this window"})
        app = self.apps.match(target).app
        if app and app.exe:  # "browser" -> msedge.exe -> its window
            hit = next((w for w in winutil.list_windows() if w.exe == app.exe), None)
            if hit:
                return Slot(True, {"action": action, "target": hit.title}, {"target": app.name})
        if find_window(target):
            return Slot(True, {"action": action, "target": target}, {"target": target})
        return Slot(False, why=f"no open window matches '{target}'")

    # ------------------------------------------------------------------ levels and media

    def _level(self, text: str, kind: str) -> Slot:
        level = parse_level(text)
        if level is None:
            return Slot(False, why=f"couldn't tell which way to change the {kind}")
        op, value = level
        if op in ("mute", "unmute"):
            return Slot(kind == "volume", {"action": op}, why="brightness can't be muted")
        args = {"action": f"{kind}_{op}"}
        if value is not None:
            args["value"] = value
        return Slot(True, args)

    def volume(self, text: str, strip: list[str]) -> Slot:
        return self._level(text, "volume")

    def brightness(self, text: str, strip: list[str]) -> Slot:
        return self._level(text, "brightness")

    _MEDIA = [("next", r"\b(next|skip|forward)\b"), ("previous", r"\b(previous|back|last song|last track)\b"),
              ("stop", r"\bstop\b"), ("pause", r"\b(pause|hold)\b"),
              ("resume", r"\b(resume|unpause|continue|play)\b")]
    _MEDIA_WORDS = (r"\b(next|skip|forward|previous|back|last|go|stop|pause|hold|resume|unpause|continue|"
                    r"play(ing)?|the|this|that|music|song|songs|track|tracks|tune|one|a|an|it|again|spotify|"
                    r"playback|audio|to|of|my|me|now|for|by|on|ahead)\b")

    def media(self, text: str, strip: list[str]) -> Slot:
        low = text.lower()
        action = next((a for a, rx in self._MEDIA if re.search(rx, low)), None)
        if action is None:
            return Slot(False, why="no media action")
        leftover = remainder(re.sub(self._MEDIA_WORDS, " ", _FILLER.sub(" ", low)), [])
        if leftover:  # "play jazz" is a music request, not a resume
            return Slot(False, why=f"'{leftover}' is more than a media command")
        return Slot(True, {"action": action})

    _GENERIC_MUSIC = {"", "music", "some music", "a song", "songs", "something", "tunes", "a tune",
                      "anything", "spotify", "my music"}

    def music_query(self, text: str, strip: list[str]) -> Slot:
        query = remainder(text, strip)
        if re.search(r"\b(next|skip|previous|pause|resume|unpause|stop)\b", query):
            return Slot(False, why=f"'{query}' sounds like a playback command, not something to search")
        if query in self._GENERIC_MUSIC:
            return Slot(True, {}, {"query": "some music"})
        return Slot(True, {"query": query}, {"query": query})

    # ------------------------------------------------------------------ info, search, timers

    def time_date(self, text: str, strip: list[str]) -> Slot:
        now = datetime.now()
        if re.search(r"\b(date|day|today|month|year)\b", text, re.I):
            reply = f"It's {now:%A}, {now.day} {now:%B %Y}."
        else:
            reply = f"It's {now:%I:%M %p}.".replace("It's 0", "It's ")
        return Slot(True, reply=reply)

    _STATUS_PARTS = [("Battery", r"\b(battery|charg\w*|power|plugged)\b"), ("CPU", r"\b(cpu|processor)\b"),
                     ("RAM", r"\b(ram|memory)\b"), ("Disk", r"\b(disk|storage|space|drive)\b"),
                     ("Up", r"\b(uptime|been on|running for|how long)\b")]

    def status(self, text: str, strip: list[str]) -> Slot:
        part = next((p for p, rx in self._STATUS_PARTS if re.search(rx, text, re.I)), None)
        return Slot(True, {}, {"part": part})

    _SEARCH_LEAD = re.compile(r"^(search|google|look up|web search|do a search|search up|find (me )?"
                              r"(information|info|stuff) (on|about)|(find|look for|get me)\b.*\b(on|in) "
                              r"(youtube|wikipedia|amazon))\b", re.I)

    def search_query(self, text: str, strip: list[str]) -> Slot:
        if not self._SEARCH_LEAD.search(_FILLER.sub(" ", text).strip()):
            return Slot(False, why="doesn't start with a search verb")
        site = None
        on_site = re.search(r"\b(?:on|in|at) (youtube|wikipedia|amazon)\b|^\W*(?:search|look up|check)\s+"
                            r"(youtube|wikipedia|amazon)\b(?:\s+for)?", text, re.I)
        if on_site:
            site = (on_site.group(1) or on_site.group(2)).lower()
            text = text[:on_site.start()] + " " + text[on_site.end():]
        query = remainder(text, strip)
        if len(query) < 2:
            return Slot(False, why="no search query")
        args = {"action": "search", "value": query, **({"site": site} if site else {})}
        return Slot(True, args, {"query": query})

    _TIME_PHRASE = re.compile(r"\b(today|yesterday|this week|last week|past week|this month|last month|"
                              r"past month|this year|recently|recent|(?:in the )?(?:last|past) \d+ "
                              r"(?:days?|weeks?|months?|hours?))\b", re.I)
    _FOLDER = re.compile(r"\b(?:in|on|from) (?:my |the )?(desktop|documents|downloads|pictures|music|videos)\b|"
                         r"\b(desktop|documents|downloads|pictures|music|videos) folder\b", re.I)

    _NAMED = re.compile(r"\b(?:called|named|titled)\s+[\"'“]?(.+?)[\"'”]?"
                        r"(?:\s+(?:in|on|from)\s+(?:my|the)\b.*)?[?.!]*$", re.I)
    _FOLDER_WORDS = {"desktop", "documents", "downloads", "download", "pictures", "music", "videos"}

    def files(self, text: str, strip: list[str]) -> Slot:
        """'find the PDF I downloaded this week' -> file_type pdf, folder downloads, modified_within this week.

        Only words that could be part of a file's name become the name query. Everything else is how
        people ask ("can you give me a list of…", "check if I have any new…"), and once became the
        "name" to look for, so natural requests found nothing. A folder, type or time alone lists those.
        """
        from mary.skills.files import FILE_TYPES, GENERIC_WORDS

        low = text.lower()
        args: dict = {}
        folder = self._FOLDER.search(low)
        if folder:
            args["folder"] = folder.group(1) or folder.group(2)
        elif re.search(r"\bdownload(ed|s)?\b", low):
            args["folder"] = "downloads"
        elif re.search(r"\bdesktop\b", low):
            args["folder"] = "desktop"
        rest = self._FOLDER.sub(" ", low)  # "in my documents" names a folder, not a file type
        named = self._NAMED.search(rest)    # "the file called budget": that's the name, whatever it is
        if named:
            rest = rest[:named.start()]
        for word in re.findall(r"[a-z]+", rest):
            key = word if word in FILE_TYPES else word[:-1] if word.endswith("s") and word[:-1] in FILE_TYPES else None
            if key:
                args["file_type"] = key
                break
        when = self._TIME_PHRASE.search(rest)
        if when:
            args["modified_within"] = when.group(1)
        if named:
            args["query"] = f'"{named.group(1).strip()}"'  # quoted = taken literally by find_files
        else:
            words = re.findall(r"[a-z0-9][\w'.-]*", remainder(self._TIME_PHRASE.sub(" ", rest), strip))
            name = [w for w in words if w not in GENERIC_WORDS and w not in self._FOLDER_WORDS
                    and w not in FILE_TYPES and not (w.endswith("s") and w[:-1] in FILE_TYPES)]
            if name:
                args["query"] = " ".join(name)
        if set(args) == {"modified_within"} and not re.search(r"\b(files?|download\w*|saved|documents?)\b", low):
            return Slot(False, why="only a time, and nothing says it's about files")
        if not args:
            return Slot(False, why="nothing to search for")
        return Slot(True, args)

    _WEATHER_PLACE = re.compile(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]*?)(?:\s+(?:today|tomorrow|tonight|"
                                r"right now|now|later|this \w+))?\s*[?.!]*$", re.I)
    _NOT_PLACES = {"my area", "here", "my location", "the moment", "the morning", "the afternoon", "the evening",
                   "the night", "general"}

    def weather(self, text: str, strip: list[str]) -> Slot:
        """'what's the weather in Paris tomorrow' -> place Paris, when forecast."""
        from mary.skills.weather import _FORECAST_WORDS

        if self._SEARCH_LEAD.search(_FILLER.sub(" ", text).strip()):
            return Slot(False, why="asked for a web search")  # "google the weather…": web_search does it
        args = {"when": "forecast" if _FORECAST_WORDS.search(text) else "now"}
        m = self._WEATHER_PLACE.search(text.strip())
        if m and m.group(1).lower().strip() not in self._NOT_PLACES:
            args["place"] = m.group(1).strip()
        return Slot(True, args)

    _CLICK = re.compile(r"^\W*(?:(?:please|can you|could you|now|ok(?:ay)?)[\s,]+)*(?:click|tap|press|hit|select|"
                        r"choose|pick)(?:\s+on)?\s+(.+?)\s*[.!?]*$", re.I | re.S)
    _SAYS = re.compile(r"\b(?:where it says|that says|which says|the one saying|called|titled|named)\s+"
                       r"[\"'“]?(.+?)[\"'”]?\s*[.!?]*$", re.I | re.S)
    _KEYS = re.compile(r"^(?:the )?(?:enter|return|escape|esc|tab|space(?:bar)?|backspace|delete|del|home|end|"
                       r"page ?up|page ?down|up|down|left|right|f\d{1,2}|all|(?:ctrl|control|alt|shift|win|windows)\b.*)"
                       r"(?: key)?$", re.I)

    def click(self, text: str, strip: list[str]) -> Slot:
        """'click subscribe' / 'press the play button' / 'pick the one where it says Eat Everything…'."""
        m = self._CLICK.match(text.strip())
        if not m:
            return Slot(False, why="doesn't start with click/press/select")
        says = self._SAYS.search(text)
        target = (says.group(1) if says else m.group(1)).strip()
        target = re.sub(r"[\s,]+(?:for me|please|now|for me please)$", "", target, flags=re.I).strip(" .,")
        if self._KEYS.match(target):
            return Slot(False, why="that's a key: the keyboard tool presses keys")
        if target.lower() in ("it", "that", "this", "here", "there", "them") or len(target) < 2:
            return Slot(False, why="click what?")
        return Slot(True, {"action": "click", "target": target})

    def scroll(self, text: str, strip: list[str]) -> Slot:
        low = text.lower()
        direction = next((d for d in ("top", "bottom", "up", "down") if re.search(rf"\b{d}\b", low)), "down")
        bit = " a bit" if re.search(r"\b(a bit|a little|slightly)\b", low) else ""
        return Slot(True, {"action": "scroll", "target": direction + bit})

    def folder(self, text: str, strip: list[str]) -> Slot:
        """'open my downloads folder' -> file_action open 'downloads' (File Explorer on that folder)."""
        m = re.search(r"\b(desktop|documents|downloads?|pictures|photos|music|videos)\b", text.lower())
        if not m:
            return Slot(False, why="no known folder")
        name = "downloads" if m.group(1) == "download" else m.group(1)
        return Slot(True, {"action": "open", "path": name})

    _NTH = {"first": 1, "1st": 1, "top": 1, "newest": 1, "latest": 1, "most recent": 1, "second": 2, "2nd": 2,
            "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
    _NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                     "nine": 9, "ten": 10}

    def found_file(self, text: str, strip: list[str]) -> Slot:
        """'open the newest one' / 'open it' / 'play the video' / 'open number two', right after a file search.

        Results are newest first and MARY names the newest, so "it" means that one."""
        from mary.skills.files import FILE_TYPES, _extensions

        low = normalize_spaces(text.lower())
        web = getattr(self.ctx, "last_web_at", 0.0)
        if web and web > getattr(self.ctx, "last_files_at", 0.0) and time.monotonic() - web < 15 * 60:
            # The last thing shown was a web page: "play the first video" means a result on it.
            which = re.search(r"\b(first|1st|top|second|2nd|third|3rd|fourth|4th|fifth|5th|last)\b", low)
            noun = re.search(r"\b(video|result|link|one)s?\b", low)
            if not which:
                return Slot(False, why="which result?")
            return Slot(True, {"action": "click", "target": f"{which.group(1)} {noun.group(1) if noun else 'one'}"},
                        tool="screen")
        files = getattr(self.ctx, "last_files", None) or []
        if not files or time.monotonic() - self.ctx.last_files_at > 15 * 60:
            return Slot(False, why="no recent file search to refer to")
        n = None
        number = re.search(r"\b(?:number|no\.?|#)\s*(\d+|" + "|".join(self._NUMBER_WORDS) + r")\b", low)
        nth = re.search(r"\b(" + "|".join(self._NTH) + r")\b", low)
        kind = re.search(r"\b(?:the|that|this)\s+(\w+?)s?\s*$", low)
        if number:
            n = int(number.group(1)) if number.group(1).isdigit() else self._NUMBER_WORDS[number.group(1)]
        elif re.search(r"\boldest\b", low):
            n = len(files)
        elif nth:
            n = self._NTH[nth.group(1)]
        elif kind and kind.group(1) in FILE_TYPES:  # "play the video": the newest video found
            exts = tuple(_extensions(kind.group(1)))
            n = next((i for i, f in enumerate(files, 1) if f.lower().endswith(exts)), None)
            if n is None:
                return Slot(False, why=f"no {kind.group(1)} in the last search")
        elif re.search(r"\b(it|that|this|the file|that one|this one)\s*$", low):
            n = 1
        if n is None or not 1 <= n <= len(files):
            return Slot(False, why="can't tell which file")
        return Slot(True, {"action": "open", "path": str(n)})

    # "type"/"write" + optional "out/down", "the following (text)", "this", "in it/in notepad", then ":" or
    # "that says". Speech recognition often turns "in it:" into "and it's", so that's accepted too.
    _DICTATE = re.compile(
        r"^\W*(?:(?:please|can you|could you|now)\s+)*(?:type|write|enter|put|dictate)(?:\s+(?:out|down))?"
        r"(?:\s+(?:the\s+)?(?:following|text)(?:\s+text)?|\s+this(?:\s+text)?|\s+that)?"
        r"(?:\s+(?:in|into|on)\s+(?:it|there|here|this|the\s+\w+|notepad|word))?"
        r"(?:\s*[:,.\-]\s*|\s+(?:and\s+it'?s|that\s+says|saying|which\s+says)\s+|\s+|$)", re.I)

    def dictation(self, text: str, strip: list[str]) -> Slot:
        """The words to type, with their capitals kept (unlike other slots, which lower-case)."""
        m = self._DICTATE.match(text)
        if not m:
            return Slot(False, why="doesn't start with type/write")
        words = text[m.end():].strip().strip("\"'“”")
        words = re.sub(r"\s+(?:in|into)\s+(?:it|there|notepad)\W*$", "", words, flags=re.I)
        if not re.search(r"\w", words):  # "write the following text" and nothing yet: ask for it
            return Slot(True, reply="What should I type?", fill={"await": "dictation"})
        into = self._INTO_FIELD.search(words)
        if into:  # a named box on screen: the screen tool finds it and types there
            return Slot(True, {"action": "type", "target": into.group(1), "text": words[:into.start()].strip(),
                               **({"submit": True} if into.group(2) else {})}, tool="screen")
        return Slot(True, {"action": "type", "text": words}, {"text": words})

    _INTO_FIELD = re.compile(r"\s+(?:into|in|on)\s+the\s+((?:search|address|url|name|email|subject|message|"
                             r"comment|chat)\s*(?:box|bar|field)?|[\w ]{1,20}? (?:box|field|bar))"
                             r"(\s+and (?:search|press enter|hit enter|submit|send)(?: it)?)?\s*[.!?]*$", re.I)

    _REMEMBER = re.compile(r"^\W*(?:(?:please|can you|could you|hey mary)[\s,]+)*(?:remember|keep in mind)"
                           r"(?:\s+that|\s+this)?\s*[:,]?\s*", re.I)

    def remember(self, text: str, strip: list[str]) -> Slot:
        """The fact to keep, capitals kept ("remember that I prefer Edge" -> "I prefer Edge")."""
        m = self._REMEMBER.match(text)
        fact = text[m.end():].strip() if m else ""
        if len(fact) < 3:
            return Slot(False, why="nothing to remember")
        return Slot(True, {"action": "remember", "fact": fact})

    _FORGET = re.compile(r"^\W*(?:(?:please|can you|could you|hey mary)[\s,]+)*(?:forget|stop remembering)"
                         r"(?:\s+what i (?:said|told you) about|\s+about|\s+that)?\s*[:,]?\s*", re.I)
    _FORGET_NOTHING = {"it", "that", "this", "everything", "all", "all that", "all of it", "about it", "me"}

    def forget(self, text: str, strip: list[str]) -> Slot:
        m = self._FORGET.match(text)
        about = text[m.end():].strip(" .!") if m else ""
        if len(about) < 3 or about.lower() in self._FORGET_NOTHING:  # "forget it" = never mind
            return Slot(False, why="nothing specific to forget")
        return Slot(True, {"action": "forget", "fact": about})

    _NAME = re.compile(r"\b(?:my name is|my name's|call me|i'm called|i am called)\s+([A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)",
                       re.I)

    def name(self, text: str, strip: list[str]) -> Slot:
        m = self._NAME.search(text)
        if not m:
            return Slot(False, why="no name")
        return Slot(True, {"action": "set_name", "fact": m.group(1)})

    def timer(self, text: str, strip: list[str]) -> Slot:
        seconds = parse_duration(text)
        if not seconds:
            return Slot(False, why="no duration")
        args = {"duration": f"{seconds} seconds"}
        low = text.lower()
        for pattern in (r"\b(?!a\b|an\b|the\b|my\b|new\b|set\b|start\b|\d)(\w+) timer\b",  # "pasta timer"
                        r"\bfor (?:the |my )?(?!\d|a |an |half)([a-z]+(?: [a-z]+)?)$"):  # "... for the tea"
            label = re.search(pattern, low)
            if label and not re.search(r"\b(second|minute|hour|sec|min)s?\b", label.group(1)):
                args["label"] = label.group(1)
                break
        return Slot(True, args)
