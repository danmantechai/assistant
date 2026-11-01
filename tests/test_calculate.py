"""The calculate brick: random expressions checked against Python's own arithmetic (exactly, with fractions), maths
functions against the math module, dates across leap years and month ends, units both ways, and attempts to sneak
code in, all refused."""
from __future__ import annotations

import ast
import calendar
import math
import random
import time
from datetime import date, datetime, timedelta
from fractions import Fraction

import pytest

from mary.config import load_config
from mary.output import Output
from mary.skills import calculate as calc
from mary.tools.registry import ToolContext

CalcError = calc.CalcError


def value(expression: str):
    return calc.evaluate(expression)


# ------------------------------------------------------------------ arithmetic against Python

def _random_expression(rng: random.Random, depth: int = 0) -> str:
    if depth > 3 or rng.random() < 0.3:
        kind = rng.random()
        if kind < 0.6:
            return str(rng.randint(0, 99))
        if kind < 0.9:
            return f"{rng.randint(0, 99)}.{rng.randint(0, 99):02d}"
        return f"-{rng.randint(1, 9)}"
    op = rng.choice(["+", "-", "*", "/", "//", "%", "**"])
    left = _random_expression(rng, depth + 1)
    right = str(rng.randint(-3, 3)) if op == "**" else _random_expression(rng, depth + 1)
    return f"({left} {op} {right})"


class _FloorStaysExact(ast.NodeTransformer):
    """Python's Fraction // Fraction is an int, and int / int is a float: keep it a Fraction."""

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.FloorDiv):
            return ast.Call(ast.Name("floor_div", ast.Load()), [node.left, node.right], [])
        return node


def _python_exact(expression: str):
    """The same expression in Python with every number as an exact Fraction."""
    import re

    exact = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)", r'Fraction("\1")', expression)
    tree = ast.fix_missing_locations(_FloorStaysExact().visit(ast.parse(exact, mode="eval")))
    names = {"Fraction": Fraction, "floor_div": lambda a, b: Fraction(a // b), "__builtins__": {}}
    return eval(compile(tree, "<test>", "eval"), names)  # noqa: S307 - our own generated test input


def test_random_arithmetic_matches_python_exactly():
    rng = random.Random(1234)
    checked = refused = 0
    for _ in range(600):
        expression = _random_expression(rng)
        try:
            expected = _python_exact(expression)
        except ZeroDivisionError:
            with pytest.raises(CalcError, match="divides by zero"):
                value(expression)
            refused += 1
            continue
        if max(abs(expected.numerator), expected.denominator).bit_length() > 3000:
            continue
        assert value(expression) == expected, expression
        checked += 1
    assert checked > 450 and refused > 10


def test_random_arithmetic_matches_python_floats():
    """Without // and % (where a float's last digit can flip a floor), Python's plain floats agree to 9 places."""
    rng = random.Random(4321)
    checked = 0
    while checked < 400:
        expression = _random_expression(rng)
        if "//" in expression or "%" in expression:
            continue
        try:
            expected = eval(expression, {"__builtins__": {}})  # noqa: S307 - our own generated test input
        except ZeroDivisionError:
            continue
        assert float(value(expression)) == pytest.approx(expected, rel=1e-9, abs=1e-12), expression
        checked += 1


def test_decimals_are_exact():
    assert value("0.1 + 0.2") == Fraction(3, 10) and calc.show(value("0.1 + 0.2")) == "0.3"
    assert value("1/3 * 3") == 1
    assert calc.show(value("1/3")) == "0.333333333333 (exactly 1/3)"
    assert calc.show(value("1.1 * 1.1")) == "1.21"
    assert calc.show(value("15 * 23")) == "345"


@pytest.mark.parametrize("expression, shown", [
    ("2**64", "18,446,744,073,709,551,616"), ("2^10", "1024"), ("12345.5 * 2", "24,691"),
    ("1_000_000 / 8", "125,000"), ("-7 // 2", "-4"), ("7.5 % 2", "1.5"), ("2**-2", "0.25"),
    ("15% of 80", "12"), ("15 % of 80", "12"), ("80 * 15%", "12"), ("(15%)", "0.15"), ("10 % 3", "1"),
    ("10%3", "1"), ("81 % -8", "-7"), ("200 - 200 * 12.5%", "175"),
    ("3 × 4 ÷ 2 − 1", "5"), ("5²", "25"), ("√(16)", "4"), ("= 1 + 1 =", "2"),
    ("10**40", "1 × 10^40 (41 digits)"), ("1e-10", "1 × 10^-10"), ("123456789.125", "123,456,789.125"),
])
def test_how_results_are_shown(expression, shown):
    assert calc.show(value(expression)) == shown


@pytest.mark.parametrize("expression, expected", [
    ("sqrt(2)", math.sqrt(2)), ("root(27, 3)", 3), ("cbrt(-8)", -2), ("(-8)**(1/3)", -2), ("sqrt(16/9)", 4 / 3),
    ("log(1000, 10)", 3), ("log10(0.001)", -3), ("ln(e)", 1), ("log2(1024)", 10), ("exp(2)", math.exp(2)),
    ("sin(radians(30))", 0.5), ("cos(pi)", -1), ("sin(pi)", 0), ("tan(radians(45))", 1), ("degrees(pi)", 180),
    ("atan2(1, 1)", math.pi / 4), ("hypot(3, 4)", 5), ("factorial(10)", 3628800), ("gcd(12, 18, 30)", 6),
    ("lcm(4, 6)", 12), ("mean(3, 5, 10)", 6), ("average(1, 2)", 1.5), ("median(1, 2, 3, 4)", 2.5),
    ("min(3, -1, 2)", -1), ("max(3, -1, 2)", 3), ("sum(1, 2, 3.5)", 6.5), ("abs(-4.5)", 4.5),
    ("floor(-2.5)", -3), ("ceil(2.1)", 3), ("trunc(-2.7)", -2), ("round(2.5)", 3), ("round(-2.5)", -3),
    ("round(2.675, 2)", 2.68), ("round(1234.5, -2)", 1200), ("percent(15, 80)", 12),
    ("percent_change(50, 65)", 30), ("percent_change(80, 60)", -25),
])
def test_functions(expression, expected):
    assert float(calc.evaluate(expression)) == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_random_functions_match_the_math_module():
    rng = random.Random(99)
    for _ in range(300):
        x = round(rng.uniform(0.01, 500), 3)
        name, fn = rng.choice([("sqrt", math.sqrt), ("ln", math.log), ("log10", math.log10), ("exp", math.exp),
                               ("sin", math.sin), ("cos", math.cos), ("atan", math.atan)])
        if name == "exp" and x > 700:
            continue
        expected = fn(x)
        got = float(value(f"{name}({x})"))
        assert got == pytest.approx(expected, rel=1e-12, abs=1e-12), (name, x)


@pytest.mark.parametrize("expression, why", [
    ("1/0", "divides by zero"), ("5 % 0", "divides by zero"), ("0**-1", "divides by zero"),
    ("sqrt(-1)", "no real square root"), ("(-8)**0.5", "no real answer"), ("log(0)", "above zero"),
    ("log(8, 1)", "not 1"), ("asin(2)", "isn't defined"), ("factorial(-1)", "0 or more"),
    ("factorial(2.5)", "whole number"), ("percent_change(0, 5)", "from zero"), ("mean()", "at least one"),
    ("2(3+4)", r"Write \* for multiplication"), ("today", "call it as today()"), ("x + 1", "'x' isn't something"),
    ("", "nothing to calculate"), ("1 +", "isn't a calculation I can read"),
])
def test_plain_reasons_when_it_cant(expression, why):
    with pytest.raises(CalcError, match=why):
        value(expression)


# ------------------------------------------------------------------ dates

@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(calc, "_today", lambda: date(2026, 11, 1))
    monkeypatch.setattr(calc, "_now", lambda: datetime(2026, 11, 1, 9, 30, 15))


def test_leap_years():
    assert [value(f"is_leap_year({y})") for y in (1900, 2000, 2023, 2024, 2100, 2400)] == \
        [False, True, False, True, False, True]
    assert [int(value(f"days_in_month({y}, 2)")) for y in (1900, 2000, 2024, 2100)] == [28, 29, 29, 28]


@pytest.mark.parametrize("expression, expected", [
    ("date('2024-02-28') + days(1)", date(2024, 2, 29)),
    ("date('2023-02-28') + days(1)", date(2023, 3, 1)),
    ("date('2100-02-28') + days(1)", date(2100, 3, 1)),
    ("date('2000-02-28') + days(1)", date(2000, 2, 29)),
    ("date('2024-02-29') + days(365)", date(2025, 2, 28)),
    ("date('2024-12-31') + days(1)", date(2025, 1, 1)),
    ("date('2024-03-01') - days(1)", date(2024, 2, 29)),
    ("add_months('2024-01-31', 1)", date(2024, 2, 29)),
    ("add_months('2023-01-31', 1)", date(2023, 2, 28)),
    ("add_months('2024-03-31', -1)", date(2024, 2, 29)),
    ("add_months('2024-10-31', 1)", date(2024, 11, 30)),
    ("add_months('2024-12-15', 1)", date(2025, 1, 15)),
    ("add_months('2024-01-15', -13)", date(2022, 12, 15)),
    ("add_years('2024-02-29', 1)", date(2025, 2, 28)),
    ("add_years('2024-02-29', 4)", date(2028, 2, 29)),
    ("add_years('2096-02-29', 4)", date(2100, 2, 28)),
    ("date(2024, 2, 29) + weeks(2)", date(2024, 3, 14)),
    ("date('25 December 2026')", date(2026, 12, 25)),
    ("date('December 25, 2026')", date(2026, 12, 25)),
    ("date('25th Dec 2026')", date(2026, 12, 25)),
    ("date('2026-12-25 18:30') + hours(6)", datetime(2026, 12, 26, 0, 30)),
])
def test_date_arithmetic(expression, expected):
    assert value(expression) == expected


def test_month_ends_across_four_centuries_of_months():
    """Every month end from 1896 to 2104, moved by whole months either way: the day is kept, or becomes the last
    day of a shorter month, and the month moves by exactly that much."""
    for year in range(1896, 2105):
        for month in range(1, 13):
            last = calendar.monthrange(year, month)[1]
            for day_ in (last, 29, 30):
                if day_ > last:
                    continue
                for k in (-25, -12, -1, 1, 2, 12, 13):
                    got = value(f"add_months('{year}-{month:02d}-{day_:02d}', {k})")
                    to_year, to_month = divmod(year * 12 + month - 1 + k, 12)
                    assert (got.year, got.month) == (to_year, to_month + 1)
                    assert got.day == min(day_, calendar.monthrange(got.year, got.month)[1])


def test_days_between_matches_python_across_leap_days():
    rng = random.Random(7)
    start = date(1896, 1, 1)
    for _ in range(400):
        a = start + timedelta(days=rng.randint(0, 76000))
        b = a + timedelta(days=rng.randint(-2000, 2000))
        assert value(f"days_between('{a}', '{b}')") == (b - a).days
        assert value(f"date('{b}') - date('{a}')") == b - a


@pytest.mark.parametrize("a, b, years", [
    ("2000-02-29", "2001-02-28", 0), ("2000-02-29", "2001-03-01", 1), ("2004-02-29", "2008-02-29", 4),
    ("1990-05-17", "2026-05-16", 35), ("1990-05-17", "2026-05-17", 36), ("2026-05-17", "1990-05-17", -36),
])
def test_whole_years(a, b, years):
    assert value(f"years_between('{a}', '{b}')") == years


def test_today_and_age(today):
    assert value("today()") == date(2026, 11, 1)
    assert value("now()") == datetime(2026, 11, 1, 9, 30)
    assert value("days_between(today(), '2026-12-25')") == 54
    assert calc.show(value("date('2026-12-25') - today()")) == "54 days"
    assert value("age('1990-11-02')") == 35 and value("age('1990-11-01')") == 36
    assert value("date('tomorrow')") == date(2026, 11, 2) and value("weekday('yesterday')") == "Saturday"
    assert calc.show(value("now() + hours(2.5)")) == "Sunday 1 November 2026, 12:00"


@pytest.mark.parametrize("expression, shown", [
    ("weekday('2000-01-01')", "Saturday"), ("weekday('2026-12-25')", "Friday"),
    ("date('2024-02-29')", "Thursday 29 February 2024"), ("hours(2) + minutes(30)", "2 hours, 30 minutes"),
    ("days(1) - days(3)", "-2 days"), ("weeks(1) / days(1)", "7"), ("days(3) * 2", "6 days"),
])
def test_dates_shown(expression, shown):
    assert calc.show(value(expression)) == shown


@pytest.mark.parametrize("expression, why", [
    ("date('2023-02-29')", "isn't a date I can read"), ("date(2024, 2, 30)", "isn't a real date"),
    ("date('01/02/2026')", "day/month or month/day"), ("date('next friday')", "isn't a date I can read"),
    ("date('2024-01-01') + 5", "can't be combined"), ("date('2024-01-01') * 2", "can't be combined"),
    ("add_months('2024-01-01', 1.5)", "whole number"), ("days(10**12)", "too long"),
    ("add_years('9999-01-01', 1)", "out of range"), ("weekday(5)", "needs a date"),
])
def test_dates_that_cant_be(expression, why):
    with pytest.raises(CalcError, match=why):
        value(expression)


# ------------------------------------------------------------------ units

@pytest.mark.parametrize("expression, expected", [
    ("convert(1, 'mi', 'km')", Fraction("1.609344")), ("convert(1, 'in', 'cm')", Fraction("2.54")),
    ("convert(1, 'lb', 'kg')", Fraction("0.45359237")), ("convert(0, 'C', 'F')", 32), ("convert(-40, 'C', 'F')", -40),
    ("convert(100, 'celsius', 'kelvin')", Fraction("373.15")), ("convert(98.6, '°F', '°C')", 37),
    ("convert(1, 'GiB', 'MiB')", 1024), ("convert(1, 'GB', 'MB')", 1000), ("convert(100, 'Mbit', 'MB')", Fraction(25, 2)),
    ("convert(1, 'kWh', 'kJ')", 3600), ("convert(1, 'atm', 'hPa')", Fraction("1013.25")),
    ("convert(1, 'hectare', 'm2')", 10000), ("convert(36, 'km/h', 'm/s')", 10),
    ("convert(60, 'kilometers per hour', 'mph')", Fraction(60000) / Fraction("1609.344")),
    ("convert(2, 'hours', 'minutes')", 120), ("convert(3, 'feet', 'inches')", 36), ("convert(1, 'sq ft', 'in2')", 144),
    ("convert(1, 'US gallon', 'litres')", Fraction("3.785411784")), ("convert(14, 'stone', 'kilograms')", Fraction("88.90410452")),
])
def test_conversions(expression, expected):
    assert value(expression).value == expected


def test_every_unit_converts_both_ways_exactly():
    rng = random.Random(3)
    by_kind: dict[str, list[str]] = {}
    for unit, (kind, _) in calc.UNITS.items():
        by_kind.setdefault(kind, []).append(unit)
    by_kind["temperature"] = sorted(calc._TEMPERATURE)
    for kind, units in by_kind.items():
        for a in units:
            for b in units:
                x = Fraction(rng.randint(-10**6, 10**6), 1000)
                there = calc._convert(x, a, b).value
                assert calc._convert(there, b, a).value == x, (kind, a, b)


def test_conversion_results_carry_their_unit():
    assert calc.show(value("convert(5, 'km', 'mi')")) == "3.10685596119 mi"
    assert calc.show(value("convert(5, 'km', 'mi') * 2")) == "6.21371192237 mi"
    assert calc.show(value("convert(1, 'km', 'm') + convert(1, 'mi', 'm')")) == "2609.344 m"
    assert calc.show(value("convert(10, 'km', 'm') + convert(10, 'mi', 'm')")) == "26,093.44 m"


@pytest.mark.parametrize("expression, why", [
    ("convert(1, 'km', 'kg')", "don't convert"), ("convert(1, 'C', 'km')", "don't measure the same thing"),
    ("convert(1, 'furlongs', 'm')", "isn't a unit I know"), ("convert(1, 2, 3)", "two units as text"),
])
def test_conversions_that_cant_be(expression, why):
    with pytest.raises(CalcError, match=why):
        value(expression)


# ------------------------------------------------------------------ nothing but a calculation gets in

SNEAKY = [
    "__import__('os').system('calc')", "open('x').read()", "().__class__.__bases__[0].__subclasses__()",
    "[c for c in ()]", "lambda: 0", "exec('1')", "eval('1')", "globals()", "__builtins__", "x.y", "a[0]",
    "f'{1}'", "b'1'", "1 if 1 else 2", "(x := 1)", "{1: 2}", "{1}", "[1, 2]", "(1, 2)", "*a", "1 and 2",
    "not 1", "1 < 2", "True + 1", "None", "...", "print(1)", "getattr(1, 'real')", "date.__class__",
    "date('2024-01-01').year", "'%s' % 1", "'a' 'b'", "__import__", "1; import os", "import os", "1j",
    "convert(1, 'km', 'mi').__class__", "sqrt.__globals__", "sqrt(x=4)", "max(*[1, 2])", "today().replace(year=1)",
    "'a' * 10", "date('a' * 100)", "date('" + "9" * 50 + "')", "type(1)", "breakpoint()", "input()",
    "(1).__add__(2)", "~1", "1 << 100", "1 | 2", "1 & 2", "1 @ 2", "-'1'", "abs('x')", "sum('abc')",
]

TOO_BIG = ["9**9**9", "10**10**10", "2**2**2**2**2", "factorial(10**6)", "factorial(factorial(10))",
           "10**999 * 10**999", "1e300 ** 1e300", "exp(10**6)", "root(2, 10**6)", "round(1, 10**6)",
           "-" * 250 + "1",                       # more parts than MAX_NODES
           "1+" * 140 + "1",                      # the same, flat
           "(" * 150 + "1" + ")" * 150 + "+1",    # longer than MAX_CHARS
           ]


@pytest.mark.parametrize("expression", SNEAKY + TOO_BIG)
def test_anything_else_is_refused_quickly(expression):
    start = time.perf_counter()
    with pytest.raises(CalcError):
        value(expression)
    assert time.perf_counter() - start < 0.5


def test_the_evaluator_never_hands_text_to_python():
    """No eval, exec, compile, imports by name or attribute lookups by name anywhere in the brick: an expression
    is only ever walked, node by node."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(calc))
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"eval", "exec", "compile", "__import__", "getattr", "setattr", "open", "globals", "vars"}


# ------------------------------------------------------------------ the brick

def test_the_tool():
    tool = calc.tools(ToolContext(load_config(), None, Output()))[0]
    assert tool.watch is False and tool.name == "calculate"
    r = tool.fn("15 * 23")
    assert r.ok and r.text == "15 * 23 = 345" and r.say == "That's 345."
    r = tool.fn("1/0")
    assert not r.ok and r.text == "Couldn't calculate '1/0': That divides by zero."


def test_the_brick_is_switched_on_everywhere():
    from mary import skills
    from mary.settings.choices import SKILLS

    assert "calculate" in skills.ALL and "calculate" in load_config().skills
    assert SKILLS["calculate"][1] == "Everyday"
    assert "calculate" in " ".join(skills.guides(skills.load(["calculate"])))
