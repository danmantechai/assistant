"""The memory brick: remembering, recalling, forgetting (kept, ended) and deleting for good (asked first)."""
from datetime import datetime

import pytest
import yaml

from mary.memory import clock
from mary.memory.embed import HashEmbedder
from mary.memory.files import MemoryFiles
from mary.memory.store import MemoryStore
from mary.output import Output
from mary.profile import Profile
from mary.skills import memory as brick
from mary.tools.registry import ToolContext
from mary.tools.safety import ungrounded_reason


@pytest.fixture
def world(tmp_path):
    store = MemoryStore(tmp_path / "memory" / "memory.db", embedder=HashEmbedder(), backups=tmp_path / "b")
    MemoryFiles(store, tmp_path / "memory")
    profile = Profile(tmp_path / "profile.yaml")
    store.sync_core(profile.facts)
    profile.listeners.append(lambda: store.sync_core(profile.facts))
    ctx = ToolContext(cfg=None, apps=None, output=Output(), profile=profile, memory=store)
    tool = brick.tools(ctx)[0]
    yield tool, store, profile, tmp_path
    store.close()
    clock.reset()


def run(tool, **args):
    return tool.fn(**args)


def test_remember_keeps_a_fact_pinned_in_the_profile_and_searchable(world):
    tool, store, profile, _ = world
    r = run(tool, action="remember", fact="my sister's name is Lena")
    assert r.ok and "Remembered" in r.text and profile.facts == ["My sister's name is Lena"]
    assert [m.text for m in store.list(kinds=("core",))] == ["My sister's name is Lena"]
    again = run(tool, action="remember", fact="My sister's name is Lena.")
    assert again.ok and "already" in again.text and len(profile.facts) == 1


def test_a_fact_she_had_learned_and_the_user_now_says_outright_is_not_kept_twice(world):
    tool, store, profile, _ = world
    learned, _ = store.add_fact("Marcus is my friend from university", importance=0.6)
    run(tool, action="remember", fact="Marcus is my friend from university")
    assert not store.get(learned.id).current and store.get(learned.id).ended_why == "superseded"
    assert profile.facts == ["Marcus is my friend from university"]


def test_remember_with_replaces_ends_the_older_fact_by_its_number(world):
    tool, store, profile, _ = world
    old, _ = store.add_fact("I live in Lisbon")
    r = run(tool, action="remember", fact="I live in Porto", replaces=f"#{old.id}")
    assert r.ok
    assert not store.get(old.id).current and store.get(old.id).superseded_by is not None
    assert profile.facts == ["I live in Porto"]
    # a core fact can be replaced the same way
    core = store.by_key("core", __import__("mary.memory.store", fromlist=["core_key"]).core_key("I live in Porto"))
    run(tool, action="remember", fact="I live in Faro", replaces=f"#{core.id}")
    assert profile.facts == ["I live in Faro"] and [e["fact"] for e in profile.ended] == ["I live in Porto"]


def test_recall_finds_what_was_said_with_numbers_to_replace_by(world):
    tool, store, _, _ = world
    f, _ = store.add_fact("My dentist is Dr. Haddad")
    store.add("diary", 'Asked: "remind me about the dentist". Replied: "Noted."', happened=datetime(2026, 10, 15, 9, 0))
    r = run(tool, action="recall", fact="who is my dentist")
    assert r.ok and f"#{f.id} " in r.text and "Dr. Haddad" in r.text and "remembered" in r.text


def test_recall_with_nothing_says_so_and_tells_her_not_to_guess(world):
    tool, _, _, _ = world
    r = run(tool, action="recall", fact="my horse")
    assert r.ok and "Nothing in memory" in r.text and "don't guess" in r.text
    assert not run(tool, action="recall", fact="  ").ok


def test_recall_can_look_back_at_what_used_to_be_true(world):
    tool, store, _, _ = world
    old, _ = store.add_fact("I live in Lisbon")
    store.add_fact("I live in Porto", replaces=old.id)
    now = run(tool, action="recall", fact="where do I live")
    before = run(tool, action="recall", fact="where did I use to live")
    assert "Lisbon" not in now.text and "Porto" in now.text
    assert "Lisbon" in before.text and "no longer true" in before.text


def test_forget_ends_a_fact_and_keeps_it(world):
    tool, store, profile, _ = world
    f, _ = store.add_fact("I go swimming every Thursday evening")
    r = run(tool, action="forget", fact="swimming")
    assert r.ok and "Forgot" in r.text
    assert not store.get(f.id).current and store.get(f.id).ended_why == "forgotten"
    assert store.count(("fact",), False) == 1


def test_forget_a_core_fact_moves_it_to_the_profiles_ended_list(world, tmp_path):
    tool, store, profile, folder = world
    run(tool, action="remember", fact="My car is a green Toyota")
    r = run(tool, action="forget", fact="my car")
    assert r.ok and profile.facts == []
    saved = yaml.safe_load((folder / "profile.yaml").read_text(encoding="utf-8"))
    assert saved["ended"][0]["fact"] == "My car is a green Toyota" and saved["ended"][0]["why"] == "forgotten"
    assert Profile(folder / "profile.yaml").ended == saved["ended"]       # and read back


def test_forget_it_alone_never_wipes_anything(world):
    tool, store, profile, _ = world
    run(tool, action="remember", fact="I like it when music is quiet")
    assert not run(tool, action="forget", fact="it").ok
    assert profile.facts == ["I like it when music is quiet"]


def test_forget_with_two_equally_good_matches_asks_which(world):
    tool, store, _, _ = world
    store.add("fact", "My dentist is Dr. Haddad")
    store.add("fact", "My dentist is Dr. Haddad on Mondays")
    r = run(tool, action="forget", fact="Dr. Haddad dentist")
    assert not r.ok and "More than one" in r.text and store.count(("fact",)) == 2


def test_forgetting_by_number(world):
    tool, store, _, _ = world
    f, _ = store.add_fact("I work at the museum")
    assert run(tool, action="forget", fact=f"#{f.id}").ok and not store.get(f.id).current


# ---------------------------------------------------------------- delete for good


def test_delete_needs_the_users_own_words_and_asks_first(world):
    tool, _, _, _ = world
    assert ungrounded_reason("memory", {"action": "delete", "fact": "x"}, "forget my car", brick.GROUNDING) is not None
    assert ungrounded_reason("memory", {"action": "delete", "fact": "x"}, "never mind", brick.GROUNDING) is not None
    for said in ("delete it for good", "erase that permanently", "wipe it forever", "delete everything about Lena for good"):
        assert ungrounded_reason("memory", {"action": "delete", "fact": "x"}, said, brick.GROUNDING) is None, said
    assert tool.tier_for({"action": "delete"}) == 2 and tool.tier_for({"action": "forget"}) == 1
    assert tool.tier_for({"action": "delete"}, new_mode=True) == 2
    assert "for good" in tool.ask({"action": "delete", "fact": "my locker code"}) and "locker" in tool.ask({"fact": "my locker code"})


def test_delete_removes_it_everywhere_including_the_files(world):
    tool, store, profile, folder = world
    gone, _ = store.add_fact("my locker combination is qqzzmarker")
    store.add_fact("Marcus is my friend")
    store.flush()
    assert "qqzzmarker" in (folder / "memory" / "facts.md").read_text(encoding="utf-8")
    r = run(tool, action="delete", fact="locker combination")
    assert r.ok and "for good" in r.text
    store.flush()
    assert store.get(gone.id) is None and store.count(("fact",), None) == 1
    assert "qqzzmarker" not in (folder / "memory" / "facts.md").read_text(encoding="utf-8")
    store.close()
    assert b"qqzzmarker" not in b"".join(p.read_bytes() for p in (folder / "memory").glob("memory.db*"))


def test_delete_a_core_fact_clears_the_profile_and_its_ended_trace(world):
    tool, store, profile, folder = world
    run(tool, action="remember", fact="My passport number is XQ12345")
    run(tool, action="forget", fact="passport")
    assert [e["fact"] for e in profile.ended] == ["My passport number is XQ12345"]
    r = run(tool, action="delete", fact="passport number")
    assert r.ok
    assert profile.facts == [] and profile.ended == []
    assert "XQ12345" not in (folder / "profile.yaml").read_text(encoding="utf-8")
    assert store.count(("core",), None) == 0


def test_delete_that_matches_nothing_or_many_is_refused_before_asking(world):
    tool, store, _, _ = world
    assert "no memory matches" in tool.precheck({"action": "delete", "fact": "my boat"})
    store.add("fact", "My dentist is Dr. Haddad")
    store.add("fact", "My dentist is Dr. Haddad on Mondays")
    assert "more than one" in tool.precheck({"action": "delete", "fact": "Dr. Haddad dentist"})
    assert tool.precheck({"action": "forget", "fact": "anything"}) is None
    assert tool.precheck({"action": "delete", "fact": "dentist on Mondays"}) is None


def test_list_shows_the_name_the_facts_and_what_she_learned(world):
    tool, store, profile, _ = world
    run(tool, action="set_name", fact="sam")
    run(tool, action="remember", fact="I prefer Edge")
    store.add_fact("Marcus is my friend")
    text = run(tool, action="list").text
    assert "Name: Sam" in text and "I prefer Edge" in text and "Marcus is my friend (learned" in text


def test_without_a_store_it_still_remembers_and_forgets_the_old_way(tmp_path):
    ctx = ToolContext(cfg=None, apps=None, output=Output(), profile=Profile(tmp_path / "p.yaml"), memory=None)
    tool = brick.tools(ctx)[0]
    assert tool.fn(action="remember", fact="I like tea").ok
    assert tool.fn(action="forget", fact="tea").ok and ctx.profile.facts == []
    assert not tool.fn(action="recall", fact="tea").ok


def test_remember_tells_the_model_which_older_memories_it_may_have_changed(world):
    tool, store, profile, _ = world
    store.settings["recall"].update(cosine_low=0.2, cosine_high=0.7)   # the stand-in meaning is word overlap, not bge
    first = run(tool, action="remember", fact="I live in Lisbon")
    assert "You already had" not in first.text                       # nothing like it yet
    second = run(tool, action="remember", fact="I live in Porto now")
    old = store.by_key("core", __import__("mary.memory.store", fromlist=["core_key"]).core_key("I live in Lisbon"))
    assert f'#{old.id} "I live in Lisbon"' in second.text and "replaces set to its number" in second.text
    assert profile.facts == ["I live in Lisbon", "I live in Porto now"]        # the model decides what is out of date
    third = run(tool, action="remember", fact="I live in Porto now", replaces=f"#{old.id}")   # and says so by number
    assert third.ok and 'It replaces "I live in Lisbon"' in third.text
    assert profile.facts == ["I live in Porto now"] and [e["fact"] for e in profile.ended] == ["I live in Lisbon"]
    unrelated = run(tool, action="remember", fact="my dentist is Dr. Haddad")
    assert "You already had" not in unrelated.text


def test_a_delete_asks_about_the_memory_in_its_own_words_not_its_number(world):
    tool, store, _, _ = world
    m, _ = store.add_fact("my locker combination is 4417")
    ask = tool.ask({"action": "delete", "fact": f"#{m.id}"})
    assert "locker combination is 4417" in ask and f"#{m.id}" not in ask and "for good" in ask
    assert "locker combination" in tool.confirm_text({"fact": f"#{m.id}"})
