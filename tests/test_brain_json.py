"""Brain.json: one answer that fits a schema, from Ollama (a model on this PC or its cloud) and from any
OpenAI-compatible service. Fake servers only: nothing here leaves the PC."""
import json
from types import SimpleNamespace

import httpx
import ollama
import pytest

from mary.brain import Brain, BrainGarbled, BrainLost, BrainOffline, parse_json
from mary.brain.api import ApiBrain
from mary.brain.ollama import OllamaBrain
from mary.config import Section, load_config

SCHEMA = {"type": "object", "properties": {"facts": {"type": "array"}}, "required": ["facts"]}
MESSAGES = [{"role": "system", "content": "Answer in JSON."}, {"role": "user", "content": "Anything?"}]


def llm(**over):
    return Section.wrap({**load_config().llm, **over})


def test_the_reader_takes_the_object_wherever_the_model_put_it():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('Sure!\n```json\n{"a": 2}\n```\nDone.') == {"a": 2}
    assert parse_json('Here you go: {"a": {"b": 3}} thanks') == {"a": {"b": 3}}
    for bad in ("", "no json at all", "[1, 2, 3]", "{not json}"):
        with pytest.raises(BrainGarbled):
            parse_json(bad)


def test_a_brain_that_cannot_do_it_says_so():
    with pytest.raises(NotImplementedError):
        Brain().json(MESSAGES, SCHEMA)


# ---------------------------------------------------------------- Ollama


class FakeClient:
    def __init__(self, content="", error=None):
        self.content, self.error, self.kw = content, error, None

    def chat(self, **kw):
        self.kw = kw
        if self.error:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=self.content))


def ollama_brain(client, model="beam:cloud"):
    brain = OllamaBrain(llm(model=model))
    brain.client = client
    return brain


def test_ollama_sends_the_schema_without_thinking_or_streaming():
    client = FakeClient('{"facts": []}')
    out = ollama_brain(client).json(MESSAGES, SCHEMA)
    assert out == {"facts": []}
    assert client.kw["format"] == SCHEMA and client.kw["stream"] is False and client.kw["think"] is False
    assert client.kw["options"]["temperature"] == 0 and client.kw["messages"] == MESSAGES


def test_ollama_errors_are_the_same_ones_chat_raises():
    cases = [(ConnectionError("down"), BrainLost), (httpx.ConnectError("down"), BrainLost),
             (ollama.ResponseError("no key", 401), BrainOffline), (ollama.ResponseError("slow down", 429), BrainOffline),
             (ollama.ResponseError("gone", 404), BrainOffline), (ollama.ResponseError("crash", 500), BrainLost)]
    for error, expected in cases:
        with pytest.raises(expected):
            ollama_brain(FakeClient(error=error)).json(MESSAGES, SCHEMA)
    with pytest.raises(BrainGarbled):
        ollama_brain(FakeClient("sorry, I can't")).json(MESSAGES, SCHEMA)


# ---------------------------------------------------------------- an OpenAI-compatible service


def service(handler):
    brain = ApiBrain({"url": "https://api.example.com/v1", "model": "some-model"}, llm(), key="sk-test-key-1234567890")
    brain.http = httpx.Client(transport=httpx.MockTransport(handler))
    return brain


def answer(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_the_service_gets_a_json_schema_and_one_whole_answer():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return answer('{"facts": ["a"]}')

    assert service(handler).json(MESSAGES, SCHEMA) == {"facts": ["a"]}
    body = sent[0]
    assert body["stream"] is False and body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA and body["model"] == "some-model"


def test_a_service_without_schemas_gets_plain_json_mode():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            return httpx.Response(400, text="response_format json_schema is not supported")
        return answer('```json\n{"facts": []}\n```')

    assert service(handler).json(MESSAGES, SCHEMA) == {"facts": []}
    assert len(sent) == 2 and sent[1]["response_format"] == {"type": "json_object"}


def test_service_errors():
    for status, expected in ((401, BrainOffline), (429, BrainOffline), (503, BrainLost)):
        with pytest.raises(expected):
            service(lambda r, s=status: httpx.Response(s, text="no")).json(MESSAGES, SCHEMA)
    with pytest.raises(BrainGarbled):
        service(lambda r: answer("not json")).json(MESSAGES, SCHEMA)
    with pytest.raises(BrainGarbled):
        service(lambda r: httpx.Response(200, json={"choices": []})).json(MESSAGES, SCHEMA)

    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(BrainLost):
        service(down).json(MESSAGES, SCHEMA)
