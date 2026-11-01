"""The settings page's store (mary/settings/store.py): every change lands in the right file, comments survive, the
shipped files are never touched, and keys only go in."""
import base64
import io
import json
import zipfile

import pytest
import yaml

from mary import persona as personas
from mary.config import DEFAULT_CONFIG, load_config
from mary.settings import schema, store

USER_CONFIG = """\
# My own settings.

# Her persona.
persona: gran

llm:
  model: qwen3:4b-q4_K_M        # slower, follows rules better
  think_by_default: true        # she thinks first

stt:
  provider: elevenlabs          # the key is in .env
  # The persona adds her names to these.
  words: ["Bolt Food", "Notepad"]
"""

GRAN = """\
# Grandma.
name: Gran
user_calls_her: [Gran, Granny]

# One each.
replies:
  - "Yes, dear?"
  - "I'm here."

voice:
  provider: kokoro
  voice: bf_emma                # calm
"""


@pytest.fixture
def mine(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    monkeypatch.setattr(store, "ENV_FILE", tmp_path / "env")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    personas._load.cache_clear()
    yield tmp_path
    personas._load.cache_clear()


def gran(root):
    folder = root / "personas" / "gran"
    (folder / "lore").mkdir(parents=True)
    (folder / "persona.yaml").write_text(GRAN, encoding="utf-8")
    (folder / "soul.md").write_text("You are Gran.\n", encoding="utf-8")
    (folder / "speech.md").write_text("How you talk.\n", encoding="utf-8")
    (folder / "lore" / "jollof.md").write_text("keywords: jollof, rice\nHer jollof story.\n", encoding="utf-8")
    return folder


def with_config(root, text=USER_CONFIG):
    gran(root)
    (root / "config.yaml").write_text(text, encoding="utf-8")


def shipped_snapshot():
    return {p: p.read_bytes() for p in sorted(personas.SHIPPED.rglob("*")) if p.is_file()} | \
        {DEFAULT_CONFIG: DEFAULT_CONFIG.read_bytes()}


# ------------------------------------------------------------------ settings (user/config.yaml)


def test_values_say_where_they_come_from(mine):
    with_config(mine)
    state = store.settings_state()
    v = state["values"]
    assert v["llm.model"] == {"value": "qwen3:4b-q4_K_M", "origin": "yours", "base": "qwen3.5:2b-q4_K_M"}
    assert v["brain.mode"]["origin"] == "default"
    assert state["persona"] == "gran"
    assert {f["path"] for f in state["pages"]["model"]} >= {"llm.model", "llm.think_by_default"}
    assert state["presets"] and all("{name}" in p["text"] for p in state["presets"])


def test_a_new_setting_joins_a_section_that_ends_in_a_list(mine):
    with_config(mine, "stt:\n  words:\n    - Bolt Food\n    - Notepad   # apps\n\n# The model.\nllm:\n  model: x\n")
    store.save_settings({"stt.provider": "elevenlabs"})
    text = (mine / "config.yaml").read_text(encoding="utf-8")
    assert "    - Notepad   # apps\n  provider: elevenlabs\n\n# The model.\nllm:" in text


def test_each_setting_is_on_one_page_of_its_section():
    paths = [f.path for page in schema.PAGES.values() for f in page]
    assert len(paths) == len(set(paths)) == len(schema.FIELDS)
    on = {f.path: page for page, fields in schema.PAGES.items() for f in fields}
    assert on["wakeword.enabled"] == on["hotkey.combo"] == "listening"   # the whole app, not one persona
    assert "llm.history_reset_minutes" not in on and "weather.place" not in on   # file-only: defaults suit most
    with pytest.raises(store.SettingsError):
        store.save_settings({"weather.place": "Lisbon"})


def test_saving_changes_only_that_value_and_keeps_the_comments(mine):
    with_config(mine)
    store.save_settings({"llm.think_level": "high", "stt.words": ["Bolt Food", "Notepad", "Kente"]})
    text = (mine / "config.yaml").read_text(encoding="utf-8")
    assert "# My own settings." in text and "# Her persona." in text and "# The persona adds her names" in text
    assert "think_by_default: true        # she thinks first" in text
    assert 'words: ["Bolt Food", "Notepad", "Kente"]' in text   # still one line, still quoted
    # a new setting joins the end of its section, above the blank line before the next one
    assert "think_by_default: true        # she thinks first\n  think_level: high\n\nstt:" in text
    assert load_config().llm.think_level == "high"
    assert len(list((mine / "backups").glob("config.yaml.*"))) == 1


def test_a_changed_value_loses_only_its_own_line_comment(mine):
    with_config(mine)
    store.save_settings({"stt.provider": "local", "llm.model": "qwen3:1.7b"})
    text = (mine / "config.yaml").read_text(encoding="utf-8")
    assert "the key is in .env" not in text and "slower, follows rules better" not in text
    assert "  # The persona adds her names to these.\n  words:" in text
    assert "model: qwen3:1.7b\n" in text


def test_setting_a_value_back_to_the_default_removes_it(mine):
    with_config(mine)
    store.save_settings({"llm.model": "qwen3.5:2b-q4_K_M"})
    data = yaml.safe_load((mine / "config.yaml").read_text(encoding="utf-8"))
    assert "model" not in data["llm"] and data["llm"]["think_by_default"] is True
    store.reset_setting("llm.think_by_default")
    data = yaml.safe_load((mine / "config.yaml").read_text(encoding="utf-8"))
    assert "llm" not in data and data["stt"]["provider"] == "elevenlabs"


def test_a_comment_only_file_keeps_its_header(mine):
    store.save_settings({"llm.think_by_default": True})
    text = (mine / "config.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Your own settings.") and "think_by_default: true" in text


def test_bad_values_and_unknown_settings_are_refused(mine):
    for changes in ({"paths.data": "C:/elsewhere"}, {"llm.think_by_default": "yes"},
                    {"llm.history_reset_minutes": 9999}, {"stt.provider": "groq"}, {"skills": ["apps", "rm"]},
                    {"llm.model": "bad name; rm"}, {"hotkey.combo": "ctrl+nosuchkey"},
                    {"vad.followup_s": 7}):
        with pytest.raises(store.SettingsError):
            store.save_settings(changes)
    assert not (mine / "config.yaml").exists() or "paths" not in (mine / "config.yaml").read_text()


def test_skills_keep_the_tool_order(mine):
    store.save_settings({"skills": ["clock", "apps", "weather"]})
    assert load_config().skills == ["apps", "weather", "clock"]


def test_choosing_a_persona(mine):
    with_config(mine, "llm:\n  num_ctx: 8192\n")
    store.use_persona("gran")
    text = (mine / "config.yaml").read_text(encoding="utf-8")
    assert text.startswith("persona: gran")
    assert load_config().persona == "gran"
    store.use_persona("mary")  # the default: the line goes
    assert "persona" not in yaml.safe_load((mine / "config.yaml").read_text(encoding="utf-8"))
    with pytest.raises(store.SettingsError):
        store.use_persona("nobody")
    with pytest.raises(store.SettingsError):
        store.use_persona("../../etc")


# ------------------------------------------------------------------ personas


def test_editing_a_shipped_persona_makes_your_own_copy(mine):
    before = shipped_snapshot()
    store.save_persona("mary", {"greeting": "{part}, {name}. Here I am.", "soul": "You are kind."})
    assert shipped_snapshot() == before
    copy = mine / "personas" / "mary"
    assert (copy / "soul.md").read_text(encoding="utf-8") == "You are kind.\n"
    assert "# MARY's default persona." in (copy / "persona.yaml").read_text(encoding="utf-8")  # comments kept
    info = store.persona_info("mary")
    assert info["private"] and info["greeting"] == "{part}, {name}. Here I am." and info["ships"]
    assert personas.load("mary").soul == "You are kind."


def test_persona_settings_keep_their_comments_and_quotes(mine):
    folder = gran(mine)
    store.save_persona("gran", {"replies": ["Yes, dear?", "I'm here.", "Mm?"], "nicknames": ["Gran"],
                                "voice": {"provider": "elevenlabs", "elevenlabs": {"voice_id": "abc123XYZ"}}})
    text = (folder / "persona.yaml").read_text(encoding="utf-8")
    assert "# One each.\nreplies:\n  - \"Yes, dear?\"\n  - \"I'm here.\"\n  - \"Mm?\"" in text
    assert "voice: bf_emma                # calm" in text
    assert "user_calls_her: [Gran]" in text and "nicknames" not in text  # an older file keeps its own key
    her = store.persona_info("gran")
    assert her["voice"]["provider"] == "elevenlabs" and her["voice"]["elevenlabs"]["voice_id"] == "abc123XYZ"
    cfg = load_config()  # gran isn't active: the active persona's voice is unchanged
    assert cfg.tts.provider == "kokoro"


@pytest.mark.parametrize("fields", [
    {"name": ""}, {"replies": "Yes?"}, {"greeting": "a\nb"}, {"voice": {"provider": "azure"}},
    {"voice": {"elevenlabs": {"voice_id": "../x"}}}, {"voice": {"speed": 5}}, {"wake": "x"}, {"soul": "x" * 30000},
])
def test_bad_persona_changes_are_refused(mine, fields):
    gran(mine)
    with pytest.raises(store.SettingsError):
        store.save_persona("gran", fields)


def test_stories_are_saved_renamed_and_retired(mine):
    folder = gran(mine)
    name = store.save_lore("gran", "Sunday rice", ["jollof", "sunday"], "She made it\nevery Sunday.", "jollof.md")
    assert name == "Sunday_rice.md" and not (folder / "lore" / "jollof.md").exists()
    assert (folder / "lore" / name).read_text(encoding="utf-8") == "keywords: jollof, sunday\nShe made it every Sunday.\n"
    assert personas.load("gran").lore[0].keywords == ["jollof", "sunday"]
    store.delete_lore("gran", name)
    assert not list((folder / "lore").glob("*.md"))
    assert list((mine / "backups").glob("personas__gran__lore__jollof.md.*"))  # moved, not deleted
    with pytest.raises(store.SettingsError):
        store.delete_lore("gran", "../persona.yaml")
    with pytest.raises(store.SettingsError):
        store.save_lore("gran", "No words", [], "text")


CHAT = """\
12/31/23, 9:41 PM - Gran: Happy new year my dear
12/31/23, 9:42 PM - Sam: Same to you!
1/1/24, 8:00 AM - Gran: Did you eat?
Call me later
1/1/24, 8:01 AM - Gran: <Media omitted>
"""


def test_messages_come_in_from_a_chat_export_or_its_zip(mine):
    from mary.persona_io import whatsapp_senders

    gran(mine)
    assert whatsapp_senders(CHAT) == [("Gran", 2), ("Sam", 1)]
    assert store.inspect_texts("chat.txt", CHAT.encode()) == {"kind": "chat", "senders": [("Gran", 2), ("Sam", 1)]}
    with pytest.raises(store.SettingsError):
        store.import_texts("gran", "chat.txt", CHAT.encode())  # a chat: whose messages?
    count, name = store.import_texts("gran", "WhatsApp Chat with Gran.txt", CHAT.encode(), "Gran")
    assert (count, name) == (2, "WhatsApp_Chat_with_Gran.txt")
    assert personas.load("gran").texts == ["Happy new year my dear", "Did you eat?\nCall me later"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", CHAT)
    assert store.chat_text("export.zip", buf.getvalue()) == CHAT
    with pytest.raises(store.SettingsError):
        store.import_texts("gran", "chat.txt", CHAT.encode(), "Nobody")
    store.delete_texts("gran", name)
    assert personas.load("gran").texts == []


def test_messages_come_in_from_any_text_file(mine):
    gran(mine)
    text = "Good morning my dear.\n\nDon't forget your umbrella,\nit will rain.\n\n\n"
    assert store.inspect_texts("notes.txt", text.encode()) == {"kind": "plain", "count": 2}
    assert store.import_texts("gran", "her notes.txt", text.encode()) == (2, "her_notes.txt")
    assert personas.load("gran").texts == ["Good morning my dear.", "Don't forget your umbrella,\nit will rain."]
    with pytest.raises(store.SettingsError):
        store.inspect_texts("empty.txt", b"  \n\n ")


def test_wake_phrases_are_saved_to_the_persona(mine):
    folder = gran(mine)
    store.save_persona("gran", {"wake": [
        {"phrase": "hey  granny", "names": ["Granny", "grannie"]},
        {"model": "hey_jarvis", "phrase": "anything"},          # a built-in keeps its own phrase
        {"phrase": "Hey Granny"},                                # the same model twice: kept once
    ]})
    her = personas.load("gran")
    assert [(w.phrase, w.model, w.names) for w in her.wake] == [
        ("Hey Granny", "hey_granny", ["granny", "grannie"]), ("Hey Jarvis", "hey_jarvis", ["jarvis"])]
    text = (folder / "persona.yaml").read_text(encoding="utf-8")
    assert "# Grandma." in text and "names: [granny, grannie]" in text
    info = store.persona_info("gran")["wake"]
    assert info[0]["status"] == "needs training" and info[1]["builtin"] and info[1]["status"] == "built-in"
    for bad in ([{"phrase": "hey"}], [{"phrase": ""}], [{}] * 5, "Hey Gran"):
        with pytest.raises(store.SettingsError):
            store.save_persona("gran", {"wake": bad})


def test_changing_wake_phrases_keeps_the_comments_around_them(mine):
    folder = mine / "personas" / "gran"
    folder.mkdir(parents=True)
    (folder / "persona.yaml").write_text("""\
name: Gran
wake:
  - phrase: Hey Gran                # the one she likes
    model: hey_gran
    names: [gran]
    bare: false                     # "Gran's coming" isn't the wake word

# Her replies.
replies: ["Yes?"]
""", encoding="utf-8")
    store.save_persona("gran", {"wake": [{"phrase": "Hey Gran", "model": "hey_gran", "names": ["gran", "granny"]},
                                         {"model": "alexa"}]})
    text = (folder / "persona.yaml").read_text(encoding="utf-8")
    assert "# the one she likes" in text and '# "Gran\'s coming" isn\'t the wake word' in text
    assert "names: [gran, granny]" in text and "model: alexa" in text
    assert text.index("model: alexa") < text.index("# Her replies.") < text.index("replies:")
    store.save_persona("gran", {"wake": [{"model": "alexa"}]})
    text = (folder / "persona.yaml").read_text(encoding="utf-8")
    assert "hey_gran" not in text and "# Her replies.\nreplies:" in text


def test_a_new_persona_from_the_guided_questions(mine):
    before = shipped_snapshot()
    store.new_persona("ama", answers={
        "name": "Ama", "who": "my grandmother", "nicknames": ["Nana"], "traits": ["Warm", "funny", "patient"],
        "sayings": ["Have you eaten?"],
        "story": {"title": "Her garden", "keywords": ["garden", "tomatoes"], "text": "She grew tomatoes."}})
    assert shipped_snapshot() == before
    her = personas.load("ama")
    assert her.name == "Ama" and her.nicknames == ["Nana"]
    assert "nicknames: [Nana]" in (mine / "personas" / "ama" / "persona.yaml").read_text(encoding="utf-8")
    assert her.soul.startswith("Who you are: you are Ama, the user's grandmother. You are warm, funny and patient.")
    assert '- "Have you eaten?"' in her.speech
    assert [entry.name for entry in her.lore] == ["Her garden"]
    assert her.greeting_no_name == "{part}. Ama here, ready when you are."
    with pytest.raises(store.SettingsError):
        store.new_persona("ama")
    store.new_persona("ama2", copy_from="ama")
    assert personas.load("ama2").soul == her.soul


def test_removing_a_persona_keeps_a_copy_and_never_the_active_one(mine):
    with_config(mine)
    with pytest.raises(store.SettingsError):
        store.delete_persona("gran")       # active
    with pytest.raises(store.SettingsError):
        store.delete_persona("mary")       # ships
    store.new_persona("spare")
    store.delete_persona("spare")
    assert not (mine / "personas" / "spare").exists()
    assert list((mine / "backups").glob("personas__spare.*"))


def test_the_list_and_the_prompt_preview(mine):
    with_config(mine)
    listed = {p["key"]: p for p in store.persona_list()}
    assert listed["gran"]["active"] and listed["gran"]["private"] and not listed["mary"]["active"]
    assert "_template" not in listed
    preview = store.prompt_preview("gran")
    assert "You are Gran." in preview["text"] and "How you talk." in preview["text"]
    assert "create_note" in preview["text"]  # the abilities' guide lines are in it
    assert 0 < preview["persona_tokens"] < preview["tokens"]


def test_the_preview_matches_what_the_agent_builds(mine):
    from mary import skills
    from mary.llm.prompts import new_prompt
    from mary.settings.choices import guides

    names = skills.enabled(load_config())
    assert guides(names) == skills.guides(skills.load(names))
    assert store.prompt_preview("mary")["text"] == new_prompt(skills.guides(skills.load(names)), personas.load())


# ------------------------------------------------------------------ you, keys, backups


def test_your_name_and_what_she_remembers(mine):
    from mary.profile import Profile

    store.save_profile("Doctor", ["I like jazz", " ", "My sister is Ada"])
    p = Profile()
    assert p.name == "Doctor" and p.facts == ["I like jazz", "My sister is Ada"]
    seen = []
    p.listeners.append(lambda: seen.append(1))
    store.save_profile("Doc", [], profile=p)   # a running MARY's own profile: her prompt is rebuilt at once
    assert seen == [1] and Profile().name == "Doc"
    assert store.profile_info() == {"name": "Doc", "about": "", "facts": []}


def test_about_you_in_your_own_words(mine):
    from mary.profile import Profile

    store.save_profile("Sam", ["I prefer Chrome"], about="  I teach maths.\nI have two kids.  \n")
    p = Profile()
    assert p.about == "I teach maths.\nI have two kids." and p.facts == ["I prefer Chrome"]
    assert p.prompt_section().startswith("Background, not a request. About me, in my own words: I teach maths.")
    assert "- I prefer Chrome" in p.prompt_section()
    store.save_profile("Sam", ["I prefer Chrome"])          # About you left out: kept as it was
    assert Profile().about == "I teach maths.\nI have two kids."
    store.save_profile("Sam", [], about="")
    assert Profile().about == "" and "\nabout:" not in (mine / "profile.yaml").read_text(encoding="utf-8")
    with pytest.raises(store.SettingsError):
        store.save_profile("Sam", [], about="x" * (store.ABOUT_LIMIT + 1))


def test_a_key_goes_into_env_and_replaces_the_old_one(mine, monkeypatch):
    env = mine / "env"
    env.write_text("OTHER=1\nELEVENLABS_API_KEY=old\n", encoding="utf-8")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    store.set_key("ELEVENLABS_API_KEY", "  sk_" + "a1" * 12 + "  ")
    assert env.read_text(encoding="utf-8") == "OTHER=1\nELEVENLABS_API_KEY=sk_" + "a1" * 12 + "\n"  # release-check: ignore
    assert not (mine / "backups").exists()  # no copies of a key
    for bad in ("short", "has space in it 1234567890", "x" * 300, None):
        with pytest.raises(store.SettingsError):
            store.set_key("ELEVENLABS_API_KEY", bad)
    with pytest.raises(store.SettingsError):
        store.set_key("PATH", "x" * 30)


def test_only_the_last_backups_are_kept(mine):
    for threads in range(8):
        store.save_settings({"llm.num_thread": threads + 1})
    assert len(list((mine / "backups").glob("config.yaml.*"))) == store.KEEP_BACKUPS


def test_voices_set_saves_to_her_persona(mine, capsys):
    from mary.voices import set_voice

    before = shipped_snapshot()
    set_voice("bf_emma")
    assert shipped_snapshot() == before
    assert personas.load("mary").voice["voice"] == "bf_emma" and load_config().tts.voice == "bf_emma"
    assert "user/personas/mary" in capsys.readouterr().out


def test_skills_are_described_in_plain_words():
    from mary.settings.choices import skill_list

    listed = skill_list()
    assert [s["name"] for s in listed] == __import__("mary.skills", fromlist=["ALL"]).ALL
    assert all(s["about"] and "_" not in s["about"] and s["title"] and s["group"] for s in listed)
    assert {s["group"] for s in listed} == {"Apps and windows", "Your PC", "Web", "Everyday"}


def test_every_page_field_exists_in_the_shipped_defaults():
    defaults = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    missing = [p for p in schema.FIELDS if store.get(defaults, p, "<missing>") == "<missing>"]
    assert missing == []


def test_the_model_page_checks_its_values():
    assert schema.check("brain.api.url", " https://openrouter.ai/api/v1/ ") == "https://openrouter.ai/api/v1"
    assert schema.check("brain.api.url", "") == ""   # cleared
    for bad in ("openrouter.ai/api/v1", "ftp://example.com", "https://a b", 5):
        with pytest.raises(ValueError):
            schema.check("brain.api.url", bad)
    assert schema.check("brain.api.model", "anthropic/claude-sonnet") == "anthropic/claude-sonnet"
    assert schema.check("brain.api.model", "") == ""
    with pytest.raises(ValueError):
        schema.check("llm.model", "")   # Ollama always needs one
    assert schema.check("brain.provider", "api") == "api" and schema.check("llm.think_level", "high") == "high"
    for path, bad in (("brain.provider", "openai"), ("llm.think_level", "max")):
        with pytest.raises(ValueError):
            schema.check(path, bad)


def test_the_model_service_key_goes_in_and_is_only_said_to_be_there(mine, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")   # set_key puts it in the environment too: undone after the test
    assert store.key_status()["llm"] is False
    store.set_key("LLM_API_KEY", "gsk_" + "Z9" * 20)
    assert store.key_status() == {"elevenlabs": False, "llm": True}
    assert store.read_key("LLM_API_KEY") == "gsk_" + "Z9" * 20
    assert "LLM_API_KEY=gsk_" in (mine / "env").read_text(encoding="utf-8")
