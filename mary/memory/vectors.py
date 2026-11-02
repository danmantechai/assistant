"""Vectors by space (text now; face and image in later phases), kept in the database and, for search, in one numpy
matrix per space: at tens of thousands of records a brute-force comparison takes milliseconds (measured: 50,000 x
384 in 11 ms), so no separate vector database is needed. A writer here updates the matrix in step, so a search never
has to rebuild it."""
from __future__ import annotations

import threading

import numpy as np

from mary.memory.db import Database


class _Space:
    """The vectors of one (space, table), loaded from the database on first use."""

    def __init__(self, db: Database, space: str, table: str):
        self.dim = 0
        self.n = 0
        self.ids = np.zeros(0, dtype=np.int64)
        self.mat = np.zeros((0, 0), dtype=np.float32)
        self.pos: dict[int, int] = {}
        rows = db.conn().execute("SELECT ref_id, dim, vec FROM vectors WHERE space=? AND ref_table=?",
                                 (space, table)).fetchall()
        if rows:
            self.dim = rows[0]["dim"]
            rows = [r for r in rows if r["dim"] == self.dim]
            self.ids = np.array([r["ref_id"] for r in rows], dtype=np.int64)
            self.mat = np.vstack([np.frombuffer(r["vec"], dtype="<f4") for r in rows]).astype(np.float32)
            self.n = len(rows)
            self.pos = {int(i): k for k, i in enumerate(self.ids)}

    def put(self, ref_id: int, vec: np.ndarray) -> None:
        if vec.shape[0] != self.dim:
            if self.n:  # another model: its vectors can't be compared with the old ones
                raise ValueError(f"vector of {vec.shape[0]} numbers in a space of {self.dim}")
            self.dim, self.mat = vec.shape[0], np.zeros((0, vec.shape[0]), dtype=np.float32)
        k = self.pos.get(ref_id)
        if k is None:
            if self.n == len(self.ids):  # grow by half again
                size = max(64, self.n + self.n // 2)
                self.ids = np.concatenate([self.ids, np.zeros(size - self.n, dtype=np.int64)])
                self.mat = np.vstack([self.mat, np.zeros((size - self.n, self.dim), dtype=np.float32)])
            k = self.n
            self.n += 1
            self.pos[ref_id] = k
            self.ids[k] = ref_id
        self.mat[k] = vec

    def drop(self, ref_id: int) -> None:
        k = self.pos.pop(ref_id, None)
        if k is None:
            return
        last = self.n - 1
        if k != last:  # the last row fills the hole
            self.mat[k], self.ids[k] = self.mat[last], self.ids[last]
            self.pos[int(self.ids[k])] = k
        self.mat[last] = 0
        self.n -= 1


class VectorIndex:
    def __init__(self, db: Database):
        self.db = db
        self._spaces: dict[tuple[str, str], _Space] = {}
        self._lock = threading.RLock()

    def _space(self, space: str, table: str) -> _Space:
        key = (space, table)
        if key not in self._spaces:
            self._spaces[key] = _Space(self.db, space, table)
        return self._spaces[key]

    def put(self, space: str, ref_id: int, vec: np.ndarray, model: str, table: str = "memory") -> None:
        vec = np.asarray(vec, dtype=np.float32)
        with self._lock:
            s = self._space(space, table)
            with self.db.tx() as c:
                c.execute("INSERT INTO vectors VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(space, ref_table, ref_id) "
                          "DO UPDATE SET model=excluded.model, dim=excluded.dim, vec=excluded.vec",
                          (space, table, ref_id, model, int(vec.shape[0]), vec.astype("<f4").tobytes()))
                s.put(ref_id, vec)

    def put_many(self, space: str, ref_ids: list[int], vecs: np.ndarray, model: str, table: str = "memory") -> None:
        """Many at once, in one transaction (a batch of new records, an import)."""
        vecs = np.asarray(vecs, dtype=np.float32)
        with self._lock:
            s = self._space(space, table)
            with self.db.tx() as c:
                c.executemany("INSERT INTO vectors VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(space, ref_table, ref_id) "
                              "DO UPDATE SET model=excluded.model, dim=excluded.dim, vec=excluded.vec",
                              [(space, table, int(i), model, int(v.shape[0]), v.astype("<f4").tobytes())
                               for i, v in zip(ref_ids, vecs)])
                for i, v in zip(ref_ids, vecs):
                    s.put(int(i), v)

    def delete(self, table: str, ref_id: int) -> None:
        """Every vector of this thing, in every space."""
        with self._lock:
            with self.db.tx() as c:
                c.execute("DELETE FROM vectors WHERE ref_table=? AND ref_id=?", (table, ref_id))
            for (space, t), s in self._spaces.items():
                if t == table:
                    s.drop(ref_id)

    def delete_space(self, space: str, table: str, ref_id: int | None = None) -> None:
        """Drop one space's vector for `ref_id` (or all rows in `(space, table)` when `ref_id` is None)."""
        with self._lock:
            with self.db.tx() as c:
                if ref_id is None:
                    c.execute("DELETE FROM vectors WHERE space=? AND ref_table=?", (space, table))
                else:
                    c.execute("DELETE FROM vectors WHERE space=? AND ref_table=? AND ref_id=?", (space, table, int(ref_id)))
            s = self._spaces.get((space, table))
            if s is not None:
                if ref_id is None:
                    self._spaces.pop((space, table), None)
                else:
                    s.drop(int(ref_id))

    def purge_other_models(self, space: str, model: str, table: str = "memory") -> int:
        """Drop the vectors another model made (they must be made again)."""
        with self._lock:
            with self.db.tx() as c:
                gone = [r[0] for r in c.execute("SELECT ref_id FROM vectors WHERE space=? AND ref_table=? "
                                                "AND model<>?", (space, table, model))]
                c.execute("DELETE FROM vectors WHERE space=? AND ref_table=? AND model<>?", (space, table, model))
            if gone:
                s = self._space(space, table)
                for ref_id in gone:
                    s.drop(ref_id)
            return len(gone)

    def count(self, space: str, table: str = "memory") -> int:
        with self._lock:
            return self._space(space, table).n

    def get(self, space: str, ref_id: int, table: str = "memory") -> np.ndarray | None:
        with self._lock:
            s = self._space(space, table)
            k = s.pos.get(ref_id)
            return None if k is None else s.mat[k].copy()

    def top(self, space: str, query: np.ndarray, k: int, allowed: np.ndarray | None = None,
            table: str = "memory") -> list[tuple[int, float]]:
        """The k closest (ref_id, cosine), best first. `allowed`: only these ref_ids may be returned."""
        with self._lock:
            s = self._space(space, table)
            if not s.n or query.shape[0] != s.dim:
                return []
            sims = s.mat[:s.n] @ np.asarray(query, dtype=np.float32)
            if allowed is not None:
                sims = np.where(np.isin(s.ids[:s.n], allowed), sims, -2.0)
            k = min(k, s.n)
            best = np.argpartition(-sims, k - 1)[:k] if k < s.n else np.arange(s.n)
            best = best[np.argsort(-sims[best])]
            return [(int(s.ids[i]), float(sims[i])) for i in best if sims[i] > -2.0]

    def snapshot(self, space: str, table: str = "memory") -> tuple[np.ndarray, np.ndarray]:
        """(ref_ids, vectors) of everything in a space, as copies: for a pass that reads them all (grouping faces)."""
        with self._lock:
            s = self._space(space, table)
            return s.ids[:s.n].copy(), s.mat[:s.n].copy()

    def reload(self) -> None:
        with self._lock:
            self._spaces.clear()
