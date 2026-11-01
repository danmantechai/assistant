"""Copies (nothing counted twice, nothing wrongly merged), places named offline, and words read in pictures."""
import itertools
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from mary.photos import copies, places, read, synth, text


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("photos")
    truth = synth.hard_set(root)
    looks, facts = {}, {}
    for t in truth:
        try:
            f = read.read_file(root / t.rel)
        except read.Unreadable:
            continue
        facts[t.rel] = f
        looks[t.rel] = copies.Look(f.sha, int(f.dhash, 16), f.width, f.height, f.taken)
    return root, truth, looks


# ---------------------------------------------------------------- copies


def test_the_library_s_copies_are_found_and_nothing_else_is_merged(library):
    _, truth, looks = library
    group = {}
    for t in truth:
        root_of = t.same_as or t.rel
        group[t.rel] = root_of
    wrong = []
    for a, b in itertools.combinations(looks, 2):
        should = group[a] == group[b]
        if copies.same_photo(looks[a], looks[b]) != should:
            wrong.append((a, b, should))
    assert not wrong, wrong


def test_a_burst_s_frames_are_alike_but_two_photos(library):
    _, _, looks = library
    a, b = looks["Camera/IMG_0004.jpg"], looks["Camera/IMG_0005.jpg"]
    assert copies.bits(a.dhash, b.dhash) <= copies.SAME_BITS and a.sha != b.sha
    assert not copies.same_photo(a, b)                 # the camera's own times (a second apart) tell them apart
    stripped = copies.Look(b.sha, b.dhash, b.width, b.height, None)
    assert copies.same_photo(a, stripped)              # with no time to go by, nearly the same picture is the same photo


def test_exact_copies_need_no_picture_at_all():
    a = copies.Look("abc", 0, 10, 10)
    assert copies.same_photo(a, copies.Look("abc", 2**63, 400, 300))
    assert not copies.same_photo(a, copies.Look("def", 2**63, 400, 300))


def test_another_shape_is_another_photo_even_when_the_hash_matches():
    a = copies.Look("a", 0b1011, 400, 300)
    assert not copies.same_photo(a, copies.Look("b", 0b1011, 300, 400))       # turned on its side: a different picture
    assert not copies.same_photo(a, copies.Look("b", 0b1011, 400, 360))       # cropped
    assert copies.same_photo(a, copies.Look("b", 0b1011, 404, 300))           # within 2% (a rounding of the size)


def test_alike_but_not_the_same_is_offered_never_merged():
    a = copies.Look("a", 0, 400, 300)
    near = copies.Look("b", (1 << 8) - 1, 400, 300)          # 8 bits apart
    assert not copies.same_photo(a, near) and copies.might_be(a, near)
    far = copies.Look("c", (1 << 20) - 1, 400, 300)
    assert not copies.might_be(a, far)
    assert not copies.might_be(a, a)                          # the same photo is not "might be"


def test_the_hash_index_answers_in_bits():
    index = copies.HashIndex([(1, 0), (2, 0b111), (3, (1 << 40) - 1), (4, 2**64 - 1)])
    assert index.within(0, 3) == [(1, 0), (2, 3)]
    assert index.within(0, 0) == [(1, 0)]
    assert index.within(2**64 - 1, 3) == [(4, 0)]            # all 64 bits set survives the array's unsigned type
    index.remove(2)
    index.add(9, 1)
    assert index.within(0, 3) == [(1, 0), (9, 1)]
    index.update(9, 0b1111)
    assert index.within(0, 3) == [(1, 0)] and copies.HashIndex().within(5, 10) == []


# ---------------------------------------------------------------- places

PLACES = [
    ("Harbourton", "Coastal Region", "Ghanaland", 5.1053, -1.2466, 90000),
    ("Smallhamlet", "Coastal Region", "Ghanaland", 5.1500, -1.2400, 600),         # 5 km away, tiny
    ("Northfield", "Greater North", "Britannia", 51.5072, -0.1276, 8_000_000),
    ("Lonely Village", "Far Region", "Atlantis", -33.0, 18.0, 2500),
    ("Eastern Isle", "Dateline", "Fiji-ish", -17.0, 179.95, 30000),
    ("Western Isle", "Dateline", "Fiji-ish", -17.0, -179.95, 28000),
]


@pytest.fixture
def gazetteer():
    return places.Gazetteer(PLACES)


def test_a_photo_is_named_for_the_town_not_the_hamlet_it_is_nearest_to(gazetteer):
    got = gazetteer.nearest(5.1495, -1.2402)              # beside the hamlet, 5 km from the town
    assert got.name == "Harbourton" and got.country == "Ghanaland" and got.km < 10
    assert got.label() == "Harbourton, Coastal Region, Ghanaland"


def test_with_no_town_nearby_the_nearest_place_is_named(gazetteer):
    got = gazetteer.nearest(-33.1, 18.1)
    assert got.name == "Lonely Village" and 10 < got.km < 20


def test_the_sea_has_no_name(gazetteer):
    assert gazetteer.nearest(-33.0, 20.0) is None         # 180 km from anywhere
    assert gazetteer.nearest(0.0, -30.0) is None


def test_names_work_across_the_date_line_and_at_the_pole():
    g = places.Gazetteer(PLACES + [("Polar Station", "", "Antarctica", -89.99, 12.0, 100)])
    assert g.nearest(-17.0, 179.99).name == "Eastern Isle"
    assert g.nearest(-17.0, -179.99).name == "Western Isle"
    assert g.nearest(-17.0, 180.0).name in ("Eastern Isle", "Western Isle")
    assert g.nearest(-89.995, -120.0).name == "Polar Station"      # the other side of the world in longitude, a few metres away


def test_no_list_means_no_names_and_no_error():
    g = places.Gazetteer()
    assert not g.available and g.nearest(5.0, -1.0) is None
    assert not places.Gazetteer.open(Path("nowhere") / "gazetteer.db").available


def test_the_list_is_built_from_geonames_files_and_read_back(tmp_path):
    cities = "\n".join([
        "1\tHarbourton\tHarbourton\t\t5.1053\t-1.2466\tP\tPPLA\tGH\t\t09\t\t\t\t90000\t\t10\tUTC\t2020-01-01",
        "2\tSmall Place\tSmall Place\t\t5.2\t-1.3\tP\tPPL\tGH\t\t09\t\t\t\t1200\t\t10\tUTC\t2020-01-01",
        "bad line"])
    countries = "#header\nGH\tGHA\t288\tGH\tGhanaland\tAccra\t238537"
    admin1 = "GH.09\tCoastal Region\tCoastal Region\t123"
    out = tmp_path / "places" / "gazetteer.db"
    assert places.build(cities, countries, admin1, out) == 2
    g = places.Gazetteer.open(out)
    assert g.available and g.nearest(5.1053, -1.2466).label() == "Harbourton, Coastal Region, Ghanaland"
    assert sqlite3.connect(out).execute("select count(*) from places").fetchone()[0] == 2


def test_the_list_can_be_fetched_when_asked_and_only_then(tmp_path, monkeypatch):
    from geonames_fake import fetch

    asked = []
    out = tmp_path / "places" / "gazetteer.db"
    n = places.download(out, lambda url: (asked.append(url), fetch(url))[1])
    assert n == 2 and out.exists() and (tmp_path / "places" / "ATTRIBUTION.txt").read_text().startswith("Place names: GeoNames")
    assert asked == [places.GEONAMES + name for name in places.DOWNLOAD_FILES]       # three files, from GeoNames, nothing else
    assert places.Gazetteer.open(out).nearest(5.1053, -1.2466).label() == "Harbourton, Coastal Region, Ghanaland"
    monkeypatch.setattr(places, "_fetch", lambda url: fetch(url))
    other = tmp_path / "again.db"
    assert places.download(other) == 2 and other.exists()                              # the default fetch is the module's


def test_a_failed_fetch_leaves_nothing_half_written(tmp_path):
    def broken(url):
        raise OSError("no internet")

    with pytest.raises(OSError):
        places.download(tmp_path / "places" / "gazetteer.db", broken)
    assert not (tmp_path / "places" / "gazetteer.db").exists()


REAL = Path(__file__).resolve().parent.parent / "data" / "places" / "gazetteer.db"


@pytest.mark.skipif(not REAL.exists(), reason="the real list of places hasn't been fetched on this PC")
@pytest.mark.parametrize("what,lat,lon,name", [      # well-known public places, and the name a person would give
    ("Eiffel Tower", 48.8584, 2.2945, "Paris"), ("Big Ben", 51.5007, -0.1246, "London"),
    ("Statue of Liberty", 40.6892, -74.0445, "New York City"), ("Sydney Opera House", -33.8568, 151.2153, "Sydney"),
    ("Cape Coast Castle", 5.1054, -1.2466, "Cape Coast"), ("Table Mountain", -33.9628, 18.4098, "Cape Town"),
    ("Taj Mahal", 27.1751, 78.0421, "Agra"), ("Christ the Redeemer", -22.9519, -43.2105, "Rio de Janeiro"),
    ("Red Square", 55.7539, 37.6208, "Moscow"), ("Colosseum", 41.8902, 12.4922, "Rome"),
    ("Versailles palace", 48.8049, 2.1204, "Versailles")])
def test_the_real_list_names_well_known_places_as_a_person_would(what, lat, lon, name):
    global _real
    try:
        _real
    except NameError:
        _real = places.Gazetteer.open(REAL)
    assert _real.nearest(lat, lon).name == name, what


@pytest.mark.skipif(not REAL.exists(), reason="the real list of places hasn't been fetched on this PC")
def test_the_real_list_has_no_name_for_the_open_sea_and_is_quick():
    import time

    g = places.Gazetteer.open(REAL)
    assert g.nearest(0.0, -30.0) is None and g.nearest(89.99, 0.0) is None
    start = time.perf_counter()
    for i in range(500):
        g.nearest(10 + i % 50, 5 + i % 90)
    assert (time.perf_counter() - start) / 500 < 0.005


def test_a_neighbourhood_is_never_the_name_and_size_beats_the_hamlet_next_door():
    rows = [("Old Town Section", "R", "C", 10.0, 10.0, 60000, "PPLX"),       # a neighbourhood right on the spot
            ("Bigcity", "R", "C", 10.05, 10.0, 3_000_000, "PPLA"),            # 5.5 km away
            ("Hamlet", "R", "C", 10.001, 10.0, 900, "PPL")]
    assert places.Gazetteer(rows).nearest(10.0, 10.0).name == "Bigcity"
    only = places.Gazetteer([rows[0]])
    assert only.nearest(10.0, 10.0).name == "Old Town Section"           # with nothing else, a neighbourhood is better than nothing
    close_small = places.Gazetteer([("Midtown", "R", "C", 10.0, 10.0, 30000, "PPL"), ("Bigcity", "R", "C", 10.2, 10.0, 3_000_000, "PPLA")])
    assert close_small.nearest(10.0, 10.0).name == "Midtown"             # 22 km away is too far to outweigh being there


def test_distances():
    assert places.km_between(0, 0, 0, 1) == pytest.approx(111.19, abs=0.1)
    assert places.km_between(5.1053, -1.2466, 5.1053, -1.2466) == 0


def test_every_site_in_the_made_up_library_is_named_by_its_own_town():
    g = places.Gazetteer(PLACES)
    assert g.nearest(*synth.SITES["harbourton"]).name == "Harbourton"
    assert g.nearest(*synth.SITES["northfield"]).name == "Northfield"


# ---------------------------------------------------------------- the words in a picture


def test_only_something_that_reads_like_words_is_kept():
    assert text.useful("HARBOUR CAFE open 8am - 6pm") == "HARBOUR CAFE open 8am - 6pm"
    assert text.useful("  Fresh   bread\nSOLD OUT ") == "Fresh bread SOLD OUT"
    for noise in ("", "ii l | 1 .", "xzq", "7 3", "ttt kkk"):
        assert text.useful(noise) == "", noise
    assert len(text.useful("word " * 1000)) <= text.LIMIT
    assert text.useful("Room 204") == "Room 204"


@pytest.mark.skipif(not text.available(), reason="Windows has no OCR language here")
def test_a_sign_in_a_picture_is_read(library):
    root, truth, _ = library
    got = text.read_text(root / "Camera/IMG_0006.jpg")
    assert "HARBOUR" in got.upper() and "CAFE" in got.upper()
    assert text.read_text(root / "Camera/IMG_0001.jpg") == ""          # shapes and colours: no words


@pytest.mark.skipif(not text.available(), reason="Windows has no OCR language here")
def test_reading_a_file_that_is_not_a_picture_raises_like_the_rest(library):
    root, _, _ = library
    with pytest.raises(read.Unreadable):
        text.read_text(root / "Camera/broken.jpg")


def test_without_an_ocr_language_nothing_is_read_and_nothing_breaks(monkeypatch, library):
    root, _, _ = library
    monkeypatch.setattr(text, "available", lambda: False)
    assert text.read_text(root / "Camera/IMG_0006.jpg") == ""
