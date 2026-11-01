"""Load the settings into attribute-style sections (cfg.llm.model, cfg.paths.notes, ...).

Two layers: config.yaml holds the defaults that ship with MARY, and user/config.yaml holds only your own
changes, merged over them. user/ is your private folder (settings, profile, personas, memory): it's
gitignored and never published.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_DIR / "config.yaml"


def user_dir() -> Path:
    """Your private folder. MARY_USER_DIR moves it (the tests point it at an empty folder)."""
    return Path(os.environ.get("MARY_USER_DIR") or PROJECT_DIR / "user")


_USER_CONFIG_HEADER = """\
# Your own settings. Anything here overrides the same setting in config.yaml (which holds the defaults and
# explains every option); copy a setting here and change it, keeping its section. This folder (user/) is
# private: it's never published. Restart MARY after editing.
#
# Example:
#   llm:
#     model: qwen3:4b-q4_K_M
#   tts:
#     voice: bf_emma
"""


def user_config() -> Path:
    """user/config.yaml, created with a short how-to if it isn't there yet."""
    path = user_dir() / "config.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_USER_CONFIG_HEADER, encoding="utf-8")
    return path


def merge(base: dict, mine: dict) -> dict:
    """mine over base: nested settings merge key by key; anything else (lists included) is replaced."""
    out = dict(base)
    for key, value in mine.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


class Section(dict):
    """A dict whose keys are also attributes; nested dicts become Sections."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"config has no setting '{name}'") from None

    @classmethod
    def wrap(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return cls({k: cls.wrap(v) for k, v in value.items()})
        if isinstance(value, list):
            return [cls.wrap(v) for v in value]
        return value


def expand_path(template: str) -> Path:
    """Resolve {Desktop}/{Documents}/{Downloads}/{Pictures}/{project} placeholders and ~."""
    from mary.winutil import known_folder  # local import: winutil is Windows-only

    out = template
    for name in ("Desktop", "Documents", "Downloads", "Pictures"):
        token = "{" + name + "}"
        if token in out:
            out = out.replace(token, str(known_folder(name)))
    out = out.replace("{project}", str(PROJECT_DIR))
    return Path(os.path.expandvars(os.path.expanduser(out)))


def load_config(path: str | os.PathLike | None = None) -> Section:
    """config.yaml, then what the persona brings (her voice, wake words and replies), then user/config.yaml over
    both, so your own settings always win. A config file passed by path is read alone."""
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if path == DEFAULT_CONFIG:
        from mary import persona

        mine = {}
        if (user_dir() / "config.yaml").exists():
            with open(user_dir() / "config.yaml", encoding="utf-8") as f:
                mine = yaml.safe_load(f) or {}
        her = persona.load(mine.get("persona") or data.get("persona"))
        data = merge(merge(data, her.config()), mine)
        stt = data.setdefault("stt", {})
        stt["words"] = list(dict.fromkeys(her.words() + list(stt.get("words") or [])))
    cfg = Section.wrap(data)
    cfg["config_path"] = str(path)
    return cfg
