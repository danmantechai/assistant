"""Tests for full diary word retention, chunked passage vectors, and semantic diary recall.

Covers:
- Verbatim retention of long messages (3,000+ chars) in DB and markdown files.
- Round-tripping messages with quotes and newlines.
- Multiple long messages on the same day.
- Repeats, edits, and deletes with chunked passage vectors.
- Purging other models and rebuilding passages.
- Semantic recall matching a detail near the end of a long message.
- Context compaction (only the matching passage is placed in context with its date).
- Unrelated requests recalling nothing from the diary.
- Latency measurement for recall.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from mary.memory import clock, recall
from mary.memory.diary import Did, Diary, line
from mary.memory.embed import HashEmbedder, chunk_text, embedding_passages
from mary.memory.files import parse_diary, render_diary
from mary.memory.store import MemoryStore


def _sample_3000_char_text() -> str:
    """A realistic 3,000-character trip plan with distinct details throughout."""
    parts = [
        "We are finally putting together our grand three-week journey across Italy starting next May.",
        "On day one we fly into Milan Malpensa on flight AZ 604 and check into Hotel Spadari near the Duomo.",
        "We will spend two days exploring the Galleria and seeing Leonardo's Last Supper at Santa Maria delle Grazie.",
        "On day three we catch the morning Frecciarossa high-speed train to Venice Santa Lucia station.",
        "In Venice our private water taxi drops us at the historic Palazzo Gritti right on the Grand Canal.",
        "We plan to tour Saint Mark's Basilica, cross the Rialto Bridge, and spend an evening eating cicchetti in Cannaregio.",
        "Our favorite excursion will be taking the vaporetto out to Murano to watch the master glassblowers at work.",
        "From Venice we take the train south into Tuscany, arriving at Florence Santa Maria Novella on day seven.",
        "In Florence we booked an apartment on Via dei Calzaiuoli just a short stroll from the Uffizi Gallery.",
        "We have timed museum tickets for Michelangelo's David at the Accademia on Tuesday morning at ten o'clock.",
        "For dining in Florence our top choice is Trattoria Mario for the legendary bistecca alla Fiorentina.",
        "Next we rent an Alfa Romeo Giulia and drive through the rolling hills of Chianti towards Siena and San Gimignano.",
        "We will stay three nights at a vineyard agriturismo outside Castellina in Chianti, tasting olive oil and wine.",
        "After Tuscany we head down to Rome for the final six days of our trip, staying near the Piazza Navona.",
        "In Rome we have scheduled a guided sunset tour of the Colosseum and the Roman Forum on Thursday.",
        "On Friday we will walk through the Vatican Museums, marvel at the Sistine Chapel, and visit Saint Peter's.",
        "For our farewell dinner in Rome on the final evening, we reserved the rooftop terrace at Ristorante Aroma.",
        "The return flight departs Rome Fiumicino on flight AZ 610 back home on Sunday morning at eleven fifteen.",
        "Emergency contacts for this trip: Marco Rossi in Florence at plus thirty-nine zero five five two one two three."
    ]
    full = " ".join(parts)
    while len(full) < 2900:
        full += " Remember to pack comfortable walking shoes, sunscreen, voltage adapters, and light linen shirts."
    full += " Our travel insurance policy number is IT-TRAVEL-998844 with Worldwide Care Assistance."
    return full


def test_chunk_text_preserves_sentence_boundaries_and_overlaps():
    text = _sample_3000_char_text()
    assert len(text) >= 3000
    passages = chunk_text(text, max_chars=400, overlap=100)
    assert len(passages) >= 8
    # Every passage fits in 400 chars
    assert all(len(p) <= 410 for p in passages)
    # The detail near the end is contained in the final passage
    assert "IT-TRAVEL-998844" in passages[-1]
    # Overlap check: words near end of passage 0 appear near start of passage 1
    p0_words = passages[0].split()[-5:]
    p1_words = passages[1].split()[:15]
    assert any(w in p1_words for w in p0_words)


def test_3000_char_message_verbatim_in_db_and_markdown(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    long_msg = _sample_3000_char_text()
    spoken_reply = "I have noted all your travel plans for Italy in detail."
    t = datetime(2026, 11, 2, 14, 30)

    m = diary.record(long_msg, [], spoken_reply, when=t)
    assert m is not None

    # Check store DB contains full words word-for-word
    in_db = store.get(m.id)
    assert in_db is not None
    assert "IT-TRAVEL-998844" in in_db.text
    assert "Palazzo Gritti" in in_db.text
    assert "AZ 610" in in_db.text
    assert spoken_reply in in_db.text
    assert len(in_db.text) >= 3000

    # Render day file to markdown
    day = t.date()
    md = render_diary(day, [in_db])
    assert f"- 14:30 {in_db.text}" in md

    # Parse back from markdown
    parsed = parse_diary(md, day)
    assert len(parsed) == 1
    assert parsed[0].text == in_db.text
    assert parsed[0].when == t
    assert "IT-TRAVEL-998844" in parsed[0].text

    store.close()


def test_messages_with_quotes_and_newlines(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    t = datetime(2026, 11, 2, 10, 15)

    req = 'Here is the rule: "Never look back".\nAlso remember:\n1. Check the locks.\n2. Turn off the lights.'
    rep = 'Understood. "Locks and lights" will be remembered.'

    m = diary.record(req, [], rep, when=t)
    rec = store.get(m.id)
    # Double quotes softened to single quotes, newlines normalized to spaces
    assert "'Never look back'" in rec.text
    assert "'Locks and lights'" in rec.text
    assert "Check the locks" in rec.text
    assert "Turn off the lights" in rec.text

    # Markdown round-trip
    day = t.date()
    md = render_diary(day, [rec])
    parsed = parse_diary(md, day)
    assert len(parsed) == 1
    assert parsed[0].text == rec.text
    store.close()


def test_several_long_messages_same_day(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    day = date(2026, 11, 2)

    msg1 = "Morning notes: " + ("alpha " * 500)
    msg2 = "Afternoon notes: " + ("bravo " * 500)
    msg3 = "Evening notes: " + ("charlie " * 500)

    m1 = diary.record(msg1, [], "Reply 1", when=datetime(2026, 11, 2, 9, 0))
    m2 = diary.record(msg2, [], "Reply 2", when=datetime(2026, 11, 2, 14, 0))
    m3 = diary.record(msg3, [], "Reply 3", when=datetime(2026, 11, 2, 20, 0))

    assert store.count(("diary",)) == 3
    all_recs = store.list(kinds=("diary",), newest_first=False)
    assert len(all_recs) == 3

    md = render_diary(day, all_recs)
    parsed = parse_diary(md, day)
    assert len(parsed) == 3
    assert "alpha" in parsed[0].text
    assert "bravo" in parsed[1].text
    assert "charlie" in parsed[2].text
    store.close()


def test_quick_repeats_of_long_messages(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    t = datetime(2026, 11, 2, 11, 0)
    long_msg = "Please ensure the backup process runs at midnight: " + ("step " * 200)

    first = diary.record(long_msg, [], "Understood.", when=t)
    second = diary.record(long_msg, [], "Understood.", when=t + timedelta(minutes=2))

    assert store.count(("diary",)) == 1
    assert second.id == first.id
    rec = store.get(first.id)
    assert "(×2)" in rec.text
    assert "Please ensure the backup process runs at midnight" in rec.text
    store.close()


def test_chunked_passage_vectors_created_for_long_entry(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)

    short_entry = diary.record("What is the time", [Did("get_time", True)], "It is 10:00.")
    long_entry = diary.record(_sample_3000_char_text(), [], "Plan saved.")

    store.flush()
    # Short entry has 1 vector
    assert store.vectors.get("text", short_entry.id, ord=0) is not None
    assert store.vectors.get("text", short_entry.id, ord=1) is None

    # Long entry has multiple vectors across ords
    passages = embedding_passages("diary", long_entry.text)
    assert len(passages) >= 8
    for o in range(len(passages)):
        assert store.vectors.get("text", long_entry.id, ord=o) is not None
    assert store.vectors.get("text", long_entry.id, ord=len(passages)) is None

    # Total vectors in store text space = 1 (short) + len(passages)
    assert store.vectors.count("text") == 1 + len(passages)
    store.close()


def test_deleting_long_entry_removes_all_passages(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)

    short_entry = diary.record("hello", [], "hi")
    long_entry = diary.record(_sample_3000_char_text(), [], "Noted.")
    store.flush()

    assert store.vectors.count("text") > 5
    store.delete(long_entry.id)

    # All passage vectors for long_entry are gone
    assert store.get(long_entry.id) is None
    assert store.vectors.get("text", long_entry.id, ord=0) is None
    assert store.vectors.get("text", long_entry.id, ord=1) is None
    # Only short entry vector remains
    assert store.vectors.count("text") == 1
    store.close()


def test_editing_long_entry_rebuilds_all_passages(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)

    long_entry = diary.record(_sample_3000_char_text(), [], "Noted.")
    store.flush()
    old_count = store.vectors.count("text")
    assert old_count >= 8

    # Edit to short text
    store.edit(long_entry.id, 'Asked: "Just visit Rome". Replied: "Sure."')
    store.flush()

    assert store.vectors.count("text") == 1
    assert store.vectors.get("text", long_entry.id, ord=0) is not None
    assert store.vectors.get("text", long_entry.id, ord=1) is None
    store.close()


def test_vector_model_change_purges_and_rebuilds_all_passages(tmp_path):
    path = tmp_path / "m.db"
    store = MemoryStore(path, embedder=HashEmbedder(dim=64, model="hash-64"), backups=tmp_path / "b")
    diary = Diary(store)
    long_entry = diary.record(_sample_3000_char_text(), [], "Noted.")
    store.flush()
    passages_len = len(embedding_passages("diary", long_entry.text))
    assert store.vectors.count("text") == passages_len
    store.close()

    # Re-open with new embedder
    store2 = MemoryStore(path, embedder=HashEmbedder(dim=128, model="hash-128"), backups=tmp_path / "b")
    assert store2.vectors.count("text") == 0  # old model vectors purged
    store2.flush()
    assert store2.vectors.count("text") == passages_len  # rebuilt with new model
    store2.close()


def test_question_about_detail_near_end_recalls_matching_passage_only(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    t = datetime(2026, 11, 2, 15, 0)
    diary.record(_sample_3000_char_text(), [], "Plan noted.", when=t)
    store.flush()

    # Query about a detail only at the end: travel insurance policy number
    now = datetime(2026, 11, 3, 12, 0)
    question = "what is our travel insurance policy number"
    lines = recall.lines(store, question, now=now)

    assert len(lines) >= 1
    line_out = lines[0]
    # Check compact passage injection: matching passage with date
    assert "IT-TRAVEL-998844" in line_out
    assert "Mon 02 Nov" in line_out
    # Must NOT inject the entire 3,000 characters
    assert len(line_out) < 600
    assert "Milan Malpensa" not in line_out  # from the beginning of the long entry

    store.close()


def test_unrelated_requests_recall_nothing_from_diary(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    t = datetime(2026, 11, 2, 15, 0)
    diary.record(_sample_3000_char_text(), [], "Plan noted.", when=t)
    diary.record("open Spotify", [Did("open_app", True)], "Opening Spotify.", when=t)
    store.flush()

    now = datetime(2026, 11, 3, 12, 0)
    unrelated_queries = [
        "tell me a joke",
        "what is the weather like",
        "open Spotify",
        "turn the volume up",
        "take a screenshot",
        "how much disk space is left",
        "calculate 15 times 23"
    ]
    for q in unrelated_queries:
        recalled = recall.lines(store, q, now=now)
        assert recalled == [], f"Query '{q}' unexpectedly recalled: {recalled}"

    store.close()


def test_recall_latency_stays_fast(tmp_path):
    store = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    diary = Diary(store)
    t = datetime(2026, 11, 2, 15, 0)
    diary.record(_sample_3000_char_text(), [], "Plan noted.", when=t)
    for i in range(20):
        diary.record(f"Routine note number {i}", [], f"Acknowledged {i}.")
    store.flush()

    now = datetime(2026, 11, 3, 12, 0)
    # Warm up
    recall.lines(store, "what is our insurance policy number", now=now)

    # Measure 50 recall calls
    start = time.perf_counter()
    n_runs = 50
    for _ in range(n_runs):
        recall.lines(store, "what is our travel insurance policy number", now=now)
        recall.lines(store, "tell me a joke", now=now)
    elapsed_ms = (time.perf_counter() - start) * 1000 / (n_runs * 2)

    # Recall runs in single-digit milliseconds per request
    assert elapsed_ms < 15.0, f"Average recall latency was {elapsed_ms:.2f} ms"
    store.close()


def test_bge_real_model_recall_and_latency(tmp_path):
    from mary.config import expand_path, load_config
    from mary.memory.embed import BgeEmbedder

    cfg = load_config()
    model_path = expand_path(cfg.paths.data) / "models" / "fastembed"
    embedder = BgeEmbedder(cfg.router.model, model_path, threads=2)
    if not embedder.load():
        pytest.skip("BGE embedding model not available on this machine")

    store = MemoryStore(tmp_path / "m.db", embedder=embedder, backups=tmp_path / "b")
    diary = Diary(store)
    t = datetime(2026, 11, 2, 15, 0)
    diary.record(_sample_3000_char_text(), [], "Plan noted.", when=t)
    for i in range(15):
        diary.record(f"Daily chat session #{i}", [], "Understood.")
    store.flush()

    now = datetime(2026, 11, 3, 12, 0)
    # Warmup
    recall.lines(store, "what is our insurance policy number", now=now)

    # 1. Test question about detail near end finds it
    detail_q = "what is our travel insurance policy number"
    t0 = time.perf_counter()
    recalled = recall.lines(store, detail_q, now=now)
    ms_detail = (time.perf_counter() - t0) * 1000

    assert len(recalled) >= 1
    assert "IT-TRAVEL-998844" in recalled[0]
    assert len(recalled[0]) < 600

    # 2. Test unrelated chat recalls nothing
    unrelated_q = "tell me a joke"
    t1 = time.perf_counter()
    unrelated_recalled = recall.lines(store, unrelated_q, now=now)
    ms_unrelated = (time.perf_counter() - t1) * 1000

    assert unrelated_recalled == []

    # 3. Measure latency across 20 calls
    t_start = time.perf_counter()
    n_runs = 20
    for _ in range(n_runs):
        recall.lines(store, detail_q, now=now)
        recall.lines(store, unrelated_q, now=now)
    avg_ms = (time.perf_counter() - t_start) * 1000 / (n_runs * 2)

    # BGE on CPU takes ~10-25 ms per query
    assert avg_ms < 40.0, f"BGE recall latency was {avg_ms:.2f} ms"
    store.close()
