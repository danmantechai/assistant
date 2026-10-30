from datetime import datetime, timedelta

import pytest

from mary.parsers import format_duration, parse_duration, parse_hotkey, parse_time_window, words_to_numbers


@pytest.mark.parametrize("text, seconds", [
    ("5 minutes", 300),
    ("5 min", 300),
    ("90 seconds", 90),
    ("90s", 90),
    ("1h30m", 5400),
    ("1 hour 30 minutes", 5400),
    ("an hour", 3600),
    ("a minute and a half", 90),
    ("an hour and a half", 5400),
    ("one and a half hours", 5400),
    ("half an hour", 1800),
    ("quarter of an hour", 900),
    ("twenty five minutes", 1500),
    ("two minutes", 120),
    ("00:05:00", 300),
    ("1:30", 90),
    ("300", 300),
    (45, 45),
])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "pasta", 0])
def test_parse_duration_rejects(text):
    assert parse_duration(text) is None


def test_format_duration():
    assert format_duration(90) == "1 minute 30 seconds"
    assert format_duration(3600) == "1 hour"


def test_words_to_numbers():
    assert words_to_numbers("twenty-five") == "25"
    assert words_to_numbers("a couple of minutes") == "2 minutes"


NOW = datetime(2026, 10, 25, 15, 30)


@pytest.mark.parametrize("text, expected", [
    ("today", datetime(2026, 10, 25)),
    ("yesterday", datetime(2026, 10, 24)),
    ("this week", NOW - timedelta(days=7)),
    ("last week", NOW - timedelta(days=7)),
    ("7d", NOW - timedelta(days=7)),
    ("24h", NOW - timedelta(hours=24)),
    ("3 days", NOW - timedelta(days=3)),
    ("two weeks", NOW - timedelta(days=14)),
    ("this month", datetime(2026, 10, 1)),
    ("2 months", NOW - timedelta(days=62)),
])
def test_parse_time_window(text, expected):
    assert parse_time_window(text, now=NOW) == expected


def test_parse_time_window_none():
    assert parse_time_window(None) is None
    assert parse_time_window("whenever") is None


@pytest.mark.parametrize("text, keys", [
    ("Ctrl+Shift+Esc", ["ctrl", "shift", "esc"]),
    ("ctrl shift esc", ["ctrl", "shift", "esc"]),
    ("Win-D", ["win", "d"]),
    ("control + s", ["ctrl", "s"]),
    ("alt tab", ["alt", "tab"]),
    ("windows+print screen", ["win", "printscreen"]),
])
def test_parse_hotkey(text, keys):
    assert parse_hotkey(text) == keys
