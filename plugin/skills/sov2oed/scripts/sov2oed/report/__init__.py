"""The quality report: the score, the issues ranked by the share of insured value they concern, and how each key
value was obtained, location by location. `build` makes the JSON report; `to_html` renders it alone, so a saved
report can be shown later.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from importlib import resources
from typing import Any

from jinja2 import Environment, select_autoescape

from ..check.load import Portfolio
from ..check.rules import TITLES, Result
from ..check.score import Score

KEY_FIELDS = ("BuildingTIV", "ContentsTIV", "OccupancyCode", "ConstructionCode", "YearBuilt", "NumberOfStoreys",
              "FloorArea", "PostalCode", "LocPerilsCovered")  # fmt: skip
# The fields of the provenance matrix, in the report's words.
MATRIX_FIELDS = {"BuildingTIV": "Building value", "ContentsTIV": "Contents", "OtherTIV": "Other value",
                 "OccupancyCode": "Occupancy", "ConstructionCode": "Construction", "YearBuilt": "Year built",
                 "NumberOfStoreys": "Storeys", "FloorArea": "Floor area", "RoofCover": "Roof",
                 "FlexiLocSprinklered": "Sprinklers", "StreetAddress": "Address", "LocPerilsCovered": "Perils"}  # fmt: skip
MATRIX_ROWS = 300  # a larger schedule shows its first rows; provenance.csv has them all
STATUS = {"declared": "d", "derived": "r", "assumed": "a"}  # "-": blank, "x": flagged by a rule
RULE_FIELDS = {"VALUE-001": ("BuildingTIV",), "VALUE-002": ("BuildingTIV", "FloorArea"), "VALUE-006": ("BuildingTIV",),
               "VALUE-009": ("BuildingTIV", "ContentsTIV"), "WHAT-001": ("ConstructionCode",),
               "WHAT-002": ("OccupancyCode",), "WHAT-003": ("NumberOfStoreys",), "WHAT-006": ("YearBuilt",),
               "WHAT-007": ("YearBuilt", "NumberOfStoreys"), "WHAT-008": ("FloorArea",)}  # fmt: skip
SUB_LABELS = {
    ("WHERE-001", "placeholder_text"): "address is a placeholder (various, TBD)",
    ("WHERE-001", "country_level_only"): "no coordinates and no address below the country",
    ("WHERE-001", "no_street"): "no coordinates and no street address, only a city or postcode",
    ("WHERE-001", "no_coordinates"): "no coordinates, an address to geocode",
    ("WHERE-002", "not_a_number"): "coordinates that are not numbers",
    ("WHERE-002", "null_island"): "coordinates 0, 0",
    ("WHERE-002", "out_of_range"): "coordinates out of range",
    ("WHERE-002", "swapped"): "latitude and longitude swapped",
    ("WHERE-002", "outside_country"): "point outside the stated country",
    ("WHAT-002", "generic"): "a code for a whole category",
    ("WHAT-006", ""): "blank or 0",
    ("VALUE-006", ""): "100 times off the median of comparable buildings",
    ("WHAT-003", "band"): "a height band, not a count",
    ("WHAT-007", "placeholder"): "placeholder year (1, 99, 999, 9999)",
    ("WHAT-007", "too_old"): "year before 1000",
    ("WHAT-007", "future"): "year after this year",
    ("WHAT-007", "secondary_year"): "roof or upgrade year before the build year or in the future",
    ("WHAT-007", "storeys_above_max"): "more than 170 storeys",
    ("WHAT-007", "storeys_below_min"): "storeys below -3",
    ("WHAT-007", "storeys_sentinel"): "99 storeys on many rows",
    ("WHAT-008", "implausible"): "area too small or too large",
    ("WHAT-012", "wind"): "roof covering and shape both unknown (tropical wind: anchorage, deck, windows too)",
    ("WHAT-012", "flood"): "first floor height, basement and flood defences all unknown",
    ("WHAT-012", "earthquake"): "soft storey, foundation type and retrofit all unknown",
    ("WHAT-012", "fire"): "sprinkler type and share sprinklered both unknown",
    ("WHAT-012", "hail"): "roof covering unknown",
    ("WHAT-011", "repeated_digit"): "an amount of one repeated digit (999999)",
    ("WHAT-011", "repeated_triplet"): "the same values at many addresses",
    ("WHAT-011", "round_share"): "round amounts on most of the value",
    ("VALUE-001", "zero_or_missing"): "no insured value",
    ("VALUE-001", "negative"): "a negative value",
    ("VALUE-002", "too_low"): "below 100 per m²",
    ("VALUE-002", "too_high"): "above 20,000 per m²",
    ("VALUE-009", "market"): "market value",
    ("VALUE-009", "actual_cash"): "actual cash value (depreciated)",
    ("VALUE-009", "limit"): "a policy limit, not a value",
    ("VALUE-009", "agreed"): "agreed value",
    ("LINK-002", "same_key"): "same location number twice",
    ("LINK-002", "same_content"): "same row under two numbers",
    ("LINK-009", "required_fields"): "required OED field blank or missing",
    ("LINK-009", "not_a_number"): "text in a number field",
    ("LINK-009", "invalid_country"): "country code not in OED",
    ("LINK-009", "invalid_area"): "area code not valid for the country",
    ("LINK-009", "perils"): "peril code not in OED",
    ("LINK-009", "currency"): "currency code not valid",
    ("LINK-009", "orphan_location"): "location without its account",
    ("LINK-009", "valid_values"): "value outside the OED range",
    ("LINK-009", "invalid_code"): "code not in its OED list",
}  # fmt: skip


def title(rule: str, sub: str) -> str:
    """The rule's title; the secondary attributes rule names the peril of its sub-code."""
    return f"{sub.capitalize()} attributes unknown" if rule == "WHAT-012" and sub else TITLES[rule]


def label(rule: str, sub: str) -> str:
    if (rule, sub) in SUB_LABELS:
        return SUB_LABELS[(rule, sub)]
    if rule == "WHAT-011":
        return f"{sub}: one value on most rows"
    return {"missing": "blank", "unknown": "an OED unknown code", "invalid": "not an OED code"}.get(sub, sub)


def _matrix(p: Portfolio, issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Per location, one letter per field of MATRIX_FIELDS: d declared, r derived, a assumed, - blank, x flagged."""
    if not p.provenance:
        return None
    flagged = {(n, f) for x in issues for n in x["location_numbers"] for f in RULE_FIELDS.get(x["rule"], ())}
    rows = []
    for i, r in enumerate(p.locations[:MATRIX_ROWS]):
        n = r.get("LocNumber", str(i + 1))
        cells = "".join("x" if (n, f) in flagged else STATUS.get(prov.status, "-") if (prov := p.provenance.get((n, f)))
                        else "-" for f in MATRIX_FIELDS)  # fmt: skip
        rows.append({"number": n, "name": r.get("LocName") or r.get("StreetAddress", ""), "cells": cells})
    return {"fields": list(MATRIX_FIELDS), "rows": rows, "of": len(p.locations)}


def build(
    p: Portfolio, s: Score, results: list[Result], tiv: dict[str, float], reading: dict[str, Any] | None = None
) -> dict[str, Any]:
    numbers = [r.get("LocNumber", str(i + 1)) for i, r in enumerate(p.locations)]
    issues = []
    for x in s.issues:
        issues.append({
            "rule": x.rule, "sub": x.sub, "title": title(x.rule, x.sub), "label": label(x.rule, x.sub), "severity": x.severity,
            "locations": len(x.rows), "share": round(x.share, 4), "impact": round(x.impact, 4),
            "location_numbers": [numbers[i] for i in x.rows], "details": x.details,
        })  # fmt: skip
    status: dict[str, Counter[str]] = defaultdict(Counter)
    for (_, name), prov in p.provenance.items():
        if name in KEY_FIELDS:
            status[name][prov.status] += 1
    return {
        "score": s.score,
        "grade": s.grade,
        "families": s.families,
        "locations": len(p.locations),
        "tiv": {k: round(v, 2) for k, v in sorted(tiv.items())},
        "issues": issues,
        "provenance": {f: dict(sorted(status[f].items())) for f in KEY_FIELDS if f in status},
        "matrix": _matrix(p, issues),
        "unknown_columns": p.unknown_columns,
        "reading": reading,
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def _amount(v: float | None) -> str:
    if v is None:
        return "–"
    for size, unit in ((1e9, "bn"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= size:
            return f"{v / size:.1f}".rstrip("0").rstrip(".") + unit
    return f"{v:.0f}"


def _pct(v: float) -> str:
    return "<0.1%" if 0 < v < 0.001 else f"{100 * v:.1f}%"


VALUES = {"BuildingTIV": "Buildings", "ContentsTIV": "Contents", "OtherTIV": "Other", "BITIV": "Business interruption"}
FAMILIES = {"WHERE": "Location", "WHAT": "Building", "VALUE": "Insured values", "LINK": "OED keys and schema",
            "secondary": "Peril attributes"}  # fmt: skip
VERDICT = {"A": "Good", "B": "Good", "C": "Fair", "D": "Needs work", "E": "Poor"}
FILE_GUARDS = ("totals", "formulas", "row_totals", "unreadable")


def _waffle(tiv: dict[str, float]) -> list[str]:
    """100 squares split by kind of value, largest remainders rounded up."""
    total = sum(tiv.values()) or 1.0
    exact = {k: 100 * v / total for k, v in tiv.items()}
    counts = {k: int(x) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: counts[k] - exact[k])[: 100 - sum(counts.values())]:
        counts[k] += 1
    return [k for k in tiv for _ in range(counts[k])]


def _short(cell: str) -> str:
    return cell.rpartition("!")[2]


def _view(report: dict[str, Any]) -> dict[str, Any]:
    reading = report.get("reading") or {}
    questions = report.get("questions") or []
    tiv = {k: report["tiv"][k] for k in VALUES if report["tiv"].get(k)}
    breaks = [{"amount": f"{f['amount']:,.0f}", "cell": _short(f["cells"][0]) if f["cells"] else "", "text": f["message"]}
              for f in reading.get("findings", []) if f.get("kind") == "file" and f.get("amount")]  # fmt: skip
    asked = []
    for q in questions:
        where = [_short(c) for c in q["cells"]] or q["locations"]
        more = f" +{len(where) - 4}" if len(where) > 4 else ""
        asked.append(
            {"title": q.get("title", ""), "detail": q.get("detail") or q["text"], "where": ", ".join(where[:4]) + more}
        )
    return {
        "file": reading.get("file"),
        "verdict": VERDICT.get(report["grade"], ""),
        "ring": round(3.142 * report["score"], 1),  # of a circumference of 314.2
        "tiv": tiv,
        "waffle": _waffle(tiv),
        "families": [(FAMILIES[k], v) for k, v in report["families"].items() if k in FAMILIES],
        "breaks": breaks,
        "questions": asked,
        "on_figures": sum(q["ref"] in FILE_GUARDS for q in questions),
        "assumed": (reading.get("assumed") or {}).get("file", []),
    }


def to_html(report: dict[str, Any], title: str = "OED quality report", insured: str | None = None) -> str:
    """The report as one HTML page; `report` may carry the broker questions under "questions"."""
    env = Environment(autoescape=select_autoescape(default=True))
    env.filters.update(amount=_amount, pct=_pct)
    template = resources.files("sov2oed.report").joinpath("templates/report.html.j2").read_text("utf-8")
    return env.from_string(template).render(
        r=report, v=_view(report), title=title, insured=insured, values=VALUES, fields=MATRIX_FIELDS
    )
