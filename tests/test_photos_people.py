"""People: faces grouped, named by the person, recognised in new photos, asked about when unsure, merged, split, ignored and
deleted for good (mary/photos/people.py). The faces here are invented fingerprints (synth.Cast): no picture, no model."""
import io
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mary.memory.store import MemoryStore
from mary.photos import library
from mary.photos.cluster import unit
from mary.photos.faces import Found, quality
from mary.photos.places import Gazetteer
from mary.photos.synth import Cast

TAG = "test-model-1"


def tiny_jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (16, 16), (120, 90, 60)).save(out, "JPEG")
    return out.getvalue()


CROP = tiny_jpeg()


@pytest.fixture
def world(tmp_path):
    store = MemoryStore(tmp_path / "user" / "memory" / "memory.db", backups=tmp_path / "backups")
    lib = library.PhotoLibrary(store, {}, Gazetteer([]))
    yield lib, lib.people, tmp_path
    store.close()


def photo(lib, n=1) -> list[int]:
    out = []
    with lib.db.tx() as c:
        for i in range(n):
            out.append(c.execute("INSERT INTO photos(owner, library, sha, dhash, width, height, added, updated) "
                                 "VALUES (?,?,?,?,?,?,?,?)", (lib.owner, lib.lib, f"sha{i}", "0" * 16, 800, 600,
                                                              "2025-01-01T00:00:00", "2025-01-01T00:00:00")).lastrowid)
    return out


def found(vec, box=(0.3, 0.2, 0.2, 0.25), score=0.99, px=120) -> Found:
    return Found(box=box, points=(0.35, 0.3, 0.45, 0.3, 0.4, 0.38, 0.36, 0.42, 0.44, 0.42), score=score, px=px,
                 quality=quality(score, px), vector=None if vec is None else unit(vec), crop=CROP if vec is not None else b"")


def unclear(box=(0.7, 0.1, 0.05, 0.06)) -> Found:
    return Found(box=box, points=(), score=0.5, px=20, quality=0.1)


def feed(lib, people, vecs, per_photo=1, tag=TAG):
    """Put these fingerprints in as faces of new photos (per_photo of them in each). Returns [(photo_id, [face ids])]."""
    out = []
    vecs = list(vecs)
    for i in range(0, len(vecs), per_photo):
        chunk = vecs[i:i + per_photo]
        (pid,) = photo(lib)
        boxes = [(0.05 + 0.3 * k, 0.2, 0.2, 0.25) for k in range(len(chunk))]
        people.add(pid, [found(v, b) for v, b in zip(chunk, boxes)], tag)
        out.append((pid, [r["id"] for r in lib._all("SELECT id FROM photo_faces WHERE photo_id=? ORDER BY x", (pid,))]))
    return out


def piles(people):
    """{person id: set of face ids} for every group that has faces."""
    out = {}
    for r in people._all("SELECT id, person_id FROM photo_faces WHERE person_id IS NOT NULL"):
        out.setdefault(r["person_id"], set()).add(r["id"])
    return out


def pile_of(people, face_id):
    return people._one("SELECT person_id FROM photo_faces WHERE id=?", (face_id,))[0]


def settle(people):
    n = 0
    while people.maintain() and n < 1000:
        n += 1


def cast_of(seed, n=4):
    cast = Cast(seed)
    return cast, [cast.person(f"p{i}") for i in range(n)]


# ---------------------------------------------------------------- the switch

def test_face_recognition_is_on_by_default_and_deleting_all_face_data_turns_it_off(world):
    lib, people, _ = world
    assert people.enabled() is True                      # on by default (the models are what has to be fetched)
    assert people.set_enabled(False) is False and people.set_enabled(True) is True


def test_the_switch_cannot_be_turned_on_when_the_config_forbids_it(tmp_path):
    store = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"faces": {"allowed": False}}, Gazetteer([]))
    with pytest.raises(library.PhotoError, match="switched off"):
        lib.people.set_enabled(True)
    assert lib.people.enabled() is False
    store.close()


# ---------------------------------------------------------------- finding people, never naming them

def test_faces_are_grouped_by_who_they_are_and_nobody_is_given_a_name(world):
    lib, people, _ = world
    cast, names = cast_of(1)
    vecs, truth = [], []
    for n in names:
        for _ in range(8):
            vecs.append(cast.face(n))
            truth.append(n)
    order = np.random.default_rng(0).permutation(len(vecs))
    fed = feed(lib, people, [vecs[i] for i in order])
    settle(people)
    got = {}
    for (pid, (fid,)), i in zip(fed, order):
        got[fid] = truth[i]
    groups = piles(people)
    assert len(groups) == len(names)
    for members in groups.values():
        assert len({got[f] for f in members}) == 1 and len(members) == 8
    assert people._one("SELECT count(*) FROM people WHERE name<>'' OR relation<>'' OR state<>'unnamed'")[0] == 0
    assert people.people() == []


def test_two_faces_in_one_photo_are_never_the_same_person(world):
    lib, people, _ = world
    cast, names = cast_of(2, 2)
    a, b = names
    for _ in range(6):
        feed(lib, people, [cast.face(a), cast.face(b)], per_photo=2)
    for pid in {r["photo_id"] for r in people._all("SELECT photo_id FROM photo_faces")}:
        ps = [r["person_id"] for r in people._all("SELECT person_id FROM photo_faces WHERE photo_id=?", (pid,))]
        assert len(ps) == len(set(ps)) == 2
    # a crowd of strangers in one photo is as many groups as faces
    (pid,) = photo(lib)
    people.add(pid, [found(v, (0.02 + 0.1 * i, 0.2, 0.08, 0.1)) for i, v in enumerate(cast.strangers(8))], TAG)
    assert len({r["person_id"] for r in people._all("SELECT person_id FROM photo_faces WHERE photo_id=?", (pid,))}) == 8


def test_a_small_or_unsure_face_is_kept_as_a_box_and_never_grouped_or_fingerprinted(world):
    lib, people, _ = world
    cast, names = cast_of(3, 1)
    (pid,) = photo(lib)
    stats = people.add(pid, [found(cast.face(names[0])), unclear()], TAG)
    assert stats["faces"] == 2 and stats["unclear"] == 1
    rows = people._all("SELECT * FROM photo_faces WHERE photo_id=? ORDER BY usable", (pid,))
    assert [r["usable"] for r in rows] == [0, 1] and rows[0]["person_id"] is None and rows[0]["crop"] == ""
    assert people.vectors.count("face", "photo_faces") == 1
    assert people.status()["unclear"] == 1 and people.in_photo(pid)["unclear"] == 1


# ---------------------------------------------------------------- naming, recognising, asking

def named_world(world, seed=4, per=6):
    lib, people, tmp = world
    cast, names = cast_of(seed, 3)
    feed(lib, people, [cast.face(n) for n in names for _ in range(per)])
    settle(people)
    ids = {}
    for pid, members in piles(people).items():
        first = min(members)
        for n, k in zip(names, range(3)):
            pass
    # which pile is which person: the fingerprints of the first photo of each
    by_person = {}
    for r in people._all("SELECT id, person_id FROM photo_faces ORDER BY id"):
        by_person.setdefault(r["person_id"], []).append(r["id"])
    order = sorted(by_person.values(), key=lambda ids_: ids_[0])
    for n, members in zip(names, order):
        ids[n] = pile_of(people, members[0])
    return lib, people, cast, names, ids


def test_a_named_person_is_recognised_in_new_photos_and_strangers_are_not(world):
    lib, people, cast, names, ids = named_world(world)
    people.name(ids["p0"], "Ama", "friend")
    people.name(ids["p1"], "Kofi")
    settle(people)
    before = people.person(ids["p0"])["faces"]
    fed = feed(lib, people, [cast.face("p0") for _ in range(10)])
    mine = [pile_of(people, f[0]) for _, f in fed]
    assert sum(m == ids["p0"] for m in mine) >= 9
    assert all(m in (ids["p0"],) or people._person_row(m)["state"] == "unnamed" for m in mine)     # never a wrong name
    assert people.person(ids["p0"])["faces"] >= before + 9
    assert {r["how"] for r in people._all("SELECT how FROM photo_faces WHERE person_id=?", (ids["p0"],))} == {"auto"}
    strangers = feed(lib, people, cast.strangers(30))
    assert all(people._person_row(pile_of(people, f[0]))["state"] == "unnamed" for _, f in strangers)
    assert people.questions() == []


def at(cast, name, cos, seed):
    """A fingerprint with exactly this cosine to the person's centre (and nothing in common with the other such faces)."""
    centre = cast.people[name][0]
    rng = np.random.default_rng(seed)
    off = unit(rng.normal(size=centre.shape[0]))
    off = unit(off - (off @ centre) * centre)
    return unit(cos * centre + (1 - cos ** 2) ** 0.5 * off).astype(np.float32)


def test_naming_a_group_finds_that_person_in_groups_that_were_waiting(tmp_path):
    """With grouping set strict, faces of Ama stay in piles of their own; once her group is named they join her (they are, one by
    one, clearly her)."""
    store = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"faces": {"group": 0.8, "alone": 0.1}}, Gazetteer([]))
    people = lib.people
    cast = Cast(5)
    cast.person("ama")
    feed(lib, people, [cast.face("ama", noise=0.3) for _ in range(6)])
    feed(lib, people, [at(cast, "ama", 0.64, k) for k in range(4)])
    settle(people)
    assert len(piles(people)) == 5                                  # the four are singles, apart from her six
    main = pile_of(people, 1)
    people.name(main, "Ama")
    settle(people)
    assert people.found_more["joined"] == 4 and people.person(main)["faces"] == 10 and len(piles(people)) == 1
    assert {r["how"] for r in people._all("SELECT how FROM photo_faces")} == {"auto"}
    store.close()


def test_naming_a_group_asks_about_faces_that_might_be_that_person_and_joins_none_of_them(world):
    lib, people, tmp = world
    cast = Cast(6)
    cast.person("ama")
    feed(lib, people, [cast.face("ama", noise=0.3) for _ in range(6)])
    maybes = feed(lib, people, [at(cast, "ama", 0.45, k) for k in range(4)])
    settle(people)
    assert len(piles(people)) == 5
    people.name(pile_of(people, 1), "Ama")
    settle(people)
    assert people.found_more["asked"] == 4 and people.found_more["joined"] == 0
    q = people.questions()
    assert len(q) == 1 and q[0]["person"]["name"] == "Ama" and {f["id"] for f in q[0]["faces"]} == {f[0] for _, f in maybes}
    assert people.person(pile_of(people, 1))["faces"] == 6
    people.move([f["id"] for f in q[0]["faces"]], pile_of(people, 1))                  # "yes, all of them"
    assert people.questions() == [] and people.person(pile_of(people, 1))["faces"] == 10


def test_a_look_alike_group_is_not_pulled_into_a_named_person(world):
    lib, people, tmp = world
    cast = Cast(7)
    cast.person("ama")
    cast.person("efua", like="ama", closeness=0.6)
    feed(lib, people, [cast.face("ama") for _ in range(6)])
    feed(lib, people, [cast.face("efua") for _ in range(6)])
    settle(people)
    ps = piles(people)
    assert len(ps) == 2
    people.name(pile_of(people, 1), "Ama")
    settle(people)
    assert people.person(pile_of(people, 1))["faces"] == 6 and len(piles(people)) == 2     # her look-alike is a person of her own


def test_a_face_the_person_placed_is_not_moved_by_a_name_given_later(tmp_path):
    store = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    lib = library.PhotoLibrary(store, {"faces": {"group": 0.8, "alone": 0.1}}, Gazetteer([]))
    people = lib.people
    cast = Cast(8)
    cast.person("ama")
    cast.person("zed")
    feed(lib, people, [cast.face("ama", noise=0.3) for _ in range(6)])
    feed(lib, people, [cast.face("zed", noise=0.3) for _ in range(4)])
    strays = feed(lib, people, [at(cast, "ama", 0.64, k) for k in range(3)])
    settle(people)
    main, zed = pile_of(people, 1), pile_of(people, 7)
    mine = strays[0][1][0]
    people.move([mine], zed)                                    # the person put it with Zed's faces: their word
    people.name(main, "Ama")
    settle(people)
    assert pile_of(people, mine) == zed and people._one("SELECT how FROM photo_faces WHERE id=?", (mine,))[0] == "you"
    assert people.found_more["joined"] == 2 and people.person(main)["faces"] == 8     # the other two stray faces did join her
    store.close()


def test_two_groups_with_a_face_in_the_same_photo_are_never_made_one(world):
    lib, people, tmp = world
    cast = Cast(9)
    cast.person("a")
    cast.person("b", like="a", closeness=0.97)                  # as alike as twins, and side by side in every photo
    for _ in range(8):
        feed(lib, people, [cast.face("a", noise=0.3), cast.face("b", noise=0.3)], per_photo=2)
    settle(people)
    ps = piles(people)
    assert len(ps) == 2
    a, b = sorted(ps)
    assert people._one("SELECT count(*) FROM people_nots WHERE kind='same' AND a=? AND b=?", (a, b))[0] == 1
    assert people.same_person() == []


def test_an_unsure_match_is_asked_about_not_given_a_name(world):
    lib, people, cast, names, ids = named_world(world, seed=6)
    people.name(ids["p0"], "Ama")
    settle(people)
    gal = people._gal()
    base = unit(sum(people.vectors.get("face", f, "photo_faces") for f in gal.exemplar_ids(ids["p0"])))
    rng = np.random.default_rng(3)
    off = unit(rng.normal(size=128))
    off = unit(off - (off @ base) * base)
    v = 0.47 * base + (1 - 0.47 ** 2) ** 0.5 * off
    ((pid, (fid,)),) = feed(lib, people, [v])
    assert people.person(ids["p0"])["faces"] == 6                     # not given to Ama
    asked = people.questions()
    assert len(asked) == 1 and asked[0]["person"]["name"] == "Ama" and [f["id"] for f in asked[0]["faces"]] == [fid]
    assert people._person_row(pile_of(people, fid))["state"] == "unnamed"
    # yes: it is Ama; kept as the person's own decision
    people.move([fid], ids["p0"])
    assert pile_of(people, fid) == ids["p0"] and people.questions() == []
    assert people._one("SELECT how FROM photo_faces WHERE id=?", (fid,))[0] == "you"


def test_a_no_is_remembered_and_the_face_is_not_offered_to_that_person_again(world):
    lib, people, cast, names, ids = named_world(world, seed=7)
    people.name(ids["p1"], "Kofi")
    settle(people)
    gal = people._gal()
    base = unit(sum(people.vectors.get("face", f, "photo_faces") for f in gal.exemplar_ids(ids["p1"])))
    off = unit(np.random.default_rng(4).normal(size=128))
    off = unit(off - (off @ base) * base)
    v = 0.46 * base + (1 - 0.46 ** 2) ** 0.5 * off
    ((_, (fid,)),) = feed(lib, people, [v])
    assert [q["person"]["name"] for q in people.questions()] == ["Kofi"]
    people.deny([fid], ids["p1"])
    assert people.questions() == []
    people.name(ids["p0"], "Ama")                 # anything that makes her look again at the other groups
    settle(people)
    people.look_again()
    assert people.questions() == [] or all(q["person"]["name"] != "Kofi" for q in people.questions())


def test_look_alikes_are_asked_about_and_the_wrong_name_is_not_given(world):
    lib, people, tmp = world
    cast = Cast(8)
    cast.person("tomi")
    cast.person("tayo", like="tomi", closeness=0.6)
    feed(lib, people, [cast.face(n) for n in ("tomi", "tayo") for _ in range(8)])
    settle(people)
    by = {}
    for r in people._all("SELECT id, person_id FROM photo_faces ORDER BY id"):
        by.setdefault(r["person_id"], []).append(r["id"])
    assert len(by) == 2
    first, second = sorted(by.values(), key=min)
    people.name(pile_of(people, first[0]), "Tomi")
    people.name(pile_of(people, second[0]), "Tayo")
    settle(people)
    fed = feed(lib, people, [cast.face(n) for n in ("tomi", "tayo") for _ in range(20)])
    wrong = 0
    for k, (_, (fid,)) in enumerate(fed):
        owner = "Tomi" if k < 20 else "Tayo"
        p = people._person_row(pile_of(people, fid))
        wrong += p["state"] == "named" and p["name"] != owner
    assert wrong <= 1


def test_a_name_is_trimmed_refused_when_empty_and_not_given_twice(world):
    lib, people, cast, names, ids = named_world(world, seed=9)
    with pytest.raises(library.PhotoError, match="Write their name"):
        people.name(ids["p0"], "   ")
    got = people.name(ids["p0"], "  Ama   Mensah ", "  my   cousin ")
    assert got["name"] == "Ama Mensah" and got["relation"] == "my cousin" and got["state"] == "named"
    with pytest.raises(library.PhotoError, match="already have someone called ama mensah"):
        people.name(ids["p1"], "ama mensah")
    assert people.name(ids["p0"], "Ama M.")["name"] == "Ama M."
    with pytest.raises(library.PhotoError, match="isn't there"):
        people.name(99999, "Nobody")
    assert people.unname(ids["p0"])["state"] == "unnamed"


# ---------------------------------------------------------------- merge, split, ignore

def test_merging_a_young_and_an_old_group_makes_one_person_recognised_at_both_ages(world):
    lib, people, tmp = world
    cast = Cast(10)
    cast.person("gran")
    feed(lib, people, [cast.face("gran", 0) for _ in range(8)])
    feed(lib, people, [cast.face("gran", 30) for _ in range(8)])
    settle(people)
    ps = piles(people)
    assert len(ps) == 2
    young, old = sorted(ps, key=lambda p: min(ps[p]))
    asked = people.same_person()
    assert len(asked) == 1 and {asked[0]["a"]["id"], asked[0]["b"]["id"]} == {young, old}
    people.name(young, "Gran")
    stay = people.merge(young, old)
    assert stay["id"] == young and stay["name"] == "Gran" and stay["faces"] == 16
    assert people._person_row(old) is None and people.same_person() == []
    ((_, (fid,)),) = feed(lib, people, [cast.face("gran", 30)])
    assert pile_of(people, fid) == young                     # recognised in an old photo now


def test_merging_keeps_the_name_whichever_way_round_and_two_names_keep_the_one_you_chose(world):
    lib, people, cast, names, ids = named_world(world, seed=11)
    people.name(ids["p0"], "Ama")
    stay = people.merge(ids["p1"], ids["p0"])             # the unnamed one "kept": the name still wins
    assert stay["id"] == ids["p0"] and stay["name"] == "Ama"
    people.name(ids["p2"], "Kofi")
    stay = people.merge(ids["p2"], ids["p0"])             # two names: the one you kept
    assert stay["id"] == ids["p2"] and stay["name"] == "Kofi" and people._person_row(ids["p0"]) is None
    with pytest.raises(library.PhotoError, match="two different"):
        people.merge(ids["p2"], ids["p2"])


def test_splitting_two_people_who_were_mixed_up_keeps_them_apart_for_good(world):
    lib, people, tmp = world
    cast = Cast(12)
    cast.person("a")
    cast.person("b", like="a", closeness=0.97)                 # as alike as twins: one pile
    fed = feed(lib, people, [cast.face("a", noise=0.3) for _ in range(6)] + [cast.face("b", noise=0.3) for _ in range(6)])
    settle(people)
    ps = piles(people)
    assert len(ps) == 1
    (pid,) = ps
    mine = [f[0] for _, f in fed[6:]]
    new = people.split(pid, mine)
    assert new["faces"] == 6 and people.person(pid)["faces"] == 6
    assert {pile_of(people, f) for f in mine} == {new["id"]}
    feed(lib, people, [cast.face("a", noise=0.3) for _ in range(3)] + [cast.face("b", noise=0.3) for _ in range(3)])
    settle(people)
    assert people._person_row(pid) is not None and people._person_row(new["id"]) is not None
    assert people.same_person() == [] or all({x["a"]["id"], x["b"]["id"]} != {pid, new["id"]} for x in people.same_person())
    with pytest.raises(library.PhotoError, match="all of them"):
        people.split(pid, [r["id"] for r in people._all("SELECT id FROM photo_faces WHERE person_id=?", (pid,))])
    with pytest.raises(library.PhotoError, match="Pick some faces"):
        people.split(pid, [])
    with pytest.raises(library.PhotoError, match="aren't in this group"):
        people.split(pid, mine)


def test_a_face_moved_out_of_a_named_person_is_marked_not_them(world):
    lib, people, cast, names, ids = named_world(world, seed=13)
    people.name(ids["p0"], "Ama")
    settle(people)
    fid = min(r["id"] for r in people._all("SELECT id FROM photo_faces WHERE person_id=?", (ids["p0"],)))
    people.move([fid], ids["p1"])
    assert people._one("SELECT count(*) FROM people_nots WHERE kind='face' AND a=? AND b=?", (fid, ids["p0"]))[0] == 1
    assert pile_of(people, fid) == ids["p1"]
    people.request_look_again()
    settle(people)
    assert pile_of(people, fid) == ids["p1"]      # you put it there: nothing moves it


def test_a_set_aside_group_takes_similar_faces_quietly_and_can_be_brought_back(world):
    lib, people, cast, names, ids = named_world(world, seed=14)
    stay = people.ignore(ids["p2"])
    assert stay["state"] == "ignored" and people.ignored()[0]["id"] == ids["p2"]
    fed = feed(lib, people, [cast.face("p2") for _ in range(5)])
    mine = [pile_of(people, f[0]) for _, f in fed]
    assert mine.count(ids["p2"]) >= 4 and people.questions() == []
    assert people.status()["ignored"] == 1
    assert people.unignore(ids["p2"])["state"] == "unnamed"
    people.name(ids["p0"], "Ama")
    with pytest.raises(library.PhotoError, match="They have a name"):
        people.ignore(ids["p0"])
    with pytest.raises(library.PhotoError, match="isn't set aside"):
        people.unignore(ids["p1"])


def test_a_person_can_say_two_groups_are_not_one_and_is_not_asked_again(world):
    lib, people, tmp = world
    cast = Cast(15)
    cast.person("x")
    feed(lib, people, [cast.face("x", 0) for _ in range(6)])
    feed(lib, people, [cast.face("x", 35) for _ in range(6)])
    settle(people)
    (a, b) = sorted(piles(people))
    assert len(people.same_person()) == 1
    people.not_same(a, b)
    assert people.same_person() == []


# ---------------------------------------------------------------- reading what is there

def test_the_lists_the_page_shows(world):
    lib, people, cast, names, ids = named_world(world, seed=16, per=5)
    feed(lib, people, cast.strangers(4))
    people.name(ids["p0"], "Ama")
    settle(people)
    g = people.groups()
    assert g["total"] == 2 and g["singles"] == 4 and all(len(x["samples"]) >= 1 for x in g["items"])
    assert people.groups(min_faces=1)["total"] == 6
    assert [p["name"] for p in people.people()] == ["Ama"] and people.people()[0]["photos"] == 5
    assert people.faces_of(ids["p0"], limit=2)["more"] is True
    assert len(people.photo_ids(ids["p0"])) == 5
    st = people.status()
    assert st["named"] == 1 and st["groups"] == 6 and st["faces"] == 19 and st["usable"] == 19


def test_who_is_in_a_photo_and_browsing_by_person(world):
    lib, people, cast, names, ids = named_world(world, seed=17, per=4)
    people.name(ids["p0"], "Ama")
    (pid,) = photo(lib)
    people.add(pid, [found(cast.face("p0"), (0.1, 0.2, 0.2, 0.25)), found(cast.strangers(1)[0], (0.5, 0.2, 0.2, 0.25)), unclear()], TAG)
    who = people.in_photo(pid)
    assert who["named"][0]["name"] == "Ama" and who["unnamed"] == 1 and who["unclear"] == 1 and len(who["faces"]) == 2
    got = lib.get(pid)["people"]
    assert got["named"][0]["name"] == "Ama"
    assert lib.list(person=ids["p0"])["total"] == 5 and lib.ids(person=ids["p0"])
    assert lib.list(person=999999)["total"] == 0
    assert lib.get(photo(lib)[0])["people"] is None


# ---------------------------------------------------------------- reading again, other models

def test_a_photo_read_again_keeps_what_the_person_said_about_it(world):
    lib, people, cast, names, ids = named_world(world, seed=18)
    people.name(ids["p0"], "Ama")
    (pid,) = photo(lib)
    v = cast.face("p0", 20, noise=1.0)
    people.add(pid, [found(v)], TAG)
    fid = people._one("SELECT id FROM photo_faces WHERE photo_id=?", (pid,))[0]
    people.move([fid], ids["p1"])                                  # the person put it with someone else
    people.add(pid, [found(v, (0.31, 0.21, 0.2, 0.25))], "another-model")        # read again, the box a little different
    now = people._one("SELECT person_id, how FROM photo_faces WHERE photo_id=?", (pid,))
    assert now["person_id"] == ids["p1"] and now["how"] == "you"
    assert people._one("SELECT count(*) FROM photo_faces WHERE photo_id=?", (pid,))[0] == 1


def test_a_new_model_drops_the_old_fingerprints_and_its_photos_are_read_again(world):
    lib, people, cast, names, ids = named_world(world, seed=19, per=3)
    assert people.needing(TAG, 100) == []                       # every photo has been read by this model
    assert people.vectors.count("face", "photo_faces") == 9
    assert people.use_model("other-model") == 9
    assert people.vectors.count("face", "photo_faces") == 0
    assert len(people.needing("other-model", 100)) == 9
    assert people._gal().persons() == []                         # and nothing is compared with fingerprints of another model


def test_photos_with_nothing_to_read_are_not_asked_for_again(world):
    lib, people, tmp = world
    ps = photo(lib, 3)
    assert len(people.needing(TAG, 10)) == 3
    people.skip(ps[0], TAG)
    people.add(ps[1], [], TAG)
    assert [r["id"] for r in people.needing(TAG, 10)] == [ps[2]]


# ---------------------------------------------------------------- taking faces away, for good

def vector_bytes(v) -> bytes:
    return np.asarray(v, dtype="<f4").tobytes()[:64]


def file_bytes(lib) -> bytes:
    lib.db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    path = lib.db.path
    return b"".join(p.read_bytes() for p in (path, path.with_name(path.name + "-wal")) if p.exists())


def test_deleting_a_person_removes_their_faces_fingerprints_pictures_and_links_for_good(world):
    lib, people, tmp = world
    cast = Cast(20)
    cast.person("gone")
    cast.person("stays")
    mine = [cast.face("gone", noise=0.3) for _ in range(6)]
    feed(lib, people, mine + [cast.face("stays") for _ in range(6)])
    settle(people)
    ps = piles(people)
    gone = pile_of(people, 1)
    people.name(gone, "Gone")
    ids = sorted(ps[gone])
    crops = [people.crop_path(i) for i in ids]
    assert all(c.exists() for c in crops) and vector_bytes(unit(mine[0])) in file_bytes(lib)
    photos_before = lib._one("SELECT count(*) FROM photos")[0]
    out = people.remove_person(gone)
    assert out["faces"] == 6
    assert people._person_row(gone) is None and people.vectors.count("face", "photo_faces") == 6
    assert people._one("SELECT count(*) FROM photo_faces WHERE person_id=?", (gone,))[0] == 0
    assert not any(c.exists() for c in crops)
    assert lib._one("SELECT count(*) FROM vectors WHERE space='face'")[0] == 6
    assert lib._one("SELECT count(*) FROM photos")[0] == photos_before          # the photos stay
    assert all(vector_bytes(unit(v)) not in file_bytes(lib) for v in mine)      # nor is a fingerprint left in the file
    assert people.name(pile_of(people, 7), "Stays")["name"] == "Stays"          # and the other person is untouched
    # not read for faces again unless asked
    assert people.needing(TAG, 100) == []


def test_deleting_a_person_also_removes_them_from_the_copies_made_before_a_format_change(world):
    lib, people, tmp = world
    cast = Cast(21)
    cast.person("gone")
    mine = [cast.face("gone", noise=0.3) for _ in range(4)]
    feed(lib, people, mine)
    gone = pile_of(people, 1)
    people.name(gone, "Gone")
    backup = Path(lib.db.backups) / "memory-v3-20250101-000000.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(backup)
    lib.db.conn().backup(dest)
    dest.close()
    assert vector_bytes(unit(mine[0])) in backup.read_bytes()
    people.remove_person(gone)
    data = b"".join(p.read_bytes() for p in backup.parent.glob("memory-v3*"))
    assert all(vector_bytes(unit(v)) not in data for v in mine)
    c = sqlite3.connect(backup)
    assert c.execute("SELECT count(*) FROM photo_faces").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM people WHERE name='Gone'").fetchone()[0] == 0
    c.close()


def test_delete_all_face_data_leaves_nothing_and_turns_the_switch_off(world):
    lib, people, cast, names, ids = named_world(world, seed=22)
    people.name(ids["p0"], "Ama")
    people.set_enabled(True)
    sample = people.vectors.get("face", 1, "photo_faces")
    crops = list((lib.dir / "faces").rglob("*.jpg"))
    assert crops and people.status()["faces"] == 18
    out = people.delete_all()
    assert out["faces"] == 18 and people.enabled() is False
    for table in ("photo_faces", "people", "people_nots"):
        assert lib._one(f"SELECT count(*) FROM {table}")[0] == 0
    assert lib._one("SELECT count(*) FROM vectors WHERE space='face'")[0] == 0
    assert not (lib.dir / "faces").exists() and vector_bytes(sample) not in file_bytes(lib)
    assert lib._one("SELECT count(*) FROM photos WHERE faces_model<>''")[0] == 0     # switched on again, they would be read again
    assert people.status()["faces"] == 0 and people.people() == []


def test_removing_a_photo_removes_its_faces_and_empty_groups(world):
    lib, people, cast, names, ids = named_world(world, seed=23, per=3)
    people.name(ids["p0"], "Ama")
    fid = min(r["id"] for r in people._all("SELECT id FROM photo_faces WHERE person_id=?", (ids["p0"],)))
    pid = people._one("SELECT photo_id FROM photo_faces WHERE id=?", (fid,))[0]
    lib.remove([pid])
    assert people._one("SELECT count(*) FROM photo_faces WHERE id=?", (fid,))[0] == 0
    assert not people.crop_path(fid).exists() and people.vectors.get("face", fid, "photo_faces") is None
    assert people.person(ids["p0"])["faces"] == 2
    for other in [r["photo_id"] for r in people._all("SELECT photo_id FROM photo_faces WHERE person_id=?", (ids["p1"],))]:
        lib.remove([other])
    assert people._person_row(ids["p1"]) is None                    # nobody left in an unnamed group: it is gone
    assert people.person(ids["p0"])["state"] == "named"


# ---------------------------------------------------------------- the records

def test_every_record_carries_the_owner_and_library_and_another_owners_are_not_seen(world):
    lib, people, cast, names, ids = named_world(world, seed=24, per=3)
    people.name(ids["p0"], "Ama")
    for table in ("photo_faces", "people", "people_nots"):
        assert lib._one(f"SELECT count(*) FROM {table} WHERE owner<>? OR library<>?", (lib.owner, lib.lib))[0] == 0
    assert lib._one("SELECT min(fmt) FROM photo_faces")[0] == 1 and lib._one("SELECT min(fmt) FROM people")[0] == 1
    lib.store.owner = "someone-else"
    assert people.people() == [] and people.groups()["total"] == 0 and people.status()["faces"] == 0


def test_the_worker_and_the_page_can_work_at_the_same_time(world):
    lib, people, cast, names, ids = named_world(world, seed=25)
    errors = []

    def worker():
        try:
            for _ in range(10):
                feed(lib, people, [cast.face("p0") for _ in range(3)])
                people.maintain()
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    def page():
        try:
            people.name(ids["p0"], "Ama")
            for _ in range(10):
                people.people(), people.groups(), people.questions(), people.same_person(), people.status()
                people.person(ids["p0"])
            people.merge(ids["p1"], ids["p2"])
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker), threading.Thread(target=page)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert errors == [] and not any(t.is_alive() for t in ts)
