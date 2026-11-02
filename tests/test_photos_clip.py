"""Picture embeddings (Phase 4: mary/photos/clip.py): searching by what a photo shows ('pink car', 'beach at sunset'),
finding visually similar photos, giving precedence to the person's own words, and cleaning up vectors when a photo is removed."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mary.memory.store import MemoryStore
from mary.photos import clip, ingest, library, synth
from mary.photos.places import Gazetteer

SCENES = [
    ("pink_car.jpg", "pink car", "a bright pink car parked on the road", datetime(2023, 4, 10, 14, 0)),
    ("sunset_beach.jpg", "beach at sunset", "sandy beach at sunset over the ocean", datetime(2023, 6, 15, 19, 30)),
    ("birthday_cake.jpg", "birthday cake", "a birthday cake with lit candles", datetime(2023, 8, 2, 20, 0)),
    ("harbour_boat.jpg", "harbour boat", "a sailboat in the harbour on blue water", datetime(2023, 9, 12, 11, 0)),
    ("snowy_mountain.jpg", "snowy mountain", "snowy mountain peaks under a blue sky", datetime(2023, 11, 20, 10, 0)),
]

QUESTIONS = [
    ("pink car", "pink_car.jpg"),
    ("a pink automobile", "pink_car.jpg"),
    ("beach at sunset", "sunset_beach.jpg"),
    ("sundown by the seashore", "sunset_beach.jpg"),
    ("birthday cake with candles", "birthday_cake.jpg"),
    ("sailboat in the harbour", "harbour_boat.jpg"),
    ("snowy mountains", "snowy_mountain.jpg"),
]


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "Pictures"
    clip_eng = synth.StandInClip()
    for fname, kind, desc, taken in SCENES:
        img = synth.scene_picture(kind, seed=10)
        clip_eng.teach(img, desc)
        synth.save(root / fname, img, taken)
    store = MemoryStore(tmp_path / "user" / "memory" / "memory.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"read_text": False, "faces": {"allowed": False}}, Gazetteer([]))
    ing = ingest.Ingest(lib, clip_engine=clip_eng)
    lib.add_folder(root)
    ing.scan()
    ing.drain()
    yield root, lib, ing, clip_eng, store
    ing.stop()
    store.close()


def by_name(lib: library.PhotoLibrary) -> dict[str, int]:
    out = {}
    for r in lib._all("SELECT photo_id, path FROM photo_files WHERE state='ok' AND photo_id IS NOT NULL"):
        out[Path(r["path"]).name] = int(r["photo_id"])
    return out


def test_picture_search_finds_the_right_photos_on_a_test_set_of_questions(world):
    _, lib, _, clip_eng, store = world
    ids = by_name(lib)
    assert len(ids) == len(SCENES)
    assert store.vectors.count("image", "photos") == len(SCENES)
    assert lib.status()["clip_left"] == 0

    for query, expected_file in QUESTIONS:
        want_id = ids[expected_file]
        hits = lib.picture_search(query, limit=3)
        assert hits and hits[0][0] == want_id, f"picture_search({query!r}) returned {hits}, expected {want_id} ({expected_file})"
        listed = lib.list(q=query)["items"]
        assert listed and listed[0]["id"] == want_id, f"list(q={query!r}) returned {listed}, expected {want_id} ({expected_file})"


def test_the_persons_own_caption_and_album_take_precedence_over_picture_embeddings(world):
    _, lib, _, _, _ = world
    ids = by_name(lib)
    # The picture in pink_car.jpg visually shows a pink car, but the person captioned harbour_boat.jpg "our pink car"
    lib.set_caption(ids["harbour_boat.jpg"], "our pink car by the dock")
    got = [i["id"] for i in lib.list(q="pink car")["items"]]
    assert got[:2] == [ids["harbour_boat.jpg"], ids["pink_car.jpg"]]


def test_similar_photos_are_found_by_picture_embeddings_as_well_as_dhash(world):
    root, lib, ing, clip_eng, _ = world
    # Add a second beach-at-sunset photo taken days later with a different layout (different dhash, close CLIP vector)
    img2 = synth.scene_picture("beach at sunset", seed=99, size=(640, 480))
    clip_eng.teach(img2, "sandy beach at sunset over the ocean", variant=1)
    synth.save(root / "sunset_beach_2.jpg", img2, datetime(2023, 6, 20, 19, 45))
    ing.scan()
    ing.drain()
    ids = by_name(lib)
    sim = [i["id"] for i in lib.similar(ids["sunset_beach.jpg"])]
    assert ids["sunset_beach_2.jpg"] in sim
    assert ids["pink_car.jpg"] not in sim


def test_switching_clip_models_drops_old_vectors_and_embeds_again(world):
    _, lib, ing, _, store = world
    assert store.vectors.count("image", "photos") == len(SCENES)
    ing._clip_engine = synth.StandInClip(tag="stand-in-clip-2")
    for _, kind, desc, _ in SCENES:
        ing._clip_engine.teach(synth.scene_picture(kind, seed=10), desc)
    ing.drain()
    assert store.vectors.count("image", "photos") == len(SCENES)
    assert {r["image_model"] for r in lib._all("SELECT image_model FROM photos")} == {"stand-in-clip-2"}


def test_removing_a_photo_deletes_its_picture_vector_for_good(world):
    _, lib, _, _, store = world
    ids = by_name(lib)
    pid = ids["pink_car.jpg"]
    assert store.vectors.get("image", pid, "photos") is not None
    lib.remove([pid])
    assert store.vectors.get("image", pid, "photos") is None
    assert store.vectors.count("image", "photos") == len(SCENES) - 1


def test_real_clip_model_searches_synthetic_scenes_on_cpu(tmp_path, monkeypatch):
    """When the real CLIP ViT-B/32 ONNX models are present in data/models/fastembed, verify real image-text search."""
    monkeypatch.setattr(clip, "ENABLED", True)
    store = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"read_text": False, "faces": {"allowed": False}}, Gazetteer([]))
    if not clip.available(lib):
        store.close()
        pytest.skip("CLIP models not downloaded in data/models/fastembed")
    engine = clip.load(lib)
    assert engine is not None
    root = tmp_path / "RealClip"
    for fname, kind, _, taken in SCENES:
        synth.save(root / fname, synth.scene_picture(kind, seed=10), taken)
    ing = ingest.Ingest(lib, clip_engine=engine)
    lib.add_folder(root)
    ing.scan()
    ing.drain()
    ids = by_name(lib)
    for query, expected_file in [
        ("a pink car", "pink_car.jpg"),
        ("sunset over the beach", "sunset_beach.jpg"),
        ("birthday cake with lit candles", "birthday_cake.jpg"),
        ("sailboat on the water", "harbour_boat.jpg"),
        ("snowy mountain peaks", "snowy_mountain.jpg"),
    ]:
        hits = lib.picture_search(query, limit=2)
        assert hits and hits[0][0] == ids[expected_file], f"real CLIP query {query!r} got {hits}"
    ing.stop()
    store.close()
