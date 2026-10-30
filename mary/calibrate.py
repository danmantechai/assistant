"""Router calibration: python -m mary.calibrate [cases.yaml]

Routes every case (nothing is executed), prints score, margin, guards and the chosen route, then
sweeps the thresholds. The number that matters most is "wrong direct": a request the router would
have *run* as the wrong action. Missing a match only costs an LLM call.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from mary.config import PROJECT_DIR, load_config

GREEN, RED, AMBER, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"


@dataclass
class Outcome:
    text: str
    expect: str
    route: str
    intent: str
    score: float
    margin: float
    guards: list
    reason: str

    @property
    def verdict(self) -> str:
        if self.expect == "llm":
            return "ok" if self.route != "direct" else "WRONG"
        if self.route == "direct":
            return "ok" if self.intent == self.expect else "WRONG"
        return "missed"  # went to the LLM instead: slower, not harmful


def run_cases(router, cases: list[dict]) -> list[Outcome]:
    from mary.router.guards import split_clauses

    outcomes = []
    for case in cases:
        clauses = split_clauses(case["text"])
        expects = case["expect"] if isinstance(case["expect"], list) else [case["expect"]] * len(clauses)
        if len(expects) != len(clauses):  # the split itself went wrong
            outcomes.append(Outcome(case["text"], str(case["expect"]), "split", f"{len(clauses)} parts",
                                    0, 0, [], f"split into {clauses}"))
            continue
        for clause, expect in zip(clauses, expects):
            d = router.route(clause)
            outcomes.append(Outcome(clause, expect, d.route, d.intent, d.score, d.margin, d.guards, d.reason))
    return outcomes


def print_table(outcomes: list[Outcome]) -> None:
    print(f"{'request':<46} {'expect':<15} {'route':<7} {'intent':<15} {'score':>5} {'margin':>6}  why")
    for o in outcomes:
        colour = GREEN if o.verdict == "ok" else RED if o.verdict == "WRONG" else AMBER
        mark = {"ok": "✓", "WRONG": "✗", "missed": "~"}[o.verdict]
        print(f"{colour}{mark}{RESET} {o.text[:44]:<44} {o.expect:<15} {o.route:<7} {o.intent:<15} "
              f"{o.score:5.2f} {o.margin:6.2f}  {DIM}{o.reason[:70]}{RESET}")


def summarize(outcomes: list[Outcome]) -> str:
    n = len(outcomes)
    ok = sum(o.verdict == "ok" for o in outcomes)
    wrong = sum(o.verdict == "WRONG" for o in outcomes)
    missed = sum(o.verdict == "missed" for o in outcomes)
    should_direct = sum(o.expect != "llm" for o in outcomes)
    direct_hits = sum(o.verdict == "ok" and o.expect != "llm" for o in outcomes)
    return (f"{ok}/{n} correct · {wrong} wrong direct · {missed} missed (went to the LLM) · "
            f"direct coverage {direct_hits}/{should_direct}")


def sweep(router, outcomes_cases: list[dict]) -> None:
    """Re-decide every case under other thresholds (scores are computed once)."""
    from mary.router.guards import split_clauses

    clauses = []
    for case in outcomes_cases:
        parts = split_clauses(case["text"])
        expects = case["expect"] if isinstance(case["expect"], list) else [case["expect"]] * len(parts)
        if len(parts) == len(expects):
            clauses += [(p, e, router.scores(p)) for p, e in zip(parts, expects)]
    original = dict(router.cfg)
    print(f"\nthreshold sweep (hint_score {router.cfg.hint_score}):")
    print(f"  {'direct_score':>12} {'margin':>7} {'direct ok':>10} {'wrong':>6}")
    best = None
    for score in [x / 100 for x in range(70, 93, 2)]:
        for margin in (0.02, 0.04, 0.05, 0.06, 0.08):
            router.cfg["direct_score"], router.cfg["direct_margin"] = score, margin
            good = bad = 0
            for text, expect, ranked in clauses:
                d = router.decide(text, ranked)
                if d.route == "direct":
                    good += d.intent == expect
                    bad += d.intent != expect
            if margin in (0.04, 0.05, 0.06):
                print(f"  {score:12.2f} {margin:7.2f} {good:10d} {bad:6d}")
            if bad == 0 and (best is None or good > best[2]):
                best = (score, margin, good)
    router.cfg.update(original)
    if best:
        print(f"\n  most direct hits with zero wrong: direct_score {best[0]:.2f}, margin {best[1]:.2f} "
              f"({best[2]} direct)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.calibrate")
    parser.add_argument("cases", nargs="?", default=str(PROJECT_DIR / "tests" / "router_cases.yaml"))
    parser.add_argument("--no-sweep", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from mary import winutil
    from mary.app_index import AppIndex
    from mary.router.router import Router

    winutil.enable_console_colors()
    cfg = load_config()
    router = Router(cfg, AppIndex(cfg))
    cases = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    outcomes = run_cases(router, cases)
    print_table(outcomes)
    print(f"\n{summarize(outcomes)}")
    print(f"current: direct_score {router.cfg.direct_score}, direct_margin {router.cfg.direct_margin}, "
          f"hint_score {router.cfg.hint_score}")
    if not args.no_sweep:
        sweep(router, cases)


if __name__ == "__main__":
    main()
