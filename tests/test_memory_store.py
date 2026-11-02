"""The memory store: records, keywords, vectors, migrations, and forgetting versus deleting for good."""
import threading
from datetime import datetime, timedelta

import numpy as np
import pytest

from mary.memory import clock, db as dbmod
from mary.memory.embed import HashEmbedder
from mary.memory.store import MemoryStore, core_key, memory_id
from mary.memory.vectors import VectorIndex


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory" / "memory.db", embedder=HashEmbedder(), backups=tmp_path / "backups")
    yield s
    s.close()
    clock.reset()


def test_add_get_list_count_days(store):
    a = store.add("fact", "Marcus is my friend", importance=0.8, source="user")
    b = store.add("diary", "Asked for the weather", happened=datetime(2026, 10, 1, 9, 0))
    assert store.get(a.id).text == "Marcus is my friend" and a.owner == store.owner and a.library == store.library
    assert [m.id for m in store.list(kinds=("fact",))] == [a.id]
    assert store.count() == 2 and store.count(("diary",)) == 1
    assert store.days()[0][0].isoformat() == "2026-10-01"
    assert memory_id("#7") == 7 and memory_id(" 7 ") == 7 and memory_id("seven") is None
    assert b.current and b.as_dict()["kind"] == "diary"


def test_text_is_cleaned_and_empty_text_refused(store):
    assert store.add("fact", "  My   sister \n is Ama ").text == "My sister is Ama"
    with pytest.raises(ValueError):
        store.add("fact", "   ")


def test_ending_keeps_the_record_with_a_date_and_a_reason(store):
    old = store.add("fact", "I live in Lisbon")
    new = store.add("fact", "I live in Porto")
    ended = store.end(old.id, "superseded", superseded_by=new.id)
    assert not ended.current and ended.ended_why == "superseded" and ended.superseded_by == new.id
    assert [m.text for m in store.list(kinds=("fact",))] == ["I live in Porto"]
    assert [m.text for m in store.list(kinds=("fact",), current=False)] == ["I live in Lisbon"]
    assert store.end(old.id, "forgotten").ended_why == "superseded"   # ending twice changes nothing


def test_delete_removes_the_record_its_keywords_and_its_vector(store):
    m = store.add("fact", "the vault code is zzqxunique")
    store.flush()
    assert store.vectors.count("text") == 1
    assert store.search("zzqxunique", kinds=("fact",))
    store.delete(m.id)
    assert store.get(m.id) is None and store.vectors.count("text") == 0
    assert store.search("zzqxunique", kinds=("fact",)) == []
    assert store.db.conn().execute("SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'zzqxunique'").fetchone()[0] == 0


def test_a_deleted_memory_is_gone_from_the_file_itself(tmp_path):
    path = tmp_path / "memory.db"
    s = MemoryStore(path, embedder=HashEmbedder(), backups=tmp_path / "b")
    keep = s.add("fact", "the spare key lives under the blue pot")
    gone = s.add("fact", "my locker combination is qqzzmarker")
    s.flush()
    assert b"qqzzmarker" in b"".join(p.read_bytes() for p in tmp_path.glob("memory.db*"))
    s.delete(gone.id)
    s.close()
    assert b"qqzzmarker" not in b"".join(p.read_bytes() for p in tmp_path.glob("memory.db*"))
    again = MemoryStore(path, embedder=HashEmbedder(), backups=tmp_path / "b")
    assert again.get(keep.id) is not None
    again.close()


def test_records_belong_to_an_owner_and_a_library(store):
    mine = store.add("fact", "I like tea")
    store.owner = "someone-else"
    assert store.get(mine.id) is None and store.count() == 0 and store.search("tea") == []
    other = store.add("fact", "I like coffee")
    store.owner = mine.owner
    assert store.get(other.id) is None and store.get(mine.id) is not None


def test_meta_has_ids_and_a_format_number(store):
    assert len(store.owner) >= 16 and len(store.library) >= 16 and store.owner != store.library
    assert store.db.meta("format") == "1"
    assert store.db.conn().execute("PRAGMA user_version").fetchone()[0] == len(dbmod.MIGRATIONS)


def test_the_same_file_opens_again_with_the_same_ids(tmp_path):
    a = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    owner = a.owner
    a.add("fact", "x marks the spot")
    a.close()
    b = MemoryStore(tmp_path / "m.db", backups=tmp_path / "b")
    assert b.owner == owner and b.count() == 1
    b.close()


def test_a_file_from_a_newer_mary_is_refused_without_harm(tmp_path):
    path = tmp_path / "m.db"
    MemoryStore(path, backups=tmp_path / "b").close()
    import sqlite3

    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version = 99")
    c.close()
    before = path.read_bytes()
    with pytest.raises(dbmod.MemoryFormatError, match="newer MARY"):
        MemoryStore(path, backups=tmp_path / "b")
    assert path.read_bytes() == before


def test_a_migration_keeps_the_data_and_makes_a_backup_first(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    s = MemoryStore(path, backups=tmp_path / "b")
    s.add("fact", "survives the move")
    s.close()
    assert not list((tmp_path / "b").glob("*.db"))   # a brand new file needs no backup

    def v2(c):
        c.execute("ALTER TABLE memory ADD COLUMN mood TEXT")

    before = len(dbmod.MIGRATIONS)
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, v2])
    s = MemoryStore(path, backups=tmp_path / "b")
    assert s.get(1).text == "survives the move"
    assert s.db.conn().execute("PRAGMA user_version").fetchone()[0] == before + 1
    saved = list((tmp_path / "b").glob(f"memory-v{before}-*.db"))
    assert len(saved) == 1
    s.close()
    import sqlite3

    old = sqlite3.connect(saved[0])
    assert old.execute("PRAGMA user_version").fetchone()[0] == before   # the copy is the file as it was
    assert old.execute("SELECT text FROM memory").fetchone()[0] == "survives the move"
    old.close()


def test_backups_keep_only_the_last_few(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    MemoryStore(path, backups=tmp_path / "b").close()
    migrations = list(dbmod.MIGRATIONS)
    for n in range(len(migrations) + 1, len(migrations) + 8):
        migrations.append(lambda c, n=n: c.execute(f"ALTER TABLE memory ADD COLUMN extra{n} TEXT"))
        monkeypatch.setattr(dbmod, "MIGRATIONS", list(migrations))
        MemoryStore(path, backups=tmp_path / "b").close()
        import time as _t; _t.sleep(1.05)   # backup names carry the second
    assert len(list((tmp_path / "b").glob("memory-v*.db"))) == dbmod.KEEP_BACKUPS


# ---------------------------------------------------------------- vectors


def _unit(rng, n, dim=16):
    m = rng.normal(size=(n, dim)).astype(np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def test_vector_index_matches_a_brute_force_search_through_adds_and_deletes(tmp_path):
    d = dbmod.Database(tmp_path / "v.db")
    index, rng = VectorIndex(d), np.random.default_rng(1)
    alive = {}
    for step in range(300):   # past the first growth, with deletes mixed in
        if alive and rng.random() < 0.3:
            gone = int(rng.choice(list(alive)))
            index.delete("memory", gone)
            alive.pop(gone)
        else:
            vec = _unit(rng, 1)[0]
            index.put("text", step + 1, vec, "m")
            alive[step + 1] = vec
    q = _unit(rng, 1)[0]
    want = sorted(((float(v @ q), i) for i, v in alive.items()), reverse=True)[:5]
    got = index.top("text", q, 5)
    assert [i for i, _ in got] == [i for _, i in want]
    assert index.count("text") == len(alive)
    index.reload()   # and from the file
    assert [i for i, _ in index.top("text", q, 5)] == [i for _, i in want]
    d.close()


def test_vector_search_can_be_limited_to_some_records(tmp_path):
    d = dbmod.Database(tmp_path / "v.db")
    index, rng = VectorIndex(d), np.random.default_rng(2)
    vecs = _unit(rng, 10)
    for i, v in enumerate(vecs):
        index.put("text", i + 1, v, "m")
    got = index.top("text", vecs[0], 3, allowed=np.array([4, 5, 6]))
    assert {i for i, _ in got} == {4, 5, 6}
    assert index.top("text", vecs[0], 3, allowed=np.zeros(0, dtype=np.int64)) == []
    d.close()


def test_spaces_are_separate(tmp_path):
    d = dbmod.Database(tmp_path / "v.db")
    index, rng = VectorIndex(d), np.random.default_rng(3)
    index.put("text", 1, _unit(rng, 1, 8)[0], "t")
    index.put("face", 1, _unit(rng, 1, 32)[0], "f")   # another size, another model: no clash
    assert index.count("text") == 1 and index.count("face") == 1
    index.delete("memory", 1)
    assert index.count("text") == 0 and index.count("face") == 0
    d.close()


def test_vectors_of_another_model_are_dropped_and_made_again(tmp_path):
    path = tmp_path / "m.db"
    s = MemoryStore(path, embedder=HashEmbedder(64, "old"), backups=tmp_path / "b")
    s.add("fact", "my sister is Ama")
    s.flush()
    assert s.vectors.count("text") == 1
    s.close()
    s = MemoryStore(path, embedder=HashEmbedder(128, "new"), backups=tmp_path / "b")
    assert s.vectors.count("text") == 0
    s.flush()
    assert s.vectors.count("text") == 1
    s.close()


# ---------------------------------------------------------------- facts


def test_a_fact_said_twice_is_kept_once_and_keeps_the_higher_importance(store):
    first, status = store.add_fact("My sister's name is Ama", importance=0.5)
    again, status2 = store.add_fact("my sister's name is ama.", importance=0.9)
    assert (status, status2) == ("saved", "already") and again.id == first.id
    assert store.get(first.id).importance == 0.9 and store.count(("fact",)) == 1


def test_a_changed_fact_ends_the_old_one_by_the_id_the_caller_named(store):
    old, _ = store.add_fact("I live in Lisbon")
    new, status = store.add_fact("I live in Porto", replaces=f"#{old.id}")
    assert status == "replaced"
    assert store.get(old.id).ended_why == "superseded" and store.get(old.id).superseded_by == new.id
    assert [m.text for m in store.list(kinds=("fact",))] == ["I live in Porto"]
    # a made-up id changes nothing
    other, status = store.add_fact("I work at the museum", replaces="#999")
    assert status == "saved" and store.count(("fact",)) == 2


def test_replacing_never_ends_a_diary_line(store):
    line = store.add("diary", "Asked for the time")
    _, status = store.add_fact("I like jazz", replaces=line.id)
    assert status == "saved" and store.get(line.id).current


def test_core_records_follow_the_profile(store):
    store.sync_core(["I prefer Edge", "My car is a Toyota"])
    assert {m.text for m in store.list(kinds=("core",))} == {"I prefer Edge", "My car is a Toyota"}
    store.sync_core(["I prefer Edge", "I own a bike"])
    assert {m.text for m in store.list(kinds=("core",))} == {"I prefer Edge", "I own a bike"}
    gone = store.by_key("core", core_key("My car is a Toyota"), current=False)
    assert gone is not None and not gone.current
    store.sync_core(["I prefer Edge", "I own a bike"])   # nothing new: nothing added
    assert store.count(("core",), None) == 3


def test_editing_makes_the_vector_again(store):
    m = store.add("fact", "I like tea")
    store.flush()
    before = store.vectors.get("text", m.id)
    store.edit(m.id, "I like strong coffee")
    store.flush()
    after = store.vectors.get("text", m.id)
    assert store.get(m.id).text == "I like strong coffee" and not np.allclose(before, after)
    assert store.search("coffee", kinds=("fact",))[0].memory.id == m.id and store.search("tea", kinds=("fact",)) == []


def test_several_threads_can_write_at_once(store):
    def work(n):
        for i in range(25):
            store.add("diary", f"line {n}-{i}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert store.count(("diary",)) == 100
    store.flush()
    assert store.vectors.count("text") == 100


def test_stats_are_only_counts(store):
    store.add("fact", "a secret fact")
    stats = store.stats()
    assert stats["fact"] == {"current": 1, "ended": 0} and "secret" not in str(stats)


def test_the_clock_can_live_several_days(store):
    t0 = clock.now().replace(microsecond=0)   # records keep whole seconds
    clock.advance(days=2)
    assert clock.now() - t0 >= timedelta(days=2)
    assert store.add("diary", "two days on").happened - t0 >= timedelta(days=2)


def test_a_file_missing_a_column_of_an_earlier_format_is_brought_level(tmp_path):
    import sqlite3

    path = tmp_path / "m.db"
    MemoryStore(path, backups=tmp_path / "b").close()
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE photos DROP COLUMN exif_taken")      # what a file made by an unfinished draft of format 2 looks like
    c.execute(f"PRAGMA user_version = {len(dbmod.MIGRATIONS) - 1}")
    c.close()
    s = MemoryStore(path, backups=tmp_path / "b")
    assert "exif_taken" in {r[1] for r in s.db.conn().execute("PRAGMA table_info(photos)")}
    s.close()


def test_v5_migration_and_column_repair_bring_older_photo_and_moment_tables_level(tmp_path):
    import sqlite3

    path = tmp_path / "m.db"
    c = sqlite3.connect(path)
    dbmod._v1(c)
    dbmod._v2(c)
    dbmod._v3(c)
    dbmod._v4(c)
    c.execute("INSERT INTO photos(owner, library, sha, dhash, width, height, text, caption, album, place, region, country, added, updated) "
              "VALUES ('o', 'l', 's1', '0000000000000000', 640, 480, 'CAFE', 'holiday', 'Trip', 'Harbourton', '', 'Ghanaland', '2025-01-01', '2025-01-01')")
    c.execute("PRAGMA user_version = 4")
    c.commit()
    c.close()

    # Opening upgrades v4 -> v5, preserves the photo, adds all v5 columns, creates moments tables, and rebuilds photos_fts
    s = MemoryStore(path, backups=tmp_path / "b")
    cols = {r[1] for r in s.db.conn().execute("PRAGMA table_info(photos)")}
    assert {"sharpness", "image_model", "seen_model", "seen_raw", "seen_faces", "seen", "tags"} <= cols
    tables = {r[0] for r in s.db.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"moments", "moment_photos", "moment_nots"} <= tables
    assert s.db.conn().execute("SELECT count(*) FROM photos_fts WHERE photos_fts MATCH 'holiday'").fetchone()[0] == 1
    s.close()

    # Even if an already-v5 database loses columns from photos or moments, opening repairs them automatically
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE photos DROP COLUMN seen")
    c.execute("ALTER TABLE photos DROP COLUMN image_model")
    c.execute("ALTER TABLE moments DROP COLUMN theme")
    c.execute("ALTER TABLE moments DROP COLUMN mentions")
    c.execute("ALTER TABLE moments DROP COLUMN last_mentioned")
    c.commit()
    c.close()
    s = MemoryStore(path, backups=tmp_path / "b")
    assert {"seen", "image_model"} <= {r[1] for r in s.db.conn().execute("PRAGMA table_info(photos)")}
    assert {"theme", "mentions", "last_mentioned"} <= {r[1] for r in s.db.conn().execute("PRAGMA table_info(moments)")}
    s.close()

