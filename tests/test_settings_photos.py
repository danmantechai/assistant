"""The settings page's Photos page, through its API on a scratch folder (invented pictures, never the real user folder)."""
import http.client
import json
import threading
from pathlib import Path

import pytest

from mary import memory, photos
from mary.config import load_config
from mary.photos import ingest, synth
from mary.photos.places import Gazetteer
from mary.settings import photos as photos_page
from mary.settings import server as web
from mary.settings import store

PLACES = [("Harbourton", "Coastal Region", "Ghanaland", 5.1053, -1.2466, 90000),
          ("Northfield", "Greater North", "Britannia", 51.5072, -0.1276, 8_000_000)]


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(store, "ENV_FILE", tmp_path / "env")
    monkeypatch.setattr(memory, "ENABLED", True)
    monkeypatch.setattr(photos, "ENABLED", True)
    monkeypatch.setattr(memory, "FOLDER", tmp_path / "user" / "memory")
    root = tmp_path / "Pictures"
    truth = synth.hard_set(root)
    s = web.SettingsServer(0)
    mem = memory.open_store(load_config())
    lib = photos.open_library(load_config(), mem)
    lib.gazetteer = Gazetteer(PLACES)
    ing = ingest.Ingest(lib)
    s.hooks.memory, s.hooks.photos, s.hooks.ingest = mem, lib, ing
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, lib, ing, root, truth
    s.shutdown()
    s.server_close()
    ing.stop()
    mem.close()


def call(s, method, path, body=None, raw=False, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=20)
    headers = {"Host": f"127.0.0.1:{s.port}", "X-MARY": "1", **(headers or {})}
    headers = {k: v for k, v in headers.items() if v is not None}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    out = r.read()
    conn.close()
    return (r.status, out, r) if raw else (r.status, json.loads(out))


def added(srv):
    s, lib, ing, root, truth = srv
    status, folder = call(s, "POST", "/api/photos/folders", {"path": str(root)})
    assert status == 200
    ing.scan()
    ing.drain()
    return folder


def test_nothing_is_there_until_a_folder_is_added_and_it_offers_the_usual_ones(srv):
    s, lib, *_ = srv
    status, st = call(s, "GET", "/api/photos")
    assert status == 200 and st["enabled"] and st["folders"] == [] and st["progress"]["photos"] == 0
    assert isinstance(st["suggestions"], list) and "places" in st["progress"] and st["facets"]["years"] == []


def test_adding_a_folder_reads_it_and_the_page_shows_the_counts(srv):
    s, lib, ing, root, truth = srv
    folder = added(srv)
    assert folder["label"] == "Pictures" and folder["recursive"] is True
    _, st = call(s, "GET", "/api/photos")
    p = st["progress"]
    assert p["photos"] > 15 and p["copies"] == 3 and p["files"]["unreadable"] == 3 and p["files"]["tiny"] == 1
    assert st["folders"][0]["photos"] == p["photos"] and st["suggestions"] == [] or all(g["path"] != str(root) for g in st["suggestions"])
    assert "Camera" not in json.dumps(p)                  # progress is counts


def test_a_path_that_is_not_a_folder_or_is_inside_another_is_refused_with_a_sentence(srv):
    s, lib, ing, root, truth = srv
    assert "isn't a folder" in call(s, "POST", "/api/photos/folders", {"path": str(root / "nope")})[1]["error"]
    call(s, "POST", "/api/photos/folders", {"path": str(root)})
    status, body = call(s, "POST", "/api/photos/folders", {"path": str(root / "Camera")})
    assert status == 400 and "already included" in body["error"]


def test_the_list_filters_pages_and_never_names_a_path(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    _, every = call(s, "GET", "/api/photos/list")
    assert every["total"] == lib.status()["photos"] and len(every["items"]) == min(60, every["total"])
    first = every["items"][0]
    assert set(first) >= {"id", "when", "guess", "flag", "place_label", "thumb", "files", "caption"}
    assert str(root) not in json.dumps(every)
    assert call(s, "GET", "/api/photos/list?year=2023")[1]["total"] == 2
    assert call(s, "GET", "/api/photos/list?place=Harbourton")[1]["total"] == 2
    assert call(s, "GET", "/api/photos/list?flag=check")[1]["total"] == 1
    assert call(s, "GET", "/api/photos/list?flag=nonsense")[0] == 400
    assert call(s, "GET", "/api/photos/list?year=abc")[0] == 400
    assert call(s, "GET", "/api/photos/list?limit=5&offset=1000")[1]["items"] == []
    facets = call(s, "GET", "/api/photos/facets")[1]
    assert facets["check"] == 1 and {"place": "Harbourton", "country": "Ghanaland", "n": 2} in facets["places"]
    assert [m["month"] for m in call(s, "GET", "/api/photos/facets?year=2019")[1]["months"]] == [7, 12]


def test_a_photo_has_a_record_a_thumbnail_and_its_files(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    pid = lib.list(q="", year=2023, month=12)["items"][0]["id"]
    _, p = call(s, "GET", f"/api/photos/{pid}")
    assert p["when"] == "25 December 2023, 10:15" and p["place_label"] == "Harbourton, Coastal Region, Ghanaland"
    assert len(p["file_list"]) == 3 and all(f["exists"] for f in p["file_list"])
    assert p["thumb"].startswith(f"/thumb/{pid}?v=")
    status, data, r = call(s, "GET", p["thumb"], raw=True, headers={"Sec-Fetch-Site": "same-origin", "X-MARY": None})
    assert status == 200 and r.getheader("Content-Type") == "image/jpeg" and data[:2] == b"\xff\xd8"
    assert "max-age" in r.getheader("Cache-Control") and r.getheader("Cross-Origin-Resource-Policy") == "same-origin"
    for other in ({"Sec-Fetch-Site": "cross-site", "X-MARY": None}, {"X-MARY": None}):         # another site, or no browser at all
        assert call(s, "GET", p["thumb"], raw=True, headers=other)[0] == 403
    assert call(s, "GET", "/thumb/999999", raw=True, headers={"Sec-Fetch-Site": "same-origin"})[0] == 400
    assert p["view"].startswith(f"/view/{pid}?v=")
    status, big, r = call(s, "GET", p["view"], raw=True, headers={"Sec-Fetch-Site": "same-origin", "X-MARY": None})
    assert status == 200 and r.getheader("Content-Type") == "image/jpeg" and big[:2] == b"\xff\xd8" and len(big) > len(data)
    for other in ({"Sec-Fetch-Site": "cross-site", "X-MARY": None}, {"X-MARY": None}):
        assert call(s, "GET", p["view"], raw=True, headers=other)[0] == 403
    assert call(s, "GET", "/view/999999", raw=True, headers={"Sec-Fetch-Site": "same-origin"})[0] == 400
    assert call(s, "GET", "/api/photos/999999")[0] == 400
    assert call(s, "GET", "/api/photos/../../config.yaml")[0] == 404


def test_fixing_a_date_a_place_and_a_caption_applies_at_once(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    pid = lib.list(year=2023, month=12)["items"][0]["id"]
    assert call(s, "POST", "/api/photos/fix", {"ids": [pid], "date": {"year": 1995, "month": 6}})[1] == {"changed": {"date": 1}}
    assert call(s, "POST", "/api/photos/fix", {"ids": [pid], "place": "Grandma's house", "caption": "Dad at the harbour"})[1] == {
        "changed": {"place": 1, "caption": 1}}
    p = call(s, "GET", f"/api/photos/{pid}")[1]
    assert (p["when"], p["source"], p["place"], p["caption"]) == ("June 1995", "person", "Grandma's house", "Dad at the harbour")
    assert call(s, "GET", "/api/photos/list?q=grandma")[1]["total"] == 1
    call(s, "POST", "/api/photos/fix", {"ids": [pid], "clear": "date"})
    call(s, "POST", "/api/photos/fix", {"ids": [pid], "clear": "place"})
    back = call(s, "GET", f"/api/photos/{pid}")[1]
    assert (back["when"], back["source"], back["place"]) == ("25 December 2023, 10:15", "exif", "Harbourton")


def test_a_selection_can_be_all_that_match_a_filter(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    n = lib.list(flag="guess")["total"]
    assert call(s, "POST", "/api/photos/fix", {"filter": {"flag": "guess"}, "date": {"year": 1985, "decade": True}})[1] == {"changed": {"date": n}}
    assert lib.list(flag="guess")["total"] == 0 and lib.list(year=1985)["total"] == n


def test_bad_fixes_are_sentences(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    pid = lib.list()["items"][0]["id"]
    bad = [({"ids": [pid], "date": {"year": "abc"}}, "year"), ({"ids": [pid], "date": {"year": 3000}}, "between"),
           ({"ids": [pid], "date": {"year": 2020, "day": 5}}, "month"), ({"ids": [pid], "date": {"year": 2020, "month": 2, "day": 31}}, "real date"),
           ({"ids": [pid], "place": "   "}, None), ({"ids": []}, "Pick some photos"), ({"ids": [pid]}, "Say what"),
           ({"ids": ["x"]}, "Pick some photos"), ({"ids": [pid, pid + 1], "caption": "x"}, "one photo")]
    for body, word in bad:
        status, out = call(s, "POST", "/api/photos/fix", body)
        assert status == 400 and (word is None or word in out["error"]), body


def test_a_whole_folder_gets_a_date_and_a_place(srv):
    s, lib, ing, root, truth = srv
    folder = added(srv)
    status, out = call(s, "POST", "/api/photos/folders/album",
                       {"id": folder["id"], "date": {"year": 1985}, "scope": "all", "place": "Home town"})
    assert status == 200 and out["photos"] == lib.status()["photos"]
    assert {i["source"] for i in lib.list(limit=200)["items"]} == {"album"}
    assert out["folder"]["date"]["label"] == "1985"
    assert call(s, "POST", "/api/photos/folders/album", {"id": folder["id"], "clear": True})[1]["date"] is None
    assert call(s, "POST", "/api/photos/folders/album", {"id": folder["id"]})[0] == 400
    assert call(s, "POST", "/api/photos/folders/album", {"id": folder["id"], "date": {"year": 1985}, "scope": "some"})[0] == 400


def test_photos_can_be_merged_and_removed_and_a_removed_one_stays_removed(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    ids = [i["id"] for i in lib.list(year=2022, month=3)["items"]]
    assert len(ids) == 3                                          # a burst of two, and a name-dated one
    burst = [i["id"] for i in lib.list(year=2022, month=3, limit=200)["items"] if i["when"].startswith("3 March")]
    assert call(s, "GET", f"/api/photos/{burst[0]}/similar")[1]["items"][0]["id"] == burst[1]
    assert call(s, "POST", "/api/photos/merge", {"ids": [burst[0]]})[0] == 400
    assert call(s, "POST", "/api/photos/merge", {"ids": burst})[1] == {"photo": burst[0]}
    before = lib.status()["photos"]
    assert call(s, "POST", "/api/photos/remove", {"ids": [burst[0]]})[1] == {"removed": 1}
    assert lib.status()["photos"] == before - 1
    ing.request_scan()
    ing.scan()
    ing.drain()
    assert lib.status()["photos"] == before - 1
    assert (root / "Camera" / "IMG_0004.jpg").exists() and (root / "Camera" / "IMG_0005.jpg").exists()


def test_removing_a_folder_keeps_the_originals(srv):
    s, lib, ing, root, truth = srv
    folder = added(srv)
    before = sorted(p.name for p in root.rglob("*") if p.is_file())
    out = call(s, "POST", "/api/photos/folders/remove", {"id": folder["id"]})[1]
    assert out["photos_removed"] > 15 and lib.status()["photos"] == 0 and lib.folders() == []
    assert sorted(p.name for p in root.rglob("*") if p.is_file()) == before
    assert call(s, "POST", "/api/photos/folders/remove", {"id": folder["id"]})[0] == 400
    assert not list((lib.dir / "thumbs").rglob("*.jpg"))


def test_the_folder_dialog_and_show_in_folder_use_what_the_server_knows_not_what_the_page_sends(srv, monkeypatch):
    s, lib, ing, root, truth = srv
    added(srv)
    monkeypatch.setattr(photos_page, "pick_folder", lambda: str(root / "Camera"))
    assert call(s, "POST", "/api/photos/folders/pick", {})[1] == {"path": str(root / "Camera")}
    opened = []
    monkeypatch.setattr(photos_page, "show_in_folder", lambda path: opened.append(path))
    pid = lib.list(year=2023, month=12)["items"][0]["id"]
    assert call(s, "POST", f"/api/photos/{pid}/show", {"path": "C:\\Windows\\System32\\cmd.exe"})[1] == {"ok": True}
    assert opened and Path(opened[0]).is_relative_to(root) and "System32" not in opened[0]
    (Path(opened[0])).unlink()
    for other in lib.file_paths(pid):
        Path(other).unlink(missing_ok=True)
    assert call(s, "POST", f"/api/photos/{pid}/show", {})[0] == 400          # the original is gone: a sentence


def test_scan_asks_the_worker_to_look_again(srv):
    s, lib, ing, root, truth = srv
    added(srv)
    ing._scan_requested = False
    assert call(s, "POST", "/api/photos/scan", {})[1] == {"ok": True} and ing._scan_requested


def test_the_list_of_places_is_fetched_only_when_asked_and_names_the_waiting_photos(srv, monkeypatch, tmp_path):
    from geonames_fake import fetch
    from mary.photos import places

    s, lib, ing, root, truth = srv
    lib.gazetteer, lib.gazetteer_path = Gazetteer(), tmp_path / "data" / "places" / "gazetteer.db"
    added(srv)
    assert call(s, "GET", "/api/photos")[1]["progress"]["places"] is False and lib.list(place="Harbourton")["total"] == 0
    asked = []
    monkeypatch.setattr(places, "_fetch", lambda url: (asked.append(url), fetch(url))[1])
    assert asked == []                                           # nothing was fetched by looking
    out = call(s, "POST", "/api/photos/places", {})[1]
    assert out["places"] == 2 and out["photos_named"] >= 2 and len(asked) == 3
    assert call(s, "GET", "/api/photos")[1]["progress"]["places"] is True
    assert call(s, "GET", "/api/photos/list?place=Harbourton")[1]["total"] == 2


def test_a_failed_fetch_of_the_list_is_a_sentence(srv, monkeypatch, tmp_path):
    from mary.photos import places

    s, lib, ing, root, truth = srv
    lib.gazetteer_path = tmp_path / "data" / "places" / "gazetteer.db"

    def down(url):
        raise OSError("offline")

    monkeypatch.setattr(places, "_fetch", down)
    status, body = call(s, "POST", "/api/photos/places", {})
    assert status == 400 and "Couldn't fetch" in body["error"] and not lib.gazetteer_path.exists()


def test_photos_switched_off_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(photos, "ENABLED", False)
    s = web.SettingsServer(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        assert call(s, "GET", "/api/photos")[1] == {"enabled": False}
        status, body = call(s, "GET", "/api/photos/list")
        assert status == 400 and "switched off" in body["error"]
    finally:
        s.shutdown()
        s.server_close()


def test_the_photo_settings_are_ordinary_settings_and_the_page_script_has_the_page(srv):
    s, *_ = srv
    _, state = call(s, "GET", "/api/state")
    assert {f["path"] for f in state["settings"]["pages"]["photos"]} == {"photos.enabled", "photos.read_text"}
    assert state["settings"]["values"]["photos.read_text"]["value"] is True
    js = (Path(web.__file__).parent / "web" / "settings.js").read_text(encoding="utf-8")
    assert "function pagePhotos" in js and '["photos", "Photos"]' in js and "Coming soon" not in js
    body = js.split("function pagePhotos")[1].split("function pageRemembered")[0]
    assert "innerHTML" not in body and "originals" in body
