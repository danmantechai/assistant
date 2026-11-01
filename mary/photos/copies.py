"""Is this the same photo as one MARY already has? A copy, a smaller version, a re-saved one, one stored on its side with
a tag: all the same photo, kept once with several files. Two different photos must never be merged, so the rule is strict
and what is merely alike is only offered to you.

Same photo:
  - the same bytes, or
  - a difference hash within 3 of 64 bits, the same shape (within 2%), and no quarrel between the cameras' own times
    (two frames of a burst can have one hash; their camera times, a second apart, tell them apart)
Might be the same (never merged on its own): within 10 bits and 5% of the same shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

SAME_BITS = 3
ALIKE_BITS = 10
SAME_SHAPE = 0.02
ALIKE_SHAPE = 0.05


@dataclass(frozen=True)
class Look:
    """What matching needs to know about a picture."""
    sha: str
    dhash: int
    width: int
    height: int
    exif: datetime | None = None     # the camera's own time, when it wrote one


def bits(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def shape(look: Look) -> float:
    return look.width / look.height if look.height else 0.0


def alike_shape(a: Look, b: Look, tolerance: float) -> bool:
    ra, rb = shape(a), shape(b)
    return rb > 0 and abs(ra - rb) / rb <= tolerance


def same_photo(a: Look, b: Look) -> bool:
    if a.sha and a.sha == b.sha:
        return True
    if bits(a.dhash, b.dhash) > SAME_BITS or not alike_shape(a, b, SAME_SHAPE):
        return False
    if a.exif is not None and b.exif is not None and a.exif != b.exif:
        return False          # two moments, however alike the pictures
    return True


def might_be(a: Look, b: Look) -> bool:
    return not same_photo(a, b) and bits(a.dhash, b.dhash) <= ALIKE_BITS and alike_shape(a, b, ALIKE_SHAPE)


class HashIndex:
    """The difference hashes of every photo, in one numpy array, so 'who is within 3 bits?' takes microseconds even
    with tens of thousands of photos."""

    def __init__(self, items: list[tuple[int, int]] = ()):
        self.ids = np.array([i for i, _ in items], dtype=np.int64)
        self.hashes = np.array([h for _, h in items], dtype=np.uint64)

    def add(self, photo_id: int, dhash: int) -> None:
        self.ids = np.append(self.ids, np.int64(photo_id))
        self.hashes = np.append(self.hashes, np.uint64(dhash))

    def remove(self, photo_id: int) -> None:
        keep = self.ids != photo_id
        self.ids, self.hashes = self.ids[keep], self.hashes[keep]

    def update(self, photo_id: int, dhash: int) -> None:
        self.remove(photo_id)
        self.add(photo_id, dhash)

    def within(self, dhash: int, bits_: int) -> list[tuple[int, int]]:
        """(photo id, bits apart) for every photo within `bits_` of this hash, nearest first."""
        if not len(self.ids):
            return []
        apart = np.bitwise_count(self.hashes ^ np.uint64(dhash))
        close = np.nonzero(apart <= bits_)[0]
        order = close[np.argsort(apart[close], kind="stable")]
        return [(int(self.ids[i]), int(apart[i])) for i in order]
