"""calculate: sums, percentages, dates and unit conversions worked out exactly by code, never in the model's head
(a 4B model said 575 for 15 × 23).

The model writes one expression; Python's own parser (ast) reads it, and only these are accepted: numbers, + - * /
// % ** (^ is power too), brackets, the constants pi, e and tau, text only as the argument of a date or unit
function, and calls to the functions in FUNCTIONS. No names, attributes, indexing, lists, lambdas or anything else,
so nothing can reach files, modules or the network. Numbers are exact fractions while they can be (0.1 + 0.2 is
0.3, 1/3 * 3 is 1); roots, logs and trigonometry are floating point, shown to 12 significant figures. Limits keep
it quick: at most MAX_CHARS characters and MAX_NODES parts, and no number past MAX_DIGITS digits.
"""
from __future__ import annotations

import ast
import calendar
import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any, Callable

from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("Any sum, percentage, average, date question (days between, what day it falls on, adding months) or unit "
         "conversion: calculate. Never work numbers out in your head.")

MAX_CHARS = 300
MAX_NODES = 200
MAX_DIGITS = 1000
MAX_TEXT = 40
_MAX_BITS = int(MAX_DIGITS * 3.33)

_today: Callable[[], date] = date.today      # swapped in tests
_now: Callable[[], datetime] = datetime.now


class CalcError(ValueError):
    """Why an expression can't be worked out, as a plain sentence."""


@dataclass(frozen=True)
class Qty:
    """A converted amount: its value and the unit it's in, as asked for ("mi")."""
    value: Fraction | float
    unit: str


Number = Fraction | float


# ------------------------------------------------------------------ numbers

def _number(x: Any, what: str = "that") -> Number:
    if isinstance(x, Qty):
        x = x.value
    if isinstance(x, bool) or not isinstance(x, (Fraction, float, int)):
        raise CalcError(f"{_kind(x)} can't be used as a number in {what}.")
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float) and not math.isfinite(x):
        raise CalcError("The result is too big to work out.")
    return x


def _whole(x: Any, what: str) -> int:
    n = _number(x, what)
    if n != int(n):
        raise CalcError(f"{what} needs a whole number.")
    return int(n)


def _kind(x: Any) -> str:
    return {str: "Text", date: "A date", datetime: "A date and time", timedelta: "A length of time"}.get(
        type(x), type(x).__name__)


def _check(x: Any) -> Any:
    """Refuse numbers past MAX_DIGITS digits (and keep floats finite)."""
    if isinstance(x, Fraction) and max(abs(x.numerator), x.denominator).bit_length() > _MAX_BITS:
        raise CalcError(f"The result has more than {MAX_DIGITS} digits, which is too big to work out.")
    if isinstance(x, float) and not math.isfinite(x):
        raise CalcError("The result is too big to work out.")
    if isinstance(x, Qty):
        _check(x.value)
    return x


def _float(x: Any) -> float:
    try:
        return float(_number(x))
    except OverflowError:
        raise CalcError("That number is too big for this function.") from None


def _exact_root(x: Fraction, n: int) -> Fraction | None:
    """x ** (1/n) when it's a fraction (sqrt(16/9) is exactly 4/3), else None."""
    if x < 0 or n <= 0:
        return None
    roots = []
    for part in (x.numerator, x.denominator):
        guess = round(part ** (1.0 / n)) if part.bit_length() < 1000 else None
        hit = next((g for g in (guess - 1, guess, guess + 1) if g is not None and g >= 0 and g ** n == part), None)
        if hit is None:
            return None
        roots.append(hit)
    return Fraction(roots[0], roots[1])


def _pow(a: Any, b: Any) -> Number:
    a, b = _number(a, "a power"), _number(b, "a power")
    if isinstance(b, Fraction) and b.denominator == 1 and isinstance(a, Fraction):
        n = int(b)
        size = max(abs(a.numerator), a.denominator)
        if size > 1 and abs(n) * math.log10(size) > MAX_DIGITS:
            raise CalcError(f"The result has more than {MAX_DIGITS} digits, which is too big to work out.")
        if a == 0 and n < 0:
            raise ZeroDivisionError
        return a ** n
    if isinstance(a, Fraction) and isinstance(b, Fraction) and b.denominator <= 12:
        root = _exact_root(a, b.denominator)
        if root is not None:
            return _pow(root, b.numerator)
    if a < 0 and (not isinstance(b, Fraction) or b.denominator % 2 == 0):
        raise CalcError("That has no real answer (an even root of a negative number).")
    if a < 0:  # an odd root of a negative number is negative: (-8) ** (1/3) is -2
        return -_pow(-a, b)
    try:
        return math.pow(float(a), float(b))
    except OverflowError:
        raise CalcError("The result is too big to work out.") from None
    except ValueError:
        raise CalcError("That has no real answer.") from None


def _sqrt(x: Any) -> Number:
    x = _number(x, "sqrt")
    if x < 0:
        raise CalcError("A negative number has no real square root.")
    return _pow(x, Fraction(1, 2))


def _root(x: Any, n: Any) -> Number:
    n = _whole(n, "root")
    if not 1 <= abs(n) <= 1000:
        raise CalcError("root() takes a root between 1 and 1000.")
    return _pow(x, Fraction(1, n))


def _round(x: Any, digits: Any = 0) -> Number:
    """Halves round away from zero, as on a calculator (Python's own round gives 2 for 2.5)."""
    digits = _whole(digits, "round")
    if not -100 <= digits <= 100:
        raise CalcError("round() takes at most 100 digits.")
    x = _number(x, "round")
    exact = x if isinstance(x, Fraction) else Fraction(repr(x))
    scale = Fraction(10) ** digits
    v = abs(exact) * scale
    out = Fraction(math.floor(v + Fraction(1, 2))) / scale
    out = out if exact >= 0 else -out
    return out if isinstance(x, Fraction) else float(out)


def _float_fn(fn: Callable[..., float], name: str) -> Callable[..., Number]:
    def call(*args: Any) -> Number:
        try:
            return fn(*(_float(a) for a in args))
        except ValueError:
            raise CalcError(f"{name}() isn't defined for that number.") from None
        except OverflowError:
            raise CalcError("The result is too big to work out.") from None
    return call


def _trig(fn: Callable[[float], float], name: str) -> Callable[[Any], Number]:
    """sin, cos and tan, with the float noise at their zeros taken off: sin(pi) is 0, not 1.2e-16."""
    def call(x: Any) -> Number:
        y = _float_fn(fn, name)(x)
        return 0.0 if abs(y) < 1e-12 else y
    return call


def _log(x: Any, base: Any = None) -> Number:
    x = _number(x, "log")
    if x <= 0:
        raise CalcError("A logarithm needs a number above zero.")
    if base is None:
        return math.log(x) if isinstance(x, float) else _float_fn(math.log, "log")(x)
    b = _number(base, "log")
    if b <= 0 or b == 1:
        raise CalcError("A logarithm's base must be above zero and not 1.")
    if isinstance(x, Fraction) and isinstance(b, Fraction) and b.denominator == 1 and x.denominator == 1:
        n = round(math.log(float(x)) / math.log(float(b))) if x.numerator.bit_length() < 1000 else None
        if n is not None and n >= 0 and Fraction(b) ** n == x:
            return Fraction(n)  # log(1000, 10) is exactly 3
    return _float_fn(math.log, "log")(x, b)


def _factorial(n: Any) -> Fraction:
    n = _whole(n, "factorial")
    if n < 0:
        raise CalcError("factorial() needs a whole number of 0 or more.")
    if n > 2000:
        raise CalcError(f"The result has more than {MAX_DIGITS} digits, which is too big to work out.")
    return Fraction(math.factorial(n))


def _ints(name: str, values: tuple) -> list[int]:
    if not values:
        raise CalcError(f"{name}() needs at least one number.")
    return [_whole(v, name) for v in values]


def _numbers(name: str, values: tuple) -> list[Number]:
    if not values:
        raise CalcError(f"{name}() needs at least one number.")
    return [_number(v, name) for v in values]


def _percent(p: Any, of: Any) -> Number:
    return _number(p, "percent") / 100 * _number(of, "percent")


def _percent_change(old: Any, new: Any) -> Number:
    old, new = _number(old, "percent_change"), _number(new, "percent_change")
    if old == 0:
        raise CalcError("A change from zero has no percentage.")
    return (new - old) / abs(old) * 100


# ------------------------------------------------------------------ dates and times

_DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y",
                 "%A %d %B %Y", "%A, %d %B %Y", "%A, %B %d, %Y"]
_TIME_FORMATS = ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]


def _parse_date(text: str) -> date | datetime:
    t = re.sub(r"\s+", " ", text.strip().replace(".", "")).strip()
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t, flags=re.I)  # "25th December 2026"
    word = t.lower()
    if word in ("today", "now"):
        return _now().replace(second=0, microsecond=0) if word == "now" else _today()
    if word in ("tomorrow", "yesterday"):
        return _today() + timedelta(days=1 if word == "tomorrow" else -1)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    if re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", t):
        raise CalcError(f"'{text}' could be day/month or month/day; write dates as 2026-12-25.")
    raise CalcError(f"'{text}' isn't a date I can read; write dates as 2026-12-25.")


def _as_date(x: Any, name: str) -> date | datetime:
    if isinstance(x, str):
        return _parse_date(x)
    if isinstance(x, (date, datetime)):
        return x
    raise CalcError(f"{name}() needs a date, like '2026-12-25' or today().")


def _date(*args: Any) -> date | datetime:
    if len(args) == 1:
        return _as_date(args[0], "date")
    if len(args) == 3:
        y, m, d = (_whole(a, "date") for a in args)
        try:
            return date(y, m, d)
        except ValueError as e:
            raise CalcError(f"That isn't a real date ({e}).") from None
    raise CalcError("date() takes a date as text ('2026-12-25') or as year, month, day.")


def _span(unit: str) -> Callable[[Any], timedelta]:
    def make(n: Any) -> timedelta:
        try:
            return timedelta(**{unit: _float(n)})
        except OverflowError:
            raise CalcError("That's too long a time to work with.") from None
    return make


def _add_months(d: Any, n: Any) -> date | datetime:
    """The same day n months on; a day the month doesn't have becomes its last (31 January + 1 month is 28 or 29
    February)."""
    d, n = _as_date(d, "add_months"), _whole(n, "add_months")
    index = d.year * 12 + d.month - 1 + n
    year, month = divmod(index, 12)
    if not 1 <= year <= 9999:
        raise CalcError("That date is out of range.")
    return d.replace(year=year, month=month + 1, day=min(d.day, calendar.monthrange(year, month + 1)[1]))


def _add_years(d: Any, n: Any) -> date | datetime:
    return _add_months(d, _whole(n, "add_years") * 12)


def _plain_date(x: date | datetime) -> date:
    return x.date() if isinstance(x, datetime) else x


def _days_between(a: Any, b: Any) -> Fraction:
    return Fraction((_plain_date(_as_date(b, "days_between")) - _plain_date(_as_date(a, "days_between"))).days)


def _years_between(a: Any, b: Any) -> Fraction:
    """Whole years from a to b, as birthdays count them."""
    a, b = _plain_date(_as_date(a, "years_between")), _plain_date(_as_date(b, "years_between"))
    sign = 1
    if b < a:
        a, b, sign = b, a, -1
    years = b.year - a.year - ((b.month, b.day) < (a.month, a.day))
    return Fraction(sign * years)


def _age(birth: Any, on: Any = None) -> Fraction:
    return _years_between(birth, _today() if on is None else on)


def _weekday(d: Any) -> str:
    return f"{_as_date(d, 'weekday'):%A}"


def _days_in_month(year: Any, month: Any) -> Fraction:
    y, m = _whole(year, "days_in_month"), _whole(month, "days_in_month")
    if not (1 <= y <= 9999 and 1 <= m <= 12):
        raise CalcError("days_in_month() takes a year and a month from 1 to 12.")
    return Fraction(calendar.monthrange(y, m)[1])


def _is_leap_year(year: Any) -> bool:
    return calendar.isleap(_whole(year, "is_leap_year"))


# ------------------------------------------------------------------ units

def _units() -> dict[str, tuple[str, Fraction]]:
    """name -> (what it measures, size in that measure's base unit), every factor exact by definition."""
    F = Fraction
    table = {
        "length": {"mm": F(1, 1000), "cm": F(1, 100), "m": F(1), "km": F(1000), "in": F("0.0254"),
                   "ft": F("0.3048"), "yd": F("0.9144"), "mi": F("1609.344"), "nmi": F(1852)},
        "mass": {"mg": F(1, 10**6), "g": F(1, 1000), "kg": F(1), "t": F(1000), "oz": F("0.028349523125"),
                 "lb": F("0.45359237"), "st": F("6.35029318")},
        "volume": {"ml": F(1, 1000), "cl": F(1, 100), "dl": F(1, 10), "l": F(1), "m3": F(1000),
                   "tsp": F("0.00492892159375"), "tbsp": F("0.01478676478125"), "floz": F("0.0295735295625"),
                   "cup": F("0.2365882365"), "pt": F("0.473176473"), "qt": F("0.946352946"),
                   "gal": F("3.785411784"), "ukgal": F("4.54609"), "ukpt": F("0.56826125")},
        "area": {"mm2": F(1, 10**6), "cm2": F(1, 10**4), "m2": F(1), "km2": F(10**6), "ha": F(10**4),
                 "acre": F("4046.8564224"), "in2": F("0.00064516"), "ft2": F("0.09290304"),
                 "yd2": F("0.83612736"), "mi2": F("2589988.110336")},
        "time": {"ms": F(1, 1000), "s": F(1), "min": F(60), "h": F(3600), "day": F(86400), "week": F(604800)},
        "speed": {"m/s": F(1), "km/h": F(1000, 3600), "mph": F("1609.344") / 3600, "kn": F(1852, 3600),
                  "ft/s": F("0.3048")},
        "data": {"bit": F(1, 8), "b": F(1), "kb": F(10**3), "mb": F(10**6), "gb": F(10**9), "tb": F(10**12),
                 "kib": F(2**10), "mib": F(2**20), "gib": F(2**30), "tib": F(2**40), "kbit": F(1000, 8),
                 "mbit": F(10**6, 8), "gbit": F(10**9, 8)},
        "energy": {"j": F(1), "kj": F(1000), "cal": F("4.184"), "kcal": F(4184), "wh": F(3600), "kwh": F(3600000)},
        "power": {"w": F(1), "kw": F(1000), "hp": F("745.69987158227022")},
        "pressure": {"pa": F(1), "hpa": F(100), "kpa": F(1000), "bar": F(10**5), "mbar": F(100),
                     "psi": F("6894.757293168361"), "atm": F(101325), "mmhg": F("133.322387415")},
    }
    return {unit: (kind, size) for kind, units in table.items() for unit, size in units.items()}


UNITS = _units()
_TEMPERATURE = {"c", "f", "k"}
_SPELLED = {  # other ways to write a unit -> its name in UNITS
    "meter": "m", "metre": "m", "kilometer": "km", "kilometre": "km", "centimeter": "cm", "centimetre": "cm",
    "millimeter": "mm", "millimetre": "mm", "inch": "in", "inches": "in", "foot": "ft", "feet": "ft", "yard": "yd",
    "mile": "mi", "nauticalmile": "nmi", "gram": "g", "kilogram": "kg", "kilo": "kg", "milligram": "mg",
    "tonne": "t", "metricton": "t", "pound": "lb", "lbs": "lb", "ounce": "oz", "stone": "st", "liter": "l",
    "litre": "l", "milliliter": "ml", "millilitre": "ml", "gallon": "gal", "usgal": "gal", "usgallon": "gal",
    "pint": "pt", "uspint": "pt",
    "quart": "qt", "teaspoon": "tsp", "tablespoon": "tbsp", "fluidounce": "floz", "ukgallon": "ukgal",
    "imperialgallon": "ukgal", "ukpint": "ukpt", "imperialpint": "ukpt", "squaremeter": "m2", "squaremetre": "m2",
    "sqm": "m2", "squarekilometer": "km2", "squarekilometre": "km2", "sqkm": "km2", "hectare": "ha",
    "squarefoot": "ft2", "squarefeet": "ft2", "sqft": "ft2", "squareinch": "in2", "squareyard": "yd2",
    "squaremile": "mi2", "sqmi": "mi2", "second": "s", "sec": "s", "minute": "min", "hour": "h", "hr": "h",
    "millisecond": "ms", "d": "day", "wk": "week", "kmh": "km/h", "kph": "km/h", "kmph": "km/h",
    "kilometer/hour": "km/h", "kilometre/hour": "km/h", "km/hour": "km/h", "mile/hour": "mph", "mi/h": "mph",
    "knot": "kn", "kt": "kn", "mps": "m/s", "meter/second": "m/s", "metre/second": "m/s", "m/sec": "m/s",
    "fps": "ft/s", "foot/second": "ft/s", "feet/second": "ft/s", "celsius": "c",
    "centigrade": "c", "fahrenheit": "f", "kelvin": "k", "byte": "b", "kilobyte": "kb", "megabyte": "mb",
    "gigabyte": "gb", "terabyte": "tb", "kibibyte": "kib", "mebibyte": "mib", "gibibyte": "gib", "tebibyte": "tib",
    "kilobit": "kbit", "megabit": "mbit", "gigabit": "gbit", "joule": "j", "kilojoule": "kj", "calorie": "cal",
    "kilocalorie": "kcal", "watthour": "wh", "kilowatthour": "kwh", "watt": "w", "kilowatt": "kw",
    "horsepower": "hp", "pascal": "pa", "hectopascal": "hpa", "kilopascal": "kpa", "millibar": "mbar",
    "atmosphere": "atm",
}


def unit_name(text: str) -> str:
    """'Kilometres' -> 'km', '°F' -> 'f', 'sq ft' -> 'ft2'; CalcError when it isn't a unit this knows."""
    t = str(text).strip().lower().replace("°", "").replace("²", "2").replace("³", "3").replace(" per ", "/")
    t = re.sub(r"[\s_.-]+", "", t).replace("s/", "/")   # "kilometers/hour" -> "kilometer/hour"
    for candidate in (t, t[:-1] if t.endswith("s") else None, t[:-2] if t.endswith("es") else None):
        if candidate and (candidate in UNITS or candidate in _TEMPERATURE):
            return candidate
        if candidate and candidate in _SPELLED:
            return _SPELLED[candidate]
    raise CalcError(f"'{text}' isn't a unit I know.")


def _to_kelvin(v: Number, unit: str) -> Number:
    return {"c": v + Fraction("273.15"), "f": (v - 32) * Fraction(5, 9) + Fraction("273.15"), "k": v}[unit]


def _from_kelvin(v: Number, unit: str) -> Number:
    return {"c": v - Fraction("273.15"), "f": (v - Fraction("273.15")) * Fraction(9, 5) + 32, "k": v}[unit]


def _convert(value: Any, source: Any, target: Any) -> Qty:
    if not isinstance(source, str) or not isinstance(target, str):
        raise CalcError("convert() takes a number and two units as text: convert(5, 'km', 'mi').")
    v, a, b = _number(value, "convert"), unit_name(source), unit_name(target)
    if a in _TEMPERATURE or b in _TEMPERATURE:
        if not (a in _TEMPERATURE and b in _TEMPERATURE):
            raise CalcError(f"{source} and {target} don't measure the same thing.")
        return Qty(_from_kelvin(_to_kelvin(v, a), b), target.strip())
    (kind_a, size_a), (kind_b, size_b) = UNITS[a], UNITS[b]
    if kind_a != kind_b:
        raise CalcError(f"{source} is a {kind_a} and {target} is a {kind_b}; they don't convert.")
    return Qty(v * size_a / size_b, target.strip())


# ------------------------------------------------------------------ the evaluator

FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sqrt": _sqrt, "root": _root, "cbrt": lambda x: _root(x, 3), "abs": lambda x: abs(_number(x, "abs")),
    "round": _round, "floor": lambda x: Fraction(math.floor(_number(x, "floor"))),
    "ceil": lambda x: Fraction(math.ceil(_number(x, "ceil"))), "trunc": lambda x: Fraction(math.trunc(_number(x))),
    "min": lambda *v: min(_numbers("min", v)), "max": lambda *v: max(_numbers("max", v)),
    "sum": lambda *v: sum(_numbers("sum", v), Fraction(0)),
    "mean": lambda *v: sum(_numbers("mean", v), Fraction(0)) / len(v), "average": lambda *v: FUNCTIONS["mean"](*v),
    "median": lambda *v: statistics.median(_numbers("median", v)),
    "log": _log, "ln": lambda x: _log(x), "log10": lambda x: _log(x, 10), "log2": lambda x: _log(x, 2),
    "exp": _float_fn(math.exp, "exp"), "sin": _trig(math.sin, "sin"), "cos": _trig(math.cos, "cos"),
    "tan": _trig(math.tan, "tan"), "asin": _float_fn(math.asin, "asin"), "acos": _float_fn(math.acos, "acos"),
    "atan": _float_fn(math.atan, "atan"), "atan2": _float_fn(math.atan2, "atan2"),
    "degrees": _float_fn(math.degrees, "degrees"), "radians": _float_fn(math.radians, "radians"),
    "hypot": _float_fn(math.hypot, "hypot"), "factorial": _factorial,
    "gcd": lambda *v: Fraction(math.gcd(*_ints("gcd", v))), "lcm": lambda *v: Fraction(math.lcm(*_ints("lcm", v))),
    "percent": _percent, "percent_change": _percent_change,
    "today": lambda: _today(), "now": lambda: _now().replace(second=0, microsecond=0), "date": _date,
    "days": _span("days"), "weeks": _span("weeks"), "hours": _span("hours"), "minutes": _span("minutes"),
    "seconds": _span("seconds"), "add_days": lambda d, n: _as_date(d, "add_days") + _span("days")(n),
    "add_months": _add_months, "add_years": _add_years, "days_between": _days_between,
    "years_between": _years_between, "age": _age, "weekday": _weekday, "days_in_month": _days_in_month,
    "is_leap_year": _is_leap_year, "convert": _convert,
}
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_TEXT_ARGS = {"date", "add_days", "add_months", "add_years", "days_between", "years_between", "age", "weekday",
              "convert"}


def _binop(op: ast.operator, a: Any, b: Any) -> Any:
    if isinstance(a, str) or isinstance(b, str):
        raise CalcError("Text can only go into a date or unit function, like date('2026-12-25').")
    if isinstance(op, (ast.Pow, ast.BitXor)):
        return _pow(a, b)
    times = (date, datetime, timedelta)
    if isinstance(a, times) or isinstance(b, times):
        return _time_op(op, a, b)
    if isinstance(a, Qty) and isinstance(b, Qty) and a.unit == b.unit and isinstance(op, (ast.Add, ast.Sub)):
        return Qty(_binop(op, a.value, b.value), a.unit)
    if isinstance(a, Qty) and not isinstance(b, Qty) and isinstance(op, (ast.Mult, ast.Div)):
        return Qty(_binop(op, a.value, b), a.unit)
    if isinstance(b, Qty) and not isinstance(a, Qty) and isinstance(op, ast.Mult):
        return Qty(_binop(op, a, b.value), b.unit)
    a, b = _number(a), _number(b)
    if isinstance(op, ast.Add):
        return a + b
    if isinstance(op, ast.Sub):
        return a - b
    if isinstance(op, ast.Mult):
        return a * b
    if isinstance(op, ast.Div):
        return a / b
    if isinstance(op, ast.FloorDiv):
        return Fraction(a // b) if isinstance(a // b, int) else a // b
    if isinstance(op, ast.Mod):
        return a % b
    raise CalcError(f"The operator {type(op).__name__} isn't one I work with.")


def _time_op(op: ast.operator, a: Any, b: Any) -> Any:
    """Dates and times: date ± length of time, date - date, lengths added, scaled and divided."""
    span, day = timedelta, (date, datetime)
    if isinstance(a, datetime) and isinstance(b, date) and not isinstance(b, datetime):
        b = datetime.combine(b, datetime.min.time())
    if isinstance(b, datetime) and isinstance(a, date) and not isinstance(a, datetime):
        a = datetime.combine(a, datetime.min.time())
    try:
        if isinstance(op, ast.Add) and isinstance(a, day) and isinstance(b, span):
            return a + b
        if isinstance(op, ast.Add) and isinstance(a, span) and isinstance(b, day):
            return b + a
        if isinstance(op, ast.Sub) and isinstance(a, day) and isinstance(b, (span, *day)):
            return a - b
        if isinstance(op, (ast.Add, ast.Sub)) and isinstance(a, span) and isinstance(b, span):
            return a + b if isinstance(op, ast.Add) else a - b
        if isinstance(op, ast.Mult) and isinstance(a, span) and not isinstance(b, (span, *day)):
            return a * _float(b)
        if isinstance(op, ast.Mult) and isinstance(b, span) and not isinstance(a, (span, *day)):
            return b * _float(a)
        if isinstance(op, ast.Div) and isinstance(a, span) and isinstance(b, span):
            return Fraction(a / b).limit_denominator(10**9)
        if isinstance(op, ast.Div) and isinstance(a, span):
            return a / _float(b)
    except OverflowError:
        raise CalcError("That date is out of range.") from None
    raise CalcError(f"{_kind(a)} and {_kind(b)} can't be combined that way.")


class _Evaluator:
    def __init__(self, source: str):
        self.source = source

    def eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            if isinstance(node.body, ast.Constant) and isinstance(node.body.value, str):
                raise CalcError("Text on its own isn't a calculation.")
            return self.eval(node.body)
        if isinstance(node, ast.Constant):
            return self.constant(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = self.eval(node.operand)
            if isinstance(v, timedelta):
                return -v if isinstance(node.op, ast.USub) else v
            if isinstance(v, Qty):
                return Qty(-v.value if isinstance(node.op, ast.USub) else v.value, v.unit)
            v = _number(v)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            return _check(_binop(node.op, self.eval(node.left), self.eval(node.right)))
        if isinstance(node, ast.Name):
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            hint = f"; call it as {node.id}()" if node.id in FUNCTIONS else ""
            raise CalcError(f"'{node.id}' isn't something I can calculate with{hint}.")
        if isinstance(node, ast.Call):
            return self.call(node)
        raise CalcError(f"That part ({self.part(node)}) isn't allowed in a calculation.")

    def constant(self, node: ast.Constant) -> Any:
        v = node.value
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise CalcError(f"That value ({self.part(node)}) isn't allowed in a calculation.")
        if isinstance(v, str):
            if len(v) > MAX_TEXT:
                raise CalcError(f"Text in a calculation can be at most {MAX_TEXT} characters.")
            return v
        if isinstance(v, int):
            return _check(Fraction(v))
        text = (ast.get_source_segment(self.source, node) or repr(v)).replace("_", "")
        try:
            return _check(Fraction(text))  # the decimal as written: 0.1 is exactly a tenth
        except (ValueError, ZeroDivisionError):
            return float(v)

    def call(self, node: ast.Call) -> Any:
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name not in FUNCTIONS:
            if isinstance(node.func, ast.Constant):  # 2(3+4)
                raise CalcError(f"Write * for multiplication: {self.part(node.func)}*(…), not {self.part(node)}.")
            raise CalcError(f"{self.part(node.func)} isn't one of the functions I have.")
        if node.keywords:
            raise CalcError(f"{name}() takes its values in order, without names.")
        args = []
        for a in node.args:
            if isinstance(a, ast.Starred):
                raise CalcError("* in a call isn't allowed.")
            v = self.eval(a)
            if isinstance(v, str) and name not in _TEXT_ARGS:
                raise CalcError(f"{name}() takes numbers, not text.")
            args.append(v)
        try:
            return _check(FUNCTIONS[name](*args))
        except TypeError:
            raise CalcError(f"{name}() was given the wrong number or kind of values.") from None
        except OverflowError:
            raise CalcError("The result is too big to work out.") from None
        except statistics.StatisticsError as e:
            raise CalcError(f"{name}(): {e}.") from None

    def part(self, node: ast.AST) -> str:
        return (ast.get_source_segment(self.source, node) or type(node).__name__)[:40]


_SYMBOLS = {"×": "*", "÷": "/", "−": "-", "–": "-", "²": "**2", "³": "**3", "√": "sqrt"}


def normalize(expression: str) -> str:
    """Calculator ways of writing things, made Python: × ÷ −, x² and percentages. A % stuck to its number with
    nothing but an operator, a bracket or the end after it is a percentage (15% of 80 is 12, 80 * 15% is 12);
    with a space before it or a number after it, it's the remainder (10 % 3, 81 % -8, 10%3)."""
    text = expression.strip().strip("=").strip()
    for symbol, python in _SYMBOLS.items():
        text = text.replace(symbol, python)
    number = r"(\d+(?:\.\d+)?|\.\d+)"
    text = re.sub(number + r"\s*%\s*of\b", r"(\1/100)*", text, flags=re.I)
    text = re.sub(number + r"%(?=\s*(?:$|[)+\-*/,^]))", r"(\1/100)", text)
    return text


def evaluate(expression: str) -> Any:
    """The exact value of `expression`; CalcError (a plain sentence) when it can't be worked out or isn't allowed."""
    if len(expression) > MAX_CHARS:
        raise CalcError(f"That's longer than {MAX_CHARS} characters; split it up.")
    source = normalize(expression)
    if not source:
        raise CalcError("There's nothing to calculate.")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise CalcError("That isn't a calculation I can read; use numbers, + - * / ** and brackets "
                        "(write 2*(3+4), not 2(3+4)).") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalcError(f"That has more than {MAX_NODES} parts; split it up.")
    try:
        return _Evaluator(source).eval(tree)
    except ZeroDivisionError:
        raise CalcError("That divides by zero.") from None
    except RecursionError:
        raise CalcError("That's nested too deeply.") from None


# ------------------------------------------------------------------ showing results

def _grouped(n: int) -> str:
    return f"{n:,}" if abs(n) >= 10000 else str(n)


def _decimal(x: Fraction, digits: int = 12) -> tuple[str, bool]:
    """A fraction as a decimal, and whether that's exact: it is if the decimal ends within 20 places, else it's
    rounded to `digits` significant figures."""
    den = x.denominator
    twos = fives = 0
    while den % 2 == 0:
        den //= 2
        twos += 1
    while den % 5 == 0:
        den //= 5
        fives += 1
    exact = den == 1 and max(twos, fives) <= 20
    with localcontext() as ctx:
        ctx.prec = len(str(abs(x.numerator))) + 25 if exact else digits
        return _plain(Decimal(x.numerator) / Decimal(x.denominator)), exact


def _plain(d: Decimal) -> str:
    if d.is_zero():
        return "0"
    exponent = d.adjusted()
    if -7 <= exponent < 21:
        text = format(d, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        whole, _, frac = text.partition(".")
        sign = "-" if whole.startswith("-") else ""
        whole = _grouped(int(whole.lstrip("-") or "0"))
        return f"{sign}{whole}" + (f".{frac}" if frac else "")
    mantissa, _, power = f"{d.normalize():E}".partition("E")
    return f"{mantissa.rstrip('0').rstrip('.')} × 10^{int(power)}"


def show(value: Any, fraction: bool = True) -> str:
    """A result the way a person writes it: 345, 0.3, 1,048,576, 0.333333333333 (exactly 1/3), Friday 25 December
    2026, 3 days, 3.10685596119 mi. `fraction`: say the exact fraction behind a rounded decimal, when it's a
    simple one."""
    if isinstance(value, Qty):
        return f"{show(value.value, fraction=False)} {value.unit}"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return f"{value:%A} {value.day} {value:%B} {value.year}, {value:%H:%M}"
    if isinstance(value, date):
        return f"{value:%A} {value.day} {value:%B} {value.year}"
    if isinstance(value, timedelta):
        return _duration(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return _grouped(int(value))
        return _plain(Decimal(f"{value:.12g}"))
    if isinstance(value, Fraction):
        n, d = value.numerator, value.denominator
        if d == 1:
            digits = len(str(abs(n)))
            if digits <= 30:
                return _grouped(n)
            with localcontext() as ctx:
                ctx.prec = 12
                return f"{_plain(+Decimal(n))} ({digits} digits)"
        text, exact = _decimal(value)
        if fraction and not exact and abs(n) < 10**6 and d <= 1000:
            return f"{text} (exactly {n}/{d})"
        return text
    return str(value)


def _duration(span: timedelta) -> str:
    sign = "-" if span < timedelta(0) else ""
    span = abs(span)
    days, rest = span.days, span.seconds
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [(days, "day"), (hours, "hour"), (minutes, "minute"), (seconds, "second")]
    words = [f"{_grouped(n)} {unit}{'' if n == 1 else 's'}" for n, unit in parts if n]
    return sign + (", ".join(words) if words else "0 seconds")


def tools(ctx: ToolContext) -> list[Tool]:
    def calculate(expression: str) -> ToolResult:
        try:
            result = show(evaluate(expression))
        except CalcError as e:
            return ToolResult(False, f"Couldn't calculate {expression!r}: {e}")
        return ToolResult(True, f"{expression.strip()} = {result}", say=f"That's {result}.")

    return [Tool(
        "calculate",
        "Work out a calculation exactly: arithmetic, percentages, dates or unit conversions. Write one expression with "
        "numbers, + - * / // % ** and brackets; 15% of 80 works. Functions: sqrt, root(x, n), abs, round(x, digits), "
        "floor, ceil, min, max, sum, mean, median, log(x, base), ln, log10, exp, sin, cos, tan (radians; "
        "radians(degrees) converts), factorial, gcd, lcm, percent(p, of), percent_change(old, new). Dates: today(), "
        "now(), date('2026-12-25'), days(n), weeks(n), hours(n), add_months(d, n), add_years(d, n), "
        "days_between(a, b), years_between(a, b), age('1990-05-17'), weekday(d), days_in_month(y, m), "
        "is_leap_year(y); a date minus a date is days, a date plus days(10) is a date. Units: convert(5, 'km', 'mi') "
        "for length, mass, volume, area, speed, time, temperature, data, energy, power and pressure.",
        {"expression": {"type": "string",
                        "description": "e.g. 15*23+7, 15% of 80, days_between(today(), '2026-12-25'), "
                                       "convert(70, 'kg', 'lb')"}},
        ["expression"], calculate, watch=False, announce=lambda a: f"Calculating {a.get('expression', '')}…",
    )]
