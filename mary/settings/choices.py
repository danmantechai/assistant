"""Live lists for the settings page: installed models, voices, microphones, skills and wake words. Each returns what
it can and never raises: a missing service just means an empty list and a sentence saying why."""
from __future__ import annotations

import ast
import logging
import re
from functools import lru_cache
from pathlib import Path

from mary import skills
from mary.config import PROJECT_DIR, expand_path

log = logging.getLogger(__name__)
SKILLS_DIR = PROJECT_DIR / "mary" / "skills"

# openWakeWord's own wake words, installed with it: they work without training.
BUILT_IN_WAKE = {
    "hey_jarvis": {"phrase": "Hey Jarvis", "names": ["jarvis"]},
    "alexa": {"phrase": "Alexa", "names": ["alexa"]},
    "hey_mycroft": {"phrase": "Hey Mycroft", "names": ["mycroft", "my croft", "microsoft"]},
    "hey_rhasspy": {"phrase": "Hey Rhasspy", "names": ["rhasspy", "raspy"]},
}

# Friendly names and groups for the skill bricks (the page lists them by group).
SKILLS = {
    "apps": ("Apps", "Apps and windows"), "windows": ("Windows", "Apps and windows"),
    "screen_control": ("Click and type anywhere", "Apps and windows"), "read_screen": ("Read the screen",
                                                                                      "Apps and windows"),
    "keyboard": ("Typing", "Apps and windows"), "screenshot": ("Screenshots", "Apps and windows"),
    "sound_screen": ("Volume and screen", "Your PC"), "media": ("Media controls", "Your PC"),
    "pc_status": ("PC status", "Your PC"), "power": ("Power", "Your PC"), "powershell": ("PowerShell", "Your PC"),
    "web": ("Web", "Web"), "music": ("Music on Spotify", "Web"), "messages": ("WhatsApp messages", "Web"),
    "weather": ("Weather", "Web"),
    "files": ("Files", "Everyday"), "notes": ("Notes", "Everyday"), "timers": ("Timers", "Everyday"),
    "clock": ("Time and date", "Everyday"), "memory": ("Memory", "Everyday"),
    "calculate": ("Calculator", "Everyday"),
    "waiting": ("Waiting for slow apps", "Apps and windows"),
    "look": ("Look at the screen", "Apps and windows"),
    "documents": ("Read documents", "Everyday"),
}
_ACCENTS = {"a": "American", "b": "British"}
_GENDERS = {"f": "female", "m": "male"}
_MALE_FIRST = ["am_michael", "am_fenrir", "am_puck", "bm_george", "bm_fable", "bm_lewis"]  # most natural first


def _think_levels(host: str, model: str) -> list[str]:
    """The thinking levels a model offers ("low" … "max"); empty for plain on/off thinking."""
    import httpx

    try:
        thinking = httpx.post(f"{host}/api/show", json={"model": model}, timeout=4).json().get("thinking") or {}
    except (httpx.HTTPError, ValueError):
        return []
    return [v for v in thinking.get("values") or [] if isinstance(v, str)]


def ollama_models(cfg) -> dict:
    """The models this PC's Ollama has: its own, smallest first, then the cloud models added to it (beam:cloud)."""
    import httpx

    from mary.llm.server import is_cloud

    host = cfg.llm.host
    try:
        listed = httpx.get(f"{host}/api/tags", timeout=4).json().get("models") or []
    except (httpx.HTTPError, ValueError) as e:
        log.info("couldn't list Ollama's models: %s", e)
        return {"items": [], "running": False,
                "note": "Ollama isn't running, so its models can't be listed. Start MARY, then reload."}
    items = []
    for m in listed:
        d, name = m.get("details") or {}, m.get("model") or m.get("name") or ""
        cloud = bool(m.get("remote_host")) or is_cloud(name)
        items.append({"id": name, "cloud": cloud, "size_gb": 0 if cloud else round((m.get("size") or 0) / 1e9, 1),
                      "params": d.get("parameter_size") or "", "family": d.get("family") or "",
                      "quant": d.get("quantization_level") or "", "levels": _think_levels(host, name)})
    items.sort(key=lambda i: (i["cloud"], i["size_gb"], i["id"]))
    return {"items": items, "running": True, "note": ""}


def api_models(url: str, key: str) -> dict:
    """The models a service offers (GET {url}/models), as suggestions; some services don't list them."""
    import httpx

    if not url:
        return {"items": [], "note": ""}
    try:
        r = httpx.get(f"{url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"} if key else {},
                      timeout=8)
    except httpx.HTTPError as e:
        log.info("couldn't list the service's models: %s", e)
        return {"items": [], "note": "Couldn't reach that address."}
    if r.status_code in (401, 403):
        return {"items": [], "note": "The service refused the key." if key else "Add the service's API key first."}
    try:
        ids = sorted({m["id"] for m in r.json().get("data") or [] if isinstance(m, dict) and m.get("id")})
    except (ValueError, KeyError, TypeError):
        ids = []
    return {"items": ids, "note": "" if ids else "The service didn't list its models: type the model's name."}


def kokoro_voices(cfg) -> dict:
    """Kokoro's English voices: name, accent and gender from the voice ID (af_heart: American, female), the best
    graded first (python -m mary.voices plays them all)."""
    from mary.voices import FEMALE

    graded = {name: (grade, about) for name, grade, about in FEMALE}
    names = list(graded)
    try:
        import numpy as np

        with np.load(expand_path(cfg.paths.data) / "models" / cfg.tts.voices) as voices:
            names += sorted(set(voices.files) - set(graded), key=lambda n: (n not in _MALE_FIRST,
                                                                            _MALE_FIRST.index(n) if n in _MALE_FIRST
                                                                            else 0, n))
    except Exception as e:
        log.info("couldn't read Kokoro's voices file: %s", e)
    items = []
    for name in names:
        accent, gender = _ACCENTS.get(name[:1]), _GENDERS.get(name[1:2])
        if not accent or not gender or "_" not in name:
            continue  # other languages: MARY speaks English
        items.append({"id": name, "name": name.split("_", 1)[1].replace("_", " ").title(),
                      "detail": f"{accent} · {gender}", "gender": gender,
                      "recommended": graded.get(name, ("",))[0] in ("A", "A-")})
    return {"items": items, "note": ""}


def elevenlabs_voices() -> dict:
    """The voices in the user's ElevenLabs account, their own first, with the free preview clip ElevenLabs hosts."""
    from mary.settings.store import key_status

    if not key_status()["elevenlabs"]:
        return {"items": [], "note": "Connect ElevenLabs (Connections) to choose from your voices."}
    try:
        from elevenlabs import ElevenLabs

        found = ElevenLabs().voices.search(page_size=100, request_options={"timeout_in_seconds": 8}).voices
        items = []
        for v in found:
            labels = dict(getattr(v, "labels", None) or {})
            mine = v.category in ("cloned", "generated")
            detail = " · ".join(str(x) for x in (labels.get("accent"), labels.get("gender")) if x)
            items.append({"id": v.voice_id, "name": v.name or "Voice", "mine": mine,
                          "detail": "Your voice" if mine else (detail.title() or (v.category or "").title()),
                          "preview": getattr(v, "preview_url", None) or ""})
        items.sort(key=lambda i: (not i["mine"], i["name"].lower()))
        return {"items": items, "note": ""}
    except Exception as e:
        log.info("couldn't list ElevenLabs voices: %s", e)
        return {"items": [], "note": "Couldn't reach ElevenLabs to list your voices. You can paste a voice ID."}


def microphones() -> dict:
    """Input devices, once each (Windows lists every mic under several sound systems)."""
    default = {"id": None, "name": "Windows' default microphone"}
    try:
        import sounddevice as sd

        api = sd.query_hostapis(sd.default.hostapi)["name"] if sd.default.hostapi >= 0 else None
        names = [d["name"] for d in sd.query_devices()
                 if d["max_input_channels"] > 0 and (api is None or sd.query_hostapis(d["hostapi"])["name"] == api)]
        return {"items": [default] + [{"id": n, "name": n} for n in dict.fromkeys(names)], "note": ""}
    except Exception as e:
        log.info("couldn't list microphones: %s", e)
        return {"items": [default], "note": "Couldn't list the microphones."}


@lru_cache(maxsize=None)
def _brick(name: str) -> tuple[str, str]:
    """(what the brick does, its GUIDE lines), read from its source without importing it."""
    try:
        tree = ast.parse((SKILLS_DIR / f"{name}.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return "", ""
    about = (ast.get_docstring(tree) or "").split("\n\n")[0].replace("\n", " ")
    about = about.removeprefix("Brick: ").strip()
    guide = ""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "GUIDE" for t in node.targets):
            try:
                guide = ast.literal_eval(node.value)
            except ValueError:
                guide = str(getattr(__import__(f"mary.skills.{name}", fromlist=["GUIDE"]), "GUIDE", ""))
    return about, guide


@lru_cache(maxsize=None)
def _skill_notes() -> dict[str, str]:
    """The plain notes beside each brick in config.yaml's skills: list ("apps  # open and close apps")."""
    from mary.config import DEFAULT_CONFIG

    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    section = re.search(r"(?ms)^skills:\n(.*?)^\S", text)
    return dict(re.findall(r"(?m)^\s+-\s+(\w+)\s+#\s*(.+?)\s*$", section.group(1) if section else ""))


def skill_list() -> list[dict]:
    """Each brick: a friendly name, its group, and what it does in plain words (its note in config.yaml, else its
    docstring without the tool names)."""
    out = []
    for name in skills.ALL:
        about = _skill_notes().get(name) or _brick(name)[0]
        about = re.sub(r"^\w+(?: / \w+)*:\s+", "", re.sub(r"\s*\([a-z_]+\)", "", about)).rstrip(".")
        title, group = SKILLS.get(name, (name.replace("_", " ").title(), "More"))
        out.append({"name": name, "title": title, "group": group, "about": about[:1].upper() + about[1:]})
    return out


def guides(names: list[str]) -> list[str]:
    """The GUIDE lines of these bricks, as the agent puts them in her prompt."""
    return [g for g in (_brick(name)[1] for name in names) if g]


def wake_status(model: str) -> str:
    """built-in | ready | needs training | needs more takes (trained, but it didn't pass on the user's voice)."""
    from mary.audio.wakeword import passed
    from mary.config import load_config

    if model in BUILT_IN_WAKE:
        return "built-in"
    file = Path(model) if model.endswith(".onnx") else \
        expand_path(load_config().paths.data) / "models" / "wakeword" / f"{model}.onnx"  # where training saves it
    if not file.exists():
        return "needs training"
    return "ready" if passed(str(file)) else "needs more takes"
