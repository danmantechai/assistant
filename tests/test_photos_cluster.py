"""The rules that group faces and match them to the people you named (mary/photos/cluster.py), on invented fingerprints whose
answers are known: one person across thirty years, look-alikes, twins, a crowd of strangers, the order the photos arrive in.
No picture and no model is involved."""
import random
import time
from collections import Counter

import numpy as np
import pytest

from mary.photos.cluster import Decision, Gallery, Rules, consolidate, decide, reduce, unit
from mary.photos.synth import Cast


class Pile:
    """What the people module does with these rules, without the database: a face arrives, is decided, joins or starts a group."""

    def __init__(self, rules=None):
        self.rules = rules or Rules()
        self.gallery = Gallery(self.rules)
        self.state: dict[int, str] = {}
        self.of: dict[int, int] = {}
        self.asked: dict[int, int] = {}
        self.refused: set = set()
        self.next = 1

    def add(self, face_id, vec, refused=()):
        d = decide(self.gallery.scores(vec), self.state, self.rules, set(refused))
        person = d.person
        if person is None:
            person, self.next = self.next, self.next + 1
            self.state[person] = "unnamed"
        self.of[face_id] = person
        self.gallery.add(person, face_id, vec)
        if d.ask:
            self.asked[face_id] = d.ask
        return d

    def tidy(self):
        unnamed = [p for p, s in self.state.items() if s == "unnamed"]
        merges = consolidate(self.gallery, unnamed, Counter(self.of.values()), self.rules, self.refused)
        for keep, drop in merges:
            self.state.pop(drop, None)
            for f, p in self.of.items():
                if p == drop:
                    self.of[f] = keep
        return merges

    def name(self, person):
        self.state[person] = "named"

    def partition(self):
        out = {}
        for f, p in self.of.items():
            out.setdefault(p, set()).add(f)
        return frozenset(frozenset(v) for v in out.values())


def purity(of, truth):
    """Of the faces in each group, how many are of its commonest person; and the other way (completeness)."""
    n = len(of)
    groups, people = {}, {}
    for f, g in of.items():
        groups.setdefault(g, Counter())[truth[f]] += 1
        people.setdefault(truth[f], Counter())[g] += 1
    return (sum(c.most_common(1)[0][1] for c in groups.values()) / n,
            sum(c.most_common(1)[0][1] for c in people.values()) / n)


def crowd_and_family(seed=1, photos=12):
    cast = Cast(seed)
    names = [cast.person(f"p{i}") for i in range(6)]
    items, truth = [], {}
    for n in names:
        for _ in range(photos):
            fid = len(items) + 1
            items.append((fid, cast.face(n)))
            truth[fid] = n
    return cast, items, truth


# ---------------------------------------------------------------- grouping

def test_one_person_many_photos_is_one_group_whatever_order_they_arrive_in():
    cast, items, truth = crowd_and_family()
    partitions = set()
    for shuffle in range(10):
        order = list(items)
        random.Random(shuffle).shuffle(order)
        pile = Pile()
        for fid, vec in order:
            pile.add(fid, vec)
        pile.tidy()
        partitions.add(pile.partition())
        assert purity(pile.of, truth) == (1.0, 1.0)
    assert len(partitions) == 1      # the same groups every time, not only equally good ones


def test_strangers_each_start_a_group_and_are_never_joined_to_one_another():
    cast = Cast(2)
    pile = Pile()
    for i, v in enumerate(cast.strangers(80), 1):
        pile.add(i, v)
    assert pile.tidy() == [] and len(pile.partition()) == 80


def test_a_crowd_does_not_pull_a_person_apart_or_into_it():
    cast, items, truth = crowd_and_family(3, photos=10)
    crowd = cast.strangers(60)
    mixed = list(items) + [(1000 + i, v) for i, v in enumerate(crowd)]
    truth.update({1000 + i: f"stranger{i}" for i in range(60)})
    random.Random(4).shuffle(mixed)
    pile = Pile()
    for fid, vec in mixed:
        pile.add(fid, vec)
    pile.tidy()
    assert purity(pile.of, truth) == (1.0, 1.0)


def test_a_person_across_thirty_years_is_one_group_when_the_years_between_are_there_too():
    cast = Cast(5)
    cast.person("gran")
    faces = [(i + 1, cast.face("gran", age)) for i, age in enumerate(list(range(0, 31, 3)) * 2)]
    for shuffle in range(8):
        order = list(faces)
        random.Random(shuffle).shuffle(order)
        pile = Pile()
        for fid, vec in order:
            pile.add(fid, vec)
        pile.tidy()
        assert len(pile.partition()) == 1, shuffle


def test_the_same_person_thirty_years_apart_with_nothing_between_is_asked_about_never_merged():
    cast = Cast(6)
    cast.person("kofi")
    g = Gallery(Rules())
    g.set(1, [(i, cast.face("kofi", 0), 1.0, False) for i in range(10)])
    g.set(2, [(100 + i, cast.face("kofi", 30), 1.0, False) for i in range(10)])
    rules = g.rules
    between = g.between(1, 2)
    assert rules.same <= between < rules.group                 # close enough to be worth asking, not to be merged
    assert consolidate(g, [1, 2], {1: 10, 2: 10}, rules, set()) == []
    assert g.pairs([1, 2], rules.same) == {(1, 2): pytest.approx(between)}


def test_thirty_years_apart_is_asked_about_across_many_invented_people():
    """Over many people: young and old piles of one person are never merged by tidying (asked instead), and are nearly always
    close enough to be asked about."""
    merged = asked = 0
    for seed in range(40):
        cast = Cast(100 + seed)
        cast.person("x")
        g = Gallery(Rules())
        g.set(1, [(i, cast.face("x", 0), 1.0, False) for i in range(8)])
        g.set(2, [(100 + i, cast.face("x", 30), 1.0, False) for i in range(8)])
        merged += bool(consolidate(g, [1, 2], {1: 8, 2: 8}, g.rules, set()))
        asked += g.between(1, 2) >= g.rules.same
    assert merged == 0 and asked >= 38


def test_a_few_old_faces_may_join_a_young_pile_of_the_same_person_but_never_another_persons():
    """Online, a face from another era sometimes passes for the nearest look of the same person: that is right, and harmless.
    It must not happen with another person, however alike."""
    mixed_in = wrong = 0
    for seed in range(30):
        cast = Cast(200 + seed)
        cast.person("a")
        cast.person("b", like="a", closeness=0.5)
        pile = Pile()
        for i in range(10):
            pile.add(i + 1, cast.face("a", 0))
        young = pile.of[1]
        for i in range(10):
            d = pile.add(100 + i, cast.face("a", 30))
            mixed_in += pile.of[100 + i] == young
        for i in range(10):
            pile.add(300 + i, cast.face("b", 0))
            wrong += pile.of[300 + i] == young
    print(f"\nof 300 old faces, {mixed_in} joined the young pile of the same person; {wrong} of 300 look-alike faces joined it")
    assert wrong <= 3 and mixed_in < 150      # a sibling pile (look-alike) is rarely mistaken; most old faces start their own


def test_a_bad_order_that_leaves_one_person_in_two_piles_is_put_right_by_tidying():
    cast = Cast(7)
    cast.person("ama")
    ages = [0, 30, 15, 22, 8, 27, 4, 18, 11, 25, 2, 13, 29, 6, 20]
    pile = Pile()
    for i, age in enumerate(ages, 1):
        pile.add(i, cast.face("ama", age))
    pile.tidy()
    assert len(pile.partition()) == 1


def test_piles_the_person_said_are_not_one_person_stay_two_and_the_answer_survives_a_merge():
    cast = Cast(8)
    cast.person("a")
    cast.person("b", like="a", closeness=0.97)                 # as alike as twins
    g = Gallery(Rules())
    g.set(1, [(i, cast.face("a", noise=0.3), 1.0, False) for i in range(6)])
    g.set(2, [(50 + i, cast.face("b", noise=0.3), 1.0, False) for i in range(6)])
    g.set(3, [(90 + i, cast.face("a", noise=0.3), 1.0, False) for i in range(3)])
    sizes = {1: 6, 2: 6, 3: 3}
    assert g.between(1, 2) >= g.rules.group                    # as alike as twins: tidying would make one pile of them
    refused = {(1, 2)}
    assert consolidate(g, [1, 2, 3], sizes, g.rules, refused) == [(1, 3)]      # 3 is the same person as 1; 2 stays apart
    assert (1, 2) in refused


# ---------------------------------------------------------------- naming and recognising

def named_pile(cast, names, per=4, rules=None):
    pile = Pile(rules)
    fid = 1
    for n in names:
        for _ in range(per):
            pile.add(fid, cast.face(n))
            fid += 1
    pile.tidy()
    ids = {}
    for n, fid0 in zip(names, range(1, 1 + per * len(names), per)):
        ids[n] = pile.of[fid0]
        pile.name(ids[n])
    return pile, ids


def test_named_people_are_recognised_in_new_photos_and_nobody_else_gets_a_name():
    cast = Cast(10)
    names = [cast.person(f"n{i}") for i in range(5)]
    pile, ids = named_pile(cast, names)
    stranger_ids = {}
    right = wrong = asked = fresh = 0
    for round_ in range(40):
        for n in names:
            d = pile.add(10_000 + len(pile.of), cast.face(n))
            if d.reason == "named":
                right += d.person == ids[n]
                wrong += d.person != ids[n]
            else:
                asked += d.ask is not None
                fresh += d.ask is None
    assert wrong == 0 and right >= 0.95 * 200       # recognised, and never mixed up with another person you named
    for v in cast.strangers(80):
        d = pile.add(20_000 + len(pile.of), v)
        assert d.reason != "named" and d.ask is None


def test_an_unsure_match_is_asked_about_and_not_given_a_name():
    rules = Rules()
    cast = Cast(11)
    g = Gallery(rules)
    base = unit(np.random.default_rng(1).normal(size=128))
    g.add(1, 1, base, 1.0)
    for target, expected in ((0.62, "named"), (0.47, "ask"), (0.43, "ask"), (0.38, "none")):
        other = unit(np.random.default_rng(2).normal(size=128))
        other = unit(other - (other @ base) * base)
        v = target * base + (1 - target ** 2) ** 0.5 * other
        d = decide(g.scores(v), {1: "named"}, rules)
        if expected == "named":
            assert d.person == 1 and d.reason == "named"
        elif expected == "ask":
            assert d.person is None and d.reason == "new" and d.ask == 1 and rules.ask <= d.ask_score < rules.join
        else:
            assert d.person is None and d.ask is None


def test_look_alikes_are_rarely_given_to_the_wrong_one_and_the_doubtful_ones_are_asked():
    wrong = right = asked = faces = 0
    for seed in range(20):
        cast = Cast(300 + seed)
        cast.person("tomi")
        cast.person("tayo", like="tomi", closeness=0.8)             # photos of the two are about 0.45 alike
        rules = Rules()
        g = Gallery(rules)
        for who, pid in (("tomi", 1), ("tayo", 2)):
            g.set(pid, [(pid * 100 + i, cast.face(who), 1.0, False) for i in range(8)])
        state = {1: "named", 2: "named"}
        for who, pid in (("tomi", 1), ("tayo", 2)):
            for _ in range(30):
                d = decide(g.scores(cast.face(who)), state, rules)
                faces += 1
                if d.reason == "named":
                    right += d.person == pid
                    wrong += d.person != pid
                asked += d.ask is not None
    print(f"\nlook-alikes, {faces} new faces: {right} joined the right person, {wrong} the wrong one, {asked} asked about")
    assert wrong <= 0.01 * faces and asked > 0 and right > 0.5 * faces


def test_a_face_the_person_said_is_not_them_is_not_offered_to_them_again():
    rules = Rules()
    g = Gallery(rules)
    rng = np.random.default_rng(3)
    base = unit(rng.normal(size=128))
    g.add(1, 1, base, 1.0)
    other = unit(rng.normal(size=128))
    other = unit(other - (other @ base) * base)
    v = 0.5 * base + (1 - 0.25) ** 0.5 * other
    assert decide(g.scores(v), {1: "named"}, rules).ask == 1
    d = decide(g.scores(v), {1: "named"}, rules, refused={1})
    assert d.ask is None and d.person is None


def test_a_group_clearly_closer_than_the_named_person_gets_the_face_and_the_named_one_is_asked():
    rules = Rules()
    g = Gallery(rules)
    rng = np.random.default_rng(4)
    base = unit(rng.normal(size=128))
    sib = unit(0.6 * base + 0.8 * unit(rng.normal(size=128)))
    g.add(1, 1, base, 1.0)
    g.add(2, 2, sib, 1.0)
    v = unit(sib + 0.05 * rng.normal(size=128))
    s = g.scores(v)
    d = decide(s, {1: "named", 2: "unnamed"}, rules)
    assert d.person == 2 and d.reason == "group" and d.ask in (None, 1)


def test_an_ignored_group_takes_the_faces_that_are_like_it_and_they_stay_ignored():
    rules = Rules()
    g = Gallery(rules)
    rng = np.random.default_rng(5)
    stranger = unit(rng.normal(size=128))
    g.add(7, 1, stranger, 1.0)
    v = unit(stranger + 0.3 * rng.normal(size=128) / np.sqrt(128))
    d = decide(g.scores(v), {7: "ignored"}, rules)
    assert d.person == 7 and d.reason == "ignored" and d.ask is None


# ---------------------------------------------------------------- the gallery itself

def test_a_group_keeps_a_few_fingerprints_that_cover_all_its_looks():
    cast = Cast(13)
    cast.person("elder")
    g = Gallery(Rules())
    ages = {}
    for i in range(150):
        ages[i + 1] = i % 41 - 5
        g.add(1, i + 1, cast.face("elder", age=ages[i + 1]), 1.0)
    assert g.size(1) == Rules().exemplars
    kept = sorted(ages[f] for f in g.exemplar_ids(1))
    assert len({(a + 5) // 5 for a in kept}) >= 7               # nearly every five years of the forty are still represented
    for age in (-5, 10, 25, 35):
        assert g.scores(cast.face("elder", age=age))[1] > Rules().group + Rules().alone   # so every era is still recognised


def test_what_the_person_confirmed_is_never_dropped_from_the_exemplars():
    cast = Cast(14)
    cast.person("x")
    g = Gallery(Rules())
    mine = [900 + i for i in range(5)]
    for f in mine:
        g.add(1, f, cast.face("x", age=random.Random(f).randrange(30)), 0.4, anchor=True)
    for i in range(200):
        g.add(1, i + 1, cast.face("x", age=random.Random(i).randrange(30)), 1.0)
    assert set(mine) <= set(g.exemplar_ids(1))


def test_reduce_keeps_everything_when_it_is_small_and_is_stable():
    rng = np.random.default_rng(6)
    v = np.vstack([unit(rng.normal(size=16)) for _ in range(10)])
    q, a = np.ones(10, dtype=np.float32), np.zeros(10, dtype=bool)
    assert list(reduce(v, q, a, 24)) == list(range(10))
    assert list(reduce(v, q, a, 4)) == list(reduce(v, q, a, 4)) and len(reduce(v, q, a, 4)) == 4


def test_scores_are_the_same_for_the_same_faces_in_any_order():
    cast = Cast(15)
    cast.person("p")
    faces = [(i + 1, cast.face("p", age=i)) for i in range(30)]
    probe = cast.face("p", age=7)
    results = set()
    for shuffle in range(5):
        order = list(faces)
        random.Random(shuffle).shuffle(order)
        g = Gallery(Rules(exemplars=1000))
        g.set(1, [(f, v, 1.0, False) for f, v in order])
        results.add(round(g.scores(probe)[1], 5))
    assert len(results) == 1


def test_a_better_quality_face_counts_for_more_in_a_score():
    g = Gallery(Rules())
    rng = np.random.default_rng(7)
    a = unit(rng.normal(size=64))
    b = unit(a + 0.8 * rng.normal(size=64))
    probe = unit(a + 0.1 * rng.normal(size=64))
    g.set(1, [(1, a, 1.0, False), (2, b, 0.2, False)])
    heavy = g.scores(probe, floor=-1)[1]
    g.set(1, [(1, a, 0.2, False), (2, b, 1.0, False)])
    light = g.scores(probe, floor=-1)[1]
    assert heavy > light


def test_a_gallery_of_thousands_of_groups_answers_quickly():
    cast = Cast(16)
    g = Gallery(Rules())
    for p in range(1, 2001):
        for k in range(3):
            g.add(p, p * 10 + k, cast.strangers(1)[0] if k == 0 else unit(np.random.default_rng(p).normal(size=128)), 1.0)
    probe = cast.strangers(1)[0]
    g.scores(probe)
    t = time.perf_counter()
    for _ in range(20):
        g.scores(probe)
    per_face = (time.perf_counter() - t) / 20
    t = time.perf_counter()
    g.pairs(g.persons()[:500], Rules().group)
    pairs = time.perf_counter() - t
    print(f"\n2000 groups: a face is scored in {per_face * 1000:.1f} ms; all pairs of 500 groups in {pairs:.2f} s")
    assert per_face < 0.05 and pairs < 20


# ---------------------------------------------------------------- the cut-offs

def test_the_cut_offs_from_the_settings_are_kept_in_a_sensible_order():
    r = Rules.from_settings({"join": 0.5, "ask": 0.9, "group": 0.4, "same": 0.8})
    assert r.ask <= r.join and r.same <= r.group
    r = Rules.from_settings({"join": 7, "ask": -3, "margin": 99})
    assert 0 < r.ask <= r.join <= 0.95 and r.margin <= 0.5
    assert Rules.from_settings({}) == Rules()


def test_decide_does_not_choose_among_groups_it_has_not_heard_of():
    assert decide({}, {}, Rules()) == Decision(None, "new", 0.0)
    assert decide({5: 0.9}, {}, Rules()).person is None      # a score for a group whose state is unknown is ignored
