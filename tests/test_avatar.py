"""Tests for avatar face asset store, checks, preparation, and packing (Phase 6)."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image, ImageDraw

from mary.avatar import check, landmarks, prepare, store
from mary.photos.faces import Detection


def _make_test_face_image(
    size: tuple[int, int] = (600, 600),
    face_box: tuple[int, int, int, int] = (150, 150, 300, 320),
    blur: bool = False,
    dark: bool = False,
) -> Image.Image:
    """Create a synthetic test face image with distinct eyes, nose, and mouth."""
    img = Image.new("RGB", size, (220, 210, 200))
    draw = ImageDraw.Draw(img)

    bx, by, bw, bh = face_box
    # Hair
    draw.rectangle((bx - 15, by - 25, bx + bw + 15, by + 50), fill=(30, 20, 15))
    # Face oval
    skin = (50, 45, 40) if dark else (235, 195, 170)
    draw.ellipse((bx, by, bx + bw, by + bh), fill=skin)

    # Eyes
    eye_y = by + int(bh * 0.35)
    re_x = bx + int(bw * 0.32)
    le_x = bx + int(bw * 0.68)
    draw.ellipse((re_x - 18, eye_y - 12, re_x + 18, eye_y + 12), fill=(255, 255, 255))
    draw.ellipse((re_x - 7, eye_y - 7, re_x + 7, eye_y + 7), fill=(40, 30, 20))
    draw.ellipse((le_x - 18, eye_y - 12, le_x + 18, eye_y + 12), fill=(255, 255, 255))
    draw.ellipse((le_x - 7, eye_y - 7, le_x + 7, eye_y + 7), fill=(40, 30, 20))

    # Eyebrows
    draw.line((re_x - 22, eye_y - 20, re_x + 22, eye_y - 18), fill=(50, 40, 30), width=4)
    draw.line((le_x - 22, eye_y - 18, le_x + 22, eye_y - 20), fill=(50, 40, 30), width=4)

    # Nose
    nose_x = bx + int(bw * 0.50)
    nose_y = by + int(bh * 0.55)
    draw.line((nose_x, eye_y + 5, nose_x - 5, nose_y), fill=(180, 140, 120), width=3)
    draw.line((nose_x - 5, nose_y, nose_x + 5, nose_y), fill=(180, 140, 120), width=3)

    # Mouth
    mouth_y = by + int(bh * 0.75)
    mouth_w = int(bw * 0.30)
    draw.line((nose_x - mouth_w, mouth_y, nose_x + mouth_w, mouth_y), fill=(160, 60, 60), width=4)

    # Add subtle texture so sharpness and contrast are realistic
    rng = np.random.RandomState(42)
    noise = rng.normal(0, 12, (size[1], size[0], 3))
    arr = np.clip(np.asarray(img).astype(np.float32) + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    if blur:
        arr = np.asarray(img).astype(np.float32)
        # Uniform flat image has 0 sharpness
        img = Image.fromarray((arr * 0.1).astype(np.uint8))

    return img


class StandinEngine:
    """Mock engine returning calibrated face detections for tests without loading onnx."""
    def __init__(self, detections: list[Detection] | None = None):
        self.detections = detections or []

    def detect(self, img: Image.Image) -> list[Detection]:
        if self.detections:
            return self.detections
        w, h = img.size
        bw, bh = int(w * 0.5), int(h * 0.5)
        bx, by = int(w * 0.25), int(h * 0.25)
        reye = (bx + bw * 0.32, by + bh * 0.35)
        leye = (bx + bw * 0.68, by + bh * 0.35)
        nose = (bx + bw * 0.50, by + bh * 0.55)
        rmouth = (bx + bw * 0.35, by + bh * 0.75)
        lmouth = (bx + bw * 0.65, by + bh * 0.75)
        return [
            Detection(
                box=(float(bx), float(by), float(bw), float(bh)),
                points=(reye, leye, nose, rmouth, lmouth),
                score=0.95,
            )
        ]


def test_avatar_check_size_too_small():
    img = _make_test_face_image((200, 200), face_box=(20, 20, 120, 120))
    engine = StandinEngine([
        Detection(box=(20.0, 20.0, 120.0, 120.0),
                  points=((58.0, 62.0), (82.0, 62.0), (70.0, 86.0), (60.0, 110.0), (80.0, 110.0)), score=0.95)
    ])
    res = check.check_photo(img, engine=engine)
    assert not res.ok
    assert "too small" in res.reason

    # A detection without its points can't be placed: it counts as no face, never as a guess.
    pointless = StandinEngine([Detection(box=(20.0, 20.0, 120.0, 120.0), points=(), score=0.95)])
    assert "couldn't find a face" in check.check_photo(img, engine=pointless).reason


def test_avatar_check_yaw_sideways():
    img = _make_test_face_image()
    # Asymmetric nose to eyes (large yaw)
    engine = StandinEngine([
        Detection(
            box=(150.0, 150.0, 300.0, 300.0),
            points=((200.0, 240.0), (380.0, 240.0), (370.0, 290.0), (220.0, 360.0), (360.0, 360.0)),
            score=0.95,
        )
    ])
    res = check.check_photo(img, engine=engine)
    assert not res.ok
    assert "turned sideways" in res.reason


def test_avatar_check_good_photo():
    img = _make_test_face_image()
    engine = StandinEngine()
    res = check.check_photo(img, engine=engine)
    assert res.ok
    assert "Looking good" in res.reason


def test_avatar_prepare_and_store(tmp_path: Path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir(parents=True)
    import mary.config
    monkeypatch.setattr(mary.config, "user_dir", lambda: user_root)
    monkeypatch.setattr(store, "user_dir", lambda: user_root)

    img = _make_test_face_image()
    engine = StandinEngine()

    face_dir = prepare.prepare_face(img, "test_persona", photo_id=42, person_id=7, engine=engine)
    assert face_dir.exists()
    assert (face_dir / "manifest.json").exists()
    m0 = json.loads((face_dir / "manifest.json").read_text(encoding="utf-8"))
    names = [p["base"] for p in m0["poses"]]
    names += [e["file"] for p in m0["poses"] for group in ("mouth", "eyes") for e in p[group].values()]
    assert [p["name"] for p in m0["poses"]] == ["center", "left", "right", "up", "down"]
    assert all((face_dir / n).exists() for n in names) and len(set(names)) == len(names)
    assert all(set(p["mouth"]) == set("BCDEFGH") and set(p["eyes"]) == {"half", "closed"} for p in m0["poses"])

    # Store validation and methods
    m = store.manifest("test_persona")
    assert m is not None
    assert store.available("test_persona")
    # Newly installed face arrives switched off until preview/activation
    assert not store.enabled("test_persona")

    store.set_enabled("test_persona", True)
    assert store.enabled("test_persona")
    store.set_enabled("test_persona", False)
    assert not store.enabled("test_persona")

    # Person-to-face lookup
    assert store.made_from([7]) == ["test_persona"]
    assert store.made_from([99]) == []

    # Pack and unpack
    archive_bytes = store.pack("test_persona")
    assert len(archive_bytes) > 1000

    # Erase face
    store.erase("test_persona")
    assert not store.available("test_persona")

    # Reinstall via unpack
    store.unpack("test_persona", archive_bytes)
    assert store.available("test_persona")


def test_director_lip_sync_follows_each_sentence_where_it_plays():
    """A reply's sentences are made faster than they're spoken: each one's mouth movement must start where it actually
    plays, after the ones before it, not when it was made, and a barge-in closes the mouth at once."""
    import time

    from mary.avatar.director import AvatarDirector

    T = time.monotonic() + 50     # the speaker's clock: a little ahead, as a queued reply is
    sr = 24000
    t = np.arange(sr) / sr
    voiced = (np.sin(2 * np.pi * 180 * t) * np.clip(np.sin(2 * np.pi * 4 * t), 0, 1)).astype(np.float32)
    d = AvatarDirector("x")
    d.set_state("speaking")
    # Two 1-second sentences arrive together; with no start time the second queues behind the first.
    d.feed_audio(voiced, sr, starts_at=T)
    d.feed_audio(voiced, sr)
    assert d._segments[1][0] == d._segments[0][1]

    d2 = AvatarDirector("x")
    d2.feed_audio(voiced, sr, starts_at=T)
    d2.feed_audio(voiced, sr, starts_at=T + 1)
    moving = lambda a, b: {d2.tick(T + i / 100).mouth for i in range(a, b)}
    assert moving(0, 100) - {"X"}            # the first sentence moves the mouth
    assert moving(100, 200) - {"X"}          # and so does the second, a full second later (it used to be dropped)
    assert moving(205, 300) == {"X"}         # quiet after the reply
    assert d2.tick(T - 1).mouth == "X"        # and before it starts
    d2.stop_speech()
    assert {d2.tick(T + i / 100).mouth for i in range(0, 200)} == {"X"}


def test_face_signature_changes_on_every_kind_of_change(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "user_dir", lambda: tmp_path)
    before = store.signature("p")
    root = store.folder("p")
    root.mkdir(parents=True)
    (root / store.MANIFEST).write_text("{}", encoding="utf-8")
    made = store.signature("p")
    assert made != before
    (root / store.STATE).write_text('{"enabled": true}', encoding="utf-8")
    switched = store.signature("p")
    assert switched != made
    store.erase("p")
    assert store.signature("p") == before


def _dotted_face(roll_deg: float):
    """A face-like picture with coloured dots on its 5 points, turned by `roll_deg`; the engine reports exactly those
    points, so wherever MARY carries the points the dots must be under them."""
    img = _make_test_face_image(size=(700, 700), face_box=(170, 150, 360, 400))
    d = ImageDraw.Draw(img)
    pts = [(290, 290), (410, 290), (350, 360), (305, 450), (395, 450)]
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)]
    for (x, y), c in zip(pts, colours):
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=c)
    turned = img.rotate(roll_deg, center=(350, 370), resample=Image.Resampling.BICUBIC, fillcolor=(220, 210, 200))
    import math

    a = math.radians(roll_deg)          # PIL turns counter-clockwise as seen (y down)
    def turn(p):
        dx, dy = p[0] - 350, p[1] - 370
        return (350 + dx * math.cos(a) + dy * math.sin(a), 370 - dx * math.sin(a) + dy * math.cos(a))
    moved = [turn(p) for p in pts]
    det = Detection(box=(170.0, 150.0, 360.0, 400.0), points=tuple(moved), score=0.95)
    return turned, StandinEngine([det]), colours


@pytest.mark.parametrize("roll", [-12.0, -4.0, 0.0, 3.0, 15.0])
def test_levelling_moves_the_picture_and_its_points_together(roll):
    """A tilted head is levelled before cropping; the picture must turn the same way as its points (it used to turn the
    other way, leaving each eye's point beside the eye, so blinks and mouths landed on the wrong features)."""
    img, engine, colours = _dotted_face(roll)
    lm = landmarks.extract_landmarks(img, engine=engine)[0]
    base, aligned = prepare._crop_and_align_face(img, lm)
    arr = np.asarray(base.convert("RGB"), dtype=np.int32)
    for pt, colour in zip((aligned.right_eye, aligned.left_eye, aligned.nose, aligned.mouth_right, aligned.mouth_left),
                          colours):
        x, y = int(round(pt[0])), int(round(pt[1]))
        assert abs(aligned.right_eye[1] - aligned.left_eye[1]) < 0.5
        got = arr[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3).mean(axis=0)
        assert np.abs(got - colour).max() < 70, (roll, colour, got)


def test_without_the_face_models_nothing_is_guessed(monkeypatch):
    monkeypatch.setattr(landmarks, "default_engine", lambda: None)
    with pytest.raises(landmarks.NoFaceModels):
        landmarks.extract_landmarks(_make_test_face_image())
    res = check.check_photo(_make_test_face_image())
    assert not res.ok and res.landmarks is None and "People page" in res.reason


def test_mouth_shapes_move_the_lips_and_jaw_only_and_line_up_in_every_pose(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "user_dir", lambda: tmp_path)
    img = _make_test_face_image()
    face_dir = prepare.prepare_face(img, "p", engine=StandinEngine())
    m = json.loads((face_dir / "manifest.json").read_text(encoding="utf-8"))
    load = lambda n: Image.open(face_dir / n).convert("RGBA")

    def frame(pose, group, shape):
        p = next(q for q in m["poses"] if q["name"] == pose)
        b = load(p["base"])
        e = p[group][shape]
        b.alpha_composite(load(e["file"]), tuple(e["rect"][:2]))
        return np.asarray(b, dtype=np.int32), e["rect"]

    center = np.asarray(load("pose_center.webp"), dtype=np.int32)
    opened = {}
    for shape in "BCDEFGH":
        arr, (x, y, w, h) = frame("center", "mouth", shape)
        changed = np.abs(arr - center)[..., :3].max(axis=-1) > 12
        assert changed.any()
        ys, xs = np.nonzero(changed)
        assert x <= xs.min() and xs.max() < x + w and y <= ys.min() and ys.max() < y + h   # nothing outside its region
        opened[shape] = changed.sum()
    assert opened["D"] > opened["C"] > opened["B"]          # wider shapes open further
    # A blink changes the eyes and nothing below them.
    arr, (x, y, w, h) = frame("center", "eyes", "closed")
    ys, _ = np.nonzero(np.abs(arr - center)[..., :3].max(axis=-1) > 12)
    assert len(ys) and ys.max() < y + h
    # In every pose, its own patch sits on its own face: the pose with a shape equals that shape's face, posed.
    for pose in ("left", "right", "up", "down"):
        arr, (x, y, w, h) = frame(pose, "mouth", "D")
        ref, _ = frame("center", "mouth", "D")
        posed = np.asarray(prepare._posed(Image.fromarray(ref.astype(np.uint8), "RGBA"), pose), dtype=np.int32)
        inner = (slice(y + 8, y + h - 8), slice(x + 8, x + w - 8))
        assert np.abs(arr[inner][..., :3] - posed[inner][..., :3]).mean() < 4, pose


def _voice(f0: float, formants: list[float], seconds: float, sr: int = 24000) -> np.ndarray:
    """A made-up vowel: a pitched buzz shaped by formant peaks (lower formants = a darker, rounder vowel)."""
    t = np.arange(int(seconds * sr)) / sr
    out = np.zeros_like(t)
    for h in range(1, int(4000 / f0)):
        f = h * f0
        gain = sum(np.exp(-((f - fm) / 120.0) ** 2) for fm in formants) + 0.02
        out += gain * np.sin(2 * np.pi * f * t)
    return (out / np.abs(out).max()).astype(np.float32)


def test_mouth_shapes_follow_the_sentence_not_fixed_levels():
    """Shapes are judged against the sentence itself: the same speech quieter gives the same shapes; its loud parts
    open the mouth and its quiet parts barely do; dark vowels round the lips; hiss is a consonant; silence is rest."""
    from mary.avatar.visemes import analyze_visemes

    sr = 24000
    open_vowel = _voice(180, [800, 1200], 0.4)          # AH
    round_vowel = _voice(180, [300, 700], 0.4)          # OO
    hiss = np.random.RandomState(1).normal(0, 1, int(0.25 * sr)).astype(np.float32)
    hiss = np.convolve(hiss, [1, -1], mode="same").astype(np.float32)   # bright, like S
    gap = np.zeros(int(0.2 * sr), np.float32)
    speech = np.concatenate([gap, 0.5 * open_vowel, gap, 0.1 * open_vowel, gap, 0.5 * round_vowel, gap, 0.2 * hiss, gap])

    def shapes(audio):
        return [s for _, s in analyze_visemes(audio, sr)]

    loud = shapes(speech)
    assert shapes(speech * 0.25) == loud                  # a quieter voice moves the mouth the same way
    seg = lambda start, dur: loud[int(start / 0.025) + 2:int((start + dur) / 0.025) - 2]
    assert set(seg(0.2, 0.4)) <= {"C", "D"} and "D" in seg(0.2, 0.4)            # loud open vowel: open wide
    assert set(seg(0.8, 0.4)) <= {"B", "C"}                                       # same vowel, soft: barely open
    assert set(seg(1.4, 0.4)) <= {"E", "F"}                                       # dark vowel: rounded lips
    assert set(seg(2.0, 0.25)) <= {"B", "G"}                                      # hiss: teeth together
    assert set(seg(0.0, 0.2)) == {"X"} and loud[-1] == "X"                        # silence: rest
    # no one-frame flicker anywhere
    runs = [len(list(g)) for _, g in __import__("itertools").groupby(loud)]
    assert min(runs[1:-1]) >= 2
