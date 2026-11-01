"""The settings page's People page, through its API on a scratch library (invented pictures and a stand-in for the face models; the
real user folder is never touched, and nothing is fetched from the internet)."""
import http.client
import json
import threading

import pytest

from mary import memory, photos
from mary.config import load_config
from mary.photos import faces, ingest, synth
from mary.photos.places import Gazetteer
from mary.settings import people as people_page
from mary.settings import server as web
from mary.settings import store


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(store, "ENV_FILE", tmp_path / "env")
    monkeypatch.setattr(memory, "ENABLED", True)
    monkeypatch.setattr(photos, "ENABLED", True)
    monkeypatch.setattr(memory, "FOLDER", tmp_path / "user" / "memory")
    s = web.SettingsServer(0)
    mem = memory.open_store(load_config())
    lib = photos.open_library(load_config(), mem)
    lib.gazetteer = Gazetteer([])
    lib.settings["read_text"] = False
    engine = synth.StandIn()
    ing = ingest.Ingest(lib, engine=engine)
    s.hooks.memory, s.hooks.photos, s.hooks.ingest = mem, lib, ing
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, lib, ing, engine, tmp_path / "Pictures"
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


def library(srv, per=6, people=3, on=True):
    """A folder of invented photos (per photo, one face of one of `people` invented people), read, and the switch turned on."""
    s, lib, ing, engine, root = srv
    cast = synth.Cast(3)
    names = [cast.person(f"p{i}") for i in range(people)]
    n = 0
    for _ in range(per):
        for who in names:
            n += 1
            img = synth.picture(500 + n, (640, 480))
            engine.teach(img, [((0.3, 0.2, 0.25, 0.3), cast.face(who), 0.99)])
            synth.save(root / f"IMG_{n:03}.jpg", img)
    call(s, "POST", "/api/photos/folders", {"path": str(root)})
    if on:
        assert call(s, "POST", "/api/people/switch", {"on": True})[1] == {"on": True}
    ing.scan()
    ing.drain()
    return cast, names


def groups(s, singles=False):
    return call(s, "GET", "/api/people/groups" + ("?singles=1" if singles else ""))[1]


def test_the_page_starts_off_with_nothing_found_and_says_what_it_needs(srv):
    s, lib, ing, engine, root = srv
    status, st = call(s, "GET", "/api/people")
    assert status == 200 and st["enabled"] and st["allowed"] and st["on"] is True and st["people"] == [] and st["questions"] == []
    assert st["models"]["files"][0]["name"].endswith(".onnx") and 38 < st["models"]["mb"] < 40
    assert st["status"]["faces"] == 0 and st["photos"] == 0


def test_turning_it_on_starts_reading_and_off_stops_it(srv):
    s, lib, ing, engine, root = srv
    assert call(s, "POST", "/api/people/switch", {"on": False})[1] == {"on": False}
    cast, names = library(srv, on=False)
    assert engine.detected == 0 and call(s, "GET", "/api/people")[1]["status"]["faces"] == 0
    assert call(s, "POST", "/api/people/switch", {"on": True})[1] == {"on": True}
    ing.drain()
    st = call(s, "GET", "/api/people")[1]
    assert st["on"] and st["status"]["faces"] == 18 and st["status"]["groups"] == 3 and st["ready"] is True
    assert call(s, "POST", "/api/people/switch", {"on": False})[1] == {"on": False}


def test_the_face_models_are_fetched_only_when_asked_and_a_failure_is_a_sentence(srv, monkeypatch, tmp_path):
    s, lib, ing, engine, root = srv
    asked = []

    def fake(folder, get=None):
        asked.append(folder)
        return {"a.onnx": 232_589, "b.onnx": 38_696_353}

    monkeypatch.setattr(faces, "fetch", fake)
    assert asked == []
    assert call(s, "POST", "/api/people/models", {})[1] == {"mb": 38.9}
    assert len(asked) == 1

    def broken(folder, get=None):
        raise OSError("no network")

    monkeypatch.setattr(faces, "fetch", broken)
    status, body = call(s, "POST", "/api/people/models", {})
    assert status == 400 and "Couldn't fetch the face models" in body["error"] and "no network" not in body["error"]


def test_groups_wait_for_a_name_and_naming_one_makes_a_person(srv):
    s, lib, ing, engine, root = srv
    library(srv)
    g = groups(s)
    assert g["total"] == 3 and all(len(c["samples"]) >= 1 and c["state"] == "unnamed" and c["name"] == "" for c in g["items"])
    first = g["items"][0]
    status, card = call(s, "POST", "/api/people/name", {"id": first["id"], "name": " Ama  Mensah ", "relation": "friend"})
    assert status == 200 and card["name"] == "Ama Mensah" and card["state"] == "named" and card["relation"] == "friend"
    st = call(s, "GET", "/api/people")[1]
    assert [p["name"] for p in st["people"]] == ["Ama Mensah"] and st["people"][0]["photos"] == 6
    assert groups(s)["total"] == 2
    status, body = call(s, "POST", "/api/people/name", {"id": groups(s)["items"][0]["id"], "name": "ama mensah"})
    assert status == 400 and "already have someone called" in body["error"]
    assert call(s, "POST", "/api/people/name", {"id": first["id"], "name": ""})[1]["error"] == "Write their name."
    assert call(s, "POST", "/api/people/name", {"id": "7", "name": "x"})[0] == 400
    assert call(s, "POST", "/api/people/name", {"id": 99999, "name": "x"})[0] == 400


def test_a_question_is_answered_yes_or_no(srv):
    s, lib, ing, engine, root = srv
    cast, names = library(srv, per=5)
    g = groups(s)["items"]
    ama = g[0]["id"]
    call(s, "POST", "/api/people/name", {"id": ama, "name": "Ama"})
    ing.drain()
    # faces of another group are, as far as the page is concerned, put to Ama as a question
    other = g[1]["id"]
    faces_of = call(s, "GET", f"/api/people/{other}/faces")[1]
    fids = [f["id"] for f in faces_of["items"]]
    with lib.db.tx() as c:
        c.execute("UPDATE photo_faces SET suggest_id=?, suggest_score=0.47 WHERE id=?", (ama, fids[0]))
        c.execute("UPDATE photo_faces SET suggest_id=?, suggest_score=0.44 WHERE id=?", (ama, fids[1]))
    q = call(s, "GET", "/api/people")[1]["questions"]
    assert len(q) == 1 and q[0]["person"]["name"] == "Ama" and q[0]["count"] == 2 and len(q[0]["faces"]) == 2
    assert q[0]["faces"][0]["crop"].startswith("/face/")
    assert call(s, "POST", "/api/people/deny", {"faces": [fids[1]], "person": ama})[1] == {"denied": 1}
    assert call(s, "POST", "/api/people/move", {"faces": [fids[0]], "person": ama})[0] == 200
    assert call(s, "GET", "/api/people")[1]["questions"] == []
    assert call(s, "POST", "/api/people/move", {"faces": [], "person": ama})[1]["error"] == "Pick some faces first."
    assert call(s, "POST", "/api/people/move", {"faces": ["1"], "person": ama})[0] == 400


def test_merging_splitting_setting_aside_and_saying_not_the_same(srv):
    s, lib, ing, engine, root = srv
    library(srv, per=5)
    a, b, c = (x["id"] for x in groups(s)["items"])
    merged = call(s, "POST", "/api/people/merge", {"keep": a, "other": b})[1]
    assert merged["id"] == a and merged["faces"] == 10
    assert call(s, "POST", "/api/people/merge", {"keep": a, "other": a})[1]["error"] == "Pick two different people."
    mine = [f["id"] for f in call(s, "GET", f"/api/people/{a}/faces")[1]["items"]][:5]
    new = call(s, "POST", "/api/people/split", {"id": a, "faces": mine})[1]
    assert new["faces"] == 5 and call(s, "GET", f"/api/people/{a}/faces")[1]["total"] == 5
    assert "all of them" in call(s, "POST", "/api/people/split", {"id": c, "faces": [f["id"] for f in call(s, "GET", f"/api/people/{c}/faces")[1]["items"]]})[1]["error"]
    assert call(s, "POST", "/api/people/ignore", {"id": c})[1]["state"] == "ignored"
    st = call(s, "GET", "/api/people")[1]
    assert [i["id"] for i in st["ignored"]] == [c]
    assert call(s, "POST", "/api/people/unignore", {"id": c})[1]["state"] == "unnamed"
    assert call(s, "POST", "/api/people/not-same", {"a": a, "b": new["id"]})[1] == {"ok": True}
    assert call(s, "POST", "/api/people/not-same", {"a": a, "b": 424242})[0] == 400
    call(s, "POST", "/api/people/name", {"id": a, "name": "Ama"})
    assert call(s, "POST", "/api/people/relation", {"id": a, "relation": " my  aunt "})[1]["relation"] == "my aunt"
    assert "They have a name" in call(s, "POST", "/api/people/ignore", {"id": a})[1]["error"]
    assert call(s, "POST", "/api/people/unname", {"id": a})[1]["state"] == "unnamed"


def test_a_face_picture_is_served_only_to_this_page_and_never_kept_by_the_browser(srv):
    s, lib, ing, engine, root = srv
    library(srv)
    face = groups(s)["items"][0]["samples"][0]
    status, data, r = call(s, "GET", face["crop"], raw=True, headers={"Sec-Fetch-Site": "same-origin", "X-MARY": None})
    assert status == 200 and r.getheader("Content-Type") == "image/jpeg" and data[:2] == b"\xff\xd8"
    assert r.getheader("Cache-Control") == "no-store" and r.getheader("Cross-Origin-Resource-Policy") == "same-origin"
    for other in ({"Sec-Fetch-Site": "cross-site", "X-MARY": None}, {"X-MARY": None}):
        assert call(s, "GET", face["crop"], raw=True, headers=other)[0] == 403
    assert call(s, "GET", "/face/999999", raw=True, headers={"Sec-Fetch-Site": "same-origin"})[0] == 400
    assert call(s, "GET", "/face/1/../../config.yaml", raw=True, headers={"Sec-Fetch-Site": "same-origin"})[0] in (403, 404)


def test_deleting_a_person_removes_their_pictures_and_the_page_no_longer_serves_them(srv):
    s, lib, ing, engine, root = srv
    library(srv)
    g = groups(s)["items"][0]
    call(s, "POST", "/api/people/name", {"id": g["id"], "name": "Gone"})
    face = call(s, "GET", f"/api/people/{g['id']}/faces")[1]["items"][0]
    hdr = {"Sec-Fetch-Site": "same-origin", "X-MARY": None}
    assert call(s, "GET", face["crop"], raw=True, headers=hdr)[0] == 200
    assert call(s, "POST", "/api/people/remove", {"id": g["id"]})[1] == {"faces": 6}
    assert call(s, "GET", face["crop"], raw=True, headers=hdr)[0] == 400
    assert call(s, "GET", "/api/people")[1]["people"] == [] and call(s, "GET", f"/api/people/{g['id']}/faces")[0] == 400


def test_delete_all_face_data_clears_the_page_and_turns_the_switch_off(srv):
    s, lib, ing, engine, root = srv
    library(srv)
    assert call(s, "POST", "/api/people/delete-all", {})[1] == {"faces": 18}
    st = call(s, "GET", "/api/people")[1]
    assert st["on"] is False and st["status"]["faces"] == 0 and st["people"] == [] and groups(s)["total"] == 0
    assert not (lib.dir / "faces").exists()
    assert call(s, "POST", "/api/people/look-again", {})[1]["photos"] == 18


def test_the_photos_page_can_be_filtered_by_person_and_a_photo_says_who_is_in_it(srv):
    s, lib, ing, engine, root = srv
    library(srv)
    g = groups(s)["items"][0]
    call(s, "POST", "/api/people/name", {"id": g["id"], "name": "Ama"})
    assert call(s, "GET", "/api/photos/facets")[1]["people"] == [{"id": g["id"], "name": "Ama", "photos": 6}]
    got = call(s, "GET", f"/api/photos/list?person={g['id']}")[1]
    assert got["total"] == 6 and len(got["items"]) == 6
    assert call(s, "GET", "/api/photos/list?person=abc")[0] == 400
    assert call(s, "GET", "/api/photos/list?person=999999")[1]["total"] == 0
    p = call(s, "GET", f"/api/photos/{got['items'][0]['id']}")[1]
    assert p["people"]["named"] == [{"id": g["id"], "name": "Ama"}] and p["people"]["faces"][0]["crop"].startswith("/face/")
    other = [i for i in call(s, "GET", "/api/photos/list")[1]["items"] if i["id"] not in {x["id"] for x in got["items"]}][0]
    assert call(s, "GET", f"/api/photos/{other['id']}")[1]["people"]["named"] == []


def test_nothing_the_page_gets_holds_a_fingerprint_a_path_or_a_probability(srv):
    s, lib, ing, engine, root = srv
    library(srv)
    g = groups(s)["items"][0]
    call(s, "POST", "/api/people/name", {"id": g["id"], "name": "Ama"})
    blob = json.dumps([call(s, "GET", "/api/people")[1], groups(s), call(s, "GET", f"/api/people/{g['id']}/faces")[1]])
    for secret in (str(root), "vector", "score", "IMG_0", "points", "suggest_score"):
        assert secret not in blob


def test_a_request_without_the_header_or_from_another_site_is_refused(srv):
    s, lib, ing, engine, root = srv
    for headers in ({"X-MARY": None}, {"Origin": "http://evil.example"}):
        assert call(s, "GET", "/api/people", headers=headers)[0] == 403
        assert call(s, "POST", "/api/people/delete-all", {}, headers=headers)[0] == 403
    assert call(s, "POST", "/api/people/switch", {"on": True}, headers={"Host": "evil.example"})[0] == 403


def test_with_photos_switched_off_the_page_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(store, "ENV_FILE", tmp_path / "env")
    assert people_page.state(None, None) == {"enabled": False}
    with pytest.raises(store.SettingsError, match="switched off"):
        people_page.groups(None, {})
