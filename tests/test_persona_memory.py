"""Tests for persona memories: linking a persona to a person, age-based recall,
self-memory tagging, and cleanup on person delete/merge (Phase 6)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from mary.avatar import store as avatar_store
from mary.memory import recall
from mary.memory.embed import HashEmbedder
from mary.memory.store import MemoryStore
from mary.photos import library, synth
from mary.photos.library import PhotoError, PhotoLibrary


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    user_root = tmp_path / "user"
    user_root.mkdir(parents=True)
    import mary.config
    monkeypatch.setattr(mary.config, "user_dir", lambda: user_root)
    monkeypatch.setattr(avatar_store, "user_dir", lambda: user_root)

    root = tmp_path / "Pictures"
    store = MemoryStore(
        user_root / "memory" / "memory.db",
        embedder=HashEmbedder(),
        backups=tmp_path / "b",
    )
    lib = PhotoLibrary(store, {"read_text": False})
    truths, cast, face_eng, extra = synth.populate_moments_library(lib, root)

    from mary.photos import ingest
    ing = ingest.Ingest(lib, engine=face_eng, clip_engine=lib.clip)
    ing.scan()
    ing.drain()
    lib.maintain_moments()
    ing.stop()

    yield root, truths, cast, lib, store
    store.close()


def test_persona_link_crud(world):
    _, _, _, lib, _ = world
    # Find an unnamed group or create one
    unnamed = lib.people.groups().get("groups") or []
    if not unnamed:
        with lib.db.tx() as c:
            now_iso = datetime.now().isoformat()
            pid_unnamed = c.execute(
                "INSERT INTO people(owner, library, name, state, created, updated) VALUES (?,?,'','unnamed',?,?)",
                (lib.owner, lib.lib, now_iso, now_iso),
            ).lastrowid
    else:
        pid_unnamed = unnamed[0]["id"]

    with pytest.raises(PhotoError, match="given a name to"):
        lib.link_persona("mary", pid_unnamed)

    # Name the person
    lib.people.name(pid_unnamed, "Alice")

    # Invalid birth year
    with pytest.raises(PhotoError, match="born"):
        lib.link_persona("mary", pid_unnamed, born=1800)

    # Valid link
    link = lib.link_persona("mary", pid_unnamed, born=1950)
    assert link is not None
    assert link["persona"] == "mary"
    assert link["person_id"] == pid_unnamed
    assert link["name"] == "Alice"
    assert link["born"] == 1950

    # Read back
    assert lib.persona_link("mary") == link

    # Unlink
    assert lib.link_persona("mary", None) is None
    assert lib.persona_link("mary") is None


def test_persona_age_query_and_self_memory_recall(world):
    _, _, _, lib, store = world
    lib.set_self("mary")

    # Find Grandma in the library
    grandma = next(p for p in lib.people.people() if p["name"] == "Grandma")
    gid = grandma["id"]

    # Grandma was at the wedding in June 1998. If born in 1938, she turned 60 in 1998.
    lib.link_persona("mary", gid, born=1938)
    assert lib.self_person()["born"] == 1938

    # 1. Search photos with age query
    res = lib.search_photos("Do you remember your 60th birthday?", now=datetime(2026, 11, 2))
    assert res["photos"] or res["moments"]
    assert "1998" in (res.get("when_label") or "")

    # 2. Moments describe_for_context contains self-memory instruction
    moments = res.get("moments")
    assert moments
    top_moment = moments[0]
    ctx_desc = lib.moments_store.describe_for_context(top_moment)
    # The persona's own person is named and marked as her; nobody else is, and no bare "you are in this" is left for the
    # model to read as the listener.
    assert "Grandma (= you, the assistant: say 'me')" in ctx_desc
    assert "Kofi (= you" not in ctx_desc and "you are in this" not in ctx_desc

    # 3. Unprompted recall in recall.lines() brings the moment into context with self-memory instruction
    ctx_lines = recall.lines(store, "Remember your 60th birthday?", now=datetime(2026, 11, 2))
    assert any("Grandma (= you, the assistant: say 'me')" in ln for ln in ctx_lines)


def test_persona_unmatched_age_query_honesty(world):
    _, _, _, lib, _ = world
    lib.set_self("mary")

    grandma = next(p for p in lib.people.people() if p["name"] == "Grandma")
    lib.link_persona("mary", grandma["id"], born=1938)

    # When she was 20 (year 1958) -> no photos exist from 1958
    res = lib.search_photos("when you were 20", now=datetime(2026, 11, 2))
    assert not res["photos"]
    assert not res["moments"]
    assert "1958" in res["when_label"]
    assert "no photos from 1958" in res["empty_reason"]


def test_person_delete_and_merge_cascade(world, tmp_path: Path):
    _, _, _, lib, _ = world
    named = lib.people.people()
    assert len(named) >= 2
    pid1 = named[0]["id"]
    pid2 = named[1]["id"]

    lib.link_persona("mary", pid1, born=1945)
    assert lib.persona_link("mary")["person_id"] == pid1

    # Merge pid1 into pid2
    lib.people.merge(pid2, pid1)
    # Link updated to target person
    assert lib.persona_link("mary")["person_id"] == pid2

    # Remove person pid2 -> wipes persona link
    lib.people.remove_person(pid2)
    assert lib.persona_link("mary") is None


def test_persona_conversation_replay_spec():
    import yaml
    from mary.config import PROJECT_DIR

    conv_path = PROJECT_DIR / "tests" / "conversations" / "persona_memory.yaml"
    conv = yaml.safe_load(conv_path.read_text(encoding="utf-8"))
    assert conv["name"] == "Persona memories in conversation"
    assert "photos" in conv["real"] and conv["photos"] == "moments_set"
    assert conv["persona_link"]["person"] == "Grandma"
    assert conv["persona_link"]["born"] == 1938
    assert len(conv["turns"]) == 2

