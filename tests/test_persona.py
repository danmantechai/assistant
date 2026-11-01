"""Persona packs (mary/persona.py) and bringing them in and out (mary/persona_io.py)."""
import base64
import json

import pytest
import yaml

from mary import persona as personas
from mary.config import load_config
from mary.persona import Persona, Wake
from mary.persona_io import export_card, import_card, import_whatsapp, read_card, whatsapp_messages


def make(root, name, yaml_text="name: MARY\n", **files):
    folder = root / "personas" / name
    folder.mkdir(parents=True)
    (folder / "persona.yaml").write_text(yaml_text, encoding="utf-8")
    for rel, text in files.items():
        path = folder / rel.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


@pytest.fixture
def mine(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    personas._load.cache_clear()
    yield tmp_path
    personas._load.cache_clear()


def test_the_shipped_persona_is_the_default():
    her = personas.load()
    assert her.key == "mary" and her.name == "MARY" and her.soul and her.speech
    assert [w.phrase for w in her.wake] == ["Hey Mary"]
    assert her.soul in her.prompt_section() and her.speech in her.prompt_section()


def test_yours_wins_over_a_shipped_one_and_unknown_names_fall_back(mine):
    make(mine, "mary", "name: Mine\n")
    assert personas.load("mary").name == "Mine"
    assert personas.load("nobody").key == "mary"


def test_settings_order_defaults_then_persona_then_yours(mine):
    make(mine, "gran", yaml.safe_dump({"voice": {"provider": "elevenlabs", "elevenlabs": {"voice_id": "v1"}},
                                       "replies": ["Yes, love?"], "user_calls_her": ["Gran", "Ma"],
                                       "wake": [{"phrase": "Hey Gran", "model": "hey_gran", "names": ["gran"]}]}))
    (mine / "config.yaml").write_text("persona: gran\nstt: {words: [Spotify]}\n", encoding="utf-8")
    cfg = load_config()
    assert cfg.tts.provider == "elevenlabs" and cfg.tts.elevenlabs.voice_id == "v1"   # her voice
    assert cfg.tts.elevenlabs.model                                                  # defaults kept
    assert cfg.wakeword.replies == ["Yes, love?"] and cfg.wakeword.model.endswith("hey_gran.onnx")
    assert cfg.stt.words == ["Hey Gran", "Gran", "Spotify"]   # her names first; "Ma" is too short to help
    (mine / "config.yaml").write_text("persona: gran\ntts: {provider: kokoro}\n", encoding="utf-8")
    assert load_config().tts.provider == "kokoro"             # your own settings win


def test_lore_comes_up_only_when_its_words_are_said(mine):
    make(mine, "gran", **{"lore__christmas.md": "keywords: christmas, jollof\nShe always made jollof.\n",
                          "lore__broken.md": "no keywords line here\n",
                          "texts__chat.txt": "See you soon dear\n\nGod bless you\n"})
    her = personas.load("gran")
    assert [e.name for e in her.lore] == ["christmas"]
    assert her.extras("what's the weather") == []
    assert her.extras("remember Christmas at home?") == ["from your memories (christmas): She always made jollof."]
    assert her.extras("make jollofrice") == []                                    # whole words only
    writing = her.extras("write a note for me", writing=True)
    assert len(writing) == 1 and writing[0].startswith("how you write (examples, not facts): ")


@pytest.mark.parametrize("said, kept", [
    ("Got it, Mom.", "Got it."),
    ("I'm sorry, Mom. I can't call anyone.", "I'm sorry. I can't call anyone."),
    ("Mom, I can't do that.", "I can't do that."),
    ("Okay, Mum!", "Okay!"),
    ("Your mom's birthday is on Friday.", "Your mom's birthday is on Friday."),
    ("I'll remind you to call Mom.", "I'll remind you to call Mom."),
])
def test_her_own_name_is_never_used_as_yours(said, kept):
    assert Persona("t", None, nicknames=["Mom", "Mum"]).fix_address(said) == kept


def test_the_reply_filter_applies_the_guard():
    from mary.llm.replies import ReplyFilter

    f = ReplyFilter(greetings_ok=True, fix=Persona("t", None, nicknames=["Mom"]).fix_address)
    assert (f.feed("Got it, Mom. ") + f.feed("I saved the note. ") + f.flush()).strip() == "Got it. I saved the note."


def test_wake_keys_map_to_phrases():
    her = Persona("t", None, wake=[Wake("Hey Gran", "hey_gran", names=["gran"])])
    assert her.wake_for("hey_gran").phrase == "Hey Gran"
    assert her.wake_for("C:/x/data/models/wakeword/hey_gran.onnx").phrase == "Hey Gran"
    assert her.wake_for("hey_jarvis") is None


def test_greeting_comes_from_the_persona(monkeypatch):
    from types import SimpleNamespace as NS

    from mary.core import Mary

    her = Persona("t", None, greeting="{part}, my {name}!", greeting_no_name="{part}, dear.")
    fake = NS(agent=NS(persona=her), profile=NS(name="Sam"))
    assert Mary.greeting(fake).endswith(", my Sam!")
    fake.profile.name = ""
    assert Mary.greeting(fake).endswith(", dear.")


# ------------------------------------------------------------------ WhatsApp


CHAT = """\
12/31/23, 9:41 PM - Messages and calls are end-to-end encrypted.
12/31/23, 9:41 PM - Mom: Happy new year my son
12/31/23, 9:42 PM - Sam: Thank you Mom
1/1/24, 8:00 AM - Mom: Did you eat?
Don't skip breakfast.
1/1/24, 8:01 AM - Mom: <Media omitted>
[01/01/2024, 08:05:12] Mom: \u200eimage omitted
[01/01/2024, 08:06:30] Mom: God bless you
"""


def test_her_messages_are_picked_out_of_a_whatsapp_export():
    assert whatsapp_messages(CHAT, "mom") == ["Happy new year my son", "Did you eat?\nDon't skip breakfast.",
                                              "God bless you"]


def test_whatsapp_goes_into_a_private_persona_only(mine):
    chat = mine / "WhatsApp Chat with Mom.txt"
    chat.write_text(CHAT, encoding="utf-8")
    with pytest.raises(SystemExit):
        import_whatsapp(chat, "Mom", "mary")  # the shipped one isn't yours to fill
    make(mine, "mom")
    count, out = import_whatsapp(chat, "Mom", "mom")
    assert count == 3 and out.parent == mine / "personas" / "mom" / "texts"
    assert personas._load.__wrapped__("mom", mine / "personas" / "mom").texts[0] == "Happy new year my son"


# ------------------------------------------------------------------ Character Cards


def test_a_card_round_trip_keeps_her_and_leaves_your_voice_out(mine):
    make(mine, "gran", yaml.safe_dump({"name": "Gran", "user_calls_her": ["Gran"], "greeting": "{part}, {name}!",
                                       "voice": {"provider": "elevenlabs", "elevenlabs": {"voice_id": "secret"}}}),
         **{"soul.md": "You are Gran.", "speech.md": "User: Hi\nYou: Hello, love!",
            "lore__garden.md": "keywords: garden\nShe grew tomatoes.\n"})
    card = export_card("gran")
    assert card["spec"] == "chara_card_v2" and card["data"]["description"] == "You are Gran."
    assert card["data"]["character_book"]["entries"][0]["keys"] == ["garden"]
    assert "secret" not in json.dumps(card) and "secret" in json.dumps(export_card("gran", with_voice=True))
    path = mine / "gran.json"
    path.write_text(json.dumps(card), encoding="utf-8")
    folder = import_card(path, "gran2")
    personas._load.cache_clear()
    her = personas.load("gran2")
    assert her.name == "Gran" and her.soul == "You are Gran." and her.nicknames == ["Gran"]
    assert her.lore[0].keywords == ["garden"] and folder == mine / "personas" / "gran2"


def test_a_cards_personality_becomes_hers_and_isnt_lost(mine):
    """Other apps' cards put traits in "personality": that's part of who she is (soul.md), which MARY uses."""
    card = {"spec": "chara_card_v2", "data": {"name": "Coach", "description": "You are Coach, a running coach.",
                                              "personality": "Upbeat, direct, writes short texts with lots of emojis.",
                                              "scenario": "{{user}} is training for a 10k."}}
    path = mine / "coach.json"
    path.write_text(json.dumps(card), encoding="utf-8")
    folder = import_card(path, "coach")
    personas._load.cache_clear()
    her = personas.load("coach")
    assert her.soul == ("You are Coach, a running coach.\n\nUpbeat, direct, writes short texts with lots of emojis."
                        "\n\nthe user is training for a 10k.")
    assert not (folder / "texting.md").exists()
    assert export_card("coach")["data"]["personality"] == ""   # all of it travels in the description


def test_a_png_card_is_read(tmp_path):
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("chara", base64.b64encode(json.dumps({"data": {"name": "Ama"}}).encode()).decode())
    Image.new("RGB", (2, 2)).save(tmp_path / "ama.png", pnginfo=info)
    assert read_card(tmp_path / "ama.png")["data"]["name"] == "Ama"
