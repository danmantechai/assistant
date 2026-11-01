"""The whole path with invented photos and a stand-in for the face models (synth.StandIn): add a folder, the faces are found and
grouped, you name a group once, a new photo arrives and its faces are recognised, a doubtful one is asked about; what the worker
does and does not do, and what it logs. Nothing here says anything about how well a real model sees a real face."""
import json
import threading
import time

import numpy as np
import pytest

from mary.memory.store import MemoryStore
from mary.photos import faces, ingest, library, synth
from mary.photos.cluster import unit
from mary.photos.places import Gazetteer

BOX = (0.3, 0.2, 0.25, 0.3)


@pytest.fixture
def world(tmp_path):
    store = MemoryStore(tmp_path / "user" / "memory" / "memory.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"read_text": False}, Gazetteer([]))
    engine = synth.StandIn()
    ing = ingest.Ingest(lib, engine=engine)
    yield lib, ing, engine, tmp_path / "Photos"
    ing.stop()
    store.close()


class Shoot:
    """Makes invented photos on disk and teaches the stand-in what is in each."""

    def __init__(self, root, engine, cast):
        self.root, self.engine, self.cast, self.n = root, engine, cast, 0

    def photo(self, vecs, folder="Camera", score=0.99, orientation=1, boxes=None, size=(640, 480)):
        self.n += 1
        img = synth.picture(1000 + self.n, size)
        vecs = vecs if isinstance(vecs, list) else [vecs]
        boxes = boxes or [(0.08 + 0.4 * k, 0.2, 0.25, 0.3) for k in range(len(vecs))]
        self.engine.teach(img, [(b, v, score) for b, v in zip(boxes, vecs)])
        path = synth.save(self.root / folder / f"IMG_{self.n:04}.jpg", img, orientation=orientation)
        return path


def run(lib, ing):
    ing.scan()
    ing.drain()


def groups_of(lib):
    out = {}
    for r in lib._all("SELECT id, person_id FROM photo_faces WHERE person_id IS NOT NULL"):
        out.setdefault(r["person_id"], set()).add(r["id"])
    return out


# ---------------------------------------------------------------- off until switched on

def test_nothing_is_read_for_faces_while_it_is_off_and_it_catches_up_when_turned_on_again(world):
    lib, ing, engine, root = world
    lib.people.set_enabled(False)
    shoot = Shoot(root, engine, synth.Cast(1))
    shoot.cast.person("ama")
    for _ in range(4):
        shoot.photo(shoot.cast.face("ama"))
    lib.add_folder(root)
    run(lib, ing)
    assert lib.status()["photos"] == 4 and engine.detected == 0
    assert lib._one("SELECT count(*) FROM photo_faces")[0] == 0
    assert lib.people.status(engine.tag)["left"] == 4
    lib.people.set_enabled(True)
    run(lib, ing)
    assert engine.detected == 4 and lib.people.status(engine.tag)["faces"] == 4 and lib.people.status(engine.tag)["left"] == 0
    assert ing.progress()["faces"] == {"on": True, "ready": True}


def test_a_photo_is_read_for_faces_once(world):
    lib, ing, engine, root = world
    shoot = Shoot(root, engine, synth.Cast(2))
    shoot.cast.person("ama")
    for _ in range(5):
        shoot.photo(shoot.cast.face("ama"))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    run(lib, ing)
    ing.request_scan()
    run(lib, ing)
    assert engine.detected == 5 and lib._one("SELECT count(*) FROM photo_faces")[0] == 5


def test_turning_it_off_again_stops_the_reading_and_keeps_what_was_found(world):
    lib, ing, engine, root = world
    shoot = Shoot(root, engine, synth.Cast(3))
    shoot.cast.person("ama")
    for _ in range(3):
        shoot.photo(shoot.cast.face("ama"))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    lib.people.set_enabled(False)
    for _ in range(3):
        shoot.photo(shoot.cast.face("ama"))
    run(lib, ing)
    assert lib.status()["photos"] == 6 and engine.detected == 3 and lib._one("SELECT count(*) FROM photo_faces")[0] == 3


# ---------------------------------------------------------------- the whole path

def test_faces_are_grouped_you_name_a_group_and_new_photos_are_recognised(world):
    lib, ing, engine, root = world
    cast = synth.Cast(4)
    names = [cast.person(n) for n in ("p0", "p1", "p2")]
    shoot = Shoot(root, engine, cast)
    for _ in range(8):
        for n in names:
            shoot.photo(cast.face(n))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    people = lib.people
    assert len(groups_of(lib)) == 3 and people.status(engine.tag)["named"] == 0
    first = {n: lib._one("SELECT person_id FROM photo_faces ORDER BY id LIMIT 1 OFFSET ?", (k,))[0] for k, n in enumerate(names)}
    people.name(first["p0"], "Ama", "friend")
    people.name(first["p1"], "Kofi")
    run(lib, ing)                                                  # the worker does the grouping work that follows a name
    # new photos arrive: Ama, Kofi, a stranger, and one that might be Ama
    base = unit(sum(people.vectors.get("face", f, "photo_faces") for f in people._gal().exemplar_ids(first["p0"])))
    off = unit(np.random.default_rng(1).normal(size=128))
    off = unit(off - (off @ base) * base)
    maybe = 0.46 * base + (1 - 0.46 ** 2) ** 0.5 * off
    ama = shoot.photo(cast.face("p0"))
    kofi = shoot.photo(cast.face("p1"))
    stranger = shoot.photo(cast.strangers(1)[0])
    unsure = shoot.photo(maybe.astype(np.float32))
    group = shoot.photo([cast.face("p0"), cast.face("p1")])
    run(lib, ing)

    def who(path):
        pid = lib._one("SELECT photo_id FROM photo_files WHERE path=?", (str(path),))[0]
        return people.in_photo(pid)

    assert [x["name"] for x in who(ama)["named"]] == ["Ama"]
    assert [x["name"] for x in who(kofi)["named"]] == ["Kofi"]
    assert who(stranger)["named"] == [] and who(stranger)["unnamed"] == 1
    assert who(unsure)["named"] == [] and who(unsure)["unnamed"] == 1                   # not given a name
    assert sorted(x["name"] for x in who(group)["named"]) == ["Ama", "Kofi"]
    q = people.questions()
    assert len(q) == 1 and q[0]["person"]["name"] == "Ama" and q[0]["count"] == 1        # asked, not guessed
    assert people.people()[0]["name"] == "Ama" and people.people()[0]["photos"] == 8 + 1 + 1
    assert people.status(engine.tag)["questions"] == 1


def test_the_picture_is_read_the_right_way_up_and_a_smaller_copy_is_one_photo(world):
    lib, ing, engine, root = world
    cast = synth.Cast(5)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    v = cast.face("ama")
    img = synth.picture(777, (640, 480))
    engine.teach(img, [((0.1, 0.1, 0.3, 0.4), v, 0.99)])
    synth.save(root / "a" / "turned.jpg", img, orientation=6)             # stored on its side, turned by its tag
    synth.save(root / "b" / "small.jpg", img.resize((320, 240)), quality=70)    # the same picture, smaller
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    assert lib.status()["photos"] == 1 and engine.detected == 1
    row = lib._one("SELECT * FROM photo_faces")
    assert (round(row["x"], 2), round(row["y"], 2), round(row["w"], 2), round(row["h"], 2)) == (0.1, 0.1, 0.3, 0.4)


def test_a_small_or_unsure_face_is_kept_as_a_box_and_left_out_of_grouping(world):
    lib, ing, engine, root = world
    cast = synth.Cast(6)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    shoot.photo(cast.face("ama"))
    shoot.photo(cast.face("ama"), score=0.4)                                  # the detector is not sure
    shoot.photo(cast.face("ama"), boxes=[(0.3, 0.2, 0.04, 0.05)])             # 25 pixels across
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    st = lib.people.status(engine.tag)
    assert st["faces"] == 3 and st["usable"] == 1 and st["unclear"] == 2 and st["groups"] == 1
    assert engine.embedded == 1                                               # an unclear face is never fingerprinted
    assert lib._one("SELECT count(*) FROM vectors WHERE space='face'")[0] == 1


def test_a_folder_removed_takes_its_faces_with_it(world):
    lib, ing, engine, root = world
    cast = synth.Cast(7)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    for _ in range(4):
        shoot.photo(cast.face("ama"), folder="Trip")
    lib.people.set_enabled(True)
    folder = lib.add_folder(root)
    run(lib, ing)
    assert lib.people.status(engine.tag)["faces"] == 4
    crops = list((lib.dir / "faces").rglob("*.jpg"))
    assert crops
    lib.remove_folder(folder["id"])
    assert lib.people.status(engine.tag)["faces"] == 0 and lib._one("SELECT count(*) FROM people")[0] == 0
    assert lib._one("SELECT count(*) FROM vectors WHERE space='face'")[0] == 0 and not any(c.exists() for c in crops)


# ---------------------------------------------------------------- the worker

def test_the_models_not_being_there_is_not_an_error_and_is_tried_again_later(world, monkeypatch):
    lib, ing, engine, root = world
    cast = synth.Cast(8)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    shoot.photo(cast.face("ama"))
    ing._engine = None
    calls = []
    monkeypatch.setattr(faces, "load", lambda lib_: calls.append(1) or None)
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    assert lib.status()["photos"] == 1 and calls == [1] and ing.progress()["faces"] == {"on": True, "ready": False}
    run(lib, ing)
    assert calls == [1]                                  # not tried again on every step
    ing._engine_at -= ingest.RETRY_MODELS_S + 1
    monkeypatch.setattr(faces, "load", lambda lib_: engine)
    run(lib, ing)
    assert lib.people.status(engine.tag)["faces"] == 1


def test_a_model_that_fails_on_one_picture_does_not_stop_the_rest(world):
    lib, ing, engine, root = world
    cast = synth.Cast(9)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    for _ in range(4):
        shoot.photo(cast.face("ama"))
    real = engine.detect
    n = []

    def sometimes(image):
        n.append(1)
        if len(n) == 2:
            raise RuntimeError("the model fell over")
        return real(image)

    engine.detect = sometimes
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    assert lib.people.status(engine.tag)["faces"] == 3 and lib.people.status(engine.tag)["left"] == 0


def test_the_worker_takes_turns_between_the_faces_and_the_words(world, monkeypatch):
    from mary.photos import text

    lib, ing, engine, root = world
    cast = synth.Cast(10)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    for _ in range(6):
        shoot.photo(cast.face("ama"))
    monkeypatch.setattr(text, "available", lambda: True)
    monkeypatch.setattr(text, "read_text", lambda path: "")
    lib.settings["read_text"] = True
    lib.people.set_enabled(True)
    lib.add_folder(root)
    ing.scan()
    for _ in range(6):                                   # the six quick-fact steps
        ing.step()
    order = []
    for _ in range(6):
        ing.step()
        order.append(ing.state)
    assert order.count("faces") == 3 and order.count("text") == 3 and order[0] != order[1]
    ing.drain()
    assert lib.people.status(engine.tag)["left"] == 0 and lib.status()["text_left"] == 0


def test_the_worker_steps_aside_for_faces_too(world):
    lib, ing, engine, root = world
    cast = synth.Cast(11)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    for _ in range(3):
        shoot.photo(cast.face("ama"))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    ing.scan()
    ing.drain()
    busy = {"now": True}
    ing.busy = lambda: busy["now"]
    lib.people.look_again()
    t = threading.Thread(target=lambda: ing.drain(seconds=3))
    t.start()
    time.sleep(1.0)
    assert engine.detected == 3                          # nothing was read while she was busy
    busy["now"] = False
    t.join(timeout=10)
    assert engine.detected == 6 and not t.is_alive()


def test_what_the_worker_logs_about_faces_is_counts_only(world, monkeypatch):
    lib, ing, engine, root = world
    cast = synth.Cast(12)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    for _ in range(5):
        shoot.photo(cast.face("ama"))
    logged = []
    monkeypatch.setattr(ingest, "write_jsonl", lambda name, record: logged.append(record))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    run(lib, ing)
    pid = lib._one("SELECT person_id FROM photo_faces LIMIT 1")[0]
    lib.people.name(pid, "Ama Mensah")
    ing._log()
    blob = json.dumps(logged)
    for secret in ("Ama", "Mensah", "IMG_", str(root), "vec"):
        assert secret not in blob
    assert logged[-1]["faces"]["faces"] == 5 and all(isinstance(v, int) for v in logged[-1]["faces"].values())


def test_the_thread_finds_reads_and_groups_faces_then_stops(world):
    lib, ing, engine, root = world
    cast = synth.Cast(13)
    cast.person("ama")
    shoot = Shoot(root, engine, cast)
    for _ in range(6):
        shoot.photo(cast.face("ama"))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    ing.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (lib.status()["photos"] == 6 and lib.people.status(engine.tag)["left"] == 0):
        time.sleep(0.1)
    ing.stop()
    assert lib.people.status(engine.tag)["faces"] == 6 and len(groups_of(lib)) == 1 and not ing._thread.is_alive()


def test_faces_cost_what_the_page_says_and_a_few_hundred_photos_go_through(world):
    lib, ing, engine, root = world
    cast = synth.Cast(14)
    names = [cast.person(f"p{i}") for i in range(10)]
    shoot = Shoot(root, engine, cast)
    for i in range(150):
        shoot.photo([cast.face(names[i % 10]), cast.face(names[(i + 3) % 10])] if i % 3 == 0 else cast.face(names[i % 10]),
                    size=(320, 240))
    lib.people.set_enabled(True)
    lib.add_folder(root)
    t = time.perf_counter()
    run(lib, ing)
    took = time.perf_counter() - t
    st = lib.people.status(engine.tag)
    print(f"\n150 invented photos, {st['faces']} faces into {st['groups']} groups in {took:.1f} s "
          f"(faces stage {ing.average_ms()['faces']} ms a photo, stand-in model)")
    assert st["faces"] == 200 and st["groups"] == 10 and st["left"] == 0
