"""Searching the memory: keywords, names, dates, what is ended, and which one is meant. (The meaning model is tested on
the made-up month in test_memory_eval.py; here the words alone are enough to show how the pieces behave.)"""
from datetime import datetime

import pytest

from mary.memory import clock
from mary.memory.embed import HashEmbedder
from mary.memory.search import terms
from mary.memory.store import MemoryStore

NOW = datetime(2026, 11, 4, 12, 0)   # a Wednesday


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.db", embedder=None, backups=tmp_path / "b")
    yield s
    s.close()
    clock.reset()


def texts(hits):
    return [h.memory.text for h in hits]


def test_terms_drop_filler_and_mark_names():
    assert terms("What did I say about Marcus last Tuesday?") == [("marcus", True), ("last", False),
                                                                  ("tuesday", False)]
    assert terms("text Marcus that I'm running late") == [("text", False), ("marcus", True), ("running", False),
                                                          ("late", False)]
    assert terms("Open Spotify") == [("open", False), ("spotify", True)]     # the first word is only a capital letter
    assert terms("on Friday in March") == [("friday", False), ("march", False)]   # a day or a month is not a name
    assert terms("Marcus?") == [("marcus", True)]
    assert terms("call Zoë about the café, 42 euros") == [("call", False), ("zoe", True), ("cafe", False),
                                                          ("42", False), ("euros", False)]


def test_a_rare_word_finds_its_record(store):
    store.add("fact", "The alarm code for the shed is 4417")
    for i in range(30):
        store.add("diary", f'Asked: "open Notepad". Used: open_app. Replied: "Opening Notepad." ({i})')
    assert texts(store.search("what is the shed alarm code", now=NOW)) == ["The alarm code for the shed is 4417"]


def test_a_name_the_memory_does_not_know_matches_nothing(store):
    store.add("fact", "Marcus is my friend from university")
    store.add("fact", "I work as a nurse at the city hospital")
    assert texts(store.search("Who is Marcus?", now=NOW)) == ["Marcus is my friend from university"]
    assert store.search("Who is Marcos?", now=NOW) == []
    assert store.search("What does Marcos do for work?", now=NOW) == []       # 'work' alone must not stand in for him
    assert texts(store.search("what does Marcus do for work", now=NOW))[0].startswith("Marcus")


def test_a_lower_case_name_is_still_found_by_its_own_word(store):
    store.add("fact", "Marcus is my friend from university")
    assert texts(store.search("who is marcus", now=NOW)) == ["Marcus is my friend from university"]


def test_one_ordinary_word_in_common_is_not_enough_to_be_relevant(store):
    store.add("fact", "My sister's name is Lena")
    store.settings["recall"]["min_relevance"] = 0.7
    assert store.search("what is the colour of my kettle", now=NOW) == []
    assert store.search("sister", now=NOW) == []                               # one word, not a name: capped below 0.7


def test_ended_records_stay_out_unless_asked_for(store):
    old = store.add("fact", "I live in Lisbon")
    store.add_fact("I live in Porto", replaces=old.id)
    assert texts(store.search("where do I live", now=NOW, current=True)) == ["I live in Porto"]
    both = texts(store.search("where do I live", now=NOW, current=None))
    assert set(both) == {"I live in Lisbon", "I live in Porto"}
    assert texts(store.search("where do I live", now=NOW, current=False)) == ["I live in Lisbon"]


def test_kinds_narrow_the_search(store):
    store.add("fact", "Biscuit is allergic to chicken")
    store.add("diary", 'Asked: "can biscuit eat chicken". Replied: "No, chicken is not safe for Biscuit."')
    assert len(store.search("Biscuit chicken", kinds=("fact",), now=NOW)) == 1
    assert len(store.search("Biscuit chicken", now=NOW)) == 2


def test_a_date_narrows_the_search_to_those_days(store):
    store.add("diary", 'Asked: "open the budget". Replied: "Opening it."', happened=datetime(2026, 11, 3, 9, 0))
    store.add("diary", 'Asked: "open the budget again". Replied: "Opening it."', happened=datetime(2026, 10, 20, 9, 0))
    got = texts(store.search("what did I open yesterday about the budget", now=NOW, k=5))
    assert got == ['Asked: "open the budget". Replied: "Opening it."']


def test_only_a_date_lists_that_stretch_of_the_memory_most_important_first(store):
    store.add("diary", "Asked: a. Replied: b.", happened=datetime(2026, 11, 3, 9, 0), importance=0.3)
    store.add("fact", "Marcus is my friend", happened=datetime(2026, 11, 3, 10, 0), importance=0.8)
    store.add("diary", "Asked: c. Replied: d.", happened=datetime(2026, 10, 1, 9, 0))
    hits = store.search("what happened yesterday", now=NOW, k=5)
    assert [h.memory.text for h in hits] == ["Marcus is my friend", "Asked: a. Replied: b."]
    assert all(h.how == "dates" for h in hits)


def test_a_weekday_counts_as_a_date_only_when_the_question_is_about_the_past(store):
    store.add("diary", 'Asked: "remind me about the dentist". Replied: "Noted."', happened=datetime(2026, 10, 30, 9, 0))
    store.add("diary", 'Asked: "remind me about the dentist later". Replied: "Noted."',
              happened=datetime(2026, 10, 12, 9, 0))
    past = store.search("what did I say about the dentist on Friday", now=NOW, cue=True)
    assert [h.memory.happened.day for h in past] == [30]
    plan = store.search("remind me about the dentist on Friday", now=NOW, cue=False)
    assert {h.memory.happened.day for h in plan} == {30, 12}                   # 'Friday' is a plan, not a day gone by


def test_ranking_prefers_relevance_and_then_what_is_recent_and_important(store):
    store.settings["recall"]["min_relevance"] = 0.1
    a = store.add("diary", 'Asked: "play jazz". Replied: "Jazz on."', happened=datetime(2026, 9, 1, 9, 0))
    b = store.add("diary", 'Asked: "play jazz". Replied: "Jazz on."', happened=datetime(2026, 11, 3, 9, 0))
    hits = store.search("play jazz", now=NOW)
    assert [h.memory.id for h in hits] == [b.id, a.id]
    assert hits[0].relevance == hits[1].relevance and hits[0].score > hits[1].score


def test_it_never_looks_at_another_owners_records(store):
    store.add("fact", "The spare key is under the blue pot")
    store.owner = "someone-else"
    assert store.search("spare key", now=NOW) == []


def test_resolve_names_one_memory_or_the_candidates_when_unclear(store):
    a = store.add("fact", "My dentist is Dr. Haddad")
    store.add("fact", "My vet is Dr. Weiss")
    one, others = store.resolve("my dentist")
    assert one.id == a.id and others == []
    assert store.resolve(f"#{a.id}")[0].id == a.id
    nothing, cands = store.resolve("my accountant")
    assert nothing is None and cands == []
    store.add("fact", "My dentist is Dr. Haddad on Monday")
    unclear, cands = store.resolve("Dr. Haddad dentist")
    assert unclear is None and len(cands) == 2


def test_the_meaning_leg_is_used_when_the_model_is_there(tmp_path):
    s = MemoryStore(tmp_path / "m.db", embedder=HashEmbedder(), backups=tmp_path / "b",
                    settings={"recall": {"cosine_low": 0.0, "cosine_high": 0.5, "min_relevance": 0.3}})
    s.add("fact", "I drink strong black tea")
    s.flush()
    hits = s.search("what tea do I drink", now=NOW)
    assert hits and "meaning" in hits[0].how
    s.embedder = None   # model not loaded yet: words only, and it still answers
    assert s.search("what tea do I drink", now=NOW) and "meaning" not in s.search("tea", now=NOW)[0].how
    s.close()
