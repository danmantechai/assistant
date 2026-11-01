"""The Model page's actions: add a model to Ollama (ollama pull, in the background, with progress) and test a model
with one short message before using it."""
from __future__ import annotations

import logging
import threading
import time

from mary.config import Section, load_config
from mary.settings import schema, store

log = logging.getLogger(__name__)
TEST_MESSAGE = "Say hello in five words."
_pulls: dict[str, dict] = {}
_lock = threading.Lock()


def start_pull(name: str) -> dict:
    """Start adding a model to Ollama: a download for one that runs on this PC, a small entry for a cloud model."""
    try:
        name = schema.check("llm.model", name)
    except ValueError as e:
        raise store.SettingsError(str(e)) from None
    with _lock:
        if _pulls.get(name, {}).get("running"):
            return dict(_pulls[name])
        _pulls[name] = {"name": name, "running": True, "status": "starting", "completed": 0, "total": 0,
                        "error": ""}
    threading.Thread(target=_pull, args=(name,), name=f"pull-{name}", daemon=True).start()
    return pull_status(name)


def _pull(name: str) -> None:
    import ollama

    state = _pulls[name]
    try:
        for p in ollama.Client(host=load_config().llm.host).pull(name, stream=True):
            state.update(status=p.status or "", completed=p.completed or state["completed"],
                         total=p.total or state["total"])
        state["status"] = "done"
    except Exception as e:  # not found, no connection, Ollama not running…
        log.info("couldn't add %s: %s", name, e)
        state["error"] = (f"Ollama has no model called {name}." if "not found" in str(e).lower() or
                          "file does not exist" in str(e).lower() else f"Couldn't add {name}: {e}")
    finally:
        state["running"] = False


def pull_status(name: str) -> dict:
    return dict(_pulls.get(name) or {"name": name, "running": False, "status": "", "error": ""})


def test(body: dict) -> dict:
    """One short message to the model the page has chosen (saved or not): its reply and how long it took."""
    from mary.brain import BrainGarbled, BrainLost, BrainOffline

    cfg = load_config()
    provider = body.get("provider") if body.get("provider") in ("ollama", "api") else "ollama"
    try:
        if provider == "api":
            from mary.brain.api import ApiBrain

            api = {"url": schema.check("brain.api.url", body.get("url") or ""),
                   "model": schema.check("brain.api.model", body.get("model") or "")}
            brain = ApiBrain(api, cfg.llm, key=store.read_key("LLM_API_KEY"))
        else:
            from mary.brain.ollama import OllamaBrain

            brain = OllamaBrain(Section.wrap({**cfg.llm, "model": schema.check("llm.model", body.get("model"))}))
    except ValueError as e:
        raise store.SettingsError(str(e)) from None
    start = time.perf_counter()
    try:
        brain.start()
        reply = "".join(c.text for c in brain.chat([{"role": "user", "content": TEST_MESSAGE}], [], think=False,
                                                   max_tokens=200 if brain.local else None))
    except (BrainOffline, BrainLost, BrainGarbled) as e:
        return {"ok": False, "why": str(e)[:1].upper() + str(e)[1:] + "."}
    except Exception as e:
        log.info("model test failed: %s", e)
        return {"ok": False, "why": f"It didn't answer: {e}"}
    finally:
        if provider == "ollama" and brain.local and brain.cfg.model != cfg.llm.model:
            try:
                brain.unload()  # a second model in memory beside the one MARY uses would crowd this PC
            except Exception:
                pass
    return {"ok": True, "reply": reply.strip() or "(an empty reply)", "seconds": round(time.perf_counter() - start, 1),
            "local": brain.local}
