"""Vision descriptions per photo (Phase 4: mary/photos/see.py):
- Faces get numbered boxes ([1], [2], left-to-right) before the picture goes to the model.
- Names come ONLY from the face step (`people`); the model never names or guesses a person.
- Naming, unnaming, or deleting a person updates photo descriptions immediately without calling the model again.
- The person's own words (captions and album names) win over the model's.
- Background vision runs only while the PC is idle and plugged in, and refuses cloud models unless opted in."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from mary.memory import idle
from mary.memory.store import MemoryStore
from mary.photos import ingest, library, see, synth
from mary.photos.places import Gazetteer


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "Pictures"
    cast = synth.Cast(20)
    for who in ("Grandma", "Kofi", "Ama"):
        cast.person(who)
    face_eng = synth.StandIn()

    # Photo 1: two faces (left: Kofi at x=0.15, right: Grandma at x=0.60)
    img1 = synth.picture(701, (640, 480))
    # Teach in reverse x-order to verify that boxes are numbered [1], [2] strictly left-to-right
    face_eng.teach(img1, [
        ((0.60, 0.20, 0.22, 0.28), cast.face("Grandma", noise=0.2), 0.99),
        ((0.15, 0.22, 0.22, 0.28), cast.face("Kofi", noise=0.2), 0.99),
    ])
    synth.save(root / "party.jpg", img1, datetime(2023, 5, 10, 15, 0))

    # Photo 2: one face (Ama)
    img2 = synth.picture(702, (640, 480))
    face_eng.teach(img2, [((0.35, 0.20, 0.24, 0.30), cast.face("Ama", noise=0.2), 0.99)])
    synth.save(root / "garden.jpg", img2, datetime(2023, 5, 11, 12, 0))

    store = MemoryStore(tmp_path / "user" / "memory" / "memory.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(
        store,
        {"read_text": False, "vision": {"enabled": True, "model": "bonsai-2:2b", "idle_s": 60, "only_on_ac": True}},
        Gazetteer([]),
    )
    ing = ingest.Ingest(lib, engine=face_eng)
    lib.add_folder(root)
    ing.scan()
    # Drain non-vision steps first while PC is not idle
    ing.drain()
    yield root, lib, ing, cast, face_eng, store
    ing.stop()
    store.close()


def test_numbered_face_boxes_are_drawn_left_to_right_without_touching_originals(world):
    root, lib, _, _, _, _ = world
    original_bytes = (root / "party.jpg").read_bytes()
    pid = lib.list(year=2023, month=5)["items"][-1]["id"]
    thumb_before = lib.thumb_path(pid).read_bytes()

    faces = lib.people.faces_for_vision(pid)
    assert [f["num"] for f in faces] == [1, 2]
    assert faces[0]["box"][0] < faces[1]["box"][0]       # [1] is the left face (x=0.15), [2] is the right face (x=0.60)

    img = synth.picture(701, (640, 480))
    boxed = see.draw_face_boxes(img, faces)
    assert boxed is not img
    assert not np.array_equal(np.asarray(boxed), np.asarray(img))
    assert (root / "party.jpg").read_bytes() == original_bytes
    assert lib.thumb_path(pid).read_bytes() == thumb_before


def test_descriptions_never_name_anyone_the_face_step_did_not():
    # Even if the vision model tries to invent or guess names, titles, or known people's names, they are stripped
    forbidden = ["Grandma", "Kofi", "Ama", "Uncle Mensah"]
    cases = [
        ("[1] blowing out candles with [2]", 2, "[1] blowing out candles with [2]"),
        ("Alice blowing out candles with Bob in the kitchen", 0, "someone blowing out candles with someone in the kitchen"),
        ("[1] and Grandma Sarah smiling at the table", 1, "[1] and someone smiling at the table"),
        ("A man named David standing beside [1] and [3]", 1, "someone standing beside [1] and someone"),
        ("Kofi and Ama playing football on the beach", 2, "someone and someone playing football on the beach"),
        ("Dr. Mensah and Auntie Grace cutting a cake", 0, "someone and someone cutting a cake"),
    ]
    for raw, n_faces, expected in cases:
        cleaned = see.sanitize_description(raw, n_faces, forbidden)
        for bad in ("Alice", "Bob", "Grandma", "Sarah", "David", "Kofi", "Ama", "Mensah", "Grace", "[3]"):
            assert bad not in cleaned, f"{bad!r} leaked in {cleaned!r} from {raw!r}"
        assert cleaned == expected

    tags = see.sanitize_tags(["birthday cake", "candles", "Grandma", "Alice", "kitchen", "Kofi"], forbidden)
    assert tags == ["birthday cake", "candles", "kitchen"]


def test_names_come_only_from_people_and_update_when_named_unnamed_or_deleted(world, monkeypatch):
    _, lib, ing, _, _, _ = world
    # Simulate idle & on AC power, and a vision model that writes "[1] blowing out candles while [2] claps"
    monkeypatch.setattr(idle, "on_battery", lambda: False)
    monkeypatch.setattr(idle, "input_idle_seconds", lambda: 300.0)
    seen_images = []

    def fake_ask(img, prompt, *, model="", host=""):
        seen_images.append((img.size, prompt))
        if "[2]" in prompt:
            return '{"description": "[1] blowing out candles while [2] claps", "tags": ["birthday cake", "candles", "party"]}'
        return '{"description": "[1] reading a book in the garden with Alice", "tags": ["garden", "book", "Alice"]}'

    ing._vision_ask = fake_ask
    ing.drain()
    assert len(seen_images) == 2

    items = {i["when"]: lib.get(i["id"]) for i in lib.list()["items"]}
    party = items["10 May 2023, 15:00"]
    garden = items["11 May 2023, 12:00"]

    # Before anyone is named on the People page, [1] and [2] resolve to "someone" — and "Alice" was stripped
    assert party["seen"] == "someone blowing out candles while someone claps"
    assert party["tags"] == ["birthday cake", "candles", "party"]
    assert garden["seen"] == "someone reading a book in the garden with someone"
    assert "Alice" not in garden["seen"] and "Alice" not in garden["tags"]

    # Now the person names [1] ("Kofi") and [2] ("Grandma") on the People page
    faces = party["people"]["faces"]
    kofi_group = lib._one("SELECT person_id FROM photo_faces WHERE id=?", (faces[0]["id"],))["person_id"]
    grandma_group = lib._one("SELECT person_id FROM photo_faces WHERE id=?", (faces[1]["id"],))["person_id"]
    lib.people.name(kofi_group, "Kofi")
    assert lib.get(party["id"])["seen"] == "Kofi blowing out candles while someone claps"

    lib.people.name(grandma_group, "Grandma")
    assert lib.get(party["id"])["seen"] == "Kofi blowing out candles while Grandma claps"
    # No extra vision model call was needed!
    assert len(seen_images) == 2
    # Searchable via FTS
    assert [i["id"] for i in lib.list(q="blowing candles")["items"]] == [party["id"]]

    # Unnaming Kofi immediately reverts [1] to "someone"
    lib.people.unname(kofi_group)
    assert lib.get(party["id"])["seen"] == "someone blowing out candles while Grandma claps"

    # Deleting Grandma removes her name from the description and from the database
    lib.people.remove_person(grandma_group)
    assert lib.get(party["id"])["seen"] == "someone blowing out candles while someone claps"
    assert "Grandma" not in lib.get(party["id"])["seen"]


def test_the_persons_own_caption_wins_over_the_vision_models_description(world, monkeypatch):
    _, lib, ing, _, _, _ = world
    monkeypatch.setattr(idle, "on_battery", lambda: False)
    monkeypatch.setattr(idle, "input_idle_seconds", lambda: 300.0)
    ing._vision_ask = lambda img, prompt, **kw: '{"description": "[1] standing by a fence", "tags": ["fence"]}'
    ing.drain()

    garden_id = [i["id"] for i in lib.list()["items"] if "11 May" in i["when"]][0]
    assert lib.get(garden_id)["description"] == "someone standing by a fence"
    lib.set_caption(garden_id, "Ama's first day at school")
    got = lib.get(garden_id)
    assert got["caption"] == "Ama's first day at school"
    assert got["description"] == "Ama's first day at school"


def test_vision_runs_only_when_idle_and_plugged_in_and_refuses_cloud_without_opt_in(world, monkeypatch):
    _, lib, ing, _, _, _ = world
    called = []
    ing._vision_ask = lambda img, prompt, **kw: (called.append(1), '{"description": "a quiet street", "tags": ["street"]}')[1]

    # 1. On battery -> does not run
    monkeypatch.setattr(idle, "on_battery", lambda: True)
    monkeypatch.setattr(idle, "input_idle_seconds", lambda: 300.0)
    ing.drain()
    assert called == [] and lib.status()["vision_left"] == 2

    # 2. Plugged in, but user is using mouse/keyboard (idle < 60s) -> does not run
    monkeypatch.setattr(idle, "on_battery", lambda: False)
    monkeypatch.setattr(idle, "input_idle_seconds", lambda: 10.0)
    ing.drain()
    assert called == [] and lib.status()["vision_left"] == 2

    # 3. Idle and plugged in, but model is a cloud model ("beam:cloud") and allow_cloud is False -> refused
    monkeypatch.setattr(idle, "input_idle_seconds", lambda: 300.0)
    lib.settings["vision"]["model"] = "beam:cloud"
    lib.settings["vision"]["allow_cloud"] = False
    assert ing._see() is False and called == []

    # 4. Opting in to cloud or switching to a local model allows it to run and resume where it stopped
    lib.settings["vision"]["model"] = "bonsai-2:2b"
    assert ing._see() is True and len(called) == 1 and lib.status()["vision_left"] == 1
    assert ing._see() is True and len(called) == 2 and lib.status()["vision_left"] == 0
