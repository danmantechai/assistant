"""The markdown files: written as MARY saves, edited by hand, and able to rebuild the store."""
import os
import time
from datetime import datetime

import pytest

from mary.memory import clock
from mary.memory.embed import HashEmbedder
from mary.memory.files import FORMAT, MemoryFiles, parse_diary, parse_facts, parse_reflections
from mary.memory.store import MemoryStore


@pytest.fixture
def made(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    files = MemoryFiles(store, tmp_path)
    yield store, files, tmp_path
    store.close()
    clock.reset()


def edit(path, fn):
    """Change a file the way an editor would, leaving it with a newer time than MARY's own write."""
    before = path.stat().st_mtime
    path.write_text(fn(path.read_text(encoding="utf-8")), encoding="utf-8")
    os.utime(path, (before + 5, before + 5))


def texts(store, kind, current=True):
    return sorted(m.text for m in store.list(kinds=(kind,), current=current, limit=1000))


def test_facts_are_written_with_the_format_number_and_their_dates(made):
    store, files, folder = made
    store.add("fact", "Marcus is my friend", happened=datetime(2026, 9, 12, 10, 0))
    old = store.add("fact", "I live in Lisbon", happened=datetime(2026, 3, 1, 10, 0))
    store.add_fact("I live in Porto", replaces=old.id)
    store.flush()
    text = (folder / "facts.md").read_text(encoding="utf-8")
    assert text.startswith(FORMAT)
    assert "- Marcus is my friend · 2026-09-12" in text and "- I live in Porto · " in text
    assert "- I live in Lisbon · 2026-03-01 → " in text and "(superseded)" in text
    current, ended = parse_facts(text, clock.now())
    assert [i.text for i in current] == ["Marcus is my friend", "I live in Porto"]
    assert [i.text for i in ended] == ["I live in Lisbon"] and ended[0].why == "superseded"


def test_the_diary_is_one_file_per_day(made):
    store, files, folder = made
    store.add("diary", 'Asked: "open Spotify". Used: open_app.', happened=datetime(2026, 10, 30, 9, 14))
    store.add("diary", 'Asked: "what time is it". Used: get_time.', happened=datetime(2026, 10, 31, 8, 0))
    store.flush()
    day = (folder / "diary" / "2026-10-30.md").read_text(encoding="utf-8")
    assert day.startswith(FORMAT) and "Friday 30 October 2026" in day and '- 09:14 Asked: "open Spotify"' in day
    assert (folder / "diary" / "2026-10-31.md").exists()
    assert [i.text for i in parse_diary(day, datetime(2026, 10, 30).date())] == ['Asked: "open Spotify". Used: open_app.']


def test_a_line_added_by_hand_becomes_a_fact(made):
    store, files, folder = made
    store.add("fact", "Marcus is my friend")
    store.flush()
    edit(folder / "facts.md", lambda t: t.replace("## Ended", "- Lena's birthday is on the 12th of December\n\n## Ended"))
    files.sync()
    assert texts(store, "fact") == ["Lena's birthday is on the 12th of December", "Marcus is my friend"]
    added = [m for m in store.list(kinds=("fact",)) if "Lena" in m.text][0]
    assert added.source == "hand edit"
    store.flush()   # written back tidy, with its date, and not imported a second time
    files.sync()
    assert texts(store, "fact").count("Marcus is my friend") == 1 and store.count(("fact",), None) == 2


def test_a_line_taken_out_is_ended_not_deleted(made):
    store, files, folder = made
    keep = store.add("fact", "Marcus is my friend")
    gone = store.add("fact", "I work at the museum")
    store.flush()
    edit(folder / "facts.md", lambda t: t.replace("- I work at the museum · " + gone.happened.strftime("%Y-%m-%d"), "").replace("  ", " "))
    files.sync()
    assert texts(store, "fact") == ["Marcus is my friend"]
    ended = store.get(gone.id)
    assert not ended.current and ended.ended_why == "removed by hand" and store.get(keep.id).current
    assert "I work at the museum" in (folder / "facts.md").read_text(encoding="utf-8").split("## Ended")[1]


def test_a_line_changed_is_an_edit_that_keeps_the_old_one_as_history(made):
    store, files, folder = made
    old = store.add("fact", "My dentist is Dr. Haddad", importance=0.9)
    store.flush()
    edit(folder / "facts.md", lambda t: t.replace("Dr. Haddad", "Dr. Haddad on Mondays"))
    files.sync()
    assert texts(store, "fact") == ["My dentist is Dr. Haddad on Mondays"]
    before = store.get(old.id)
    assert not before.current and before.ended_why == "edited by hand"
    new = store.get(before.superseded_by)
    assert new.text == "My dentist is Dr. Haddad on Mondays" and new.importance == 0.9


def test_a_line_moved_to_ended_is_ended_by_hand(made):
    store, files, folder = made
    m = store.add("fact", "I go swimming every Thursday")
    store.flush()

    def move(t):
        line = f"- I go swimming every Thursday · {m.happened:%Y-%m-%d}"
        return t.replace(line + "\n", "") + line + "\n"

    edit(folder / "facts.md", move)
    files.sync()
    assert store.get(m.id).ended_why == "ended by hand" and texts(store, "fact") == []


def test_notepads_quirks_are_fine(made):
    store, files, folder = made
    store.add("fact", "Marcus is my friend")
    store.flush()
    raw = (folder / "facts.md").read_text(encoding="utf-8").replace("## Ended", "- Added in Notepad\n\n## Ended")
    (folder / "facts.md").write_bytes(b"\xef\xbb\xbf" + raw.replace("\n", "\r\n").encode("utf-8"))   # BOM and CRLF
    os.utime(folder / "facts.md", (time.time() + 9, time.time() + 9))
    files.sync()
    assert texts(store, "fact") == ["Added in Notepad", "Marcus is my friend"]


def test_commentary_and_headings_are_not_memories(made):
    store, files, folder = made
    store.add("fact", "Marcus is my friend")
    store.flush()
    edit(folder / "facts.md", lambda t: t + "\nA note to myself that is not a list item.\n# Another heading\n")
    files.sync()
    assert texts(store, "fact") == ["Marcus is my friend"] and store.count(("fact",), False) == 0


def test_a_deleted_file_is_written_again(made):
    store, files, folder = made
    store.add("fact", "Marcus is my friend")
    store.flush()
    (folder / "facts.md").unlink()
    files.sync()
    assert "Marcus is my friend" in (folder / "facts.md").read_text(encoding="utf-8")
    assert texts(store, "fact") == ["Marcus is my friend"]


def test_a_change_in_the_store_and_a_hand_edit_at_once_both_survive(made):
    store, files, folder = made
    store.add("fact", "Marcus is my friend")
    store.flush()
    edit(folder / "facts.md", lambda t: t.replace("## Ended", "- Written by hand\n\n## Ended"))
    store.add("fact", "Added by MARY a moment later")      # not in the file yet, and must not look 'removed'
    files.sync()
    assert texts(store, "fact") == ["Added by MARY a moment later", "Marcus is my friend", "Written by hand"]
    text = (folder / "facts.md").read_text(encoding="utf-8")
    assert "Added by MARY a moment later" in text and "Written by hand" in text


def test_the_store_can_be_rebuilt_from_the_files(tmp_path):
    first = MemoryStore(tmp_path / "memory.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    MemoryFiles(first, tmp_path)
    old = first.add("fact", "I live in Lisbon", happened=datetime(2026, 3, 1, 10, 0))
    first.add_fact("I live in Porto", replaces=old.id)
    first.add("diary", 'Asked: "open Spotify".', happened=datetime(2026, 10, 30, 9, 14))
    first.add("reflection", "A quiet day.", happened=datetime(2026, 10, 30, 23, 30))
    first.close()
    for f in tmp_path.glob("memory.db*"):
        f.unlink()
    again = MemoryStore(tmp_path / "memory.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    files = MemoryFiles(again, tmp_path)
    files.sync()
    assert texts(again, "fact") == ["I live in Porto"]
    ended = again.list(kinds=("fact",), current=False)
    assert [m.text for m in ended] == ["I live in Lisbon"] and ended[0].ended_why == "superseded"
    assert texts(again, "diary") == ['Asked: "open Spotify".']
    assert texts(again, "reflection") == ["A quiet day."]
    again.flush()
    assert again.vectors.count("text") == again.count(None, None)   # vectors made again, ended records too
    again.close()


def test_deleting_for_good_rewrites_the_file_without_the_line(made):
    store, files, folder = made
    keep = store.add("fact", "Marcus is my friend")
    gone = store.add("fact", "my locker combination is qqzzmarker")
    store.flush()
    assert "qqzzmarker" in (folder / "facts.md").read_text(encoding="utf-8")
    store.delete(gone.id)
    store.flush()
    assert "qqzzmarker" not in (folder / "facts.md").read_text(encoding="utf-8")
    assert store.get(keep.id) is not None


def test_a_hand_removed_diary_line_is_ended_and_an_added_one_taken_in(made):
    store, files, folder = made
    store.add("diary", 'Asked: "open Spotify".', happened=datetime(2026, 10, 30, 9, 14))
    store.add("diary", 'Asked: "text Lena that I am late".', happened=datetime(2026, 10, 30, 9, 20))
    store.flush()
    edit(folder / "diary" / "2026-10-30.md",
         lambda t: t.replace('- 09:20 Asked: "text Lena that I am late".\n', "- 18:05 Went for a long walk\n"))
    files.sync()
    assert texts(store, "diary") == ['Asked: "open Spotify".', "Went for a long walk"]
    assert texts(store, "diary", current=False) == ['Asked: "text Lena that I am late".']


def test_reflections_round_trip(made):
    store, files, folder = made
    store.add("reflection", "A quiet day: mostly music.", happened=datetime(2026, 10, 28, 23, 30))
    store.flush()
    text = (folder / "reflections.md").read_text(encoding="utf-8")
    assert "## 2026-10-28" in text
    assert [i.text for i in parse_reflections(text)] == ["A quiet day: mostly music."]
