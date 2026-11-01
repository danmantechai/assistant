"""The settings page's server (mary/settings/server.py): it answers only this PC's own page, and its API works."""
import base64
import http.client
import json
import threading

import pytest

from mary import persona as personas
from mary.settings import server as web
from mary.settings import store


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    monkeypatch.setattr(store, "ENV_FILE", tmp_path / "env")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    personas._load.cache_clear()
    s = web.SettingsServer(0)
    thread = threading.Thread(target=s.serve_forever, daemon=True)
    thread.start()
    yield s
    s.shutdown()
    s.server_close()
    personas._load.cache_clear()


def call(s, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
    sent = {"Host": host or f"127.0.0.1:{s.port}", "X-MARY": "1"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    sent = {k: v for k, v in sent.items() if v is not None}
    conn.request(method, path, body=data, headers=sent)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    try:
        return r.status, json.loads(raw), r
    except ValueError:
        return r.status, raw, r


def test_the_page_and_its_safety_headers(srv):
    status, html, r = call(srv, "GET", "/", headers={"X-MARY": None})
    assert status == 200 and b"<title>" in html
    assert "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")
    assert r.getheader("Cache-Control") == "no-store"
    assert call(srv, "GET", "/settings.js", headers={"X-MARY": None})[0] == 200
    assert call(srv, "GET", "/../config.yaml", headers={"X-MARY": None})[0] in (403, 404)


@pytest.mark.parametrize("headers, host", [
    ({"X-MARY": None}, None),                                   # another site can't add the header
    ({"Origin": "https://evil.example"}, None),                 # nor pretend to be this page
    ({}, "evil.example:8765"),                                  # DNS rebinding: a strange name for 127.0.0.1
])
def test_other_websites_are_refused(srv, headers, host):
    status, body, _ = call(srv, "POST", "/api/settings", {"changes": {"llm.think_by_default": True}},
                           headers=headers, host=host)
    assert status == 403
    status, _, _ = call(srv, "GET", "/api/state", headers=headers, host=host)
    assert status == 403
    assert not (srv_user(srv) / "config.yaml").exists()


def srv_user(srv):
    from mary.config import user_dir

    return user_dir()


def test_only_json_is_accepted(srv):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    conn.request("POST", "/api/settings", body=b"changes=1",
                 headers={"Host": f"127.0.0.1:{srv.port}", "X-MARY": "1",
                          "Content-Type": "application/x-www-form-urlencoded"})
    assert conn.getresponse().status == 415


def test_state_and_saving(srv):
    status, st, _ = call(srv, "GET", "/api/state")
    assert status == 200 and st["settings"]["persona"] == "mary"
    assert st["running"] is False and st["can_restart"] is False and st["keys"] == {"elevenlabs": False, "llm": False}
    assert any(p["key"] == "mary" for p in st["personas"])
    status, st, _ = call(srv, "POST", "/api/settings", {"changes": {"llm.think_level": "high"}})
    assert status == 200 and st["settings"]["values"]["llm.think_level"]["origin"] == "yours"
    assert st["changed"] is True
    status, err, _ = call(srv, "POST", "/api/settings", {"changes": {"paths.data": "x"}})
    assert status == 400 and "can't be changed" in err["error"]


def test_persona_api_end_to_end(srv):
    status, out, _ = call(srv, "POST", "/api/personas/new", {
        "key": "gran", "answers": {"name": "Gran", "who": "my grandmother", "traits": ["kind"]},
        "calls_you": "Doctor"})
    assert status == 200 and out["key"] == "gran" and out["state"]["profile"]["name"] == "Doctor"
    status, her, _ = call(srv, "POST", "/api/personas/gran", {"fields": {"replies": ["Yes, child?"]}})
    assert status == 200 and her["replies"] == ["Yes, child?"]
    chat = "1/1/24, 8:00 AM - Gran: Did you eat?\n1/1/24, 8:01 AM - Sam: Yes\n"
    data = base64.b64encode(chat.encode()).decode()
    status, out, _ = call(srv, "POST", "/api/personas/gran/texts/inspect", {"filename": "c.txt", "data": data})
    assert out == {"kind": "chat", "senders": [["Gran", 1], ["Sam", 1]]}
    status, out, _ = call(srv, "POST", "/api/personas/gran/texts",
                          {"filename": "c.txt", "data": data, "sender": "Gran"})
    assert status == 200 and out["count"] == 1
    status, her, _ = call(srv, "POST", "/api/personas/gran/lore",
                          {"title": "Rice", "keywords": ["jollof"], "text": "Her jollof."})
    assert [x["title"] for x in her["lore"]] == ["Rice"]
    status, preview, _ = call(srv, "GET", "/api/personas/gran/prompt")
    assert "you are Gran" in preview["text"]
    assert call(srv, "GET", "/recorder.js", headers={"X-MARY": None})[0] == 200
    status, card, r = call(srv, "GET", "/api/personas/gran/card")
    assert status == 200 and card["data"]["name"] == "Gran" and "attachment" in r.getheader("Content-Disposition")
    card["data"]["name"] = "Gran Two"
    status, out, _ = call(srv, "POST", "/api/personas/import",
                          {"filename": "g.json", "data": base64.b64encode(json.dumps(card).encode()).decode()})
    assert status == 200 and out["key"] == "gran_two"
    status, st, _ = call(srv, "POST", "/api/personas/gran/use", {})
    assert st["settings"]["persona"] == "gran"
    status, err, _ = call(srv, "POST", "/api/personas/gran/delete", {})
    assert status == 400 and "active" in err["error"]


def test_bad_names_never_reach_the_disk(srv):
    for path in ("/api/personas/..%2F..%2Fx", "/api/personas/Gran", "/api/personas/_template"):
        assert call(srv, "GET", path)[0] == 404
    assert call(srv, "POST", "/api/personas/new", {"key": "../x"})[0] == 400


def test_keys_go_in_but_never_come_out(srv):
    status, out, _ = call(srv, "POST", "/api/keys", {"name": "ELEVENLABS_API_KEY", "value": "sk_" + "b2" * 12})
    assert status == 200 and out == {"elevenlabs": True, "llm": False}
    _, st, _ = call(srv, "GET", "/api/state")
    assert "b2b2" not in json.dumps(st)


def test_restart_needs_a_running_mary(srv):
    status, err, _ = call(srv, "POST", "/api/restart", {})
    assert status == 400
    done = threading.Event()
    srv.hooks.restart = done.set
    assert call(srv, "POST", "/api/restart", {})[0] == 200
    assert done.wait(3)


def test_a_model_is_tested_with_one_short_message(srv, monkeypatch):
    from mary.brain import Chunk
    from mary.brain.api import ApiBrain

    monkeypatch.setenv("LLM_API_KEY", "")   # set_key puts it in the environment too: undone after the test
    store.set_key("LLM_API_KEY", "sk-or-v1-" + "ab" * 20)
    seen = {}

    def chat(self, messages, tools, think=False, max_tokens=None):
        seen.update(key=self.key, url=self.url, model=self.model, said=messages[-1]["content"], max_tokens=max_tokens)
        yield Chunk(text="Hello from the service.", done=True)

    monkeypatch.setattr(ApiBrain, "start", lambda self: None)
    monkeypatch.setattr(ApiBrain, "chat", chat)
    status, out, _ = call(srv, "POST", "/api/models/test",
                          {"provider": "api", "url": "https://api.example.com/v1/", "model": "some/model"})
    assert status == 200 and out["ok"] and out["reply"] == "Hello from the service." and not out["local"]
    assert seen == {"key": "sk-or-v1-" + "ab" * 20, "url": "https://api.example.com/v1", "model": "some/model",
                    "said": "Say hello in five words.", "max_tokens": None}
    status, out, _ = call(srv, "POST", "/api/models/test", {"provider": "api", "url": "not an address", "model": "x"})
    assert status == 400


def test_adding_a_model_checks_its_name(srv):
    assert call(srv, "POST", "/api/models/pull", {"name": "rm -rf; x"})[0] == 400
    status, out, _ = call(srv, "GET", "/api/models/pull?name=nothing-yet")
    assert status == 200 and out["running"] is False
