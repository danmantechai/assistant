"""Brains that aren't on this PC: Ollama's cloud models (mary/brain/ollama.py) and any OpenAI-compatible service
(mary/brain/api.py), with fake servers. Nothing here leaves the PC."""
import json
import threading
from types import SimpleNamespace

import httpx
import ollama
import pytest

import mary.brain.ollama as ollama_brain
from mary.brain import BrainGarbled, BrainLost, BrainOffline
from mary.brain.api import ApiBrain, is_local, to_openai
from mary.config import Section, load_config
from mary.llm.agent import Agent

TOOLS = [{"type": "function", "function": {"name": "set_timer", "parameters": {"type": "object", "properties": {}}}}]


def llm(**over):
    return Section.wrap({**load_config().llm, **over})


# ------------------------------------------------------------------ any OpenAI-compatible service


def sse(*events) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n"


def delta(**d):
    return {"choices": [{"index": 0, "delta": d}]}


def service(handler, url="https://api.example.com/v1", key="sk-test-key-1234567890"):
    brain = ApiBrain({"url": url, "model": "some-model"}, llm(think_level="high"), key=key)
    brain.http = httpx.Client(transport=httpx.MockTransport(handler))
    return brain


def replying(*events, sent=None):
    def handler(request: httpx.Request):
        if sent is not None:
            sent.append((request.url.path, request.headers.get("authorization"),
                         json.loads(request.content) if request.content else None))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "some-model"}]})
        return httpx.Response(200, content=sse(*events), headers={"content-type": "text/event-stream"})
    return handler


def test_text_and_reasoning_stream_in():
    sent = []
    usage = {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 7}}
    brain = service(replying(delta(reasoning_content="Hmm, "), delta(reasoning="greet."), delta(content="Hello"),
                             delta(content=" there."), usage, sent=sent))
    chunks = list(brain.chat([{"role": "user", "content": "Hi"}], TOOLS, think=False))
    assert "".join(c.text for c in chunks) == "Hello there."
    assert "".join(c.thinking for c in chunks) == "Hmm, greet."
    assert chunks[-1].done and chunks[-1].read_tokens == 40 and chunks[-1].wrote_tokens == 7
    path, auth, body = sent[0]
    assert path == "/v1/chat/completions" and auth == "Bearer sk-test-key-1234567890"
    assert body["model"] == "some-model" and body["stream"] and body["tools"] == TOOLS
    assert body["reasoning_effort"] == "none"   # thinking off has to be said
    assert "max_tokens" not in body             # no CPU cap for a remote model


def test_thinking_sends_the_level():
    sent = []
    list(service(replying(delta(content="OK"), sent=sent)).chat([{"role": "user", "content": "Hi"}], [], think=True))
    assert sent[0][2]["reasoning_effort"] == "high" and "tools" not in sent[0][2]


def test_a_tool_call_arriving_in_pieces_is_joined():
    brain = service(replying(
        delta(tool_calls=[{"index": 0, "id": "x", "type": "function", "function": {"name": "set_timer", "arguments": ""}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"durat'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'ion": "5 minutes"}'}}])))
    calls = [c for chunk in brain.chat([{"role": "user", "content": "timer"}], TOOLS) for c in chunk.calls]
    assert [(c.name, c.args) for c in calls] == [("set_timer", {"duration": "5 minutes"})]


def test_a_tool_call_that_is_not_json_is_garbled():
    brain = service(replying(delta(tool_calls=[{"index": 0, "function": {"name": "set_timer", "arguments": "{oops"}}])))
    with pytest.raises(BrainGarbled):
        list(brain.chat([{"role": "user", "content": "timer"}], TOOLS))


def test_marys_messages_become_the_openai_format():
    out = to_openai([
        {"role": "system", "content": "You are Ama."},
        {"role": "user", "content": "Timer and the weather"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "set_timer", "arguments": {"duration": "5 minutes"}}},
            {"function": {"name": "check_weather", "arguments": {}}}]},
        {"role": "tool", "tool_name": "check_weather", "content": "Sunny"},
        {"role": "tool", "tool_name": "set_timer", "content": "Set"},
        {"role": "assistant", "content": "Done."}])
    calls = out[2]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["set_timer", "check_weather"]
    assert calls[0]["function"]["arguments"] == '{"duration": "5 minutes"}' and calls[0]["type"] == "function"
    assert out[3] == {"role": "tool", "tool_call_id": calls[1]["id"], "content": "Sunny"}   # paired by name
    assert out[4]["tool_call_id"] == calls[0]["id"]
    assert out[5] == {"role": "assistant", "content": "Done."}


@pytest.mark.parametrize("status, error, words", [
    (401, BrainOffline, "refused the API key"), (404, BrainOffline, "doesn't know the model"),
    (429, BrainOffline, "rate limit"), (500, BrainLost, "error 500")])
def test_errors_say_what_happened(status, error, words):
    brain = service(lambda request: httpx.Response(status, text="nope"))
    with pytest.raises(error, match=words):
        list(brain.chat([{"role": "user", "content": "Hi"}], []))


def test_a_dropped_connection_is_lost():
    def handler(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(BrainLost):
        list(service(handler).chat([{"role": "user", "content": "Hi"}], []))


def test_a_refused_extra_setting_is_dropped_for_good():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument supplied: reasoning_effort"}})
        return httpx.Response(200, content=sse(delta(content="Hi")), headers={"content-type": "text/event-stream"})

    brain = service(handler)
    assert "".join(c.text for c in brain.chat([{"role": "user", "content": "Hi"}], [])) == "Hi"
    assert brain.plain and "temperature" not in bodies[1] and "reasoning_effort" not in bodies[1]
    list(brain.chat([{"role": "user", "content": "Again"}], []))
    assert len(bodies) == 3   # no second try needed now


def test_a_remote_service_needs_a_key_but_a_server_on_this_pc_doesnt():
    with pytest.raises(BrainOffline, match="API key"):
        service(replying(), key="").start()
    service(replying(), url="http://127.0.0.1:1234/v1", key="").start()
    assert is_local("http://localhost:11434/v1") and not is_local("https://openrouter.ai/api/v1")
    assert service(replying(), url="http://127.0.0.1:1234/v1").local and not service(replying()).local


# ------------------------------------------------------------------ Ollama's cloud models


class FakeClient:
    def __init__(self, error=None):
        self.kw, self.error = None, error

    def chat(self, **kw):
        self.kw = kw
        if self.error:
            raise self.error
        part = SimpleNamespace(message=SimpleNamespace(content="Hi", thinking="", tool_calls=None), done=True)
        return iter([part])


def ollama_model(model, cloud, levels, monkeypatch):
    monkeypatch.setattr(ollama_brain, "ensure_server", lambda *a, **k: False)
    monkeypatch.setattr(ollama_brain, "ensure_model", lambda host, m: {"cloud": cloud, "levels": levels})
    brain = ollama_brain.OllamaBrain(llm(model=model, think_level="medium"))
    brain.start()
    brain.client = FakeClient()
    return brain


def test_a_cloud_model_gets_a_level_and_no_local_settings(monkeypatch):
    brain = ollama_model("beam:cloud", True, ["low", "medium", "high", "xhigh", "max"], monkeypatch)
    assert not brain.local
    list(brain.chat([{"role": "user", "content": "Hi"}], [], think=True))
    kw = brain.client.kw
    assert kw["think"] == "medium" and kw["keep_alive"] is None
    assert not {"num_ctx", "num_thread", "num_predict"} & set(kw["options"])
    list(brain.chat([{"role": "user", "content": "Hi"}], [], think=False))
    assert brain.client.kw["think"] is False
    brain.unload()   # nothing of it is on this PC: no call at all


def test_a_model_on_this_pc_is_as_before(monkeypatch):
    brain = ollama_model("qwen3.5:2b-q4_K_M", False, [], monkeypatch)
    assert brain.local
    list(brain.chat([{"role": "user", "content": "Hi"}], [], think=True))
    kw = brain.client.kw
    assert kw["think"] is True and kw["keep_alive"] == -1 and {"num_ctx", "num_thread", "num_predict"} <= set(kw["options"])


def test_not_signed_in_to_the_cloud_says_how(monkeypatch):
    brain = ollama_model("beam:cloud", True, ["low", "medium"], monkeypatch)
    brain.client = FakeClient(ollama.ResponseError("unauthorized", 401))
    with pytest.raises(BrainOffline, match="ollama signin"):
        list(brain.chat([{"role": "user", "content": "Hi"}], []))


# ------------------------------------------------------------------ no pre-reading for a brain that isn't here


def test_no_warm_up_or_prefill_calls_for_a_cloud_brain():
    calls = []
    brain = SimpleNamespace(local=False, start=lambda: calls.append("start"),
                            chat=lambda *a, **k: calls.append("chat") or iter([]))
    agent = SimpleNamespace(brain=brain, offline_reason=None, warming=threading.Event(), system={}, pinned=[],
                            schemas=[], _prefill_wanted=threading.Event(), _prefill_thread=None)
    assert Agent.warm_up(agent) == 0.0
    Agent.prefill_in_background(agent)
    assert calls == ["start"] and not agent._prefill_wanted.is_set() and agent._prefill_thread is None
