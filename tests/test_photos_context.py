"""How a photo's people and date are worded for the model (mary/photos/context.py): the persona's own person is marked
as her, never as the listener, and a file's own date is never given as when a photo was taken."""
from __future__ import annotations

from mary.photos import context

ME = {"person_id": 7, "name": "Grandma", "born": 1938}


def test_every_date_source_is_worded_by_how_sure_it_is():
    for src in ("exif", "filename", "person", "album"):
        assert context.when_note("12 May 2021", src) == "12 May 2021"
    folder = context.when_note("June 1998", "folder")
    assert folder.startswith("June 1998") and "I think" in folder
    # The file's own date is often the day it was copied: unknown, with the reason, never the date as a fact.
    for when in ("2 November 2026", "3 March 2009"):
        f = context.when_note(when, "file")
        assert f.startswith("date unknown") and "copied" in f and "never say it" in f
    assert context.when_note("", "none") == "date unknown"
    assert context.when_note("", "exif") == "date unknown"
    assert "not sure" in context.when_note("Christmas 2019", "exif", "disagree")


def test_moment_date_source_comes_from_its_photos():
    assert context.moment_source({"when": "May 2021", "guess": False}) == "exif"
    assert context.moment_source({"when": ""}) == "none"
    files = {"when": "2 November 2026", "guess": True, "items": [{"taken": "x", "source": "file"}] * 3}
    assert context.moment_source(files) == "file"
    mixed = {"when": "June 1998", "guess": True, "items": [{"taken": "x", "source": "file"}, {"taken": "x", "source": "folder"}]}
    assert context.moment_source(mixed) == "folder"


def test_the_persona_is_marked_by_id_or_name_and_nobody_else_is():
    people = [{"id": 7, "name": "Grandma"}, {"id": 3, "name": "Kofi"}, {"name": ""}, "junk"]
    out = context.who_note(people, ME)
    assert out == "Grandma (= you, the assistant: say 'me'), Kofi"
    # Matched by name when the id isn't there (a moment's people list), case and spaces aside.
    assert "(= you" in context.who_note([{"name": " grandma "}], ME)
    # No persona linked, or nobody named: nobody is "you".
    assert context.who_note(people, None) == "Grandma, Kofi"
    assert context.who_note([], ME) == "no named people"
    assert "(= you" not in context.who_note([{"id": 8, "name": "Ama"}], ME)
