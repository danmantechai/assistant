"""The memory's database: one SQLite file, opened per thread, with numbered migrations.

Every migration is one function below. Before any migration of an existing file a copy goes to user/backups/, and
a file made by a newer MARY is refused with a sentence instead of being opened and damaged. Photos, faces and
moments (later phases) arrive as migrations of this same file.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_BACKUPS = 5


class MemoryFormatError(RuntimeError):
    """The memory file can't be used by this MARY (it was made by a newer one)."""


def _v1(c: sqlite3.Connection) -> None:
    """Records, a keyword index, vectors by space, and the markdown files MARY last wrote."""
    c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("""CREATE TABLE memory(
        id INTEGER PRIMARY KEY,
        owner TEXT NOT NULL,
        library TEXT NOT NULL,
        kind TEXT NOT NULL,              -- core | fact | diary | reflection (moment: a later phase)
        text TEXT NOT NULL,
        happened TEXT NOT NULL,          -- when it happened, or was found (local time, ISO)
        created TEXT NOT NULL,
        updated TEXT NOT NULL,
        importance REAL NOT NULL DEFAULT 0.5,
        source TEXT NOT NULL DEFAULT '', -- user | diary:<id> | profile | hand edit | memory
        ended TEXT,                      -- when it stopped being true, or was forgotten
        ended_why TEXT,                  -- forgotten | superseded | removed by hand | edited by hand
        superseded_by INTEGER,
        key TEXT NOT NULL DEFAULT '',    -- what keeps a copy kept elsewhere (profile.yaml) in step
        fmt INTEGER NOT NULL DEFAULT 1)""")
    c.execute("CREATE INDEX memory_when ON memory(owner, library, kind, happened)")
    c.execute("CREATE INDEX memory_key ON memory(owner, library, kind, key)")
    # Keywords: exact names, numbers and rare words. No stemming: "Marcus" must not match "Marcel".
    c.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(text, content='memory', content_rowid='id', "
              "tokenize='unicode61 remove_diacritics 2')")
    c.execute("CREATE VIRTUAL TABLE memory_terms USING fts5vocab(memory_fts, 'row')")
    c.execute("CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN "
              "INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text); END")
    c.execute("CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN "
              "INSERT INTO memory_fts(memory_fts, rowid, text) VALUES ('delete', old.id, old.text); END")
    c.execute("CREATE TRIGGER memory_au AFTER UPDATE OF text ON memory BEGIN "
              "INSERT INTO memory_fts(memory_fts, rowid, text) VALUES ('delete', old.id, old.text); "
              "INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text); END")
    # Meaning: one row per (space, thing). space = text now; face and image in later phases.
    c.execute("""CREATE TABLE vectors(
        space TEXT NOT NULL, ref_table TEXT NOT NULL, ref_id INTEGER NOT NULL,
        model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,
        PRIMARY KEY(space, ref_table, ref_id))""")
    # The markdown files as MARY last wrote them: a different hash later means a hand edit.
    c.execute("CREATE TABLE sources(path TEXT PRIMARY KEY, sha TEXT NOT NULL, mtime REAL NOT NULL, "
              "size INTEGER NOT NULL, exported_id INTEGER NOT NULL)")
    now = datetime.now().isoformat(timespec="seconds")
    for key, value in (("format", "1"), ("owner", uuid.uuid4().hex), ("library", uuid.uuid4().hex),
                       ("created", now)):
        c.execute("INSERT INTO meta VALUES (?, ?)", (key, value))


def _v2(c: sqlite3.Connection) -> None:
    """Photos (Phase 2): the folders you add, one record per photo, the files each photo lives in, and what you
    removed (so a rescan doesn't bring it back). Faces, people and moments arrive in later migrations."""
    c.execute("""CREATE TABLE photo_folders(
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, library TEXT NOT NULL,
        path TEXT NOT NULL, label TEXT NOT NULL, recursive INTEGER NOT NULL DEFAULT 1,
        added TEXT NOT NULL, last_scan TEXT,
        date_from TEXT, date_to TEXT, date_precision TEXT, date_scope TEXT,  -- a date the person gave the whole album
        place TEXT NOT NULL DEFAULT '',                                      -- a place the person gave the whole album
        fmt INTEGER NOT NULL DEFAULT 1)""")
    c.execute("CREATE UNIQUE INDEX photo_folders_path ON photo_folders(owner, library, path)")
    c.execute("""CREATE TABLE photos(
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, library TEXT NOT NULL,
        sha TEXT NOT NULL,                 -- of the best file's bytes
        dhash TEXT NOT NULL,               -- 64-bit difference hash of the picture as it's seen, hex
        width INTEGER NOT NULL, height INTEGER NOT NULL,    -- as seen (orientation applied)
        taken TEXT, taken_end TEXT,        -- the period it was taken in: start, and the end (not included)
        taken_precision TEXT NOT NULL DEFAULT '',            -- second | day | month | year | decade | ''
        taken_source TEXT NOT NULL DEFAULT 'none',           -- exif | filename | album | person | folder | file | none
        date_flag TEXT NOT NULL DEFAULT '',                  -- disagree: the sources of its date disagree
        exif_taken TEXT,                   -- what a camera wrote for it, apart from what MARY decided (for matching copies)
        lat REAL, lon REAL,
        place TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '',
        place_source TEXT NOT NULL DEFAULT '',               -- gps | person | ''
        text TEXT NOT NULL DEFAULT '',     -- the words read in the picture
        caption TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '',
        thumb TEXT NOT NULL DEFAULT '',    -- relative to the photos folder
        stage INTEGER NOT NULL DEFAULT 1,  -- 1 facts and place done, 2 text done
        added TEXT NOT NULL, updated TEXT NOT NULL, fmt INTEGER NOT NULL DEFAULT 1)""")
    c.execute("CREATE INDEX photos_taken ON photos(owner, library, taken)")
    c.execute("CREATE INDEX photos_dhash ON photos(owner, library, dhash)")
    c.execute("""CREATE TABLE photo_files(
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, library TEXT NOT NULL,
        photo_id INTEGER, folder_id INTEGER NOT NULL,
        path TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL, sha TEXT NOT NULL DEFAULT '',
        width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,               -- new | ok | unreadable | cloud | tiny | missing
        reason TEXT NOT NULL DEFAULT '', seen TEXT NOT NULL)""")
    c.execute("CREATE UNIQUE INDEX photo_files_path ON photo_files(owner, library, path)")
    c.execute("CREATE INDEX photo_files_sha ON photo_files(owner, library, sha)")
    c.execute("CREATE INDEX photo_files_photo ON photo_files(photo_id)")
    c.execute("CREATE INDEX photo_files_state ON photo_files(folder_id, state)")
    c.execute("""CREATE TABLE photo_ignored(
        owner TEXT NOT NULL, library TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, added TEXT NOT NULL,
        PRIMARY KEY(owner, library, kind, value))""")
    c.execute("CREATE VIRTUAL TABLE photos_fts USING fts5(text, caption, album, place, "
              "tokenize='unicode61 remove_diacritics 2')")   # rows kept by mary.photos.library, rowid = photos.id


def _v3(c: sqlite3.Connection) -> None:
    """People (Phase 3): the faces found in photos and the groups they belong to. A group is a `people` row: unnamed until the
    person names it, named, or ignored (strangers, a crowd). A face's fingerprint is a vector in space 'face' of table
    photo_faces, so deleting a face deletes its fingerprint. Nothing here is ever guessed into a name."""
    c.execute("""CREATE TABLE people(
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, library TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '', relation TEXT NOT NULL DEFAULT '',     -- only what the person typed
        state TEXT NOT NULL DEFAULT 'unnamed',                                -- unnamed | named | ignored
        created TEXT NOT NULL, updated TEXT NOT NULL, fmt INTEGER NOT NULL DEFAULT 1)""")
    c.execute("CREATE INDEX people_state ON people(owner, library, state)")
    c.execute("""CREATE TABLE photo_faces(
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, library TEXT NOT NULL, photo_id INTEGER NOT NULL,
        x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,   -- the box, as fractions of the picture as seen
        points TEXT NOT NULL DEFAULT '',          -- the five landmarks (eyes, nose, mouth corners), x,y fractions
        score REAL NOT NULL, px INTEGER NOT NULL, quality REAL NOT NULL,
        usable INTEGER NOT NULL DEFAULT 1,        -- 0: too small or too unsure to group or name (kept as a box, no fingerprint)
        person_id INTEGER, how TEXT NOT NULL DEFAULT '',     -- how it got its group: auto | you | '' (none)
        suggest_id INTEGER, suggest_score REAL,   -- a named person it might be: waiting for a yes or a no
        crop TEXT NOT NULL DEFAULT '',            -- the small face picture, relative to the photos folder
        created TEXT NOT NULL, fmt INTEGER NOT NULL DEFAULT 1)""")
    c.execute("CREATE INDEX photo_faces_photo ON photo_faces(photo_id)")
    c.execute("CREATE INDEX photo_faces_person ON photo_faces(owner, library, person_id)")
    c.execute("CREATE INDEX photo_faces_suggest ON photo_faces(owner, library, suggest_id)")
    c.execute("""CREATE TABLE people_nots(
        owner TEXT NOT NULL, library TEXT NOT NULL,
        kind TEXT NOT NULL,                       -- face: this face is not this person | same: these two groups are not one person
        a INTEGER NOT NULL, b INTEGER NOT NULL, added TEXT NOT NULL,
        PRIMARY KEY(owner, library, kind, a, b))""")
    c.execute("ALTER TABLE photos ADD COLUMN faces_model TEXT NOT NULL DEFAULT ''")   # which models have read it for faces


_SCHEMA_CACHE: dict[int, tuple[list[tuple[str, str]], dict[str, list[tuple]]]] = {}


def _expected_schema(upto: list) -> tuple[list[tuple[str, str]], dict[str, list[tuple]]]:
    key = len(upto)
    if key not in _SCHEMA_CACHE:
        fresh = sqlite3.connect(":memory:")
        try:
            for fn in upto:
                fn(fresh)
            tables = [
                (r[0], r[1] or "")
                for r in fresh.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "AND name NOT LIKE '%_fts%' AND name NOT LIKE '%_terms%'"
                ).fetchall()
            ]
            cols = {name: fresh.execute(f"PRAGMA table_info({name})").fetchall() for name, _ in tables}
            _SCHEMA_CACHE[key] = (tables, cols)
        finally:
            fresh.close()
    return _SCHEMA_CACHE[key]


def _repair_columns(c: sqlite3.Connection, upto: list) -> None:
    """Ensure every table in `c` has all columns defined by migrations in `upto` (repairs half-made or older draft files)."""
    tables, cols_by_table = _expected_schema(upto)
    existing = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for table, create_sql in tables:
        if table not in existing:
            if create_sql:
                c.execute(create_sql)
            continue
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for _, name, kind, notnull, default, _pk in cols_by_table[table]:
            if name not in have:
                tail = f" DEFAULT {default}" if default is not None else (" DEFAULT ''" if notnull else "")
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}{tail}")


def _v4(c: sqlite3.Connection) -> None:
    """Repair: a file made while the photo tables were still being written can lack a column that format 2 now has. Every column
    the current format-2 tables have and this file's don't is added (so a half-made file is brought level, not refused)."""
    _repair_columns(c, [_v1, _v2, _v3])


def _v5_tables(c: sqlite3.Connection) -> None:
    for col, spec in (
        ("sharpness", "REAL NOT NULL DEFAULT 0.0"),
        ("image_model", "TEXT NOT NULL DEFAULT ''"),
        ("seen_model", "TEXT NOT NULL DEFAULT ''"),
        ("seen_raw", "TEXT NOT NULL DEFAULT ''"),
        ("seen_faces", "TEXT NOT NULL DEFAULT ''"),
        ("seen", "TEXT NOT NULL DEFAULT ''"),
        ("tags", "TEXT NOT NULL DEFAULT ''"),
    ):
        have = {r[1] for r in c.execute("PRAGMA table_info(photos)")}
        if col not in have:
            c.execute(f"ALTER TABLE photos ADD COLUMN {col} {spec}")
    c.execute("""CREATE TABLE IF NOT EXISTS moments(
        id INTEGER PRIMARY KEY, owner TEXT NOT NULL, library TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',           -- only what the person typed when renaming
        summary TEXT NOT NULL DEFAULT '',         -- e.g. "June 1998 · the coast · a wedding · Grandma, Kofi"
        theme TEXT NOT NULL DEFAULT '',           -- event/scene phrase from album, caption or tags
        start TEXT NOT NULL DEFAULT '', end TEXT NOT NULL DEFAULT '',
        precision TEXT NOT NULL DEFAULT '',       -- second | day | month | year | decade | ''
        guess INTEGER NOT NULL DEFAULT 0,
        place TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '',
        cover_id INTEGER, photos INTEGER NOT NULL DEFAULT 0,
        mentions INTEGER NOT NULL DEFAULT 0, last_mentioned TEXT NOT NULL DEFAULT '',
        how TEXT NOT NULL DEFAULT 'auto',         -- auto | you (renamed, merged or split by the person)
        created TEXT NOT NULL, updated TEXT NOT NULL, fmt INTEGER NOT NULL DEFAULT 1)""")
    for col, spec in (
        ("mentions", "INTEGER NOT NULL DEFAULT 0"),
        ("last_mentioned", "TEXT NOT NULL DEFAULT ''"),
    ):
        have_m = {r[1] for r in c.execute("PRAGMA table_info(moments)")}
        if col not in have_m:
            c.execute(f"ALTER TABLE moments ADD COLUMN {col} {spec}")
    c.execute("CREATE INDEX IF NOT EXISTS moments_when ON moments(owner, library, start)")
    c.execute("""CREATE TABLE IF NOT EXISTS moment_photos(
        moment_id INTEGER NOT NULL, photo_id INTEGER NOT NULL PRIMARY KEY,
        owner TEXT NOT NULL, library TEXT NOT NULL,
        rank INTEGER NOT NULL DEFAULT 0,          -- 0 is the best photo (cover), then 1, 2, ...
        how TEXT NOT NULL DEFAULT 'auto')""")     # auto | you
    c.execute("CREATE INDEX IF NOT EXISTS moment_photos_moment ON moment_photos(moment_id, rank)")
    c.execute("CREATE INDEX IF NOT EXISTS moment_photos_owner ON moment_photos(owner, library, moment_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS moment_nots(
        owner TEXT NOT NULL, library TEXT NOT NULL,
        a INTEGER NOT NULL, b INTEGER NOT NULL, added TEXT NOT NULL,
        PRIMARY KEY(owner, library, a, b))""")


def _v5(c: sqlite3.Connection) -> None:
    """Seeing and moments (Phase 4): picture embeddings (stored in `vectors` under space 'image', ref_table 'photos'),
    a vision description and tags per photo (with face boxes numbered [1], [2] so names come only from `people`),
    and moments (photos grouped by time and place into events)."""
    _v5_tables(c)
    _repair_columns(c, [_v1, _v2, _v3, _v5_tables])
    c.execute("DROP TABLE IF EXISTS photos_fts")
    c.execute("CREATE VIRTUAL TABLE photos_fts USING fts5(text, caption, album, place, seen, tags, "
              "tokenize='unicode61 remove_diacritics 2')")
    c.execute("INSERT INTO photos_fts(rowid, text, caption, album, place, seen, tags) "
              "SELECT id, text, caption, album, trim(place || ' ' || region || ' ' || country), seen, tags FROM photos")


def _v6_tables(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS persona_links(
        owner TEXT NOT NULL, library TEXT NOT NULL,
        persona TEXT NOT NULL,                    -- the persona's folder name (user/personas/<persona>)
        person_id INTEGER NOT NULL,               -- the person (in `people`) this persona is modelled on
        born INTEGER NOT NULL DEFAULT 0,          -- their birth year when the person said it (0: not said), for "your 60th birthday"
        created TEXT NOT NULL DEFAULT '', fmt INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(owner, library, persona))""")
    c.execute("CREATE INDEX IF NOT EXISTS persona_links_person ON persona_links(owner, library, person_id)")


def _v6(c: sqlite3.Connection) -> None:
    """Persona memories (Phase 6): a persona can be modelled on one person in `people`; moments that person is in reach her as
    her own memories."""
    _v6_tables(c)
    _repair_columns(c, SCHEMA_STEPS)


MIGRATIONS = [_v1, _v2, _v3, _v4, _v5, _v6]
SCHEMA_STEPS = [_v1, _v2, _v3, _v5_tables, _v6_tables]   # what _repair_columns brings an already-current file level with


class Database:
    def __init__(self, path: Path, backups: Path | None = None):
        self.path = Path(path)
        self.backups = Path(backups) if backups else self.path.parent.parent / "backups"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._guard = threading.Lock()
        self.changed = 0           # counts commits made through tx(), for caches
        self._migrate()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            c.row_factory = sqlite3.Row
            # secure_delete: a deleted memory is overwritten in the file, not just unlinked.
            for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON", "secure_delete=ON"):
                c.execute(f"PRAGMA {pragma}")
            self._local.conn, self._local.depth = c, 0
            with self._guard:
                self._all.append(c)
        return c

    @contextmanager
    def tx(self):
        """One transaction (joins an open one on this thread)."""
        c = self.conn()
        if self._local.depth:
            yield c
            return
        c.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            self._local.depth = 0
        with self._guard:
            self.changed += 1

    def _migrate(self) -> None:
        c = self.conn()
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise MemoryFormatError(
                f"The memory file {self.path.name} was made by a newer MARY (format {version}, this one reads up "
                f"to {len(MIGRATIONS)}). Update MARY, or move that file aside to start a new memory.")
        if version == len(MIGRATIONS):
            with self.tx():
                _repair_columns(c, SCHEMA_STEPS)
            return
        if version > 0:
            self._backup(version)
        for number in range(version, len(MIGRATIONS)):
            with self.tx():
                MIGRATIONS[number](c)
                c.execute(f"PRAGMA user_version = {number + 1}")
            log.info("memory file migrated to format %d", number + 1)

    def _backup(self, version: int) -> None:
        self.backups.mkdir(parents=True, exist_ok=True)
        target = self.backups / f"memory-v{version}-{datetime.now():%Y%m%d-%H%M%S}.db"
        dest = sqlite3.connect(target)
        try:
            self.conn().backup(dest)
        finally:
            dest.close()
        for old in sorted(self.backups.glob("memory-v*.db"))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)

    def meta(self, key: str, default: str = "") -> str:
        row = self.conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))

    def close(self) -> None:
        with self._guard:
            conns, self._all = self._all, []
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()
