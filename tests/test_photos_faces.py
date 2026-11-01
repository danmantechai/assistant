"""The parts of mary/photos/faces.py that are ours: alignment, overlapping boxes, how a big picture is covered, which faces are
clear enough, fetching the models. The real models are exercised too, when their files are on this PC (they are fetched on the People
page); no real face is used anywhere in these tests."""
import hashlib
import io
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mary.photos import faces, synth

MODELS = Path(__file__).resolve().parent.parent / "data" / "models" / "faces"
needs_models = pytest.mark.skipif(not faces.present(MODELS), reason="the face models are not on this PC (People page > fetch)")


# ---------------------------------------------------------------- alignment

def test_a_similarity_is_recovered_exactly_from_the_points_it_moved():
    rng = np.random.default_rng(1)
    src = rng.uniform(0, 300, size=(5, 2))
    angle, scale, shift = np.radians(23), 0.6, np.array([40.0, -12.0])
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    dst = scale * src @ rot.T + shift
    m = faces.similarity(src, dst)
    assert np.allclose(src @ m[:, :2].T + m[:, 2], dst, atol=1e-6)
    assert np.isclose(np.linalg.det(m[:, :2]), scale ** 2, rtol=1e-6)         # no mirror image


def test_a_face_is_turned_upright_and_scaled_so_its_points_land_on_the_template():
    angle, scale, shift = np.radians(-25), 1.8, np.array([150.0, 90.0])
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    points = faces.TEMPLATE @ rot.T * scale + shift
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)]
    img = Image.new("RGB", (420, 360), (0, 0, 0))
    px = img.load()
    for (x, y), c in zip(points, colours):
        for dx in range(-5, 6):
            for dy in range(-5, 6):
                px[int(x) + dx, int(y) + dy] = c
    out = np.asarray(faces.align(img, points), dtype=np.float64)
    assert out.shape == (112, 112, 3)
    for (tx, ty), c in zip(faces.TEMPLATE, colours):
        near = np.linalg.norm(out - np.array(c), axis=2) < 140
        ys, xs = np.nonzero(near)
        assert len(xs) > 10
        assert abs(xs.mean() - tx) < 1.5 and abs(ys.mean() - ty) < 1.5


# ---------------------------------------------------------------- boxes

def test_overlapping_boxes_keep_the_best_and_a_second_face_beside_it():
    boxes = np.array([[100, 100, 80, 80], [104, 102, 80, 80], [300, 100, 70, 70]], dtype=float)
    scores = np.array([0.90, 0.95, 0.80])
    assert faces.nms(boxes, scores) == [1, 2]


def test_a_small_box_mostly_inside_a_bigger_face_is_the_same_face_seen_in_a_piece():
    boxes = np.array([[100, 100, 400, 500], [200, 420, 200, 150], [700, 120, 150, 150]], dtype=float)
    scores = np.array([0.93, 0.85, 0.90])
    assert faces.nms(boxes, scores) == [0, 2]


def test_two_faces_that_only_touch_are_both_kept():
    boxes = np.array([[0, 0, 100, 100], [90, 0, 100, 100]], dtype=float)
    assert sorted(faces.nms(boxes, np.array([0.9, 0.9]))) == [0, 1]


# ---------------------------------------------------------------- covering a picture

def engine_without_models(side=640):
    e = faces.OnnxEngine.__new__(faces.OnnxEngine)
    e._side = side
    return e


def test_a_small_picture_is_looked_at_once_and_a_big_one_whole_and_in_overlapping_pieces():
    e = engine_without_models()
    assert e.passes(480, 320) == [(1.0, 0, 0, 480, 320)] and e.passes(640, 640) == [(1.0, 0, 0, 640, 640)]
    big = e.passes(1600, 1067)
    assert big[0][0] == pytest.approx(0.4) and all(p[0] == 1.0 for p in big[1:]) and len(big) <= 8
    assert all(w <= 640 and h <= 640 for _, _, _, w, h in big[1:])


@pytest.mark.parametrize("size", [(700, 700), (1000, 700), (1600, 1067), (1200, 1600), (1600, 1600), (2400, 800)])
def test_any_face_up_to_the_overlap_is_whole_inside_one_piece(size):
    e = engine_without_models()
    w, h = size
    tiles = [p for p in e.passes(w, h) if p[0] == 1.0]
    rng = np.random.default_rng(2)
    for _ in range(400):
        s = faces.TILE_OVERLAP
        x, y = rng.integers(0, w - s + 1), rng.integers(0, h - s + 1)
        assert any(tx <= x and ty <= y and x + s <= tx + tw and y + s <= ty + th for _, tx, ty, tw, th in tiles)


# ---------------------------------------------------------------- which faces are clear enough

def test_only_a_clear_face_gets_a_fingerprint_and_a_picture_and_the_best_comes_first():
    img = synth.picture(1, (800, 600))
    v1, v2 = np.random.default_rng(1).normal(size=(2, 128)).astype(np.float32)
    eng = synth.StandIn()
    eng.teach(img, [((0.05, 0.1, 0.2, 0.3), v1, 0.80), ((0.4, 0.1, 0.25, 0.35), v2, 0.97),
                    ((0.8, 0.1, 0.04, 0.05), v1, 0.99),          # 32 x 30 px: too small
                    ((0.7, 0.5, 0.2, 0.3), v2, 0.40)])           # the detector is unsure
    got = faces.analyse(img, eng)
    assert [f.usable for f in got] == [True, True, False, False][:0] or sorted(f.usable for f in got) == [False, False, True, True]
    assert got[0].score == 0.97 and got[0].quality >= got[1].quality
    for f in got:
        assert (f.vector is not None) == bool(f.crop)
        assert all(0.0 <= v <= 1.0 for v in (*f.box, *f.points))
        if f.vector is not None:
            assert np.isclose(np.linalg.norm(f.vector), 1.0, atol=1e-5)
    assert eng.embedded == 2
    best = [f for f in got if f.usable][0]
    pic = Image.open(io.BytesIO(best.crop))
    assert pic.size == (faces.CROP_PX, faces.CROP_PX)


def test_a_fingerprint_that_is_not_a_number_is_not_used():
    img = synth.picture(2, (800, 600))

    class Broken(synth.StandIn):
        def embed(self, image, detection):
            return np.full(128, np.nan, dtype=np.float32)

    eng = Broken()
    eng.teach(img, [((0.2, 0.2, 0.3, 0.4), np.ones(128, dtype=np.float32), 0.99)])
    (f,) = faces.analyse(img, eng)
    assert f.vector is None and not f.usable and f.crop == b""


def test_quality_follows_confidence_and_size_and_stays_between_zero_and_one():
    assert faces.quality(0.99, 200) > faces.quality(0.99, 60) > faces.quality(0.99, 20) >= faces.quality(0.99, 1)
    assert faces.quality(0.99, 200) > faces.quality(0.7, 200)
    assert 0 <= faces.quality(5, 10_000) <= 1 and 0 <= faces.quality(-1, 0) <= 1


def test_the_picture_of_a_face_at_the_edge_stays_inside_the_picture():
    img = synth.picture(3, (640, 480))
    for box in ((0, 0, 100, 100), (560, 380, 80, 100), (300, 0, 120, 120), (0, 200, 600, 400)):
        out = Image.open(io.BytesIO(faces.crop_jpeg(img, box)))
        assert out.size == (faces.CROP_PX, faces.CROP_PX)


# ---------------------------------------------------------------- fetching the models

def fake_files():
    return {info["url"]: (b"model-bytes-" + name.encode()) * 200 for name, info in faces.MODELS.items()}


def test_fetching_the_models_writes_both_and_leaves_no_half_file(tmp_path, monkeypatch):
    files = fake_files()
    monkeypatch.setattr(faces, "MODELS", {n: {**i, "sha256": hashlib.sha256(files[i["url"]]).hexdigest()}
                                          for n, i in faces.MODELS.items()})
    got = faces.fetch(tmp_path / "m", get=lambda url: files[url])
    assert set(got) == set(faces.MODELS) and faces.present(tmp_path / "m")
    assert not list((tmp_path / "m").glob("*.part"))


def test_a_wrong_or_tiny_file_is_refused_and_nothing_is_kept(tmp_path, monkeypatch):
    files = fake_files()
    monkeypatch.setattr(faces, "MODELS", {n: {**i, "sha256": hashlib.sha256(files[i["url"]]).hexdigest()}
                                          for n, i in faces.MODELS.items()})
    first = next(iter(faces.MODELS.values()))["url"]
    bad = dict(files)
    bad[list(faces.MODELS.values())[1]["url"]] = b"not the model" * 200
    with pytest.raises(ValueError, match="not the file"):
        faces.fetch(tmp_path / "m", get=lambda url: bad[url])
    assert not list((tmp_path / "m").glob("*"))
    with pytest.raises(ValueError):
        faces.fetch(tmp_path / "m", get=lambda url: b"x")


def test_the_pinned_hashes_and_sizes_are_those_of_the_files_on_this_pc_when_they_are_there():
    for name, info in faces.MODELS.items():
        assert info["url"].startswith("https://huggingface.co/opencv/") and len(info["sha256"]) == 64
        f = MODELS / name
        if f.exists():
            assert f.stat().st_size == info["bytes"] and hashlib.sha256(f.read_bytes()).hexdigest() == info["sha256"]


def test_without_the_model_files_nothing_loads_and_nothing_breaks(tmp_path):
    from mary.memory.store import MemoryStore
    from mary.photos.library import PhotoLibrary
    from mary.photos.places import Gazetteer

    store = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    lib = PhotoLibrary(store, {"faces": {"models": str(tmp_path / "nowhere")}}, Gazetteer([]))
    assert faces.load(lib) is None and not faces.present(tmp_path / "nowhere")
    store.close()


# ---------------------------------------------------------------- the real models

@pytest.fixture(scope="module")
def engine():
    return faces.OnnxEngine(MODELS, 2)


@needs_models
def test_the_real_detector_takes_one_fixed_square_and_the_fingerprint_is_128_numbers(engine):
    assert engine._side == 640 and engine.tag == "yunet-2023mar+sface-2021dec"
    det = faces.Detection((100, 80, 160, 190), ((140, 140), (220, 140), (180, 180), (150, 225), (210, 225)), 0.99)
    v = engine.embed(synth.picture(6, (480, 320)), det)
    assert v.shape == (128,) and np.isfinite(v).all() and np.linalg.norm(v) > 0


@needs_models
@pytest.mark.parametrize("size", [(320, 240), (640, 480), (1000, 700), (1600, 1067), (700, 1500)])
def test_no_clear_face_is_found_in_invented_pictures_of_any_size(engine, size):
    """The detector may make an unsure guess at a blob (it did once at 0.66, in a big picture); the gate on its confidence is what
    keeps that from ever being fingerprinted or grouped."""
    for seed in range(4):
        img = synth.picture(seed, size)
        assert all(d.score < faces.MIN_SCORE for d in engine.detect(img))
        assert not any(f.usable for f in faces.analyse(img, engine))


@needs_models
def test_the_same_input_gives_the_same_fingerprint_and_other_inputs_other_ones(engine):
    img = synth.picture(9, (480, 320))
    det = faces.Detection((120, 60, 150, 180), ((150, 120), (220, 120), (185, 160), (160, 205), (215, 205)), 0.99)
    a, b = engine.embed(img, det), engine.embed(img, det)
    assert np.array_equal(a, b)
    other = engine.embed(synth.picture(10, (480, 320)), det)
    assert float(a @ other / (np.linalg.norm(a) * np.linalg.norm(other))) < 0.9


@needs_models
def test_the_real_engine_works_through_the_whole_analysis_and_is_quick_enough(engine):
    img = synth.picture(11, (1600, 1067))
    engine.detect(img)
    t = time.perf_counter()
    found = faces.analyse(img, engine)
    took = time.perf_counter() - t
    print(f"\nreal engine on a 1600 x 1067 picture with no face: {took * 1000:.0f} ms")
    assert not any(f.usable for f in found) and took < 3.0


@needs_models
def test_the_worker_reads_a_photo_with_no_face_with_the_real_models_and_marks_it_read(tmp_path):
    from mary.memory.store import MemoryStore
    from mary.photos import ingest, library
    from mary.photos.places import Gazetteer

    store = MemoryStore(tmp_path / "user" / "memory" / "m.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"read_text": False, "faces": {"models": str(MODELS)}}, Gazetteer([]))
    synth.save(tmp_path / "Photos" / "a.jpg", synth.picture(1, (1200, 800)))
    ing = ingest.Ingest(lib)
    lib.people.set_enabled(True)
    lib.add_folder(tmp_path / "Photos")
    ing.scan()
    ing.drain()
    assert isinstance(ing._engine, faces.OnnxEngine)
    st = lib.people.status(ing._engine.tag)
    assert st["faces"] == 0 and st["left"] == 0 and lib.status()["photos"] == 1
    store.close()
