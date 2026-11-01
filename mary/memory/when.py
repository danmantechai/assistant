"""Dates in words that point at the past ("yesterday", "last Tuesday", "three weeks ago", "in March") become a range
of days, found by code, so the memory can look at that stretch of the diary. Words that point forward ("tomorrow",
"next week", "in 20 minutes") are never a range: a reminder is not a memory.

Weekdays and months alone ("on Friday", "in May") are ambiguous (a plan, or a day gone by), so they count only when
the sentence is itself about the past: `cue=True` (see recall.seeks_memory).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from mary.parsers import words_to_numbers

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = [m.lower() for m in calendar.month_name[1:]]
_MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
_MONTH = "|".join(sorted([*MONTHS, *_MONTH_ABBR], key=len, reverse=True))
_DAY = "|".join(WEEKDAYS)
_NUMBER = r"\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|couple|few"


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date        # inclusive
    span: tuple[int, int]   # where the words sit in the text, to take them out of the question
    label: str


def _month(name: str) -> int:
    name = name.lower()
    return MONTHS.index(name) + 1 if name in MONTHS else _MONTH_ABBR[name[:3] if name != "sept" else "sept"]


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _last_month_day(today: date, month: int, day: int | None = None) -> tuple[date, date]:
    """The latest occurrence of that month (and day) that isn't in the future."""
    for year in (today.year, today.year - 1):
        try:
            start, end = (date(year, month, day),) * 2 if day else _month_range(year, month)
        except ValueError:
            continue
        if start <= today:
            return start, min(end, today)
    raise ValueError("no such date")


def _count(token: str) -> int:
    token = token.lower()
    if token.isdigit():
        return int(token)
    fixed = {"a": 1, "an": 1, "one": 1, "couple": 2, "few": 3}
    return fixed.get(token) or int(words_to_numbers(token).strip())


def past_range(text: str, today: date, cue: bool = False) -> DateRange | None:
    """The stretch of the past `text` points at, or None."""
    low = text.lower()

    def hit(m: re.Match, start: date, end: date, label: str) -> DateRange:
        return DateRange(start, end, m.span(), label)

    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", low):
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return hit(m, d, d, d.isoformat()) if d <= today else None
        except ValueError:
            return None
    if m := re.search(r"\bthe day before yesterday\b|\bday before yesterday\b", low):
        d = today - timedelta(days=2)
        return hit(m, d, d, "the day before yesterday")
    if m := re.search(r"\blast night\b", low):
        return hit(m, today - timedelta(days=1), today, "last night")
    if m := re.search(r"\byesterday\b", low):
        d = today - timedelta(days=1)
        return hit(m, d, d, "yesterday")
    if m := re.search(rf"\b({_NUMBER})(?:\s+of)?\s+(day|week|month|year)s?\s+ago\b", low):
        n, unit = _count(m[1]), m[2]
        if unit == "day":
            d = today - timedelta(days=n)
            return hit(m, d, d, f"{n} days ago")
        days = {"week": 7, "month": 30, "year": 365}[unit] * n
        slack = {"week": 3, "month": 10, "year": 45}[unit]
        return hit(m, today - timedelta(days=days + slack), min(today, today - timedelta(days=days - slack)),
                   f"{n} {unit}s ago")
    if m := re.search(r"\b(today|this morning|this afternoon|this evening|tonight)\b", low):
        return hit(m, today, today, "today")
    if m := re.search(r"\blast week\b|\bthe past week\b|\bpast week\b", low):
        monday = today - timedelta(days=today.weekday() + 7)
        return hit(m, monday, monday + timedelta(days=6), "last week")
    if m := re.search(r"\bthis week\b", low):
        return hit(m, today - timedelta(days=today.weekday()), today, "this week")
    if m := re.search(r"\blast month\b", low):
        first = today.replace(day=1) - timedelta(days=1)
        start, end = _month_range(first.year, first.month)
        return hit(m, start, end, "last month")
    if m := re.search(r"\bthis month\b", low):
        return hit(m, today.replace(day=1), today, "this month")
    if m := re.search(r"\blast year\b", low):
        return hit(m, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "last year")
    if m := re.search(r"\bthis year\b", low):
        return hit(m, date(today.year, 1, 1), today, "this year")
    if m := re.search(rf"\blast ({_DAY})\b", low):
        back = (today.weekday() - WEEKDAYS.index(m[1])) % 7 or 7
        d = today - timedelta(days=back)
        return hit(m, d, d, f"last {m[1]}")
    if not cue:
        return None
    if m := re.search(rf"\b(?:on|since|that)\s+({_DAY})\b", low):
        back = (today.weekday() - WEEKDAYS.index(m[1])) % 7 or 7
        d = today - timedelta(days=back)
        return hit(m, d, d, m[1])
    try:
        if m := re.search(rf"\b(?:on\s+)?(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\b", low):
            start, end = _last_month_day(today, _month(m[2]), int(m[1]))
            return hit(m, start, end, f"{m[1]} {m[2]}")
        if m := re.search(rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*(?:am|pm|:))", low):
            start, end = _last_month_day(today, _month(m[1]), int(m[2]))
            return hit(m, start, end, f"{m[1]} {m[2]}")
        if m := re.search(rf"\b(?:in|during|back in)\s+({_MONTH})\s+(\d{{4}})\b", low):
            start, end = _month_range(int(m[2]), _month(m[1]))
            return hit(m, start, min(end, today), f"{m[1]} {m[2]}") if start <= today else None
        if m := re.search(rf"\b(?:in|during|back in)\s+({_MONTH})\b", low):
            start, end = _last_month_day(today, _month(m[1]))
            return hit(m, start, end, m[1])
    except ValueError:
        return None
    return None


def strip(text: str, found: DateRange | None) -> str:
    """The question without the words that named the date."""
    if not found:
        return text
    a, b = found.span
    return (text[:a] + " " + text[b:]).strip()
