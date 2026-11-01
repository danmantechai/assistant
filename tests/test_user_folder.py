"""Your private folder (user/): settings merged over the defaults, the profile kept there, nothing of yours in
the shipped defaults."""
import yaml

from mary import config, profile
from mary.config import DEFAULT_CONFIG, load_config, merge, user_config


def test_your_settings_merge_over_the_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"model": "qwen3:4b-q4_K_M"},
        "tts": {"provider": "elevenlabs", "elevenlabs": {"voice_id": "abc"}},
        "stt": {"words": ["Bolt Food"]},
    }), encoding="utf-8")
    cfg = load_config()
    assert cfg.llm.model == "qwen3:4b-q4_K_M" and cfg.llm.num_ctx == 8192        # one key changed, the rest kept
    assert cfg.tts.elevenlabs.voice_id == "abc" and cfg.tts.elevenlabs.model    # nested sections merge
    assert cfg.stt.words[-1] == "Bolt Food" and "Notepad" not in cfg.stt.words   # lists are replaced
    assert "Hey Mary" in cfg.stt.words                                          # (the persona adds her names)


def test_a_config_passed_by_path_is_read_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text("llm: {model: mine}\n", encoding="utf-8")
    other = tmp_path / "other.yaml"
    other.write_text("llm: {model: theirs}\n", encoding="utf-8")
    assert load_config(other).llm.model == "theirs"


def test_merge_never_changes_its_inputs():
    base, mine = {"a": {"b": 1, "c": 2}}, {"a": {"b": 9}}
    assert merge(base, mine) == {"a": {"b": 9, "c": 2}} and base == {"a": {"b": 1, "c": 2}}


def test_user_config_is_created_with_a_how_to(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    path = user_config()
    assert path.exists() and "overrides the same setting in config.yaml" in path.read_text(encoding="utf-8")
    assert yaml.safe_load(path.read_text(encoding="utf-8")) is None  # only comments: changes nothing


def test_an_old_profile_in_the_project_folder_moves_to_user(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(profile, "PROJECT_DIR", tmp_path)
    monkeypatch.delenv("MARY_USER_DIR")
    (tmp_path / "profile.yaml").write_text("name: Sam\nfacts: [I like tea]\n", encoding="utf-8")
    p = profile.Profile()
    assert p.path == tmp_path / "user" / "profile.yaml" and p.name == "Sam" and p.facts == ["I like tea"]
    assert not (tmp_path / "profile.yaml").exists()


def test_the_shipped_defaults_hold_nothing_personal():
    shipped = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    assert shipped["tts"]["provider"] == "kokoro" and shipped["tts"]["elevenlabs"]["voice_id"] == ""
    assert shipped["stt"]["provider"] == "local" and shipped["wakeword"]["enabled"] is False
