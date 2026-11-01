"""Vision (mary/vision.py) and the look skill, with a fake Ollama: which model looks, and what look says. Nothing is
captured from the real screen and no model runs."""
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from mary import vision
from mary.config import Section, load_config
from mary.output import Output
from mary.skills import look as look_skill
from mary.tools.registry import ToolContext


def cfg(model="beam:cloud", named="", provider="ollama"):
    c = load_config()
    c["llm"] = Section.wrap({**c["llm"], "model": model})
    c["vision"] = Section.wrap({"model": named})
    c["brain"] = Section.wrap({**(c.get("brain") or {}), "provider": provider})
    return c


@pytest.fixture
def ollama(monkeypatch):
    """Installed: two local models that can see, one that can't, and a cloud one that can."""
    seeing = {"qwen3.5:2b-q4_K_M", "big-vision:7b", "qwen3-vl:235b-cloud"}
    listed = [{"model": "qwen3:4b-q4_K_M", "size": 2_600_000_000},
              {"model": "big-vision:7b", "size": 5_000_000_000},
              {"model": "qwen3.5:2b-q4_K_M", "size": 1_900_000_000},
              {"model": "qwen3-vl:235b-cloud", "size": 300, "remote_host": "https://ollama.com"},
              {"model": "beam:cloud", "size": 284, "remote_host": "https://ollama.com"}]
    monkeypatch.setattr(vision, "_can_see", lambda host, model: model in seeing)
    monkeypatch.setattr(vision.httpx, "get", lambda *a, **k: SimpleNamespace(json=lambda: {"models": listed}))
    return seeing


def test_the_model_that_looks(ollama):
    assert vision.model_for(cfg(named="qwen3-vl:235b-cloud")) == "qwen3-vl:235b-cloud"   # set by name: used
    assert vision.model_for(cfg(named="qwen3:4b-q4_K_M")) is None                       # set, but can't see
    assert vision.model_for(cfg(model="big-vision:7b")) == "big-vision:7b"              # the brain can see
    assert vision.model_for(cfg()) == "qwen3.5:2b-q4_K_M"   # else the smallest local one: never a cloud one unasked
    ollama.clear()
    assert vision.model_for(cfg()) is None


def test_big_pictures_are_scaled_down():
    out = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(out, format="PNG")
    assert max(Image.open(io.BytesIO(vision.shrink(out.getvalue()))).size) == vision.MAX_SIDE


def look_tool(c, monkeypatch, answer="A beach at sunset, with two people walking."):
    seen = {}

    def fake_look(png, question, cfg, model=None):
        seen.update(png=png, question=question, model=model)
        return answer

    monkeypatch.setattr(vision, "look", fake_look)
    monkeypatch.setattr(look_skill, "window_png", lambda win, part=None: b"png:" + (part or "all").encode())
    monkeypatch.setattr("mary.skills.read_screen._target_window",
                        lambda target: SimpleNamespace(hwnd=1, title="Photos - beach.jpg"))
    return look_skill.tools(ToolContext(c, None, Output()))[0], seen


def test_look_answers_from_the_picture(ollama, monkeypatch):
    tool, seen = look_tool(cfg(), monkeypatch)
    r = tool.fn(question="What's in the photo?", part="main area")
    assert r.ok and r.text == "Looking at 'Photos - beach.jpg' (main area): A beach at sunset, with two people walking."
    assert seen == {"png": b"png:main area", "question": "What's in the photo?", "model": "qwen3.5:2b-q4_K_M"}
    assert tool.private and tool.acts_on == "target"   # screen content stays out of the log


def test_without_a_model_that_can_see_it_says_so(ollama, monkeypatch):
    ollama.clear()
    tool, _ = look_tool(cfg(), monkeypatch)
    r = tool.fn(question="What colour is the button?")
    assert not r.ok and "No model that can see images" in r.text and "read_screen" in r.text
