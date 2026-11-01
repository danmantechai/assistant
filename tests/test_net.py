"""mary/net.py and the weather brick, on recorded answers: nothing here goes out to the internet. Every test runs
on a fake transport, and one that isn't given an answer for a request fails the test.

tests/fixtures/wttr_lisbon.json is wttr.in's real answer for Lisbon (?format=j1, recorded 1 November 2026),
trimmed to the fields MARY reads."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from mary import net
from mary.config import load_config
from mary.output import Output
from mary.skills import weather
from mary.tools.registry import ToolContext

LISBON = json.loads((Path(__file__).parent / "fixtures" / "wttr_lisbon.json").read_text(encoding="utf-8"))


class Web:
    """A fake internet: answers from a list, in order, and records every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.answers:
            raise AssertionError(f"unexpected request: {request.url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


@pytest.fixture
def web(monkeypatch):
    """Install a fake internet: web(answer, …) -> the Web that records the requests."""
    monkeypatch.setattr(net, "RETRY_PAUSE_S", 0)
    made = []

    def install(*answers):
        w = Web(*answers)
        net.reset(httpx.MockTransport(w))
        made.append(w)
        return w

    install()  # nothing answers until a test says what
    yield install
    net.reset()


# ------------------------------------------------------------------ net.py

def test_get_json_and_the_cache(web, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(net, "_now", lambda: clock[0])
    w = web({"a": 1}, {"a": 2})
    assert net.get_json("https://example.test/x", params={"q": "1"}, ttl=60) == {"a": 1}
    assert net.get_json("https://example.test/x", params={"q": "1"}, ttl=60) == {"a": 1}   # cached
    assert len(w.requests) == 1 and w.requests[0].url.params["q"] == "1"
    clock[0] += 61
    assert net.get_json("https://example.test/x", params={"q": "1"}, ttl=60) == {"a": 2}   # expired
    assert len(w.requests) == 2


def test_different_params_are_different_entries(web):
    w = web({"n": 1}, {"n": 2})
    assert net.get_json("https://example.test/x", params={"q": "a"})["n"] == 1
    assert net.get_json("https://example.test/x", params={"q": "b"})["n"] == 2
    assert len(w.requests) == 2


def test_the_cache_stays_small(web):
    web(*[{"n": i} for i in range(net.CACHE_SIZE + 10)])
    for i in range(net.CACHE_SIZE + 10):
        net.get_json(f"https://example.test/{i}")
    assert len(net._cache) == net.CACHE_SIZE


def test_no_cache_when_ttl_is_zero(web):
    w = web({"n": 1}, {"n": 2})
    assert net.get_json("https://example.test/x", ttl=0)["n"] == 1
    assert net.get_json("https://example.test/x", ttl=0)["n"] == 2 and len(w.requests) == 2


@pytest.mark.parametrize("first", [httpx.Response(503, text="busy"), httpx.Response(429, text="slow down"),
                                   httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
def test_one_retry_for_what_can_pass(web, first):
    w = web(first, {"ok": True})
    assert net.get_json("https://example.test/x") == {"ok": True} and len(w.requests) == 2


def test_no_retry_for_a_plain_no(web):
    w = web(httpx.Response(404, text="no such thing"))
    with pytest.raises(net.NetError) as e:
        net.get_json("https://example.test/x")
    assert len(w.requests) == 1 and e.value.status == 404 and e.value.body == "no such thing"
    assert str(e.value) == "example.test answered with an error (404)."


def test_offline_is_a_plain_sentence(web):
    dns = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    w = web(dns, dns)
    with pytest.raises(net.NetError) as e:
        net.get_json("https://example.test/x")
    assert e.value.offline and str(e.value) == "There's no internet connection right now." and len(w.requests) == 2


def test_a_site_that_is_down_or_slow(web):
    web(httpx.ConnectError("refused"), httpx.ConnectError("refused"))
    with pytest.raises(net.NetError, match="I can't reach example.test right now."):
        net.get_json("https://example.test/x")
    web(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    with pytest.raises(net.NetError, match="example.test didn't answer in time."):
        net.get_json("https://example.test/x")


def test_an_answer_that_isnt_json(web):
    web(httpx.Response(200, text="<html>hi</html>"))
    with pytest.raises(net.NetError, match="sent an answer I couldn't read"):
        net.get_json("https://example.test/x")
    web(httpx.Response(200, text="plain"))
    assert net.get_text("https://example.test/y") == "plain"


def test_failures_are_not_cached(web):
    w = web(httpx.Response(404), {"n": 1})
    with pytest.raises(net.NetError):
        net.get_json("https://example.test/x")
    assert net.get_json("https://example.test/x") == {"n": 1} and len(w.requests) == 2


# ------------------------------------------------------------------ the weather, read from wttr.in's JSON

def check(request: str = "", place_cfg: str = "", **args):
    cfg = load_config()
    cfg["weather"] = {"place": place_cfg}
    ctx = ToolContext(cfg, None, Output())
    ctx.request = request
    tool = weather.tools(ctx)[0]
    return tool, tool.fn(**args)


def test_the_weather_now(web):
    w = web(LISBON)
    tool, r = check("what's the weather like")
    assert r.ok and tool.watch is False
    assert r.text == ("Weather in Alcantara, Portugal now: Clear, 16°C, feels like 13°C, humidity 45%, wind 7 km/h. "
                      "Today: 15 to 25°C, chance of rain up to 4% in the morning.")
    assert r.say == "It's 16 degrees, feeling like 13. Clear."
    req = w.requests[0]
    assert req.url.host == "wttr.in" and req.url.path == "/" and req.url.params["format"] == "j1"


def test_a_named_place(web):
    w = web(LISBON)
    _, r = check("weather in lisbon", place="Lisbon")
    assert w.requests[0].url.path == "/Lisbon"
    assert r.text.startswith("Weather in Lisbon (Alcantara, Portugal) now:") and r.say.startswith("In Lisbon it's 16")


def test_place_names_with_spaces_and_the_configured_place(web):
    w = web(LISBON, LISBON)
    check(place="New York")
    check(place_cfg="Sao Paulo")
    assert [r.url.raw_path.split(b"?")[0] for r in w.requests] == [b"/New+York", b"/Sao+Paulo"]


@pytest.mark.parametrize("request_words, when, starts", [
    ("what's the weather tomorrow", "now", "Weather in Alcantara, Portugal. Tomorrow (Monday 2 November): Sunny, "
                                           "15 to 25°C"),
    ("", "tomorrow", "Weather in Alcantara, Portugal. Tomorrow (Monday 2 November)"),
    ("", "today", "Weather in Alcantara, Portugal. Today (Sunday 1 November): Sunny"),
    ("will it rain on Tuesday", "now", "Weather in Alcantara, Portugal. Tuesday 3 November: Partly cloudy, 13 to 23°C"),
    ("will it rain this weekend", "now", "Forecast for Alcantara, Portugal, 3 days"),
    ("", "forecast", "Forecast for Alcantara, Portugal, 3 days"),
    ("what's it like tonight", "forecast", "Weather in Alcantara, Portugal. Today"),
])
def test_forecasts(web, request_words, when, starts):
    web(LISBON)
    _, r = check(request_words, when=when)
    assert r.ok and r.text.startswith(starts)


def test_a_three_day_forecast_has_every_day(web):
    web(LISBON)
    _, r = check(when="forecast")
    assert r.text.splitlines()[1:] == [
        "Today (Sunday 1 November): Sunny, 15 to 25°C, chance of rain up to 4% in the morning.",
        "Tomorrow (Monday 2 November): Sunny, 15 to 25°C, chance of rain up to 4% in the morning.",
        "Tuesday 3 November: Partly cloudy, 13 to 23°C, chance of rain up to 5% in the morning."]
    assert r.say == ("Today, 15 to 25 degrees, sunny. Tomorrow, 15 to 25 degrees, sunny. "
                     "Tuesday, 13 to 23 degrees, partly cloudy.")


def test_a_day_past_the_forecast_says_so(web):
    web(LISBON)
    _, r = check("will it rain on Saturday")
    assert r.ok and r.text.startswith("The forecast for Alcantara, Portugal only goes to Tuesday 3 November, so "
                                      "Saturday isn't in it yet:")
    assert r.say == "The forecast only goes as far as Tuesday."


def test_rain_and_snow_worth_saying(web):
    data = copy.deepcopy(LISBON)
    hours = data["weather"][1]["hourly"]
    hours[5]["chanceofrain"] = "70"     # 15:00
    hours[7]["chanceofsnow"] = "40"     # 21:00
    for h in hours:
        h["weatherDesc"] = [{"value": "Light rain shower "}]
    web(data)
    _, r = check("weather tomorrow")
    assert r.text.endswith("Light rain shower, 15 to 25°C, chance of rain up to 70% in the afternoon, chance of snow "
                           "up to 40% in the evening.")
    assert r.say == ("Tomorrow, 15 to 25 degrees, light rain shower, with a 70% chance of rain in the afternoon and a "
                     "40% chance of snow in the evening.")


def test_below_zero(web):
    data = copy.deepcopy(LISBON)
    data["current_condition"][0].update({"temp_C": "-5", "FeelsLikeC": "-9", "weatherDesc": [{"value": "Light snow"}]})
    web(data)
    _, r = check(place="Oslo")
    assert "-5°C, feels like -9°C" in r.text and r.say == "In Oslo it's -5 degrees, feeling like -9. Light snow."


def test_an_unknown_place(web):
    gone = httpx.Response(500, text="location not found: upstream error: opencage: invalid response")
    web(gone, gone)
    _, r = check(place="Qwxzyplk")
    assert not r.ok and r.text == "The weather service doesn't know a place called 'Qwxzyplk'."


def test_offline(web):
    dns = httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    web(dns, dns)
    _, r = check()
    assert not r.ok and r.text == "Couldn't get the weather: There's no internet connection right now."
    assert "no internet connection" in r.say


@pytest.mark.parametrize("broken", [{}, {"current_condition": []}, {"current_condition": [{"temp_C": "warm"}]},
                                    {"current_condition": [{}], "weather": []}])
def test_odd_answers_are_said_plainly(web, broken):
    web(broken)
    _, r = check()
    assert not r.ok and r.text == "The weather service sent something unexpected."


def test_a_reading_is_reused_for_ten_minutes(web):
    w = web(LISBON)
    check()
    _, r = check("and tomorrow?", when="tomorrow")
    assert r.ok and len(w.requests) == 1


def test_the_router_still_passes_forecast_words():
    assert weather._FORECAST_WORDS.search("what about tomorrow")
