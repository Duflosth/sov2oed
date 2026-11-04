"""The score: rule results weighed by insured value into a 0-100 score and a grade from A to E.

A row weighs its total insured value (a row with none weighs the median of its currency, so a blank value still
counts). A flagged row takes a penalty of severity factor x weight / 3, between 0 and 1. Within a family (WHERE,
WHAT, VALUE, LINK, and the secondary attributes of WHAT-012) a row keeps its largest penalty; across families the
penalties combine like independent failures, so a row keeps (1 - p_WHERE)(1 - p_WHAT)... of its weight. The score
is the share of weight kept. The method is in docs/scoring.md.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .load import Portfolio
from .rules import Context, Result

FACTOR = {"critical": 1.0, "warning": 0.5, "info": 0.1}
FAMILIES = ("WHERE", "WHAT", "VALUE", "LINK", "secondary")
GRADES = ((90, "A"), (80, "B"), (70, "C"), (55, "D"))


@dataclass
class Issue:
    """One flagged sub-code: its rows and the share of insured value they weigh."""

    rule: str
    sub: str
    severity: str
    rows: list[int]
    share: float
    impact: float  # share x penalty: how much of the score it costs, at most
    details: list[str]


@dataclass
class Score:
    score: int
    grade: str
    families: dict[str, int | None]
    weights: list[float]
    total: float
    issues: list[Issue] = field(default_factory=list)  # most important first


def grade(score: int) -> str:
    return next((g for bound, g in GRADES if score >= bound), "E")


def weights(c: Context) -> list[float]:
    """Each row's insured value; rows without one weigh the median value of their currency (1 if none).
    With several currencies, each currency's rows share the weight of their number of rows."""
    by_cur: dict[str, list[int]] = defaultdict(list)
    for i in c.all_ordered:
        by_cur[c.text(i, "LocCurrency").upper()].append(i)
    w = [0.0] * len(c.rows)
    for rows in by_cur.values():
        positive = [c.tiv[i] for i in rows if c.tiv[i] > 0]
        fill = statistics.median(positive) if positive else 1.0
        for i in rows:
            w[i] = c.tiv[i] if c.tiv[i] > 0 else fill
        if len(by_cur) > 1:
            total = sum(w[i] for i in rows) or 1.0
            for i in rows:
                w[i] = w[i] / total * len(rows)
    return w


def score(p: Portfolio, c: Context, results: list[Result]) -> Score:
    w = weights(c)
    total = sum(w) or 1.0
    penalty: dict[str, list[float]] = {f: [0.0] * len(w) for f in FAMILIES}
    evaluated: dict[str, set[int]] = {f: set() for f in FAMILIES}
    for r in results:
        p_row = FACTOR[r.severity] * r.weight / 3
        evaluated[r.family] |= r.evaluated
        for i in r.rows:
            penalty[r.family][i] = max(penalty[r.family][i], p_row)
    kept = sum(w[i] * _keep(penalty, i) for i in range(len(w)))
    value = int(100 * kept / total + 0.5) if w else 0
    families: dict[str, int | None] = {}
    for f in FAMILIES:
        ev = evaluated[f]
        weight = sum(w[i] for i in ev)
        families[f] = int(100 * (1 - sum(w[i] * penalty[f][i] for i in ev) / weight) + 0.5) if weight else None
    issues = []
    for r in results:
        if r.rows:
            share = sum(w[i] for i in r.rows) / total
            issues.append(
                Issue(
                    r.rule,
                    r.sub,
                    r.severity,
                    sorted(r.rows),
                    share,
                    share * FACTOR[r.severity] * r.weight / 3,
                    r.details,
                )
            )
    issues.sort(key=lambda x: (-x.impact, x.rule, x.sub))
    return Score(value, grade(value), families, w, total, issues)


def _keep(penalty: dict[str, list[float]], i: int) -> float:
    out = 1.0
    for f in FAMILIES:
        out *= 1 - penalty[f][i]
    return out
