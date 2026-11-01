"""Dates in words: the past becomes a range of days; plans and the future never do."""
from datetime import date

import pytest

from mary.memory.when import past_range, strip

TODAY = date(2026, 11, 4)   # a Wednesday


def span(text, cue=False):
    r = past_range(text, TODAY, cue=cue)
    return (r.start.isoformat(), r.end.isoformat()) if r else None


@pytest.mark.parametrize("text,expected", [
    ("what did we do yesterday", ("2026-11-03", "2026-11-03")),
    ("the day before yesterday I think", ("2026-11-02", "2026-11-02")),
    ("what did I ask you last night", ("2026-11-03", "2026-11-04")),
    ("what did I say this morning", ("2026-11-04", "2026-11-04")),
    ("three days ago", ("2026-11-01", "2026-11-01")),
    ("a day ago", ("2026-11-03", "2026-11-03")),
    ("what did I ask last week", ("2026-10-26", "2026-11-01")),
    ("anything this week", ("2026-11-02", "2026-11-04")),
    ("last month", ("2026-10-01", "2026-10-31")),
    ("this month so far", ("2026-11-01", "2026-11-04")),
    ("last year", ("2025-01-01", "2025-12-31")),
    ("what did I say last Tuesday", ("2026-11-03", "2026-11-03")),   # "last X" = the latest X that has gone
    ("last Wednesday", ("2026-10-28", "2026-10-28")),   # today is Wednesday: a week ago, not today
    ("on 2026-10-12", ("2026-10-12", "2026-10-12")),
    ("a couple of days ago", ("2026-11-02", "2026-11-02")),
])
def test_references_to_the_past(text, expected):
    assert span(text) == expected


def test_a_few_weeks_ago_is_a_stretch_around_that_time():
    start, end = span("about two weeks ago")
    assert start <= "2026-10-21" <= end and end <= "2026-11-04"


@pytest.mark.parametrize("text", [
    "remind me tomorrow", "set a timer for 20 minutes", "next week", "next Friday", "in 5 minutes", "tonight at 8 pm",
    "open spotify", "what is the weather like", "meet at 3 pm", "in two days", "a week from now"])
def test_the_future_and_plain_commands_are_not_dates(text):
    r = past_range(text, TODAY, cue=False)
    assert r is None or text in ("tonight at 8 pm",)   # "tonight" is today by itself; the sentence still isn't a question about the past


@pytest.mark.parametrize("text,expected", [
    ("what did I say on Friday", ("2026-10-30", "2026-10-30")),
    ("what did I say on Tuesday", ("2026-11-03", "2026-11-03")),
    ("what happened in March", ("2026-03-01", "2026-03-31")),
    ("what did we do in October", ("2026-10-01", "2026-10-31")),
    ("what happened in December", ("2025-12-01", "2025-12-31")),   # December hasn't come yet this year
    ("what did we do on 12 October", ("2026-10-12", "2026-10-12")),
    ("what did we do on October 12th", ("2026-10-12", "2026-10-12")),
    ("what did I say on 3 December", ("2025-12-03", "2025-12-03")),
    ("what did we do in June 2025", ("2025-06-01", "2025-06-30")),
])
def test_weekdays_and_months_count_only_when_the_sentence_is_about_the_past(text, expected):
    assert span(text, cue=True) == expected
    assert span(text, cue=False) is None


def test_the_words_that_named_the_date_come_out_of_the_question():
    found = past_range("what did I say about Marcus last Tuesday", TODAY)
    assert strip("what did I say about Marcus last Tuesday", found) == "what did I say about Marcus"
    assert strip("no date here", None) == "no date here"


def test_impossible_dates_are_no_dates():
    assert span("what did I say on 31 February", cue=True) is None
    assert past_range("on 2030-01-01", TODAY) is None   # not in the past yet
