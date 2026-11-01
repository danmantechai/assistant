"""What the settings page reads and writes: the same files you'd edit by hand.

Where a change goes (the rule):
- the assistant: name, nicknames, personality (soul), speech, writing, stories (lore), messages (texts), voice,
  wake phrases and responses, greeting -> its persona folder. A persona that ships with MARY (personas/) is never
  edited: the first save makes your private copy in user/personas/<name>/, which then wins.
- you: your name and what she remembers -> user/profile.yaml
- MARY's own settings (model, hearing, skills…) -> user/config.yaml, and only the values that differ from the
  defaults and her persona (a value set back to the default is removed from the file)
- keys -> .env, write-only: nothing here ever reads a key back out

YAML is written with ruamel.yaml, which keeps the files' comments and order (a value's own end-of-line comment is
dropped when the value changes, since it described the old one). Every write is atomic (a temp file, then a
rename), and the version before goes to user/backups/, the last 5 per file. Nothing is deleted outright: a removed
story, message file or persona is moved to user/backups/ too.
"""
from __future__ import annotations

import copy
import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

from mary import persona as personas
from mary.config import DEFAULT_CONFIG, PROJECT_DIR, merge, user_config, user_dir
from mary.settings import schema

ENV_FILE = PROJECT_DIR / ".env"
KEYS = {"ELEVENLABS_API_KEY", "LLM_API_KEY"}   # ElevenLabs; the model service (brain.provider: api)
KEEP_BACKUPS = 5
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FILE = re.compile(r"^[A-Za-z0-9_-]{1,80}\.(?:md|txt)$")
_MISSING = object()
VOICE_MODELS = ["eleven_v4_turbo", "eleven_v4"]


class SettingsError(ValueError):
    """A change the page can't make; the message is shown to the user as it is."""


# ------------------------------------------------------------------ files: read, write, back up


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _plain(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _label(path: Path) -> str:
    """A file's name in user/backups: its place under user/ with / as __ ("personas__mom__soul.md")."""
    try:
        return "__".join(path.resolve().relative_to(user_dir().resolve()).parts)
    except ValueError:
        return path.name


def backup(path: Path) -> None:
    """Copy the file to user/backups (keeping the last KEEP_BACKUPS of it)."""
    if not path.is_file():
        return
    folder = user_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    label = _label(path)
    shutil.copy2(path, folder / f"{label}.{_stamp()}")
    old = sorted(folder.glob(f"{glob_escape(label)}.2*"))
    for extra in old[:-KEEP_BACKUPS]:
        extra.unlink()  # an older copy of a copy: the file itself and the newer copies remain


def glob_escape(text: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def retire(path: Path) -> None:
    """Move a file or folder into user/backups instead of deleting it."""
    if not path.exists():
        return
    folder = user_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(folder / f"{_label(path)}.{_stamp()}"))


def write_text(path: Path, text: str, keep_backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if keep_backup:
        backup(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _load_doc(path: Path) -> tuple[CommentedMap, str]:
    """A YAML file for editing, and its text when it holds only comments (ruamel can't keep those)."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = _yaml().load(text) if text.strip() else None
    if doc is None:
        return CommentedMap(), text
    if not isinstance(doc, CommentedMap):
        raise SettingsError(f"{path.name} isn't a list of settings; fix it by hand first.")
    return doc, ""


def _tidy(text: str) -> str:
    """A blank line before each top-level section that follows a nested one (removing a section's last setting
    can take its blank line with it), and never two blank lines in a row."""
    out: list[str] = []
    for line in text.split("\n"):
        prev = out[-1] if out else ""
        if line[:1].strip() and prev[:1] == " " and not prev.lstrip().startswith(("- ", "#")):
            out.append("")
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def _save_doc(path: Path, doc: CommentedMap, header: str = "") -> None:
    buf = io.StringIO()
    if doc:
        _yaml().dump(doc, buf)
    body = _tidy(buf.getvalue())
    if header:
        body = header.rstrip("\n") + "\n\n" + body if body else header
    write_text(path, body)


# ------------------------------------------------------------------ editing a YAML document in place


def _parts(path: str) -> list[str]:
    return path.split(".")


def get(data, path: str, default=None):
    for part in _parts(path):
        if not isinstance(data, dict) or part not in data:
            return default
        data = data[part]
    return data


def _styled(old, value):
    """The new value written the way the old one was: a list stays [flow] or block, quotes stay quotes."""
    def quoted_like(sample, item):
        if isinstance(item, str) and isinstance(sample, (DoubleQuotedScalarString, SingleQuotedScalarString)):
            return type(sample)(item)
        return item

    if isinstance(value, list):
        sample = next(iter(old), None) if isinstance(old, list) else None
        seq = CommentedSeq(quoted_like(sample, v) for v in value)
        flow = old.fa.flow_style() if isinstance(old, CommentedSeq) and old else \
            all(isinstance(v, str) for v in value) and sum(len(v) + 4 for v in value) < 90
        if flow:
            seq.fa.set_flow_style()
        else:
            seq.fa.set_block_style()
        return seq
    if old is None and isinstance(value, str) and re.search(r"""[{}\[\]:#'"]|^\s|\s$""", value):
        return DoubleQuotedScalarString(value)  # a new value that needs quotes gets the files' usual "…"
    return quoted_like(old, value)


def _drop_eol_comment(node: CommentedMap, key: str) -> None:
    """Remove the comment at the end of key's line (it described the old value); comment lines after it stay."""
    tokens = node.ca.items.get(key)
    if not tokens or len(tokens) < 3 or tokens[2] is None:
        return
    first, _, rest = tokens[2].value.partition("\n")
    if not first.lstrip().startswith("#"):
        return
    if rest:
        tokens[2].value = "\n" + rest
    else:
        tokens[2] = None


def _last_line(node: CommentedMap, key):
    """Where ruamel keeps what follows node[key]'s last line (its end-of-line comment, then any blank lines and
    comment lines): (the comments table, the entry, the slot), inside a section's last setting or a list's last
    item."""
    value = node[key]
    if isinstance(value, CommentedMap) and value:
        return _last_line(value, list(value)[-1])
    if isinstance(value, CommentedSeq) and value and not value.fa.flow_style():
        last = len(value) - 1
        if isinstance(value[last], CommentedMap) and value[last]:
            return _last_line(value[last], list(value[last])[-1])
        return value.ca.items, last, 0
    return node.ca.items, key, 2


def _end_section_after(node: CommentedMap, key: str) -> None:
    """A setting just added at the end of a section: the blank line and comment lines that closed the section move
    below it, so it joins its section instead of starting the next one."""
    keys = list(node)
    if len(keys) < 2 or keys[-1] != key:
        return
    table, entry, slot = _last_line(node, keys[-2])
    tokens = table.get(entry)
    if not tokens or len(tokens) <= slot or tokens[slot] is None:
        return
    eol, _, rest = tokens[slot].value.partition("\n")
    if not rest:
        return
    moved = copy.copy(tokens[slot])
    moved.value = "\n" + rest
    if eol.strip():
        tokens[slot].value = eol + "\n"
    else:
        tokens[slot] = None
    table, entry, slot = _last_line(node, key)
    table.setdefault(entry, [None, None, None, None])[slot] = moved


def set_value(doc: CommentedMap, path: str, value) -> None:
    node = doc
    parts = _parts(path)
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = CommentedMap()
        node = node[part]
    key = parts[-1]
    old = node.get(key, _MISSING)
    if old == value:
        return
    node[key] = _styled(None if old is _MISSING else old, value)
    if old is _MISSING:
        _end_section_after(node, key)
    block_list = isinstance(old, CommentedSeq) and not old.fa.flow_style()
    if old is not _MISSING and not isinstance(old, dict) and not block_list:
        _drop_eol_comment(node, key)


def remove_value(doc: CommentedMap, path: str) -> None:
    """Remove a setting, and any section it leaves empty. Comment lines that followed it move up a line."""
    parts = _parts(path)
    chain = [doc]
    for part in parts[:-1]:
        nxt = chain[-1].get(part)
        if not isinstance(nxt, dict):
            return
        chain.append(nxt)
    for depth in range(len(parts) - 1, -1, -1):
        node, key = chain[depth], parts[depth]
        if key not in node or (depth < len(parts) - 1 and node[key]):
            return
        keys = list(node)
        tokens = node.ca.items.get(key)
        rest = tokens[2].value.partition("\n")[2] if tokens and len(tokens) > 2 and tokens[2] else ""
        at = keys.index(key)
        del node[key]
        lines = [line for line in rest.splitlines() if line.strip().startswith("#")]
        if not lines:
            continue
        if at < len(keys) - 1:  # they were about the next setting: keep them above it
            indent = len(lines[0]) - len(lines[0].lstrip())
            text = "\n".join(re.sub(r"^\s*#\s?", "", line) for line in lines)
            node.yaml_set_comment_before_after_key(keys[at + 1], before=text, indent=indent)
        elif at > 0:
            before = node.ca.items.get(keys[at - 1])
            if before and len(before) > 2 and before[2] is not None:
                before[2].value += rest


# ------------------------------------------------------------------ the three layers of settings


@dataclass
class Layers:
    defaults: dict      # config.yaml
    persona: dict       # what her persona brings (persona.config())
    mine: dict          # user/config.yaml
    key: str            # the active persona

    @property
    def base(self) -> dict:
        """What the settings would be without user/config.yaml."""
        return merge(self.defaults, self.persona)

    @property
    def effective(self) -> dict:
        return merge(self.base, self.mine)

    def origin(self, path: str) -> str:
        if get(self.mine, path, _MISSING) is not _MISSING:
            return "yours"
        if get(self.persona, path, _MISSING) is not _MISSING:
            return "persona"
        return "default"


def layers() -> Layers:
    defaults = _plain(DEFAULT_CONFIG)
    mine = _plain(user_dir() / "config.yaml")
    her = personas.load(mine.get("persona") or defaults.get("persona"))
    return Layers(defaults, her.config(), mine, her.key)


def settings_state() -> dict:
    """Every setting the page shows: its value, where that comes from, and what Reset would give."""
    lay = layers()
    eff, base = lay.effective, lay.base
    values = {path: {"value": get(eff, path), "origin": lay.origin(path), "base": get(base, path)}
              for path in schema.FIELDS}
    return {"pages": {page: [f.public() for f in fields] for page, fields in schema.PAGES.items()},
            "values": values, "persona": lay.key, "presets": schema.PRESETS,
            "voice_override": sorted(lay.mine.get("tts") or {})}  # voice settings of yours that beat the persona's


def save_settings(changes: dict) -> None:
    """Write changed settings into user/config.yaml. A value equal to the default is removed instead."""
    if not isinstance(changes, dict) or not changes:
        raise SettingsError("Nothing to save.")
    cleaned = {}
    for path, value in changes.items():
        try:
            cleaned[path] = schema.check(path, value)
        except ValueError as e:
            raise SettingsError(str(e)) from None
    base = layers().base
    file = user_config()
    doc, header = _load_doc(file)
    for path, value in cleaned.items():
        if get(base, path, _MISSING) == value:
            remove_value(doc, path)
        else:
            set_value(doc, path, value)
    _save_doc(file, doc, header)


def reset_setting(path: str) -> None:
    if path not in schema.FIELDS:
        raise SettingsError(f"'{path}' can't be changed from this page.")
    file = user_config()
    doc, header = _load_doc(file)
    remove_value(doc, path)
    _save_doc(file, doc, header)


def use_persona(key: str) -> None:
    """Make `key` the active persona (persona: in user/config.yaml)."""
    check_name(key)
    if personas.find(key) is None:
        raise SettingsError(f"There's no persona called {key}.")
    file = user_config()
    doc, header = _load_doc(file)
    if key == _plain(DEFAULT_CONFIG).get("persona"):
        remove_value(doc, "persona")
    elif "persona" in doc:
        set_value(doc, "persona", key)
    else:
        doc.insert(0, "persona", key)  # first, as in config.yaml
    _save_doc(file, doc, header)


# ------------------------------------------------------------------ personas


def check_name(key: str) -> str:
    if not isinstance(key, str) or not _NAME.match(key):
        raise SettingsError("A persona's folder name can only use a-z, 0-9, _ and -, starting with a letter or "
                            "number.")
    return key


def mine_folder(key: str) -> Path:
    return user_dir() / "personas" / check_name(key)


def persona_list() -> list[dict]:
    active = layers().key
    out, seen = [], set()
    for root, private in ((user_dir() / "personas", True), (personas.SHIPPED, False)):
        for file in sorted(root.glob("*/persona.yaml")):
            key = file.parent.name
            if key.startswith("_") or key in seen or not _NAME.match(key):
                continue
            seen.add(key)
            data = _plain(file)
            out.append({"key": key, "name": data.get("name") or "MARY", "private": private,
                        "ships": (personas.SHIPPED / key / "persona.yaml").exists(), "active": key == active})
    return out


def _fresh(key: str) -> personas.Persona:
    personas._load.cache_clear()
    folder = personas.find(check_name(key))
    if folder is None:
        raise SettingsError(f"There's no persona called {key}.")
    return personas._load(key, folder)


def persona_info(key: str) -> dict:
    """Everything the page edits about one persona."""
    her = _fresh(key)
    data = _plain(her.folder / "persona.yaml")
    private = her.folder.parent == user_dir() / "personas"
    lore = []
    for file in sorted((her.folder / "lore").glob("[!_]*.md")):
        text = file.read_text(encoding="utf-8")
        m = re.match(r"(?is)^\s*keywords:\s*(.*?)\n(.*)$", text)
        lore.append({"file": file.name, "title": file.stem.replace("_", " "),
                     "keywords": [k.strip() for k in (m.group(1) if m else "").split(",") if k.strip()],
                     "text": (m.group(2) if m else text).strip()})
    texts = []
    for file in sorted((her.folder / "texts").glob("*.txt")):
        count = len([t for t in re.split(r"\n\s*\n", file.read_text(encoding="utf-8")) if t.strip()])
        texts.append({"file": file.name, "count": count, "label": file.stem.replace("_", " ")})
    voice = dict(data.get("voice") or {})
    return {
        "key": her.key, "private": private, "ships": (personas.SHIPPED / her.key / "persona.yaml").exists(),
        "active": her.key == layers().key,
        "folder": f"user/personas/{her.key}" if private else f"personas/{her.key}",
        "name": her.name, "nicknames": her.nicknames, "greeting": her.greeting,
        "greeting_no_name": her.greeting_no_name, "replies": her.replies, "morning_replies": her.morning_replies,
        "voice": voice, "soul": her.soul, "speech": her.speech, "texting": her.texting,
        "wake": [wake_info(w) for w in her.wake],
        "lore": lore, "texts": texts,
    }


def wake_info(w: personas.Wake) -> dict:
    from mary.settings.choices import BUILT_IN_WAKE, wake_status

    return {"phrase": w.phrase, "model": w.model, "names": w.names, "alone": w.alone, "misheard": w.misheard,
            "bare": w.bare, "builtin": w.model in BUILT_IN_WAKE, "status": wake_status(w.model)}


def _wake_list(value) -> list[CommentedMap]:
    """Wake phrases from the page, checked: a built-in one keeps its own phrase; any other is trained as
    <phrase slug>.onnx (settings page › Wake word › Train)."""
    from mary.settings.choices import BUILT_IN_WAKE
    from mary.wakeword_training.phrases import GREETERS, slug, split

    if not isinstance(value, list) or len(value) > 4:
        raise SettingsError("Up to 4 wake phrases.")
    out, seen = [], set()
    for w in value:
        if not isinstance(w, dict):
            raise SettingsError("Wake phrase: unexpected settings.")
        model = str(w.get("model") or "")
        if model in BUILT_IN_WAKE:
            phrase, names = BUILT_IN_WAKE[model]["phrase"], BUILT_IN_WAKE[model]["names"]
        else:
            phrase = re.sub(r"\s+", " ", _line(w.get("phrase") or "", "Wake phrase", 40))
            greet, name = split(phrase)
            if not name or name.lower() in GREETERS:
                raise SettingsError("A wake phrase needs a name in it, like “Hey Grandma”.")
            phrase, model, names = f"{greet} {name}".strip(), slug(phrase), [name.lower()]
        if model in seen:
            continue
        seen.add(model)
        entry = CommentedMap(phrase=phrase, model=model)
        for part, default in (("names", names), ("alone", []), ("misheard", [])):
            items = [x.lower() for x in _lines(w.get(part) or [], "Spellings", 12)] or default
            if items:
                seq = CommentedSeq(items)
                seq.fa.set_flow_style()
                entry[part] = seq
        if w.get("bare"):
            entry["bare"] = True
        out.append(entry)
    return out


def _merge_wake(seq: CommentedSeq, entries: list[CommentedMap]) -> None:
    """Update the wake list in place: a phrase that was already there keeps its own comments, and the comment lines
    after the list (they belong to whatever follows it) stay after it."""
    def last_token(item):
        tokens = item.ca.items.get(list(item)[-1]) if item else None
        return tokens if tokens and len(tokens) > 2 else None

    trailing = ""
    tokens = last_token(seq[-1])
    if tokens and tokens[2] is not None:
        first, _, trailing = tokens[2].value.partition("\n")
        if first.strip():
            tokens[2].value = first + "\n"
        else:
            tokens[2] = None
    old = {item.get("model"): item for item in seq if isinstance(item, CommentedMap)}
    items = []
    for entry in entries:
        item = old.get(entry["model"])
        if item is None:
            items.append(entry)
            continue
        for key, value in entry.items():
            set_value(item, key, value)
        for key in ("alone", "misheard", "bare"):
            if key in item and key not in entry and item[key]:
                del item[key]  # cleared on the page (an empty or false one stays, comment and all)
        items.append(item)
    seq.clear()
    seq.extend(items)
    if trailing.strip():
        last = seq[-1]
        tokens = last_token(last)
        if tokens and tokens[2] is not None:
            tokens[2].value = tokens[2].value.rstrip("\n") + "\n" + trailing
        else:
            last.yaml_add_eol_comment("#", list(last)[-1])
            last.ca.items[list(last)[-1]][2].value = "\n" + trailing


def editable(key: str) -> Path:
    """Her folder, ready to write: yours, or (the first time) your private copy of one that ships."""
    mine = mine_folder(key)
    if (mine / "persona.yaml").exists():
        return mine
    shipped = personas.SHIPPED / key
    if (shipped / "persona.yaml").exists():
        shutil.copytree(shipped, mine)
        return mine
    raise SettingsError(f"There's no persona called {key}.")


def _line(value, label: str, limit: int = 300) -> str:
    if not isinstance(value, str) or "\n" in value or len(value) > limit:
        raise SettingsError(f"{label}: one line of text.")
    return value.strip()


def _lines(value, label: str, limit: int = 40) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise SettingsError(f"{label}: a shorter list.")
    return [v for v in (_line(v, label) for v in value) if v]


def _voice_changes(voice) -> dict[str, object]:
    """The voice settings to write, as voice.<path> -> value, checked."""
    if not isinstance(voice, dict):
        raise SettingsError("Voice: unexpected settings.")
    out = {}
    if "provider" in voice:
        if voice["provider"] not in ("kokoro", "elevenlabs"):
            raise SettingsError("Voice: Kokoro or ElevenLabs.")
        out["voice.provider"] = voice["provider"]
    if "voice" in voice:
        if not isinstance(voice["voice"], str) or not re.fullmatch(r"[a-z]{2}_[a-z]+", voice["voice"]):
            raise SettingsError("Voice: pick a Kokoro voice from the list.")
        out["voice.voice"] = voice["voice"]
    if "speed" in voice:
        speed = voice["speed"]
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2:
            raise SettingsError("Voice: speed between 0.5 and 2.")
        out["voice.speed"] = round(float(speed), 2)
    el = voice.get("elevenlabs") or {}
    if "voice_id" in el:
        if not isinstance(el["voice_id"], str) or not re.fullmatch(r"[A-Za-z0-9]{0,64}", el["voice_id"].strip()):
            raise SettingsError("Voice: that isn't an ElevenLabs voice ID (letters and numbers).")
        out["voice.elevenlabs.voice_id"] = el["voice_id"].strip()
    if "model" in el:
        if el["model"] not in VOICE_MODELS:
            raise SettingsError("Voice: pick an ElevenLabs model from the list.")
        out["voice.elevenlabs.model"] = el["model"]
    return out


def save_persona(key: str, fields: dict) -> None:
    """Save changed parts of her: persona.yaml settings, and soul.md / speech.md / texting.md."""
    if not isinstance(fields, dict) or not fields:
        raise SettingsError("Nothing to save.")
    yaml_changes: dict[str, object] = {}
    if "name" in fields:
        name = _line(fields["name"], "Name", 40)
        if not name:
            raise SettingsError("Give the assistant a name.")
        yaml_changes["name"] = name
    if "nicknames" in fields:
        yaml_changes["nicknames"] = _lines(fields["nicknames"], "Nicknames")
    if "wake" in fields:
        yaml_changes["wake"] = _wake_list(fields["wake"])
    for key_, label in (("greeting", "Greeting"), ("greeting_no_name", "Greeting")):
        if key_ in fields:
            yaml_changes[key_] = _line(fields[key_], label)
    for key_, label in (("replies", "Wake replies"), ("morning_replies", "Morning replies")):
        if key_ in fields:
            yaml_changes[key_] = _lines(fields[key_], label)
    if "voice" in fields:
        yaml_changes.update(_voice_changes(fields["voice"]))
    texts = {}
    for key_ in ("soul", "speech", "texting"):
        if key_ in fields:
            if not isinstance(fields[key_], str) or len(fields[key_]) > 20000:
                raise SettingsError("That text is too long (20,000 letters at most).")
            texts[key_] = fields[key_].replace("\r\n", "\n").strip() + "\n"
    unknown = set(fields) - {"name", "nicknames", "wake", "greeting", "greeting_no_name", "replies",
                             "morning_replies", "voice", "soul", "speech", "texting"}
    if unknown:
        raise SettingsError(f"Can't change {', '.join(sorted(unknown))} from this page.")

    folder = editable(key)
    if yaml_changes:
        doc, header = _load_doc(folder / "persona.yaml")
        for path, value in yaml_changes.items():
            if path == "nicknames" and "user_calls_her" in doc:
                path = "user_calls_her"  # an older persona file: keep its own key
            if path == "wake" and isinstance(doc.get("wake"), CommentedSeq) and doc["wake"] and value:
                _merge_wake(doc["wake"], value)
            else:
                set_value(doc, path, value)
        _save_doc(folder / "persona.yaml", doc, header)
    for key_, text in texts.items():
        write_text(folder / f"{key_}.md", text)
    personas._load.cache_clear()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60]


def save_lore(key: str, title: str, keywords, text: str, old_file: str | None = None) -> str:
    """One story, saying or family fact (lore/<title>.md). Returns its file name."""
    slug = _slug(_line(title, "Title", 60))
    if not slug:
        raise SettingsError("Give the story a title.")
    words = [w for w in _lines(keywords, "Words that bring it up", 30) if "," not in w]
    if not words:
        raise SettingsError("Add at least one word that brings the story up.")
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        raise SettingsError("Write the story (5,000 characters at most).")
    folder = editable(key) / "lore"
    file = folder / f"{slug}.md"
    if old_file and old_file != file.name:
        retire(folder / _safe_file(old_file))
    write_text(file, f"keywords: {', '.join(words)}\n{re.sub(r'\s+', ' ', text).strip()}\n")
    personas._load.cache_clear()
    return file.name


def _safe_file(name: str) -> str:
    if not isinstance(name, str) or not _FILE.match(name):
        raise SettingsError("That file isn't part of this persona.")
    return name


def delete_lore(key: str, file: str) -> None:
    retire(editable(key) / "lore" / _safe_file(file))
    personas._load.cache_clear()


def delete_texts(key: str, file: str) -> None:
    retire(editable(key) / "texts" / _safe_file(file))
    personas._load.cache_clear()


def chat_text(filename: str, data: bytes) -> str:
    """The text of a WhatsApp export: the .txt itself, or the chat inside an exported .zip."""
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            chats = [i for i in z.infolist() if i.filename.lower().endswith(".txt") and i.file_size < 60_000_000]
            if not chats:
                raise SettingsError(f"{filename} has no chat (.txt) in it.")
            data = z.read(max(chats, key=lambda i: i.file_size))
    return data.decode("utf-8-sig", errors="replace")


def _plain_messages(text: str) -> list[str]:
    """Messages in an ordinary text file: blank lines separate them."""
    found = [re.sub(r"[ \t]+", " ", m).strip() for m in re.split(r"\n\s*\n", text)]
    return [m[:2000] for m in found if m][:5000]


def inspect_texts(filename: str, data: bytes) -> dict:
    """What a file holds: a chat export (and who wrote in it), or plain messages separated by blank lines."""
    from mary.persona_io import whatsapp_senders

    text = chat_text(filename, data)
    senders = whatsapp_senders(text)
    if senders:
        return {"kind": "chat", "senders": senders[:30]}
    found = _plain_messages(text)
    if not found:
        raise SettingsError("That file has no messages in it.")
    return {"kind": "plain", "count": len(found)}


def import_texts(key: str, filename: str, data: bytes, sender: str | None = None) -> tuple[int, str]:
    """Example messages into texts/ (private): from a chat export, only the chosen person's; from any other text
    file, every paragraph. Returns (how many, the file name)."""
    from mary.persona_io import whatsapp_messages, whatsapp_senders

    text = chat_text(filename, data)
    if whatsapp_senders(text):
        if not sender:
            raise SettingsError("Choose whose messages to keep.")
        found = whatsapp_messages(text, _line(sender, "Whose messages", 60))
        if not found:
            raise SettingsError(f"No messages from {sender} in that chat.")
    else:
        found = _plain_messages(text)
        if not found:
            raise SettingsError("That file has no messages in it.")
    folder = editable(key)
    name = f"{_slug(Path(filename).stem) or 'messages'}.txt"
    write_text(folder / "texts" / name, "\n\n".join(found) + "\n")
    personas._load.cache_clear()
    return len(found), name


def _trait_list(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def new_persona(key: str, copy_from: str | None = None, answers: dict | None = None) -> str:
    """A new private persona: a copy of another, or the template filled in from the guided questions."""
    folder = mine_folder(key)
    if folder.exists():
        raise SettingsError(f"You already have a persona called {key}.")
    if copy_from:
        source = personas.find(check_name(copy_from))
        if source is None:
            raise SettingsError(f"There's no persona called {copy_from}.")
        shutil.copytree(source, folder)
    else:
        shutil.copytree(personas.SHIPPED / "_template", folder)
        if answers:
            _fill(folder, answers)
    personas._load.cache_clear()
    return key


def _fill(folder: Path, a: dict) -> None:
    """The template, filled in from the guided questions (no model involved)."""
    name = _line(a.get("name") or "", "Name", 40) or "MARY"
    who = _line(a.get("who") or "", "Who they are to you")
    who = re.sub(r"(?i)^(?:my|his|her|their)\s+", "the user's ", who)
    traits = [t for t in _lines(a.get("traits") or [], "Character", 6)]
    nicknames = _lines(a.get("nicknames") or [], "Nicknames")
    sayings = _lines(a.get("sayings") or [], "Things they say", 12)
    soul_text = a.get("soul") if isinstance(a.get("soul"), str) else ""

    doc, header = _load_doc(folder / "persona.yaml")
    set_value(doc, "name", name)
    set_value(doc, "nicknames", nicknames)
    set_value(doc, "greeting_no_name", f"{{part}}. {name} here, ready when you are.")
    _save_doc(folder / "persona.yaml", doc, header)

    soul = f"Who you are: you are {name}" + (f", {who}" if who else "") + "."
    if traits:
        soul += f" You are {_trait_list([t.lower() for t in traits])}."
    if soul_text.strip():  # a starting personality picked on the page
        soul += " " + re.sub(r"\s+", " ", soul_text.replace("{name}", name)).strip()[:3000]
    else:
        soul += (" You care how the user's day is going, and it shows in small ways. You're honest: when "
                 "something didn't work, you say so plainly.")
    soul += " When you use a name for the user, it's the one in their note, now and then, not in every reply."
    write_text(folder / "soul.md", soul + "\n", keep_backup=False)
    if sayings:
        speech = (folder / "speech.md").read_text(encoding="utf-8").rstrip("\n")
        speech += ("\n\nThings you like to say (now and then, in your own way, never all at once):\n"
                   + "\n".join(f'- "{s}"' for s in sayings))
        write_text(folder / "speech.md", speech + "\n", keep_backup=False)
    story = a.get("story") or {}
    if isinstance(story, dict) and str(story.get("text") or "").strip():
        save_lore(folder.name, story.get("title") or "a story", story.get("keywords") or [], story["text"])


def delete_persona(key: str) -> None:
    folder = mine_folder(key)
    if not folder.exists():
        raise SettingsError("Only your own personas can be removed (the ones that ship stay).")
    if layers().key == key:
        raise SettingsError("This is the active persona. Make another one active first.")
    retire(folder)
    personas._load.cache_clear()


def prompt_preview(key: str) -> dict:
    """The system prompt she'd get with this persona and the abilities switched on, and its rough size."""
    from mary.llm.prompts import new_prompt
    from mary.settings.choices import guides
    from mary.skills import enabled

    her = _fresh(key)
    text = new_prompt(guides(enabled(layers().effective)), her)
    own = her.prompt_section()
    return {"text": text, "tokens": round(len(text) / 3.8), "persona_tokens": round(len(own) / 3.8)}


# ------------------------------------------------------------------ you (the profile)


def profile_info(profile=None) -> dict:
    from mary.profile import Profile

    p = profile or Profile()
    return {"name": p.name, "about": p.about, "facts": list(p.facts)}


ABOUT_LIMIT = 3000  # every character goes with each request to the model


def save_profile(name, facts, profile=None, about=None) -> None:
    """Your name, what you wrote about yourself (None: unchanged) and what she remembers. Inside a running MARY this
    is her own profile, so it applies at once."""
    from mary.profile import Profile

    name = _line(name or "", "Your name", 60)
    facts = _lines(facts or [], "Memories", 200)
    if about is not None:
        if not isinstance(about, str) or len(about) > ABOUT_LIMIT:
            raise SettingsError(f"About you: at most {ABOUT_LIMIT} characters.")
        about = "\n".join(line.rstrip() for line in about.strip().splitlines())
    p = profile or Profile()
    backup(p.path)
    with p._lock:
        p.name, p.facts = name, facts
        if about is not None:
            p.about = about
        p._save()


# ------------------------------------------------------------------ keys (write-only)


def key_status() -> dict:
    """Which keys are set (yes or no; never the key)."""
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    return {"elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")), "llm": bool(read_key("LLM_API_KEY"))}


def read_key(name: str) -> str:
    """A key for this server's own use (testing a model service); never sent to the page."""
    from dotenv import dotenv_values

    return (os.environ.get(name) or (dotenv_values(ENV_FILE).get(name) if ENV_FILE.exists() else "") or "").strip()


def set_key(name: str, value: str) -> None:
    """Put a key into .env (replacing the old one). It's never read back out to the page."""
    if name not in KEYS:
        raise SettingsError("Unknown key.")
    value = value.strip() if isinstance(value, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9_\-.:/+]{16,256}", value):
        raise SettingsError("That doesn't look like an API key (letters, numbers and _ - . : / +).")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out, done = [], False
    for line in lines:
        if re.match(rf"^\s*(?:export\s+)?{name}\s*=", line):
            if not done:
                out.append(f"{name}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{name}={value}")
    write_text(ENV_FILE, "\n".join(out) + "\n", keep_backup=False)  # no copies of a secret lying around
    os.environ[name] = value
