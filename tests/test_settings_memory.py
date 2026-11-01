"""The settings page's Memory page: what it lists and changes, on a scratch user folder (never the real one)."""
import http.client
import json
import threading
from datetime import datetime

import pytest

from mary import memory
from mary.memory import clock
from mary.memory.embed import HashEmbedder
from mary.settings import server as web
from mary.settings import store


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(store, "ENV_FILE", tmp_path / "env")
    monkeypatch.setattr(memory, "ENABLED", True)
    monkeypatch.setattr(memory, "FOLDER", tmp_path / "user" / "memory")
    s = web.SettingsServer(0)
    mem = memory.open_store(__import__("mary.config", fromlist=["load_config"]).load_config(), embedder=HashEmbedder())
    s.hooks.memory = mem
    mem.add("fact", "Marcus is my friend from university", happened=datetime(2026, 9, 12, 10, 0))
    mem.add("diary", 'Asked: "open Spotify". Used: open_app.', happened=datetime(2026, 10, 30, 9, 14))
    mem.add("diary", 'Asked: "what time is it". Used: get_time.', happened=datetime(2026, 10, 30, 9, 20))
    mem.add("reflection", "A quiet day of music.", happened=datetime(2026, 10, 30, 23, 30))
    mem.flush()
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, mem, tmp_path
    s.shutdown()
    s.server_close()
    mem.close()
    clock.reset()


def call(s, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
    headers = {"Host": f"127.0.0.1:{s.port}", "X-MARY": "1"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    out = json.loads(r.read())
    conn.close()
    return r.status, out


def test_the_page_reports_what_is_there_and_never_text_in_its_counts(srv):
    s, mem, _ = srv
    status, info = call(s, "GET", "/api/memory")
    assert status == 200 and info["enabled"] and info["counts"]["fact"] == {"current": 1, "ended": 0}
    assert info["counts"]["diary"]["current"] == 2 and info["days"] == [{"day": "2026-10-30", "count": 2}]
    assert info["learn"] is False and "cloud" in info and info["folder"] == "user/memory"
    assert "Marcus" not in json.dumps(info) and "Spotify" not in json.dumps(info)


def test_facts_diary_days_and_summaries_are_listed(srv):
    s, mem, _ = srv
    _, facts = call(s, "GET", "/api/memory/list?kind=fact")
    assert [i["text"] for i in facts["items"]] == ["Marcus is my friend from university"] and facts["total"] == 1
    assert facts["items"][0]["can_edit"] and facts["items"][0]["when"].startswith("Sat 12 Sep 2026")
    _, day = call(s, "GET", "/api/memory/list?kind=diary&day=2026-10-30")
    assert [i["text"].split(". ")[0] for i in day["items"]] == ['Asked: "open Spotify"', 'Asked: "what time is it"']   # in time order
    _, summary = call(s, "GET", "/api/memory/list?kind=reflection&day=2026-10-30")
    assert summary["items"][0]["text"] == "A quiet day of music."
    assert call(s, "GET", "/api/memory/list?kind=core")[0] == 400
    assert call(s, "GET", "/api/memory/list?kind=diary&day=yesterday")[0] == 400


def test_search_finds_things_the_way_she_would(srv):
    s, mem, _ = srv
    _, r = call(s, "GET", "/api/memory/search?q=Marcus")
    assert [i["text"] for i in r["items"]] == ["Marcus is my friend from university"]
    assert call(s, "GET", "/api/memory/search?q=x")[1] == {"items": []}
    assert call(s, "GET", "/api/memory/search?q=zebra%20crossing")[1]["items"] == []


def test_a_fact_can_be_added_edited_forgotten_and_deleted_for_good(srv):
    s, mem, folder = srv
    status, added = call(s, "POST", "/api/memory/add", {"text": "My sister's name is Lena"})
    assert status == 200 and added["status"] == "saved"
    assert call(s, "POST", "/api/memory/add", {"text": "my sister's name is lena."})[1]["status"] == "already"
    mid = added["item"]["id"]
    assert call(s, "POST", "/api/memory/edit", {"id": mid, "text": "My sister is called Lena"})[1]["text"] == "My sister is called Lena"
    status, ended = call(s, "POST", "/api/memory/end", {"id": mid})
    assert ended["ended"] and ended["ended_why"] == "forgotten"
    _, gone = call(s, "GET", "/api/memory/list?kind=fact&which=ended")
    assert [i["id"] for i in gone["items"]] == [mid]
    assert call(s, "POST", "/api/memory/delete", {"id": mid})[1] == {"removed": mid}
    assert call(s, "GET", "/api/memory/list?kind=fact&which=ended")[1]["items"] == []
    assert call(s, "POST", "/api/memory/delete", {"id": mid})[0] == 400      # already gone: a sentence, not a crash


def test_bad_input_is_a_sentence_for_the_page(srv):
    s, mem, _ = srv
    assert "short" in call(s, "POST", "/api/memory/add", {"text": "  "})[1]["error"]
    assert "under 200" in call(s, "POST", "/api/memory/add", {"text": "x" * 300})[1]["error"]
    assert call(s, "POST", "/api/memory/edit", {"id": "abc", "text": "hi"})[0] == 400
    assert call(s, "POST", "/api/memory/end", {"id": 99999})[0] == 400


def test_core_facts_are_read_only_here(srv):
    s, mem, _ = srv
    mem.sync_core(["I prefer Edge"])
    core = mem.list(kinds=("core",))[0]
    _, r = call(s, "GET", "/api/memory/search?q=Edge")
    assert r["items"][0]["kind"] == "core" and r["items"][0]["can_edit"] is False
    for path in ("edit", "end"):
        status, body = call(s, "POST", f"/api/memory/{path}", {"id": core.id, "text": "x"})
        assert status == 400 and "About you" in body["error"]
    status, body = call(s, "POST", "/api/memory/delete", {"id": core.id})
    assert status == 400 and "About you" in body["error"]
    assert mem.get(core.id).current


def test_a_hand_edit_to_the_file_shows_up_when_the_page_lists(srv):
    s, mem, _ = srv
    path = memory.folder() / "facts.md"
    text = path.read_text(encoding="utf-8")
    import os
    import time

    path.write_text(text.replace("## Ended", "- Added in Notepad\n\n## Ended"), encoding="utf-8")
    os.utime(path, (time.time() + 9, time.time() + 9))
    _, facts = call(s, "GET", "/api/memory/list?kind=fact")
    assert "Added in Notepad" in [i["text"] for i in facts["items"]]


def test_the_background_switch_is_an_ordinary_setting_and_the_page_warns_about_the_cloud(srv):
    s, mem, folder = srv
    _, state = call(s, "GET", "/api/state")
    fields = {f["path"] for f in state["settings"]["pages"]["memory"]}
    assert fields == {"memory.diary.enabled", "memory.consolidate.enabled", "memory.consolidate.reflections"}
    assert state["settings"]["values"]["memory.consolidate.enabled"]["value"] is False       # off until asked for
    status, _ = call(s, "POST", "/api/settings", {"changes": {"memory.consolidate.enabled": True}})
    assert status == 200
    assert "consolidate" in (folder / "user" / "config.yaml").read_text(encoding="utf-8")
    from mary.config import load_config

    cfg = load_config()
    assert cfg.memory.consolidate.enabled is True and cfg.memory.consolidate.reflections is True
    cfg["llm"]["model"] = "beam:cloud"
    from mary.settings.memory import brain_in_cloud

    assert brain_in_cloud(cfg) is True
    cfg["llm"]["model"] = "qwen3.5:2b"
    assert brain_in_cloud(cfg) is False


def test_with_memory_switched_off_the_page_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(memory, "ENABLED", False)
    s = web.SettingsServer(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        assert call(s, "GET", "/api/memory")[1] == {"enabled": False}
        status, body = call(s, "GET", "/api/memory/list?kind=fact")
        assert status == 400 and "switched off" in body["error"]
    finally:
        s.shutdown()
        s.server_close()


def test_the_page_script_has_the_memory_page():
    from pathlib import Path

    js = (Path(web.__file__).parent / "web" / "settings.js").read_text(encoding="utf-8")
    assert "remembered: pageRemembered" in js and '["remembered", "Remembered"]' in js
    assert "Delete this for good?" in js and "innerHTML" not in js.split("function pageRemembered")[1].split("function pageConnections")[0]
