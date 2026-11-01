"""Bringing a persona in and out: python -m mary.persona <command>

    list                                   the personas there are (yours, then the ones that ship)
    new <name>                             a private persona to fill in, from the template (user/personas/<name>)
    import-whatsapp <chat.txt> --sender "<her name in the chat>" --into <persona>
                                           her messages from a WhatsApp chat export, as examples of how she writes
    export-card <persona> [--out file.json] [--with-voice]
                                           a Character Card V2 (the format character apps share): soul, speech,
                                           greeting, lore. Your ElevenLabs voice ID only with --with-voice.
    import-card <card.json|card.png> [--as <name>]
                                           a Character Card V2/V3 into user/personas/<name>

Everything written goes into user/personas/, which is private: it's never published.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from mary import persona as personas
from mary.config import user_dir

# "12/31/23, 9:41 PM - Mom: text" (Android) or "[31/12/2023, 21:41:05] Mom: text" (iPhone).
_WA_LINE = re.compile(r"^\[?(\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),?\s+(\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[AaPp]\.?\s?[Mm]\.?)?)"
                      r"\]?\s*(?:[-–]\s*)?([^:]{1,60}?):\s?(.*)$")
_WA_SKIP = re.compile(r"<media omitted>|<attached:|\b(?:image|video|audio|sticker|gif|document) omitted\b|"
                      r"this message was deleted|you deleted this message|missed (?:voice|video) call|"
                      r"^null$|end-to-end encrypted|\(file attached\)", re.I)


def private_folder(name: str) -> Path:
    return user_dir() / "personas" / name


def whatsapp_senders(text: str) -> list[tuple[str, int]]:
    """Who wrote in a WhatsApp export, most messages first: [("Mom", 412), ("Sam", 380)]."""
    counts: dict[str, int] = {}
    for raw in text.splitlines():
        m = _WA_LINE.match(raw.replace("‎", "").replace(" ", " ").strip())
        if m and not _WA_SKIP.search(m.group(4)):
            counts[m.group(3).strip()] = counts.get(m.group(3).strip(), 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def whatsapp_messages(text: str, sender: str) -> list[str]:
    """Her messages in a WhatsApp export (multi-line ones joined back up), without media and system lines."""
    messages, current = [], None
    who = sender.strip().lower()
    for raw in text.splitlines():
        line = raw.replace("‎", "").replace(" ", " ").strip()
        m = _WA_LINE.match(line)
        if m:
            if current is not None:
                messages.append(current)
            current = m.group(4).strip() if m.group(3).strip().lower() == who else None
        elif current is not None and line:
            current += "\n" + line  # a message going on over several lines
    if current is not None:
        messages.append(current)
    return [msg for msg in messages if msg and not _WA_SKIP.search(msg)]


def import_whatsapp(chat: Path, sender: str, into: str) -> tuple[int, Path]:
    folder = private_folder(into)
    if not (folder / "persona.yaml").exists():
        raise SystemExit(f"No persona {into!r} in user/personas. Make one first: python -m mary.persona new {into}")
    found = whatsapp_messages(chat.read_text(encoding="utf-8", errors="replace"), sender)
    if not found:
        raise SystemExit(f"No messages from {sender!r} in {chat.name}. Use her name exactly as the chat shows it.")
    out = folder / "texts" / f"whatsapp_{re.sub(r'[^A-Za-z0-9]+', '_', chat.stem).strip('_') or 'chat'}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(found) + "\n", encoding="utf-8")
    return len(found), out


def new_persona(name: str) -> Path:
    folder = private_folder(name)
    if folder.exists():
        raise SystemExit(f"{folder} already exists.")
    shutil.copytree(personas.SHIPPED / "_template", folder)
    return folder


# ------------------------------------------------------------------ Character Cards


def export_card(key: str, with_voice: bool = False) -> dict:
    her = personas.load(key)
    if her.folder is None:
        raise SystemExit(f"No persona {key!r}.")
    data = yaml.safe_load((her.folder / "persona.yaml").read_text(encoding="utf-8")) or {}
    extra = {k: data[k] for k in ("nicknames", "user_calls_her", "wake", "replies", "morning_replies", "greeting")
             if k in data}
    voice = dict(data.get("voice") or {})
    if not with_voice:
        voice.get("elevenlabs", {}).pop("voice_id", None)  # a voice made from someone's recordings stays yours
    if voice:
        extra["voice"] = voice
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": her.name,
        "description": her.soul,      # who they are, how they talk and write: all in the personality
        "personality": "",
        "scenario": "",
        "first_mes": her.greeting.format(part="Hello", name="{{user}}"),
        "mes_example": "<START>\n" + her.speech if her.speech else "",
        "system_prompt": "",
        "post_history_instructions": "",
        "alternate_greetings": [],
        "character_book": {"entries": [
            {"keys": entry.keywords, "content": entry.text, "name": entry.name, "enabled": True,
             "insertion_order": i, "extensions": {}} for i, entry in enumerate(her.lore)]},
        "tags": ["MARY"],
        "creator": "",
        "creator_notes": "Exported from MARY (persona pack). Voice, wake words and replies are under extensions.mary.",
        "character_version": "",
        "extensions": {"mary": extra},
    }}


def read_card(path: Path) -> dict:
    """A card's JSON: a .json file, or a .png with it in a tEXt chunk (ccv3 preferred over chara)."""
    return parse_card(path.read_bytes(), path.name)


def parse_card(data: bytes, label: str = "the card") -> dict:
    """A card's JSON from a file's bytes: JSON, or a PNG with it in a tEXt chunk."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        import io

        from PIL import Image

        text = Image.open(io.BytesIO(data)).text
        blob = text.get("ccv3") or text.get("chara")
        if not blob:
            raise SystemExit(f"{label} has no character card in it.")
        return json.loads(base64.b64decode(blob).decode("utf-8"))
    try:
        card = json.loads(data.decode("utf-8-sig"))
    except ValueError:
        raise SystemExit(f"{label} isn't a character card (a .json or .png file).") from None
    if not isinstance(card, dict):
        raise SystemExit(f"{label} isn't a character card.")
    return card


def import_card(path: Path, name: str | None = None) -> Path:
    return persona_from_card(read_card(path), name)


def persona_from_card(card: dict, name: str | None = None) -> Path:
    """A private persona (user/personas/<name>) made from a card's JSON."""
    d = card.get("data") or card  # V1 cards have the fields at the top level
    key = name or re.sub(r"[^a-z0-9]+", "_", str(d.get("name") or "persona").lower()).strip("_")
    folder = private_folder(key)
    if folder.exists():
        raise SystemExit(f"{folder} already exists; pass --as <another name>.")
    char, user = str(d.get("name") or "MARY"), "the user"
    fill = lambda s: str(s or "").replace("{{char}}", char).replace("{{user}}", user).strip()  # noqa: E731
    extra = ((d.get("extensions") or {}).get("mary") or {})
    persona = {"name": char, **{k: v for k, v in extra.items() if k != "name"}}
    folder.mkdir(parents=True)
    (folder / "persona.yaml").write_text(yaml.safe_dump(persona, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # A card's description, personality and scenario all describe who they are: MARY's personality (soul.md).
    soul = "\n\n".join(p for p in (fill(d.get("description")), fill(d.get("personality")), fill(d.get("scenario")))
                       if p)
    (folder / "soul.md").write_text(soul + "\n", encoding="utf-8")
    examples = fill(d.get("mes_example")).replace("<START>", "").strip()
    (folder / "speech.md").write_text(examples + "\n", encoding="utf-8")
    for i, entry in enumerate((d.get("character_book") or {}).get("entries") or []):
        if entry.get("enabled", True) and entry.get("keys") and entry.get("content"):
            lore = folder / "lore" / f"{re.sub(r'[^A-Za-z0-9]+', '_', str(entry.get('name') or f'entry_{i}')).strip('_')}.md"
            lore.parent.mkdir(exist_ok=True)
            lore.write_text(f"keywords: {', '.join(entry['keys'])}\n{fill(entry['content'])}\n", encoding="utf-8")
    return folder


# ------------------------------------------------------------------ the command


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.persona", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("new")
    p.add_argument("name")
    p = sub.add_parser("import-whatsapp")
    p.add_argument("chat", type=Path)
    p.add_argument("--sender", required=True)
    p.add_argument("--into", required=True)
    p = sub.add_parser("export-card")
    p.add_argument("persona")
    p.add_argument("--out", type=Path)
    p.add_argument("--with-voice", action="store_true")
    p = sub.add_parser("import-card")
    p.add_argument("card", type=Path)
    p.add_argument("--as", dest="name")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "list":
        for root, label in ((user_dir() / "personas", "yours (private)"), (personas.SHIPPED, "ships with MARY")):
            names = sorted(p.parent.name for p in root.glob("*/persona.yaml") if not p.parent.name.startswith("_"))
            print(f"{label}: {', '.join(names) or '-'}")
    elif args.cmd == "new":
        folder = new_persona(args.name)
        print(f"Made {folder}. Fill in its files, then set  persona: {args.name}  in user/config.yaml.")
    elif args.cmd == "import-whatsapp":
        count, out = import_whatsapp(args.chat, args.sender, args.into)
        print(f"Kept {count} of her messages in {out} (private). A couple are shown to her whenever she writes.")
    elif args.cmd == "export-card":
        out = args.out or Path(f"{args.persona}.card.json")
        out.write_text(json.dumps(export_card(args.persona, args.with_voice), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        print(f"Wrote {out}" + ("" if args.with_voice else " (without your ElevenLabs voice ID)."))
    elif args.cmd == "import-card":
        folder = import_card(args.card, args.name)
        print(f"Made {folder}. Set  persona: {folder.name}  in user/config.yaml to use it.")
