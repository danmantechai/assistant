"""Moments (Phase 4: mary/photos/moments.py):
- Photos grouped by time and place into events, each with a short summary ('June 1998 · the coast · a wedding · Grandma, Kofi').
- Each moment has its time span, place, people and photos, best one first.
- The person can rename, merge or split moments, and their choices survive rescans."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from mary.memory.store import MemoryStore
from mary.photos import ingest, library, synth
from mary.photos.places import Gazetteer

PLACES = [
    ("Harbourton", "Coastal Region", "Ghanaland", 5.1053, -1.2466, 90000),
    ("Northfield", "Greater North", "Britannia", 51.5072, -0.1276, 8_000_000),
    ("the coast", "Coastal Region", "Ghanaland", synth.COAST[0], synth.COAST[1], 50000),
]


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "Pictures"
    truths, cast, face_eng, extra = synth.moments_set(root)
    store = MemoryStore(tmp_path / "user" / "memory" / "memory.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"read_text": False}, Gazetteer(PLACES))
    ing = ingest.Ingest(lib, engine=face_eng)
    lib.add_folder(root)
    ing.scan()
    ing.drain()

    # Name the three people (Grandma, Kofi, Ama) by matching their known centres in `cast`
    for g in lib.people.groups(limit=20, min_faces=1)["items"]:
        fid = g["samples"][0]["id"]
        vec = store.vectors.get("face", fid, "photo_faces")
        best_who = max(("Grandma", "Kofi", "Ama"), key=lambda w: float(vec @ cast.people[w][0]))
        lib.people.name(g["id"], best_who)
    ing.drain()
    lib.maintain_moments()
    yield root, truths, extra, lib, ing, store
    ing.stop()
    store.close()


def pid_of(root: Path, lib: library.PhotoLibrary, rel: str) -> int:
    path = str(root / rel).replace("/", os.sep)
    r = lib._one("SELECT photo_id FROM photo_files WHERE path=?", (path,))
    assert r and r["photo_id"], rel
    return int(r["photo_id"])


def test_moments_match_the_made_up_set_with_known_events_and_best_photo_first(world):
    root, truths, extra, lib, _, _ = world
    moments = lib.moments()["items"]
    assert len(moments) == len(truths)

    by_when = {m["when"]: lib.moment(m["id"]) for m in moments}
    for t in truths:
        assert t.when in by_when, f"missing moment for {t.when}: got {list(by_when)}"
        m = by_when[t.when]
        assert m["summary"] == t.summary
        assert m["place"] == t.place
        assert [p["name"] for p in m["people"]] == t.people
        want_ids = {pid_of(root, lib, rel) for rel in t.rels}
        got_ids = [item["id"] for item in m["items"]]
        assert set(got_ids) == want_ids
        # Best photo is first (cover_id and items[0])
        best_id = pid_of(root, lib, t.best_rel)
        assert m["cover_id"] == best_id and got_ids[0] == best_id

    # The isolated single photo is not in any multi-photo moment
    lone_id = pid_of(root, lib, extra["lone"])
    assert lib.get(lone_id)["moment"] is None


def test_a_caption_or_vision_tag_provides_the_moment_theme_and_user_words_win(world):
    root, _, _, lib, _, _ = world
    by_when = {m["when"]: m for m in lib.moments()["items"]}
    weekend = by_when["1–2 May 2021"]
    pids = [i["id"] for i in lib.moment(weekend["id"])["items"]]

    # 1. Vision tags supply the theme when the user hasn't written a caption or album name
    lib.set_seen(pids[0], "[1] hiking in the hills", ["hike", "hills"], "bonsai-2:2b")
    lib.maintain_moments()
    assert lib.moment(weekend["id"])["summary"] == "1–2 May 2021 · Northfield · a hike · Kofi"

    # 2. The person's own photo caption wins over vision tags AND promotes that photo to #1 (best photo first)
    lib.set_caption(pids[-1], "Kofi's graduation dinner")
    m = lib.moment(weekend["id"])
    assert m["summary"] == "1–2 May 2021 · Northfield · a graduation · Kofi"
    assert m["cover_id"] == pids[-1] and m["items"][0]["id"] == pids[-1]


def test_moments_can_be_renamed_merged_and_split_and_keep_their_changes_on_rescan(world):
    _, _, _, lib, ing, _ = world
    by_when = {m["when"]: m for m in lib.moments()["items"]}
    wedding = by_when["June 1998"]
    scans = by_when["the 1990s"]

    # 1. Rename a moment
    renamed = lib.rename_moment(wedding["id"], "Auntie Esi's wedding")
    assert renamed["title"] == "Auntie Esi's wedding"
    assert renamed["summary"] == "June 1998 · the coast · Auntie Esi's wedding · Grandma, Kofi"
    assert renamed["how"] == "you"
    lib.maintain_moments()
    assert lib.moment(wedding["id"])["summary"] == "June 1998 · the coast · Auntie Esi's wedding · Grandma, Kofi"

    # Clearing the custom title restores the automatic summary
    cleared = lib.rename_moment(wedding["id"], "")
    assert cleared["summary"] == "June 1998 · the coast · a wedding · Grandma, Kofi"

    # 2. Split a moment into two
    w_items = [i["id"] for i in lib.moment(wedding["id"])["items"]]
    assert len(w_items) == 4
    split_out = lib.split_moment(wedding["id"], w_items[2:], title="The reception")
    assert split_out["photos"] == 2 and split_out["title"] == "The reception"
    assert lib.moment(wedding["id"])["photos"] == 2
    assert lib.moments()["total"] == 5

    # Rescanning / maintaining moments respects the split and keeps both moments separate
    ing.scan()
    ing.drain()
    lib.maintain_moments()
    assert lib.moment(wedding["id"])["photos"] == 2
    assert lib.moment(split_out["id"])["photos"] == 2

    # 3. Merge two moments back into one
    merged = lib.merge_moments(wedding["id"], split_out["id"])
    assert merged["id"] == wedding["id"] and merged["photos"] == 4
    assert lib.moment(split_out["id"]) is None
    assert lib.moments()["total"] == 4

    # Invalid operations raise clean sentences
    with pytest.raises(library.PhotoError, match="different moments"):
        lib.merge_moments(wedding["id"], wedding["id"])
    with pytest.raises(library.PhotoError, match="leave at least one"):
        lib.split_moment(scans["id"], [i["id"] for i in lib.moment(scans["id"])["items"]])
