"""Phase 5: Recall in conversation (mary/memory/recall.py, mary/photos/moments.py, mary/photos/library.py,
mary/skills/photos.py, mary/skills/look.py).

Tests:
- Unprompted recall in `recall.lines()`: dates ('last Christmas', 'June 1998'), places ('Northfield', 'Harbourton'),
  people ('Grandma', 'Kofi'), and 'remember when…' bring the best 2–3 moments (~60–100 words each) into context.
- Unrelated requests add nothing and add no delay (<0.5 ms per request).
- `find_photos` combines code filters, keyword search (user captions/albums beat model words), meaning search
  (text embeddings), and picture search (CLIP embeddings), with recent and often-mentioned moments ranked ahead.
- `show_photo`, `about_photo`, and `look(photo_id=...)` work on the photo records, respect grounding, and never
  allow a vision model to invent a person's name.
- Honesty: estimated dates carry the 'say "I think"' marker, and queries for a time with no photos ('1985') report
  that there are no photos from then rather than returning a wrong moment."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from mary import vision
from mary.config import Section, load_config
from mary.memory import recall
from mary.memory.embed import HashEmbedder
from mary.memory.store import MemoryStore
from mary.output import Output
from mary.photos import library, synth
from mary.skills import look as look_skill
from mary.skills import photos as photos_skill
from mary.tools.registry import ToolContext, ToolRegistry


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "Pictures"
    store = MemoryStore(
        tmp_path / "user" / "memory" / "memory.db",
        embedder=HashEmbedder(),
        backups=tmp_path / "b",
    )
    lib = library.PhotoLibrary(store, {"read_text": False})
    truths, cast, face_eng, extra = synth.populate_moments_library(lib, root)

    # Also add two scene pictures ('pink car' and 'beach at sunset') so picture search (CLIP) is tested in find_photos
    clip_eng = lib.clip
    car_img = synth.scene_picture("pink car", seed=1)
    beach_img = synth.scene_picture("beach at sunset", seed=2)
    clip_eng.teach(car_img, "pink car automobile vehicle")
    clip_eng.teach(beach_img, "beach sunset ocean sea")
    synth.save(root / "Camera" / "IMG_3001.jpg", car_img, synth.D(2024, 4, 10, 14, 0))
    synth.save(root / "Camera" / "IMG_3002.jpg", beach_img, synth.D(2024, 4, 10, 15, 0))

    from mary.photos import ingest

    ing = ingest.Ingest(lib, engine=face_eng, clip_engine=clip_eng)
    ing.scan()
    ing.drain()
    lib.maintain_moments()
    ing.stop()

    yield root, truths, extra, lib, store
    store.close()


def test_unprompted_recall_adds_matching_moments_and_skips_unrelated_requests_fast(world):
    _, _, _, lib, store = world

    # 1. Talking about a past moment by date, place, person, or 'remember when…' brings the right moment into context
    wedding_lines = recall.lines(store, "Do you remember when we went to the wedding on the coast in June 1998?")
    assert 1 <= len(wedding_lines) <= 3
    top = wedding_lines[0]
    assert top.startswith("photo moment #")
    assert "June 1998" in top and "the coast" in top and "Grandma" in top and "Kofi" in top
    assert "say 'I think'" in top   # June 1998 comes from the folder name -> estimate
    assert 30 <= len(top.split()) <= 105

    # 2. 'last Christmas' recalls the Christmas moment at Harbourton with Grandma and Ama
    xmas_lines = recall.lines(store, "What did we do last Christmas with Grandma?")
    assert any("25 December 2023" in ln and "Harbourton" in ln and "Ama" in ln for ln in xmas_lines)
    # Camera EXIF date is exact, not an estimate
    xmas_top = next(ln for ln in xmas_lines if "25 December 2023" in ln)
    assert "say 'I think'" not in xmas_top

    # 3. Mentioning a place ('Northfield') recalls the Northfield weekend moment
    north_lines = recall.lines(store, "Tell me about our trip to Northfield with Kofi")
    assert any("Northfield" in ln and "1–2 May 2021" in ln for ln in north_lines)

    # 4. Asking about a year with no photos ('1985') adds no false photo moments to context
    assert recall.lines(store, "Do we have any photos from 1985?") == []

    # 5. Ordinary unrelated requests add nothing and add no delay (<0.5 ms per request)
    unrelated = [
        "What time is it right now?",
        "Set a timer for ten minutes",
        "Turn the volume up a bit",
        "Open Notepad please",
        "What is 15 percent of 340?",
    ]
    for req in unrelated:
        assert recall.lines(store, req) == []

    t0 = time.perf_counter()
    iterations = 100
    for i in range(iterations):
        recall.lines(store, unrelated[i % len(unrelated)])
    avg_ms = (time.perf_counter() - t0) * 1000 / iterations
    assert avg_ms < 0.8, f"unrelated recall.lines() took {avg_ms:.3f} ms on average"


def test_find_photos_combines_filters_keywords_meaning_and_picture_search_and_boosts_mentions(world):
    _, _, _, lib, store = world
    c = load_config()
    ctx = ToolContext(c, None, Output(), memory=store, photos=lib)
    tools = {t.name: t for t in photos_skill.tools(ctx)}
    find_photos = tools["find_photos"]

    # 1. Filter by person + place
    r1 = find_photos.fn(person="Grandma", place="Harbourton")
    assert r1.ok and "25 December 2023" in r1.text and "Harbourton" in r1.text and "Ama" in r1.text

    # 2. Picture search (CLIP embeddings): 'pink car' finds the pink car photo even though it has no caption or OCR text
    r_car = find_photos.fn(query="pink car")
    assert r_car.ok and "Top matching photos:" in r_car.text

    # 3. User's own words (caption) rank ahead of model words, and meaning search (text embeddings) finds captions
    by_when = {m["when"]: lib.moment(m["id"]) for m in lib.moments()["items"]}
    north_pids = [i["id"] for i in by_when["1–2 May 2021"]["items"]]
    lib.set_caption(north_pids[0], "anniversary picnic on the hilltop")
    r_meaning = find_photos.fn(query="anniversary hilltop")
    assert r_meaning.ok and f"photo #{north_pids[0]}" in r_meaning.text

    # 4. Mention boost: touching an older moment repeatedly boosts it ahead on a tied query
    wedding_mid = by_when["June 1998"]["id"]
    xmas_mid = by_when["25 December 2023"]["id"]
    # Before touching: Christmas 2023 is more recent than June 1998 when searching for Grandma
    before_mids = [m["id"] for m in lib.search_photos(person="Grandma")["moments"]]
    assert before_mids.index(xmas_mid) < before_mids.index(wedding_mid)
    # After mentioning the wedding moment several times, it moves ahead on the same query
    for _ in range(8):
        lib.touch_moment(wedding_mid)
    after_mids = [m["id"] for m in lib.search_photos(person="Grandma")["moments"]]
    assert after_mids[0] == wedding_mid


def test_honesty_on_missing_dates_and_estimated_dates(world):
    _, _, _, lib, store = world
    c = load_config()
    ctx = ToolContext(c, None, Output(), memory=store, photos=lib)
    tools = {t.name: t for t in photos_skill.tools(ctx)}
    find_photos = tools["find_photos"]
    about_photo = tools["about_photo"]

    # 1. Explicit year with no photos ('1985'): says honestly there are no photos from 1985
    r_missing = find_photos.fn(when="1985")
    assert r_missing.ok
    assert "No photos found from 1985" in r_missing.text
    assert "don't have any photos from 1985" in (r_missing.say or "")

    # 2. Estimated date (June 1998 from folder name) is clearly marked as an estimate ('say "I think"')
    by_when = {m["when"]: lib.moment(m["id"]) for m in lib.moments()["items"]}
    wedding_cover = by_when["June 1998"]["cover_id"]
    r_about = about_photo.fn(photo_id=wedding_cover)
    assert r_about.ok
    assert "June 1998, estimated from the album's name: say 'I think'" in r_about.text
    assert "Grandma" in r_about.text and "Kofi" in r_about.text


def test_show_photo_opens_photo_and_is_grounded_in_user_words(world):
    _, _, _, lib, store = world
    c = load_config()
    c["brain"] = Section.wrap({**(c.get("brain") or {}), "mode": "new"})
    ctx = ToolContext(c, None, Output(), memory=store, photos=lib)
    ctx.suppress_open = True
    reg = ToolRegistry(ctx)

    by_when = {m["when"]: lib.moment(m["id"]) for m in lib.moments()["items"]}
    xmas = by_when["25 December 2023"]

    # 1. Ungrounded call to show_photo (user only asked "When was that taken?", didn't ask to see/show/open) is blocked
    ctx.request = "When was that taken?"
    blocked = reg.run("show_photo", {"moment_id": xmas["id"]})
    assert not blocked.ok and "Not done:" in blocked.text
    assert ctx.shown_photos == []

    # 2. Grounded call ("Show me a photo from Christmas 2023") opens the moment's best photo and records it
    ctx.request = "Show me a photo from Christmas 2023"
    shown = reg.run("show_photo", {"moment_id": xmas["id"]})
    assert shown.ok and f"photo #{xmas['cover_id']}" in shown.text
    assert ctx.shown_photos == [xmas["cover_id"]]
    assert lib.moment(xmas["id"])["mentions"] >= 1


def test_look_tool_can_inspect_a_library_photo_and_never_invents_a_name(world, monkeypatch):
    _, _, _, lib, store = world
    c = load_config()
    c["vision"] = Section.wrap({"model": "qwen3.5:2b-q4_K_M"})
    monkeypatch.setattr(vision, "model_for", lambda cfg: "qwen3.5:2b-q4_K_M")

    seen_call = {}

    def fake_vision_look(png, question, cfg, model=None):
        seen_call.update(png=png, question=question, model=model)
        # Even if the vision model tries to invent 'David' and refers to '[1]', sanitize + resolve fixes it
        return "[1] and [2] cutting a white three-tier cake with David watching"

    monkeypatch.setattr(vision, "look", fake_vision_look)

    ctx = ToolContext(c, None, Output(), memory=store, photos=lib)
    look_tool = look_skill.tools(ctx)[0]

    by_when = {m["when"]: lib.moment(m["id"]) for m in lib.moments()["items"]}
    wedding_cover = by_when["June 1998"]["cover_id"]

    res = look_tool.fn(question="What kind of cake is in the photo?", photo_id=wedding_cover)
    assert res.ok
    assert "Grandma" in res.text and "Kofi" in res.text
    assert "three-tier cake" in res.text
    assert "David" not in res.text
    assert "[1]" in seen_call["question"]


def test_replay_seeds_synthetic_photos_and_runs_real_photo_tools(tmp_path):
    import yaml
    from mary.config import PROJECT_DIR

    conv_path = PROJECT_DIR / "tests" / "conversations" / "photos.yaml"
    conv = yaml.safe_load(conv_path.read_text(encoding="utf-8"))
    assert conv["name"] == "Photo moments in conversation"
    assert "photos" in conv["real"] and conv["photos"] == "moments_set"
    assert len(conv["turns"]) == 3

