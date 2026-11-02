"""When a photo was taken, and how MARY knows. Every date carries where it came from and how precise it is, and a guess is
never passed off as a fact.

In order of trust:
  exif      what the camera wrote (a camera that never had its clock set writes a default, which is ignored)
  filename  a date in the file's name: IMG_20231225_101500, WhatsApp Image 2023-12-25 at 10.15.00, Screenshot_20231225-...
  person    what you told MARY for this photo: always wins
  album     what you told MARY for a whole album (it can be taken back, and reaches photos that arrive later)
  folder    the album's name ("Christmas 2019" = December 2019, "Summer 2015" = 2015, "Scans 1990s" = the 1990s): a guess
  file      the file's own date: a guess (copying a file can change it)
  none      nothing says

`flag` is "disagree" when the sources of a date disagree (a camera date of 2024 in an album called "Christmas 2019":
a print photographed later), so the page can ask rather than trust either.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

PRECISIONS = ("second", "day", "month", "year", "decade")
MIN_YEAR = 1900        # the oldest an album's name can put a photo
MIN_DIGITAL = 1990     # nothing digital is older: a camera or file date before this is not true
_DEFAULTS = {(1, 1, 0, 0, 0)}     # a camera's clock before it was ever set: Jan 1, midnight


@dataclass(frozen=True)
class Taken:
    start: datetime | None = None
    end: datetime | None = None          # not included
    precision: str = ""
    source: str = "none"
    flag: str = ""

    @property
    def guess(self) -> bool:
        """Is this an estimate (from the album's name, or the file's own date)? The page says so, and so does she."""
        return self.source in ("folder", "file")

    @property
    def known(self) -> bool:
        return self.start is not None


NONE = Taken()


def span_end(start: datetime, precision: str) -> datetime:
    if precision == "second":
        return start + timedelta(seconds=1)
    if precision == "day":
        return start + timedelta(days=1)
    if precision == "month":
        return datetime(start.year + (start.month == 12), start.month % 12 + 1, 1)
    if precision == "year":
        return datetime(start.year + 1, 1, 1)
    if precision == "decade":
        return datetime(start.year // 10 * 10 + 10, 1, 1)
    raise ValueError(precision)


def make(start: datetime, precision: str, source: str, flag: str = "") -> Taken:
    return Taken(start, span_end(start, precision), precision, source, flag)


def plausible(when: datetime, today: date) -> bool:
    if when.year < MIN_DIGITAL or when.date() > today + timedelta(days=1):
        return False
    return not ((when.month, when.day, when.hour, when.minute, when.second) in _DEFAULTS and when.year <= 2005)


# ---------------------------------------------------------------- the camera and the file name


def from_exif(when: datetime | None, today: date) -> Taken:
    if when is None or not plausible(when, today):
        return NONE
    return make(when.replace(microsecond=0), "second", "exif")


_STAMP = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
                    r"(?:(?:[ _T.-]+(?:at )?|[-_.]?)([01]\d|2[0-3])[.:_-]?([0-5]\d)[.:_-]?([0-5]\d)(?:\d{3})?)?(?!\d)")


def from_filename(name: str, today: date) -> Taken:
    """A date in a file's name (the stem, not the folder). Eight digits that aren't a date are not one."""
    stem = name.rsplit(".", 1)[0]
    for m in _STAMP.finditer(stem):
        y, mo, d, h, mi, s = m.groups()
        try:
            when = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
        except ValueError:
            continue
        if not plausible(when, today):
            continue
        return make(when, "second" if h else "day", "filename")
    return NONE


def from_file(mtime: float | None, today: date) -> Taken:
    """The file's own date, as a guess: copying or editing a file can change it."""
    if mtime is None or mtime <= 0:
        return NONE
    try:
        when = datetime.fromtimestamp(mtime)
    except (OverflowError, OSError, ValueError):
        return NONE
    if when.year < MIN_DIGITAL or when.date() > today + timedelta(days=1):
        return NONE
    return make(datetime(when.year, when.month, when.day), "day", "file")


# ---------------------------------------------------------------- the album's name

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS |= {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name} | {"sept": 9}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YEAR = r"(?<!\d)((?:19|20)\d{2})(?!\d)"


def from_folder_name(part: str, today: date) -> Taken:
    """What one folder's name says about when (nothing, for most names)."""
    text = part.strip()
    low = text.lower()

    def year(v) -> int | None:
        v = int(v)
        return v if MIN_YEAR <= v <= today.year else None

    if m := re.search(rf"{_YEAR}[-_. ](0[1-9]|1[0-2])[-_. ](0[1-9]|[12]\d|3[01])(?!\d)", low):    # 2019-12-25
        if (y := year(m[1])):
            try:
                return make(datetime(y, int(m[2]), int(m[3])), "day", "folder")
            except ValueError:
                pass
    if m := re.search(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[ -]({_MONTH_NAMES})[a-z]*[ ,-]*{_YEAR}", low):   # 25 Dec 2019
        if (y := year(m[3])):
            try:
                return make(datetime(y, _MONTHS[m[2]], int(m[1])), "day", "folder")
            except ValueError:
                pass
    if m := re.search(rf"\b({_MONTH_NAMES})\b[a-z]*[ ,._-]*{_YEAR}", low):                      # December 2019
        if (y := year(m[2])):
            return make(datetime(y, _MONTHS[m[1]], 1), "month", "folder")
    if m := re.search(rf"{_YEAR}[-_. ](0[1-9]|1[0-2])(?!\d)", low):                              # 2019-12
        if (y := year(m[1])):
            return make(datetime(y, int(m[2]), 1), "month", "folder")
    if m := re.search(rf"(?<!\d)(0[1-9]|1[0-2])[-_. ]{_YEAR}", low):                             # 12.2019
        if (y := year(m[2])):
            return make(datetime(y, int(m[1]), 1), "month", "folder")
    if m := re.search(rf"\bchristmas\b[ ,._-]*{_YEAR}", low):
        if (y := year(m[1])):
            return make(datetime(y, 12, 1), "month", "folder")
    if m := re.search(r"(?<!\d)((?:19|20)\d)0s\b", low):                                          # 1990s
        y = int(m[1]) * 10
        if MIN_YEAR <= y <= today.year:
            return make(datetime(y, 1, 1), "decade", "folder")
    years = re.findall(_YEAR, low)
    if len(years) == 1 and (y := year(years[0])):                                                # 1998, Summer 2015
        return make(datetime(y, 1, 1), "year", "folder")
    return NONE


def from_folders(parts: list[str], today: date) -> Taken:
    """The date the album says: the nearest folder that names one (parts run from the added folder down to the file's)."""
    for part in reversed(parts):
        found = from_folder_name(part, today)
        if found.known:
            return found
    return NONE


# ---------------------------------------------------------------- putting them together


def _disagree(stated: Taken, album: Taken) -> bool:
    """Does a date someone or something stated sit far from the period the album's name gives?"""
    if not album.known or not stated.known:
        return False
    low, high = album.start.year - 1, max(album.end.year, album.start.year + 1)   # a year either side is not a quarrel
    return not low <= stated.start.year <= high


def decide(exif: datetime | None, name: str, folders: list[str], mtime: float | None, today: date | None = None,
           person: Taken | None = None, album: Taken | None = None, album_scope: str = "unsure") -> Taken:
    """The best date for a photo and where it came from. `person`: what you set for this photo (always wins); `album`:
    what you set for its whole album, for photos whose date is a guess ("unsure") or for every photo ("all")."""
    today = today or date.today()
    if person is not None and person.known:
        return person
    exif_t = from_exif(exif, today)
    name_t = from_filename(name, today)
    folder_t = from_folders(folders, today)
    stated = exif_t if exif_t.known else name_t
    if album is not None and album.known and (album_scope == "all" or not stated.known):
        return replace(album, source="album", flag="")
    if stated.known:
        flag = "disagree" if _disagree(stated, folder_t) else ""
        if exif_t.known and name_t.known and abs((exif_t.start.date() - name_t.start.date()).days) > 1:
            flag = "disagree"
        return replace(stated, flag=flag)
    if folder_t.known:
        return folder_t
    return from_file(mtime, today)


def person_date(year: int, month: int | None = None, day: int | None = None, decade: bool = False) -> Taken:
    """A date you gave: as exact as you were ('1995', 'June 1995', '4 June 1995', 'the 1990s')."""
    if decade:
        return make(datetime(year // 10 * 10, 1, 1), "decade", "person")
    if month is None:
        return make(datetime(year, 1, 1), "year", "person")
    if day is None:
        return make(datetime(year, month, 1), "month", "person")
    return make(datetime(year, month, day), "day", "person")


def label(t: Taken) -> str:
    """The date in words, as precise as it is known ('25 December 2023', 'December 2019', 'the 1990s', '2015')."""
    if not t.known:
        return "no date"
    s = t.start
    if t.precision == "decade":
        return f"the {s.year // 10 * 10}s"
    if t.precision == "year":
        return str(s.year)
    if t.precision == "month":
        return f"{calendar.month_name[s.month]} {s.year}"
    out = f"{s.day} {calendar.month_name[s.month]} {s.year}"
    return out + (f", {s:%H:%M}" if t.precision == "second" else "")


@dataclass(frozen=True)
class QueryWhen:
    start: datetime
    end: datetime
    label: str
    span: tuple[int, int] = (0, 0)
    explicit: bool = True      # True when a specific year/decade/month/day was named; False for relative phrases like 'last Christmas'
    holiday: str = ""          # e.g. 'christmas' when the query asked about Christmas
    derived: bool = False      # worked out (from a birth year), not said: she says so


_SEASONS = {
    "spring": (3, 6),
    "summer": (6, 9),
    "autumn": (9, 12),
    "fall": (9, 12),
    "winter": (12, 3),
}


def parse_query_when(text: str, today: date | None = None) -> QueryWhen | None:
    """Find a date or time period mentioned in a question or search query ('June 1998', 'Christmas 2023',
    'last Christmas', 'the 1990s', '1985', 'summer 2021', 'last year')."""
    if not text or not text.strip():
        return None
    today = today or clock_today()
    low = text.lower()

    # 1. Explicit season + year ('summer 2021', 'in the summer of 1998')
    if m := re.search(rf"\b(spring|summer|autumn|fall|winter)\b(?:\s+of)?\s+{_YEAR}", low):
        season, y = m[1], int(m[2])
        if MIN_YEAR <= y <= today.year:
            m_start, m_end = _SEASONS[season]
            start = datetime(y, m_start, 1)
            end = datetime(y + 1, m_end, 1) if m_end < m_start else datetime(y, m_end, 1)
            return QueryWhen(start, end, f"{season} {y}", m.span(), explicit=True, holiday=season)

    # 2. Explicit Christmas + year ('Christmas 2023', 'Christmas of 2019')
    if m := re.search(rf"\bchristmas\b(?:\s+of)?[ ,._-]*{_YEAR}", low):
        y = int(m[1])
        if MIN_YEAR <= y <= today.year:
            t = make(datetime(y, 12, 1), "month", "person")
            return QueryWhen(t.start, t.end, f"Christmas {y}", m.span(), explicit=True, holiday="christmas")

    # 3. Relative Christmas ('last Christmas', 'this Christmas')
    if m := re.search(r"\b(last|this)\s+christmas\b", low):
        y = today.year - 1 if m[1] == "last" else today.year
        t = make(datetime(y, 12, 1), "month", "person")
        return QueryWhen(t.start, t.end, f"{m[1]} Christmas", m.span(), explicit=False, holiday="christmas")

    # 4. Relative season ('last summer', 'this summer', 'last winter')
    if m := re.search(r"\b(last|this)\s+(spring|summer|autumn|fall|winter)\b", low):
        which, season = m[1], m[2]
        y = today.year - 1 if which == "last" else today.year
        m_start, m_end = _SEASONS[season]
        start = datetime(y, m_start, 1)
        end = datetime(y + 1, m_end, 1) if m_end < m_start else datetime(y, m_end, 1)
        return QueryWhen(start, end, f"{which} {season}", m.span(), explicit=False, holiday=season)

    # 5. Any explicit date, month+year, decade ('1990s'), or year ('1998') that `from_folder_name` understands
    t = from_folder_name(text, today)
    if t.known:
        # Find the matched span so callers can strip the date words from the query if needed
        span = (0, 0)
        for pat in (
            rf"{_YEAR}[-_. ](0[1-9]|1[0-2])[-_. ](0[1-9]|[12]\d|3[01])(?!\d)",
            rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[ -]({_MONTH_NAMES})[a-z]*[ ,-]*{_YEAR}",
            rf"\b({_MONTH_NAMES})\b[a-z]*[ ,._-]*{_YEAR}",
            rf"{_YEAR}[-_. ](0[1-9]|1[0-2])(?!\d)",
            rf"(?<!\d)(0[1-9]|1[0-2])[-_. ]{_YEAR}",
            r"\b(?:the\s+)?(?<!\d)((?:19|20)\d)0s\b",
            _YEAR,
        ):
            if sm := re.search(pat, low):
                span = sm.span()
                break
        return QueryWhen(t.start, t.end, label(t), span, explicit=True)

    # 6. Relative conversational expressions ('last year', 'two years ago', 'last month', 'yesterday')
    from mary.memory import when as mem_when

    pr = mem_when.past_range(text, today, cue=False)
    if pr is not None:
        start, end, lbl, span = pr
        has_year = bool(re.search(_YEAR, low))
        return QueryWhen(start, end, lbl, span, explicit=has_year)

    return None


_AGES = (
    (re.compile(r"\byour\s+(\d{1,3}(?:st|nd|rd|th))\s+(?:birthday|b-?day)\b"), 0),      # the year they turned it
    (re.compile(r"\byou\s+turned\s+(\d{1,3})\b"), 0),
    (re.compile(r"\bwhen\s+you\s+were\s+(\d{1,3})\b"), 1),                              # ...until the next birthday: two years
)


def parse_age_when(text: str, born: int, today: date | None = None) -> QueryWhen | None:
    """A time in the life of the person a persona is modelled on, from their birth year ('your 60th birthday', 'when you were
    30'). Only the persona's own age counts: 'my 60th birthday' is someone else's. Worked out, so it says so."""
    if not text or not born:
        return None
    today = today or clock_today()
    low = text.lower()
    for pattern, extra in _AGES:
        m = pattern.search(low)
        if not m:
            continue
        age = int(re.match(r"\d+", m[1])[0])
        year = born + age
        if not MIN_YEAR <= year <= today.year or age > 120:
            return None
        return QueryWhen(datetime(year, 1, 1), datetime(year + 1 + extra, 1, 1),
                         f"{year}" + (f" to {year + 1}" if extra else ""), m.span(1), explicit=True, derived=True)
    return None


def clock_today() -> date:
    from mary.memory import clock

    return clock.now().date()

