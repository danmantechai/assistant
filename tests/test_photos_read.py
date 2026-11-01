"""Reading one picture: formats, what the camera wrote, orientation, fingerprints, and files that can't be read.
Only invented pictures (mary/photos/synth.py): never a real photo."""
import io
import itertools
from datetime import datetime

import pytest
from PIL import Image

from mary.photos import read, synth


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("photos")
    truth = synth.hard_set(root)
    facts = {}
    for t in truth:
        try:
            facts[t.rel] = read.read_file(root / t.rel)
        except read.Unreadable as e:
            facts[t.rel] = e
    return root, truth, facts


def test_what_the_camera_wrote_is_read_exactly(library):
    _, _, f = library
    one = f["Camera/IMG_0001.jpg"]
    assert one.taken == datetime(2023, 12, 25, 10, 15) and (one.lat, one.lon) == pytest.approx((5.1053, -1.2466), abs=1e-4)
    assert f["Camera/IMG_0003.webp"].taken == datetime(2021, 5, 1, 9, 0) and f["Camera/IMG_0003.webp"].lat == pytest.approx(51.5072, abs=1e-4)
    assert f["Messenger/IMG-20211102-WA0007.jpg"].taken is None and f["Messenger/IMG-20211102-WA0007.jpg"].lat is None


def test_a_picture_is_measured_as_it_is_seen_not_as_it_is_stored(library):
    _, _, f = library
    assert (f["Camera/IMG_0002.jpg"].width, f["Camera/IMG_0002.jpg"].height) == (640, 480)   # stored on its side
    assert read.hamming(int(f["Camera/IMG_0002.jpg"].dhash, 16), int(f["Shared/img2_turned.jpg"].dhash, 16)) == 0


def test_every_format_decodes_and_makes_a_small_jpeg_thumbnail(library):
    _, truth, f = library
    seen = set()
    for t in truth:
        got = f[t.rel]
        if isinstance(got, read.Unreadable):
            continue
        seen.add(t.rel.rsplit(".", 1)[1])
        thumb = Image.open(io.BytesIO(got.thumb))
        assert thumb.format == "JPEG" and max(thumb.size) <= read.THUMB_PX
    assert {"jpg", "jpeg", "png", "webp"} <= seen


@pytest.mark.skipif(not read.windows_can_read_heic(), reason="Windows has no HEIF decoder here")
def test_heic_is_read_through_windows(library):
    _, _, f = library
    heic = f["Trip June 2021/IMG_0100.heic"]
    assert (heic.width, heic.height) == (640, 480) and len(heic.thumb) > 500
    assert heic.taken is None    # Windows' encoder can't write camera notes; the date comes from the folder


def test_copies_keep_their_fingerprint_and_different_pictures_do_not(library):
    _, truth, f = library
    h = {t.rel: int(f[t.rel].dhash, 16) for t in truth if not isinstance(f[t.rel], read.Unreadable)}
    for t in truth:
        if t.same_as and t.rel in h:
            assert read.hamming(h[t.rel], h[t.same_as]) <= 3, (t.rel, t.same_as)
    # the files that aren't copies of one another are all far apart (a burst's frames, a copy, are the exceptions)
    originals = [t.rel for t in truth if not t.same_as and t.rel in h and t.rel not in ("Camera/IMG_0005.jpg",)]
    gaps = sorted(read.hamming(h[a], h[b]) for a, b in itertools.combinations(originals, 2))
    assert gaps[0] >= 10, gaps[:5]


def test_a_burst_has_one_fingerprint_so_only_the_time_tells_the_frames_apart(library):
    _, _, f = library
    a, b = f["Camera/IMG_0004.jpg"], f["Camera/IMG_0005.jpg"]
    assert read.hamming(int(a.dhash, 16), int(b.dhash, 16)) <= 3
    assert a.taken != b.taken and a.sha != b.sha


def test_the_files_bytes_are_hashed_too(library):
    _, _, f = library
    assert f["Camera/IMG_0001.jpg"].sha == f["Backup/IMG_0001 copy.jpg"].sha      # the same bytes, another name
    assert f["Camera/IMG_0001.jpg"].sha != f["Shared/img1_small.jpg"].sha         # the same picture, other bytes
    assert len(f["Camera/IMG_0001.jpg"].sha) == 64


def test_a_file_that_cant_be_read_says_why_without_naming_itself(library):
    _, _, f = library
    assert str(f["Camera/broken.jpg"]) == "not a picture MARY can read"
    assert str(f["Camera/empty.jpg"]) == "empty"
    assert str(f["Camera/cut_short.jpg"]) == "damaged or cut short"
    for name in ("broken", "cut_short"):
        assert name not in str(f[f"Camera/{name}.jpg"])


def test_a_file_that_is_too_big_is_left_alone(tmp_path, monkeypatch):
    synth.save(tmp_path / "wide.png", synth.picture(1, (400, 300)))
    monkeypatch.setattr(read, "MAX_PIXELS", 1000)
    with pytest.raises(read.Unreadable, match="too large"):
        read.read_file(tmp_path / "wide.png")


def test_a_missing_file_is_just_unreadable(tmp_path):
    with pytest.raises(read.Unreadable):
        read.read_file(tmp_path / "gone.jpg")


def test_camera_dates_and_positions_that_cant_be_true_are_ignored():
    assert read.exif_time("2023:12:25 10:15:00") == datetime(2023, 12, 25, 10, 15)
    assert read.exif_time("2023:12:25 10:15:00", "42").microsecond == 420000
    for bad in ("0000:00:00 00:00:00", "", None, "yesterday", "2023:13:40 10:15:00"):
        assert read.exif_time(bad) is None
    assert read.position(0.0, 0.0) == (None, None)          # a camera's "no fix"
    assert read.position(95.0, 10.0) == (None, None) and read.position(10.0, 200.0) == (None, None)
    assert read.position(5.1053, -1.2466) == (5.1053, -1.2466)


def test_a_transparent_picture_goes_onto_white(tmp_path):
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    img.save(tmp_path / "clear.png")
    d = read.decode((tmp_path / "clear.png").read_bytes(), ".png")
    assert d.image.getpixel((10, 10)) == (255, 255, 255)


def test_reading_is_quick(library):
    import time

    root, truth, _ = library
    paths = [root / t.rel for t in truth if t.state == "ok" and not t.rel.endswith(".heic")]
    start = time.perf_counter()
    for p in paths:
        read.read_file(p)
    per = (time.perf_counter() - start) / len(paths)
    assert per < 0.1, f"{per * 1000:.0f} ms per small file"
