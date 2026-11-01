"""The memory scored on a made-up month, with the real meaning model: how often the right record is among the top
three, and that ordinary commands add nothing. Thresholds were set on the `dev` questions; `held` and `fresh` (written
after they were set) are what is held to a bar here. Skipped when the model isn't on this PC and can't be loaded."""
import pytest

from mary.config import expand_path, load_config
from mary.memory import bench, clock, recall


@pytest.fixture(scope="module")
def month(tmp_path_factory):
    import os

    os.environ["HF_HUB_OFFLINE"] = "1"   # never the network
    from mary.memory.embed import BgeEmbedder

    cfg = load_config()
    embedder = BgeEmbedder(cfg.router.model, expand_path(cfg.paths.data) / "models" / "fastembed", 2)
    if not embedder.load():
        pytest.skip("the meaning model isn't on this PC")
    data = bench.load()
    store = bench.fresh_store(embedder, tmp_path_factory.mktemp("month"))
    ids = bench.build(store, data)
    yield store, data, ids
    store.close()
    clock.reset()


def test_the_month_is_what_the_questions_expect(month):
    store, data, ids = month
    assert store.count(None, None) >= 330
    assert store.vectors.count("text") == store.count(None, None)
    assert {q["split"] for q in data["questions"]} == {"dev", "held", "fresh", "fresh2"}


@pytest.mark.parametrize("split,bar", [("held", 0.8), ("fresh", 0.8), ("fresh2", 0.8)])
def test_the_right_record_is_in_the_top_three(month, split, bar):
    store, data, ids = month
    result = bench.evaluate(store, data, ids, split)
    missed = [r["q"] for r in result["rows"] if not r["ok"]]
    assert result["score"] >= bar, f"{split}: {result['score']:.2f}, missed {missed}"


def test_words_and_meaning_together_beat_either_alone(month):
    from mary.memory import search as searching

    store, data, ids = month
    both = bench.evaluate(store, data, ids)["score"]
    original = store.embedder, searching.keyword_relevance
    try:
        store.embedder = None
        words = bench.evaluate(store, data, ids)["score"]
        store.embedder, searching.keyword_relevance = original[0], lambda *a, **k: {}
        meaning = bench.evaluate(store, data, ids)["score"]
    finally:
        store.embedder, searching.keyword_relevance = original
    assert both > words and both > meaning, (both, words, meaning)


def test_ordinary_commands_get_no_memory_and_named_people_bring_their_fact(month):
    store, data, ids = month
    g = bench.gate(store, data, ids)
    assert g["noisy"] == [], g["noisy"]
    assert all(ok for _, ok in g["named"]), g["named"]
    seeking = [ok for _, ok in g["seeking"]]   # the model's reach has limits ("for a living" does not find "nurse")
    assert sum(seeking) / len(seeking) >= 0.8, [q for q, ok in g["seeking"] if not ok]


def test_a_name_that_isnt_in_the_memory_never_brings_a_neighbour(month):
    store, data, ids = month
    now = bench._now(data)
    for question in ("Who is Marcos?", "Who is Marcelo?", "What does Marcos do for work?"):
        assert recall.lines(store, question, now) == [], question


def test_what_was_replaced_is_not_offered_as_current_but_can_be_asked_for(month):
    store, data, ids = month
    now = bench._now(data)
    now_lines = recall.lines(store, "where do I live now", now)
    assert any(f"#{ids['f_porto']} " in line for line in now_lines)
    assert not any(f"#{ids['f_lisbon']} " in line for line in now_lines)
    used_to = recall.lines(store, "where did I live before", now)
    assert any(f"#{ids['f_lisbon']} " in line and "no longer true" in line for line in used_to)
