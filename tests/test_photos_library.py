"""Photos in: the whole pipeline on the made-up library (invented pictures only), the API, and what must never happen to an
original, a removed photo, or a file that can't be read."""
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from mary.memory.store import MemoryStore
from mary.photos import dates, ingest, library, sources, synth, text
from mary.photos.places import Gazetteer

PLACES = [("Harbourton", "Coastal Region", "Ghanaland", 5.1053, -1.2466, 90000),
          ("Northfield", "Greater North", "Britannia", 51.5072, -0.1276, 8_000_000)]


def digests(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "Photos"
    truth = synth.hard_set(root)
    store = MemoryStore(tmp_path / "user" / "memory" / "memory.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"read_text": True}, Gazetteer(PLACES))
    ing = ingest.Ingest(lib)
    yield root, truth, lib, ing, tmp_path
    ing.stop()
    store.close()


def load(world, add=True):
    root, truth, lib, ing, _ = world
    folder = lib.add_folder(root) if add else None
    ing.scan()
    ing.drain()
    return folder


def by_rel(world) -> dict[str, dict]:
    """rel path -> the photo it belongs to."""
    root, truth, lib, _, _ = world
    out = {}
    for t in truth:
        row = lib._one("SELECT photo_id FROM photo_files WHERE path=?", (str(root / t.rel).replace("/", os.sep),))
        if row and row["photo_id"]:
            out[t.rel] = lib.get(row["photo_id"])
    return out


# ---------------------------------------------------------------- the whole library goes in

def test_every_file_is_found_read_or_set_aside_with_a_reason(world):
    root, truth, lib, ing, _ = world
    load(world)
    st = lib.status()
    ok = [t for t in truth if t.state == "ok"]
    assert st["found"] == len(truth) and st["reading"] == 0
    assert st["files"]["unreadable"] == 3 and st["files"]["tiny"] == 1 and st["files"]["ok"] == len(ok)
    groups = {t.same_as or t.rel for t in ok}
    assert st["photos"] == len(groups) and st["copies"] == len(ok) - len(groups)
    reasons = {r["reason"] for r in lib._all("SELECT reason FROM photo_files WHERE state<>'ok'")}
    assert reasons == {"not a picture MARY can read", "empty", "damaged or cut short", "too small to be a photo"}


def test_dates_are_right_for_every_photo_and_every_guess_is_marked(world):
    root, truth, lib, _, _ = world
    load(world)
    photos = by_rel(world)
    ledger = {t.rel: t for t in truth}
    wrong = []
    for rel, t in ledger.items():
        if t.state != "ok":
            continue
        want = ledger[t.same_as] if t.same_as else t         # a copy's photo has the original's date (the best source wins)
        got = photos[rel]
        mine = (got["taken"], got["precision"], got["source"], got["guess"], got["flag"])
        theirs = (want.start.isoformat(timespec="seconds"), want.precision, want.source, want.guess, want.flag)
        if mine != theirs:
            wrong.append((rel, mine, theirs))
    assert not wrong, wrong


def test_nothing_is_counted_twice_and_a_burst_stays_two_photos(world):
    load(world)
    p = by_rel(world)
    assert p["Camera/IMG_0001.jpg"]["id"] == p["Backup/IMG_0001 copy.jpg"]["id"] == p["Shared/img1_small.jpg"]["id"]
    assert p["Camera/IMG_0002.jpg"]["id"] == p["Shared/img2_turned.jpg"]["id"]
    assert p["Camera/IMG_0004.jpg"]["id"] != p["Camera/IMG_0005.jpg"]["id"]
    one = p["Camera/IMG_0001.jpg"]
    assert one["files"] == 3 and (one["width"], one["height"]) == (640, 480)      # the biggest file stands for the photo
    assert {f["folder"] for f in world[2].get(one["id"])["file_list"]} == {"Photos"}


def test_a_list_of_places_that_arrives_later_names_the_photos_that_were_waiting(world):
    root, _, lib, ing, _ = world
    lib.gazetteer = Gazetteer()                       # no list of places yet
    load(world)
    p = by_rel(world)
    assert p["Camera/IMG_0001.jpg"]["place"] == "" and p["Camera/IMG_0001.jpg"]["lat"] is not None
    lib.set_place([p["Camera/IMG_0003.webp"]["id"]], "Mum's flat")                         # one the person named already
    assert lib.use_gazetteer(Gazetteer(PLACES)) == 2                                        # IMG_0001 (and its copies) and the sign
    got = by_rel(world)
    assert got["Camera/IMG_0001.jpg"]["place"] == "Harbourton" and got["Camera/IMG_0006.jpg"]["place"] == "Harbourton"
    assert got["Camera/IMG_0003.webp"]["place"] == "Mum's flat" and got["Camera/IMG_0003.webp"]["place_source"] == "person"


def test_places_are_named_from_the_gps_and_kept_by_the_copies(world):
    load(world)
    p = by_rel(world)
    one = p["Camera/IMG_0001.jpg"]
    assert (one["place"], one["country"], one["place_source"]) == ("Harbourton", "Ghanaland", "gps")
    assert one["place_label"] == "Harbourton, Coastal Region, Ghanaland"
    assert p["Camera/IMG_0003.webp"]["place"] == "Northfield"
    assert p["Camera/IMG_0002.jpg"]["place"] == "" and p["Messenger/IMG-20211102-WA0007.jpg"]["place_source"] == ""


def test_the_words_in_a_picture_are_read_and_searchable(world):
    if not text.available():
        pytest.skip("Windows has no OCR language here")
    _, _, lib, _, _ = world
    load(world)
    sign = by_rel(world)["Camera/IMG_0006.jpg"]
    assert "HARBOUR" in sign["text"].upper() and sign["stage"] == 2
    assert [i["id"] for i in lib.list(q="harbour cafe")["items"]] == [sign["id"]]
    assert lib.list(q="harbo")["total"] >= 1                     # a word being typed
    assert lib.status()["text_left"] == 0


def test_with_text_switched_off_the_rest_still_goes_in_and_the_count_says_what_is_left(world):
    _, _, lib, ing, _ = world
    lib.settings["read_text"] = False
    load(world)
    st = lib.status()
    assert st["photos"] > 0 and st["text_left"] == st["photos"] and "text" not in ing.average_ms()


def test_a_second_scan_adds_nothing_and_a_new_photo_is_picked_up(world):
    root, truth, lib, ing, _ = world
    load(world)
    before = lib.status()
    report = ing.scan()
    assert report["new"] == 0 and report["changed"] == 0 and report["missing"] == 0 and ing.drain() == 0
    assert lib.status() == before
    synth.save(root / "Camera" / "IMG_0300.jpg", synth.picture(300, (640, 480)), datetime(2024, 5, 5, 9, 0))
    assert ing.scan()["new"] == 1 and ing.drain() >= 1
    assert lib.status()["photos"] == before["photos"] + 1


def test_a_deleted_original_is_noticed_and_the_record_stays(world):
    root, _, lib, ing, _ = world
    load(world)
    gone = root / "Misc" / "holiday.jpg"
    pid = by_rel(world)["Misc/holiday.jpg"]["id"]
    gone.unlink()
    assert ing.scan()["missing"] == 1
    assert lib.status()["files"]["missing"] == 1 and lib.get(pid) is not None


def test_a_file_that_is_replaced_by_another_picture_is_read_again(world):
    root, _, lib, ing, _ = world
    load(world)
    path = root / "Misc" / "holiday.jpg"
    old = by_rel(world)["Misc/holiday.jpg"]["id"]
    synth.save(path, synth.picture(999, (640, 480)), mtime=datetime(2022, 2, 2))
    os.utime(path, (time.time() + 20, time.time() + 20))
    assert ing.scan()["changed"] == 1
    ing.drain()
    new = by_rel(world)["Misc/holiday.jpg"]["id"]
    assert new != old and lib.get(old) is None             # the old picture's record went with it


# ---------------------------------------------------------------- originals are never touched; removal is for good


def test_nothing_here_ever_changes_an_original(world):
    root, _, lib, ing, _ = world
    before = digests(root)
    mtimes = {p: p.stat().st_mtime for p in root.rglob("*") if p.is_file()}
    folder = load(world)
    pid = by_rel(world)["Camera/IMG_0001.jpg"]["id"]
    lib.set_taken([pid], dates.person_date(1990))
    lib.set_place([pid], "Home")
    lib.set_caption(pid, "the harbour")
    lib.set_album(folder["id"], dates.person_date(1985), "all", "Elsewhere")
    lib.remove([by_rel(world)["Camera/IMG_0002.jpg"]["id"]])
    lib.remove_folder(folder["id"])
    assert digests(root) == before and {p: p.stat().st_mtime for p in root.rglob("*") if p.is_file()} == mtimes


def test_a_removed_photo_stays_removed_when_the_folder_is_scanned_again(world):
    root, _, lib, ing, _ = world
    load(world)
    pid = by_rel(world)["Camera/IMG_0001.jpg"]["id"]
    thumb = lib.thumb_path(pid)
    assert thumb.exists()
    assert lib.remove([pid]) == 1 and not thumb.exists() and lib.get(pid) is None
    ing.request_scan()
    ing.scan()
    ing.drain()
    assert "Camera/IMG_0001.jpg" not in by_rel(world) and "Backup/IMG_0001 copy.jpg" not in by_rel(world)  # nor its copies
    assert (root / "Camera" / "IMG_0001.jpg").exists()


def test_removing_a_folder_takes_its_records_and_thumbnails_and_nothing_else(world, tmp_path):
    root, truth, lib, ing, _ = world
    camera = lib.add_folder(root / "Camera")
    backup = lib.add_folder(root / "Backup")
    lib.add_folder(root / "Shared")
    ing.scan()
    ing.drain()
    one = lib._one("SELECT photo_id FROM photo_files WHERE path=?", (str(root / "Camera" / "IMG_0001.jpg"),))["photo_id"]
    before = digests(root)
    only_backup = lib.list(folder=backup["id"])["items"]
    assert len(lib.get(one)["file_list"]) == 3
    out = lib.remove_folder(backup["id"])
    assert out["photos_removed"] == 0 and out["photos_kept"] == len(only_backup)   # its photo lives in other folders too
    assert len(lib.get(one)["file_list"]) == 2
    gone = lib.remove_folder(camera["id"])
    assert gone["photos_removed"] > 0
    assert lib.get(one) is not None                       # still in Shared (the small copy)
    thumbs = list((lib.dir / "thumbs").rglob("*.jpg"))
    assert len(thumbs) == lib.status()["photos"]
    assert digests(root) == before
    assert lib.add_folder(root / "Camera")["id"] != camera["id"]      # it can be added again, and comes back
    ing.scan()
    ing.drain()
    assert lib.status()["photos"] > gone["photos_removed"]


def test_a_removed_photos_words_are_gone_from_the_file_too(world):
    if not text.available():
        pytest.skip("Windows has no OCR language here")
    root, _, lib, ing, tmp = world
    load(world)
    pid = by_rel(world)["Camera/IMG_0006.jpg"]["id"]
    lib.remove([pid])
    lib.store.close()
    blob = b"".join(p.read_bytes() for p in (tmp / "user" / "memory").glob("memory.db*"))
    assert b"HARBOUR" not in blob and b"harbour cafe" not in blob.lower()


# ---------------------------------------------------------------- what the person fixes


def test_a_date_for_a_whole_album_reaches_its_photos_now_and_later(world):
    root, _, lib, ing, _ = world
    scans = lib.add_folder(root / "Scans 1990s")
    ing.scan()
    ing.drain()
    assert {p["source"] for p in lib.list(folder=scans["id"])["items"]} == {"folder"}
    out = lib.set_album(scans["id"], dates.person_date(1985), "unsure")
    assert out["photos"] == 3
    got = lib.list(folder=scans["id"])["items"]
    assert {(p["source"], p["taken"][:4], p["guess"]) for p in got} == {("album", "1985", False)}
    synth.save(root / "Scans 1990s" / "scan_099.jpg", synth.as_scan(synth.picture(99, (640, 480))), mtime=datetime(2021, 5, 5))
    ing.scan()
    ing.drain()
    assert {p["taken"][:4] for p in lib.list(folder=scans["id"])["items"]} == {"1985"}      # the new one too
    assert lib.clear_album(scans["id"])["date"] is None
    assert {p["source"] for p in lib.list(folder=scans["id"])["items"]} == {"folder"}


def test_an_album_date_leaves_a_cameras_date_alone_unless_you_say_all(world):
    root, _, lib, ing, _ = world
    xmas = lib.add_folder(root / "Christmas 2019")
    ing.scan()
    ing.drain()
    lib.set_album(xmas["id"], dates.person_date(1998, 12), "unsure")
    sources_ = {p["source"] for p in lib.list(folder=xmas["id"])["items"]}
    assert sources_ == {"album", "exif"}
    lib.set_album(xmas["id"], dates.person_date(1998, 12), "all")
    assert {p["source"] for p in lib.list(folder=xmas["id"])["items"]} == {"album"}
    assert lib.list(folder=xmas["id"], flag="check")["total"] == 0


def test_a_photo_can_be_given_a_date_and_a_place_and_get_them_back_to_what_was_read(world):
    _, _, lib, _, _ = world
    load(world)
    pid = by_rel(world)["Camera/IMG_0001.jpg"]["id"]
    original = lib.get(pid)
    lib.set_taken([pid], dates.person_date(1995, 6))
    lib.set_place([pid], "Grandma's house")
    got = lib.get(pid)
    assert (got["taken"][:7], got["source"], got["guess"], got["when"]) == ("1995-06", "person", False, "June 1995")
    assert (got["place"], got["place_source"]) == ("Grandma's house", "person")
    assert [i["id"] for i in lib.list(q="grandma")["items"]] == [pid]
    lib.clear_taken([pid])
    lib.clear_place([pid])
    back = lib.get(pid)
    assert (back["taken"], back["source"], back["place"], back["place_source"]) == (
        original["taken"], original["source"], original["place"], original["place_source"])


def test_a_caption_is_kept_and_searched_and_a_bad_place_is_refused(world):
    _, _, lib, _, _ = world
    load(world)
    pid = by_rel(world)["Camera/IMG_0001.jpg"]["id"]
    lib.set_caption(pid, "  Dad   at the harbour  ")
    assert lib.get(pid)["caption"] == "Dad at the harbour" and lib.list(q="dad harbour")["total"] == 1
    with pytest.raises(library.PhotoError):
        lib.set_place([pid], "   ")


def test_you_can_say_two_photos_are_the_same(world):
    _, _, lib, _, _ = world
    load(world)
    p = by_rel(world)
    a, b = p["Camera/IMG_0004.jpg"]["id"], p["Camera/IMG_0005.jpg"]["id"]
    assert b in {x["id"] for x in lib.similar(a)}                # offered, never merged by itself
    before = lib.status()["photos"]
    keep = lib.merge([a, b])
    assert keep == a and lib.status()["photos"] == before - 1 and lib.get(a)["files"] == 2 and lib.get(b) is None
    with pytest.raises(library.PhotoError):
        lib.merge([a])


# ---------------------------------------------------------------- browsing


def test_browsing_by_date_place_album_text_and_what_needs_a_look(world):
    _, truth, lib, _, _ = world
    folder = load(world)
    roots = [t for t in truth if t.state == "ok" and not t.same_as]       # one answer per photo

    def expect(year, month=None):
        lo = datetime(year, month or 1, 1)
        hi = datetime(year + (month == 12 if month else 1), (month % 12 + 1) if month else 1, 1)
        return sum(1 for t in roots if t.start < hi and t.end > lo)

    for year in (2023, 2022, 2021, 2020, 2019, 2015, 1990, 1995, 2005):
        assert lib.list(year=year)["total"] == expect(year), year
    assert lib.list(year=2023, month=12)["total"] == expect(2023, 12) == 1
    assert lib.list(year=2019, month=12)["total"] == expect(2019, 12) == 3       # the album's two guesses and one exact date
    assert [i["when"] for i in lib.list(year=2019, month=7)["items"]] == ["4 July 2019, 18:30"]
    assert lib.list(year=1995)["total"] == 3 and lib.list(year=2005)["total"] == 0   # the scans' decade covers 1995, not 2005
    assert lib.list(place="Harbourton")["total"] == 2 and lib.list(place="Ghanaland")["total"] == 2
    assert lib.list(folder=folder["id"])["total"] == lib.status()["photos"]
    assert lib.list(flag="check")["total"] == 1
    assert lib.list(flag="copies")["total"] == 2
    assert lib.list(flag="guess")["total"] == sum(1 for t in truth if t.guess and not t.same_as)
    assert lib.list(flag="undated")["total"] == 0               # the file's own date always says something
    assert lib.list(flag="noplace")["total"] == lib.status()["photos"] - 3


def test_order_and_paging(world):
    _, _, lib, _, _ = world
    load(world)
    newest = lib.list(order="newest", limit=5)
    assert newest["more"] and [i["taken"] for i in newest["items"]] == sorted((i["taken"] for i in newest["items"]), reverse=True)
    oldest = lib.list(order="oldest", limit=3)["items"]
    assert [i["taken"] for i in oldest] == sorted(i["taken"] for i in oldest)
    page2 = lib.list(order="newest", limit=5, offset=5)["items"]
    assert not {i["id"] for i in newest["items"]} & {i["id"] for i in page2}


def test_the_filters_the_page_offers(world):
    _, _, lib, _, _ = world
    load(world)
    f = lib.facets()
    assert {y["year"] for y in f["years"]} >= {2023, 2022, 2021, 2019, 2015, 1990}
    assert {"place": "Harbourton", "country": "Ghanaland", "n": 2} in f["places"]
    assert f["check"] == 1 and f["copies"] == 2 and f["undated"] == 0
    assert [m["month"] for m in lib.facets(year=2019)["months"]] == [7, 12]


# ---------------------------------------------------------------- folders and sources


def test_a_folder_must_exist_and_is_not_added_twice_or_inside_another(world, tmp_path):
    root, _, lib, _, _ = world
    with pytest.raises(library.PhotoError, match="isn't a folder"):
        lib.add_folder(tmp_path / "nowhere")
    first = lib.add_folder(root)
    assert lib.add_folder(root)["id"] == first["id"]
    with pytest.raises(library.PhotoError, match="already included"):
        lib.add_folder(root / "Camera")
    with pytest.raises(library.PhotoError, match="thumbnails"):
        lib.add_folder(lib.dir)


def test_the_scan_leaves_out_what_it_should(tmp_path, monkeypatch):
    root = tmp_path / "Pictures"
    for rel in ("keep/a.jpg", "$RECYCLE.BIN/b.jpg", ".thumbnails/c.jpg", "@eaDir/d.jpg", "keep/notes.txt", "keep/e.HEIC",
                "MARY/shot.png", "sub/deeper/f.webp"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    found = sorted(os.path.relpath(f.path, root).replace("\\", "/") for f in sources.walk(root, True, (root / "MARY",)))
    assert found == ["keep/a.jpg", "keep/e.HEIC", "sub/deeper/f.webp"]
    flat = sorted(os.path.relpath(f.path, root).replace("\\", "/") for f in sources.walk(root, False))
    assert flat == []


def test_hidden_folders_are_left_alone(tmp_path):
    import ctypes

    root = tmp_path / "p"
    (root / "hid").mkdir(parents=True)
    (root / "hid" / "a.jpg").write_bytes(b"x")
    (root / "b.jpg").write_bytes(b"x")
    ctypes.windll.kernel32.SetFileAttributesW(str(root / "hid"), sources.HIDDEN)
    assert [os.path.basename(f.path) for f in sources.walk(root)] == ["b.jpg"]


def test_a_onedrive_placeholder_is_recognised_by_its_attributes(monkeypatch):
    class Stat:
        st_mode, st_size, st_mtime = 0o100644, 10, 1.0

        def __init__(self, attrs):
            self.st_file_attributes = attrs

    class Entry:
        def __init__(self, name, attrs):
            self.name, self.path, self._attrs = name, "C:\\p\\" + name, attrs

        def is_symlink(self):
            return False

        def is_junction(self):
            return False

        def is_dir(self, follow_symlinks=True):
            return False

        def stat(self, follow_symlinks=True):
            return Stat(self._attrs)

    entries = [Entry("here.jpg", 0x20), Entry("cloud.jpg", 0x20 | sources.RECALL_ON_DATA_ACCESS),
               Entry("offline.jpg", sources.OFFLINE), Entry("secret.jpg", sources.HIDDEN)]
    monkeypatch.setattr(sources.os, "scandir", lambda folder: iter(entries))
    got = {os.path.basename(f.path): f.online_only for f in sources.walk(Path("C:\\p"))}
    assert got == {"here.jpg": False, "cloud.jpg": True, "offline.jpg": True}


def test_an_online_only_file_is_listed_but_never_opened(world, monkeypatch):
    from dataclasses import replace

    root, _, lib, ing, _ = world
    real_walk = sources.walk
    monkeypatch.setattr(ingest.sources, "walk", lambda *a, **k: (replace(f, online_only=True) if f.path.endswith("holiday.jpg") else f
                                                                for f in real_walk(*a, **k)))
    opened = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (opened.append(self.name), real(self))[1])
    load(world)
    assert "holiday.jpg" not in opened
    st = lib.status()
    assert st["files"]["cloud"] == 1 and "Misc/holiday.jpg" not in by_rel(world)
    assert any("cloud" in r["reason"] for r in lib._all("SELECT reason FROM photo_files WHERE state='cloud'"))


def test_a_link_to_another_folder_is_not_followed(tmp_path):
    root = tmp_path / "p"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.jpg").write_bytes(b"x")
    root.mkdir()
    (root / "a.jpg").write_bytes(b"x")
    import subprocess

    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "link"), str(outside)], capture_output=True)
    if done.returncode != 0:
        pytest.skip("this account can't make a junction")
    assert [os.path.basename(f.path) for f in sources.walk(root)] == ["a.jpg"]


# ---------------------------------------------------------------- the worker


def test_the_worker_steps_aside_while_she_is_answering(world):
    root, _, lib, ing, _ = world
    lib.add_folder(root)
    ing.scan()
    state = {"busy": 2}

    def busy():
        if state["busy"]:
            state["busy"] -= 1
            return True
        return False

    ing.busy = busy
    start = time.monotonic()
    ing.drain()
    assert state["busy"] == 0 and time.monotonic() - start >= 0.7 and lib.status()["reading"] == 0


def test_the_thread_finds_reads_and_stops(world):
    root, truth, lib, ing, _ = world
    lib.add_folder(root)
    ing.start()
    ing.request_scan()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and (lib.status()["reading"] or lib.status()["photos"] == 0 or ing.state != "idle"):
        time.sleep(0.1)
    assert lib.status()["photos"] > 0 and lib.status()["reading"] == 0
    ing.stop()
    assert not ing._thread.is_alive()


def test_what_the_worker_logs_is_counts_and_timings_only(world, monkeypatch):
    root, _, lib, ing, _ = world
    logged = []
    monkeypatch.setattr(ingest, "write_jsonl", lambda name, record: logged.append((name, record)))
    load(world)
    ing._log()
    blob = json.dumps(logged)
    assert logged and logged[0][0] == "photos"
    for secret in ("IMG_0001", "Harbourton", "HARBOUR", "Christmas", "Camera", str(root)):
        assert secret not in blob
    assert set(logged[0][1]) == {"event", "files", "photos", "ms", "faces"}


def test_progress_is_counts(world):
    _, _, lib, ing, _ = world
    load(world)
    p = ing.progress()
    assert p["state"] == "idle" and p["photos"] == lib.status()["photos"] and p["ocr"] in (True, False)
    assert set(p["average_ms"]) >= {"read", "copies", "record", "scan"}
    assert "Camera" not in json.dumps(p)


def test_the_library_belongs_to_an_owner_and_a_library(world):
    _, _, lib, _, _ = world
    load(world)
    n = lib.status()["photos"]
    lib.store.owner = "someone-else"
    assert lib.status()["photos"] == 0 and lib.list()["total"] == 0 and lib.folders() == []
    lib.store.owner = lib.store.db.meta("owner")
    assert lib.status()["photos"] == n


def test_heic_goes_in_through_windows_and_is_dated_by_its_album(world):
    _, _, lib, _, _ = world
    load(world)
    heic = by_rel(world).get("Trip June 2021/IMG_0100.heic")
    if heic is None:
        pytest.skip("this PC can't make or read a HEIC")
    assert (heic["when"], heic["source"], heic["guess"]) == ("June 2021", "folder", True)
