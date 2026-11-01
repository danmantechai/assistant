"""When a photo was taken, and how MARY knows: scored on the made-up library, then on names and albums it wasn't written for."""
from datetime import date, datetime
from pathlib import Path

import pytest

from mary.photos import dates, read, synth

TODAY = date(2026, 11, 4)


def d(*a):
    return datetime(*a)


# ---------------------------------------------------------------- the made-up library, with its answers


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("photos")
    return root, synth.hard_set(root)


def decided(root: Path, t: synth.Truth):
    path = root / t.rel
    parts = Path(t.rel).parts[:-1]
    try:
        taken = read.read_file(path).taken
    except read.Unreadable:
        taken = None
    return dates.decide(taken, path.name, list(parts), path.stat().st_mtime, TODAY)


def test_every_date_in_the_library_is_right_and_every_guess_is_marked(library):
    root, truth = library
    wrong, scored = [], 0
    for t in truth:
        if t.state != "ok":
            continue
        got = decided(root, t)
        scored += 1
        mine = (got.start, got.end, got.precision, got.source, got.guess, got.flag)
        want = (t.start, t.end, t.precision, t.source, t.guess, t.flag)
        if mine != want:
            wrong.append((t.rel, mine, want))
    assert not wrong, wrong
    assert scored >= 20


def test_by_source_exact_dates_are_exact_and_guesses_are_never_certain(library):
    root, truth = library
    by_source: dict[str, list[bool]] = {}
    for t in truth:
        if t.state != "ok" or not t.precision:
            continue
        got = decided(root, t)
        by_source.setdefault(got.source, []).append(got.start == t.start)
        if got.source in ("folder", "file"):
            assert got.guess, t.rel
        else:
            assert not got.guess, t.rel
    assert all(all(ok) for ok in by_source.values()) and {"exif", "filename", "folder", "file"} <= set(by_source)


def test_a_print_photographed_later_is_flagged_not_trusted_either_way(library):
    root, truth = library
    got = decided(root, next(t for t in truth if t.rel == "Christmas 2019/d.jpg"))
    assert got.source == "exif" and got.start.year == 2024 and got.flag == "disagree"
    agrees = decided(root, next(t for t in truth if t.rel == "Christmas 2019/c.jpg"))
    assert agrees.flag == ""


# ---------------------------------------------------------------- file names (phones, messengers, screenshots)


@pytest.mark.parametrize("name,expected,precision", [
    ("IMG_20231225_101500.jpg", d(2023, 12, 25, 10, 15), "second"),
    ("PXL_20230601_120500123.jpg", d(2023, 6, 1, 12, 5), "second"),
    ("IMG-20211102-WA0007.jpg", d(2021, 11, 2), "day"),
    ("WhatsApp Image 2022-03-09 at 14.22.31.jpeg", d(2022, 3, 9, 14, 22, 31), "second"),
    ("Screenshot_20240115-093012.png", d(2024, 1, 15, 9, 30, 12), "second"),
    ("Screenshot 2024-01-15 093012.png", d(2024, 1, 15, 9, 30, 12), "second"),
    ("VID_20200229_081522.jpg", d(2020, 2, 29, 8, 15, 22), "second"),
    ("20180704_171205.jpg", d(2018, 7, 4, 17, 12, 5), "second"),
    ("2019-08-14 beach.jpg", d(2019, 8, 14), "day"),
    ("DSC_2015-03-01.JPG", d(2015, 3, 1), "day"),
])
def test_a_date_in_a_file_name(name, expected, precision):
    got = dates.from_filename(name, TODAY)
    assert (got.start, got.precision, got.source) == (expected, precision, "filename") and not got.guess


@pytest.mark.parametrize("name", [
    "IMG_99999999.jpg", "IMG_0001.jpg", "12345678.jpg", "photo 3.jpg", "+233201234567.jpg", "IMG_20231345_101500.jpg",
    "IMG_20230230.jpg", "IMG_20991231.jpg", "IMG_18500101.jpg", "DSC_20000101.jpg", "holiday.jpg", "IMG_2019_0001.jpg"])
def test_a_number_that_is_not_a_date_is_not_read_as_one(name):
    assert not dates.from_filename(name, TODAY).known, name


# ---------------------------------------------------------------- albums


@pytest.mark.parametrize("folder,start,precision", [
    ("Christmas 2019", d(2019, 12, 1), "month"),
    ("December 2019", d(2019, 12, 1), "month"),
    ("Dec 2019 trip", d(2019, 12, 1), "month"),
    ("2019-12 Lisbon", d(2019, 12, 1), "month"),
    ("06.2021", d(2021, 6, 1), "month"),
    ("Trip June 2021", d(2021, 6, 1), "month"),
    ("25 December 2019", d(2019, 12, 25), "day"),
    ("2019-12-25", d(2019, 12, 25), "day"),
    ("Summer 2015", d(2015, 1, 1), "year"),
    ("1998 wedding", d(1998, 1, 1), "year"),
    ("Scans 1990s", d(1990, 1, 1), "decade"),
    ("The 1980s", d(1980, 1, 1), "decade"),
])
def test_what_an_album_s_name_says(folder, start, precision):
    got = dates.from_folder_name(folder, TODAY)
    assert (got.start, got.precision, got.source) == (start, precision, "folder") and got.guess


@pytest.mark.parametrize("folder", ["Camera", "Pictures", "Misc", "Holiday", "Album 3", "Vacation", "2019 2021", "May",
                                    "Backup 12345", "1850 census", "Saved Pictures", "2035"])
def test_a_name_that_names_no_time_says_nothing(folder):
    assert not dates.from_folder_name(folder, TODAY).known, folder


def test_the_nearest_folder_that_names_a_time_wins():
    got = dates.from_folders(["Family", "1998", "Christmas 1998"], TODAY)
    assert (got.start, got.precision) == (d(1998, 12, 1), "month")
    assert dates.from_folders(["Family", "1998", "Cousins"], TODAY).start == d(1998, 1, 1)
    assert not dates.from_folders(["Family", "Cousins"], TODAY).known


# ---------------------------------------------------------------- putting them together


def test_the_camera_comes_before_the_name_before_the_album_before_the_file():
    mtime = datetime(2020, 5, 5).timestamp()
    both = dates.decide(d(2023, 12, 25, 10, 15), "IMG_20231225_101500.jpg", ["Christmas 2023"], mtime, TODAY)
    assert both.source == "exif" and both.flag == ""
    name_only = dates.decide(None, "IMG_20231225_101500.jpg", ["Christmas 2019"], mtime, TODAY)
    assert name_only.source == "filename" and name_only.flag == "disagree"
    album_only = dates.decide(None, "x.jpg", ["Christmas 2019"], mtime, TODAY)
    assert album_only.source == "folder" and album_only.guess
    file_only = dates.decide(None, "x.jpg", ["Camera"], mtime, TODAY)
    assert (file_only.source, file_only.start) == ("file", d(2020, 5, 5)) and file_only.guess
    nothing = dates.decide(None, "x.jpg", ["Camera"], None, TODAY)
    assert not nothing.known and nothing.source == "none" and not nothing.guess


def test_a_camera_that_never_had_its_clock_set_is_ignored():
    for when in (d(2000, 1, 1), d(1970, 1, 1), d(1980, 1, 1), d(1989, 6, 1), d(2030, 1, 1)):
        assert not dates.from_exif(when, TODAY).known, when
    assert dates.from_exif(d(2000, 1, 1, 0, 0, 1), TODAY).known       # a real second past midnight is a real time


def test_the_name_and_the_camera_disagreeing_is_flagged():
    got = dates.decide(d(2023, 12, 25, 10, 15), "IMG-20211102-WA0007.jpg", [], None, TODAY)
    assert got.source == "exif" and got.flag == "disagree"
    close = dates.decide(d(2021, 11, 2, 23, 59), "IMG-20211103-WA0007.jpg", [], None, TODAY)
    assert close.flag == ""                                             # a day apart is a time zone, not a quarrel


def test_what_you_set_for_a_photo_always_wins():
    mine = dates.person_date(1995, 6)
    got = dates.decide(d(2023, 12, 25, 10, 15), "IMG_20231225.jpg", ["Christmas 2023"], None, TODAY, person=mine)
    assert got.source == "person" and got.start == d(1995, 6, 1) and got.precision == "month" and not got.guess


def test_what_you_set_for_an_album_reaches_photos_that_only_had_a_guess_unless_you_say_all():
    album = dates.person_date(1985)
    guessed = dates.decide(None, "scan1.jpg", ["Scans"], datetime(2021, 1, 1).timestamp(), TODAY, album=album)
    assert guessed.source == "album" and guessed.start.year == 1985 and not guessed.guess
    stated = dates.decide(d(2024, 3, 1, 11), "scan2.jpg", ["Scans"], None, TODAY, album=album)
    assert stated.source == "exif"                                      # a camera's date is not overruled by default…
    everything = dates.decide(d(2024, 3, 1, 11), "scan2.jpg", ["Scans"], None, TODAY, album=album, album_scope="all")
    assert everything.source == "album" and everything.start.year == 1985   # …but a print photographed today can be


def test_a_person_date_is_as_precise_as_they_were():
    assert dates.person_date(1995).precision == "year"
    assert dates.person_date(1995, 6).precision == "month"
    assert dates.person_date(1995, 6, 4).precision == "day"
    assert dates.person_date(1995, decade=True).start.year == 1990
    with pytest.raises(ValueError):
        dates.person_date(1995, 2, 30)


def test_the_period_ends_where_the_next_begins():
    assert dates.make(d(2019, 12, 1), "month", "folder").end == d(2020, 1, 1)
    assert dates.make(d(2019, 2, 1), "month", "folder").end == d(2019, 3, 1)
    assert dates.make(d(1993, 1, 1), "decade", "folder").end == d(2000, 1, 1)
    assert dates.make(d(2023, 12, 31, 23, 59, 59), "second", "exif").end == d(2024, 1, 1)


def test_dates_in_words():
    assert dates.label(dates.make(d(2023, 12, 25, 10, 15), "second", "exif")) == "25 December 2023, 10:15"
    assert dates.label(dates.make(d(2023, 12, 25), "day", "filename")) == "25 December 2023"
    assert dates.label(dates.make(d(2019, 12, 1), "month", "folder")) == "December 2019"
    assert dates.label(dates.make(d(2015, 1, 1), "year", "folder")) == "2015"
    assert dates.label(dates.make(d(1990, 1, 1), "decade", "folder")) == "the 1990s"
    assert dates.label(dates.NONE) == "no date"
