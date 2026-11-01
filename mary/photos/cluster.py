"""The rules that decide whose face a face is. Pure numpy: no database, no files, no model, so the rules are tested on invented
fingerprints whose answers are known.

A fingerprint is a unit-length vector; two faces of one person have a high cosine, two people a low one. What MARY knows about a
group is a few *exemplars*: fingerprints picked to cover its different looks (a person across thirty years has several), kept
by farthest-point choice, so a face is compared with the nearest looks, not with an average that matches no photo. A face's
score against a group is the quality-weighted mean of its best matches among the exemplars (a few more of them for a bigger
group, so that the luckiest of many comparisons cannot pass for a likeness). Two groups are one person when they are alike on
average, or when several distinct pairs of their faces are strongly alike (a bridge: the years between are there too).

The decision for a new face (`decide`) is deliberately cautious, because a wrong name is worse than a question:
  - very like one named person, clearly ahead of the next named person, and no other group clearly closer: it joins them;
  - possibly a named person: it is not given a name; it waits as a question ("Is this Ama?");
  - like an unnamed group: it joins that group; like an ignored group: it joins that; otherwise it starts a new group
    (and groups that turn out to be one person are merged afterwards, `consolidate`, on how alike they are on average).
Nothing here ever makes up a name: a name is a string the person typed, which this module never sees.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

CHUNK = 1024


@dataclass(frozen=True)
class Rules:
    join: float = 0.55        # at least this like a named person (and clearly ahead of the others): joins them
    margin: float = 0.08      # "clearly ahead": by at least this
    ask: float = 0.40         # from here to `join`: possibly them, so ask
    group: float = 0.45       # two unnamed groups at least this alike (on average) are one
    alone: float = 0.05       # a single face needs this much more than that to join a group: a wrong join can't be undone by
                              # tidying, a pile left in two can
    same: float = 0.30        # two groups at least this alike might be one person: ask (never joined for you)
    top: int = 3              # how many of the best matches a score is the mean of, at least
    exemplars: int = 24       # fingerprints kept to describe one group
    bridge: float = 0.10      # a bridge between two groups counts when its pairs are this much past `group`
    bridge_pairs: int = 3     # ... and there are this many distinct faces on each side

    @property
    def floor(self) -> float:
        return min(self.ask, self.group, self.same)

    @staticmethod
    def from_settings(s: dict) -> "Rules":
        """The cut-offs from the settings (photos.faces), kept in a sensible order whatever was typed."""
        base = Rules()

        def pick(name, lo, hi):
            try:
                value = float(s.get(name, getattr(base, name)))
            except (TypeError, ValueError):
                value = getattr(base, name)
            return min(hi, max(lo, value)) if value == value else getattr(base, name)     # a NaN is not a number to use

        join = pick("join", 0.2, 0.95)
        group = pick("group", 0.2, 0.95)
        return Rules(join=join, margin=pick("margin", 0.0, 0.5), ask=min(join, pick("ask", 0.1, 0.95)), group=group,
                     alone=pick("alone", 0.0, 0.3), same=min(group, pick("same", 0.1, 0.95)), top=base.top,
                     exemplars=base.exemplars)


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def best_of(n: int, top: int) -> int:
    """How many of a group's n exemplars a score is the mean of: a few, and more as the group grows."""
    return max(top, n // 6)


def topk_mean(sims: np.ndarray, weights: np.ndarray, k: int) -> float:
    """The quality-weighted mean of the k largest similarities."""
    n = sims.size
    if n == 0:
        return -1.0
    idx = np.argpartition(-sims, k - 1)[:k] if n > k else np.arange(n)
    w = weights[idx]
    total = float(w.sum())
    return float((sims[idx] * w).sum() / total) if total > 0 else float(sims[idx].mean())


def reduce(vecs: np.ndarray, quality: np.ndarray, anchor: np.ndarray, cap: int) -> np.ndarray:
    """The indices of at most `cap` rows that cover the group's looks: faces the person confirmed first, then each next one the
    furthest from those already kept (the best quality starts)."""
    n = len(vecs)
    if n <= cap:
        return np.arange(n)
    order = np.lexsort((np.arange(n), -quality, ~anchor))      # anchors first, then best quality, then position
    keep = [int(i) for i in order[: int(min(cap, anchor.sum()))]] or [int(order[0])]
    nearest = (vecs @ vecs[keep].T).max(axis=1)
    while len(keep) < cap:
        nearest[keep] = 2.0
        pick = int(np.argmin(nearest))
        keep.append(pick)
        nearest = np.maximum(nearest, vecs @ vecs[pick])
    return np.array(sorted(keep))


class _Group:
    __slots__ = ("ids", "vecs", "q", "anchor")

    def __init__(self, ids, vecs, q, anchor):
        self.ids, self.vecs, self.q, self.anchor = ids, vecs, q, anchor


class Gallery:
    """The exemplars of every group, and the scores of faces and groups against them. Not thread-safe: the caller locks."""

    def __init__(self, rules: Rules | None = None):
        self.rules = rules or Rules()
        self._g: dict[int, _Group] = {}
        self._stack: tuple | None = None

    # ------------------------------------------------------------------ keeping it

    def _make(self, ids, vecs, q, anchor) -> _Group:
        ids = np.asarray(ids, dtype=np.int64)
        vecs = np.asarray(vecs, dtype=np.float32).reshape(len(ids), -1)
        q = np.asarray(q, dtype=np.float32)
        anchor = np.asarray(anchor, dtype=bool)
        keep = reduce(vecs, q, anchor, self.rules.exemplars)
        return _Group(ids[keep], vecs[keep], q[keep], anchor[keep])

    def set(self, person: int, faces: Iterable[tuple]) -> None:
        """(Re)build one group from all its faces: (face_id, vector, quality, confirmed_by_the_person)."""
        faces = list(faces)
        self._stack = None
        if not faces:
            self._g.pop(person, None)
            return
        ids, vecs, q, anchor = zip(*faces)
        self._g[person] = self._make(ids, np.vstack([unit(v) for v in vecs]), q, anchor)

    def add(self, person: int, face_id: int, vec, quality: float = 1.0, anchor: bool = False) -> None:
        g = self._g.get(person)
        v = unit(vec)[None, :]
        self._stack = None
        if g is None:
            self._g[person] = _Group(np.array([face_id], dtype=np.int64), v, np.array([quality], dtype=np.float32),
                                     np.array([anchor]))
            return
        self._g[person] = self._make(np.append(g.ids, face_id), np.vstack([g.vecs, v]), np.append(g.q, quality),
                                     np.append(g.anchor, anchor))

    def drop(self, person: int) -> None:
        self._g.pop(person, None)
        self._stack = None

    def absorb(self, keep: int, other: int) -> None:
        """`other` is now part of `keep`: one set of exemplars for both."""
        a, b = self._g.get(keep), self._g.pop(other, None)
        self._stack = None
        if b is None:
            return
        if a is None:
            self._g[keep] = b
            return
        self._g[keep] = self._make(np.append(a.ids, b.ids), np.vstack([a.vecs, b.vecs]), np.append(a.q, b.q),
                                   np.append(a.anchor, b.anchor))

    def persons(self) -> list[int]:
        return sorted(self._g)

    def size(self, person: int) -> int:
        g = self._g.get(person)
        return 0 if g is None else len(g.ids)

    def __contains__(self, person: int) -> bool:
        return person in self._g

    def __len__(self) -> int:
        return len(self._g)

    def exemplar_ids(self, person: int) -> list[int]:
        g = self._g.get(person)
        return [] if g is None else [int(i) for i in g.ids]

    # ------------------------------------------------------------------ scores

    def _stacked(self):
        if self._stack is None:
            ids = sorted(self._g)
            if not ids:
                self._stack = (np.zeros((0, 0), dtype=np.float32), np.zeros(0, dtype=np.int64), ids,
                               np.zeros(0, dtype=np.int64))
            else:
                mats = [self._g[i].vecs for i in ids]
                starts = np.concatenate([[0], np.cumsum([len(m) for m in mats[:-1]])]).astype(np.int64)
                self._stack = (np.vstack(mats), starts, ids, np.concatenate([self._g[i].ids for i in ids]))
        return self._stack

    def scores(self, vec, floor: float | None = None, skip=(), leave_out: int | None = None) -> dict[int, float]:
        """Group -> score, for the groups this fingerprint is at least `floor` like (their best match, at least).
        `leave_out`: a face that is already one of the exemplars, so it is not scored against itself."""
        mat, starts, ids, fids = self._stacked()
        if not ids:
            return {}
        sims = mat @ unit(vec)
        left = None if leave_out is None else fids == leave_out
        if left is not None and left.any():
            sims = np.where(left, -2.0, sims)
        else:
            left = None
        best = np.maximum.reduceat(sims, starts)
        floor = self.rules.floor if floor is None else floor
        out = {}
        for j in np.nonzero(best >= floor)[0]:
            p = ids[int(j)]
            if p in skip:
                continue
            g = self._g[p]
            lo = int(starts[j])
            s, q = sims[lo: lo + len(g.vecs)], g.q
            if left is not None:
                keep = ~left[lo: lo + len(g.vecs)]
                s, q = s[keep], q[keep]
            out[p] = topk_mean(s, q, best_of(len(s), self.rules.top))
        return out

    def between(self, a: int, b: int) -> float:
        """How alike two groups are (-1 when one is unknown): how alike their faces are on average, or, if that is higher,
        what a bridge says: several distinct faces of each, every one strongly like a face of the other (the same person's
        looks in neighbouring years), counted a little less (`bridge`) than an average of the same height."""
        ga, gb = self._g.get(a), self._g.get(b)
        if ga is None or gb is None:
            return -1.0
        sims = ga.vecs @ gb.vecs.T
        w = np.outer(ga.q, gb.q)
        total = float(w.sum())
        average = float((sims * w).sum() / total) if total > 0 else float(sims.mean())
        m = min(self.rules.bridge_pairs, len(ga.ids), len(gb.ids))
        rows = np.sort(sims.max(axis=1))[::-1][m - 1]
        cols = np.sort(sims.max(axis=0))[::-1][m - 1]
        return max(average, float(min(rows, cols)) - self.rules.bridge)

    def partners(self, person: int, others: Iterable[int], floor: float) -> dict[int, float]:
        """`between` for one group against many, those at least `floor`."""
        g = self._g.get(person)
        out = {}
        if g is None:
            return out
        others = [o for o in others if o in self._g and o != person]
        for o in others:
            if float((g.vecs @ self._g[o].vecs.T).max()) >= floor:
                s = self.between(person, o)
                if s >= floor:
                    out[o] = s
        return out

    def pairs(self, persons: Iterable[int], floor: float) -> dict[tuple[int, int], float]:
        """`between` for every pair among these groups that is at least `floor` (a cheap best-match screen first, which no
        pair that passes can fail, so thousands of groups take a second, not a minute)."""
        persons = [p for p in sorted(set(persons)) if p in self._g]
        out: dict[tuple[int, int], float] = {}
        if len(persons) < 2:
            return out
        mats = [self._g[p].vecs for p in persons]
        sizes = np.array([len(m) for m in mats])
        starts = np.concatenate([[0], np.cumsum(sizes[:-1])]).astype(np.int64)
        mat = np.vstack(mats)
        g = 0
        while g < len(persons):
            h, rows = g, 0
            while h < len(persons) and (rows == 0 or rows + sizes[h] <= CHUNK):
                rows += int(sizes[h])
                h += 1
            block = mat[starts[g]: starts[g] + rows] @ mat.T
            cols = np.maximum.reduceat(block, starts, axis=1)
            top1 = np.maximum.reduceat(cols, starts[g:h] - starts[g], axis=0)
            for i in range(h - g):
                for j in np.nonzero(top1[i] >= floor)[0]:
                    j = int(j)
                    if j > g + i:
                        s = self.between(persons[g + i], persons[j])
                        if s >= floor:
                            out[(persons[g + i], persons[j])] = s
            g = h
        return out


# ---------------------------------------------------------------------- the decision


@dataclass(frozen=True)
class Decision:
    person: int | None       # the group it goes to; None: a new group
    reason: str              # named | ignored | group | new
    score: float             # its score against `person` (0 for a new group)
    ask: int | None = None   # a named person it might be, to ask about (never joined)
    ask_score: float = 0.0


def decide(scores: dict[int, float], state: dict[int, str], rules: Rules, refused: frozenset | set = frozenset()) -> Decision:
    """Where a new face goes. `scores` are its scores against the groups, `state` each group's state (named | unnamed |
    ignored), `refused` the groups the person has said this face is not."""
    named = sorted(((s, p) for p, s in scores.items() if state.get(p) == "named" and p not in refused),
                   key=lambda t: (-t[0], t[1]))
    other = sorted(((s, p) for p, s in scores.items() if state.get(p) in ("unnamed", "ignored")
                    and p not in refused), key=lambda t: (-t[0], t[1]))
    best_n, who_n = named[0] if named else (0.0, None)
    second_n = named[1][0] if len(named) > 1 else -1.0
    best_o, who_o = other[0] if other else (0.0, None)
    ask, ask_score = None, 0.0
    if who_n is not None and best_n >= rules.ask:
        clear = best_n >= rules.join and best_n - second_n >= rules.margin and best_o - best_n < rules.margin
        if clear:
            return Decision(who_n, "named", best_n)
        if not (who_o is not None and state[who_o] == "ignored" and best_o >= best_n):
            ask, ask_score = who_n, best_n          # possibly them: not joined, asked
    if who_o is not None and best_o >= rules.group + rules.alone:
        return Decision(who_o, "ignored" if state[who_o] == "ignored" else "group", best_o, ask, ask_score)
    return Decision(None, "new", 0.0, ask, ask_score)


def consolidate(gallery: Gallery, persons: Iterable[int], sizes: dict[int, int], rules: Rules,
                refused: set[tuple[int, int]], focus: Iterable[int] | None = None,
                photos: dict[int, set] | None = None) -> list[tuple[int, int]]:
    """Merge unnamed groups that are clearly one person (the order faces arrived in can leave a person in two piles).
    Returns (kept, absorbed) in the order they were merged; the gallery is updated to match. `refused` holds the pairs
    the person said are not one person (a, b with a < b); it is extended so the answer survives a merge. With `focus`, only
    the pairs that include one of those groups are looked at (the ones that just changed). `photos` says which photos each
    group has faces in: two groups with a face in the same photo are two people (nobody is in a photo twice), never merged."""
    alive = set(persons)
    sizes = dict(sizes)
    photos = {k: set(v) for k, v in (photos or {}).items()}
    if focus is None:
        found = gallery.pairs(alive, rules.group)
    else:
        found = {}
        for f in focus:
            for other, s in gallery.partners(f, alive, rules.group).items():
                found[(min(f, other), max(f, other))] = s
    scored = {k: s for k, s in found.items() if k not in refused}
    merges = []
    while scored:
        (a, b), _ = max(scored.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
        del scored[(a, b)]
        if a not in alive or b not in alive:
            continue
        if photos.get(a, set()) & photos.get(b, set()):
            refused.add((a, b))
            continue
        keep, drop = (a, b) if (sizes.get(a, 0), -a) >= (sizes.get(b, 0), -b) else (b, a)
        gallery.absorb(keep, drop)
        alive.discard(drop)
        sizes[keep] = sizes.get(keep, 0) + sizes.get(drop, 0)
        photos[keep] = photos.get(keep, set()) | photos.pop(drop, set())
        merges.append((keep, drop))
        for pair in [p for p in refused if drop in p]:
            refused.add(tuple(sorted((keep if x == drop else x for x in pair))))
        for pair in [k for k in scored if keep in k or drop in k]:
            del scored[pair]
        for other, s in gallery.partners(keep, alive, rules.group).items():
            pair = (min(keep, other), max(keep, other))
            if pair not in refused:
                scored[pair] = s
    return merges
