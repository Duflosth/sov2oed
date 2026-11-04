"""Questions for the broker: only what the broker alone can give, in the words of a property schedule.

Code fixes the substance of each question: the point, the locations or cells, the share of the insured value.
The plan may add up to five points of this file that only a reading finds (two insureds in one schedule, a row
whose total is not its building plus contents, a vacant building), each citing its cells; code checks the quotes
(`check`). Claude words the message to the broker, in the broker's language, and adds or drops nothing.

At most ten questions, in this order:
1. the schedule's own figures: a guard's finding on the file, such as a printed total that its rows do not reach
   (`from_guards`), largest amount first;
2. the plan's own questions on this file (`from_plan`);
3. which perils the programme covers, when the schedule does not say (`from_report`, as are 4 and 5);
4. the missing building characteristics, in one question, with the largest locations named: the core ones when
   the perils are not known, else the details of the perils covered;
5. one question per other rule, and the basis of values the plan reads as unknown, largest share first;
6. last, the request to keep our location numbers when the schedule has none (`numbering`).

Never asked: what sov2oed or the cat team does itself. From a schedule, that is placing an address on the map
(coordinates) and the form of the OED files (keys, schema, peril codes), which the conversion writes.
"""

from __future__ import annotations

from typing import Any

from . import parse
from .annotate import NUMBER_HEADER
from .extract import Extraction
from .guards import Finding
from .plan import Cite, ref_text

MAX_QUESTIONS = 10
MAX_LISTED = 20
MAX_LARGEST = 3

# Guard findings about the file -> (what is wrong, what to send back).
FILE_ASK: dict[str, tuple[str, str]] = {
    "totals": ("The rows of the schedule do not add up to its printed total", "Please confirm the right amounts."),
    "formulas": (
        "A column computed from others is blank, typed in by hand or worked out by hand on some rows",
        "Please confirm the right amounts.",
    ),
    "row_totals": ("A row's printed total is not the sum of its values", "Please confirm the right amounts."),
    "unreadable": ("Some cells hold text where a value is expected", "Please give the values."),
}
# Guard -> the question's short title in the report.
FILE_TITLE = {"totals": "Totals do not reconcile", "formulas": "Computed amounts typed by hand",
              "row_totals": "Row totals do not add up", "unreadable": "Text where a value is expected"}  # fmt: skip
# Rule (and sub-code, "" for all) -> (what is wrong, what to send back), in plain words.
ASK: dict[tuple[str, str], tuple[str, str]] = {
    ("WHERE-001", "placeholder_text"): ("No usable address", "Please give the full street address, postcode and city."),
    ("WHERE-001", "country_level_only"): ("No address", "Please give the full street address, postcode and city."),
    ("WHERE-001", "no_street"): ("No street address", "Please give the street address."),
    ("WHERE-002", ""): (
        "Coordinates that cannot be right",
        "Please confirm the address, or give corrected coordinates.",
    ),
    ("WHAT-007", ""): ("A year or a number of storeys that cannot be right", "Please confirm them."),
    ("WHAT-011", ""): (
        "The same value on most buildings, probably filled in by default",
        "Please confirm these values building by building.",
    ),
    ("VALUE-001", ""): (
        "No insured value",
        "Please give the building, contents and business interruption values, or confirm it is not insured.",
    ),
    ("VALUE-002", ""): (
        "Value per square foot or metre out of the usual range",
        "Please confirm the value and the area.",
    ),
    ("VALUE-006", ""): (
        "Value 100 times above or below similar buildings of the schedule",
        "Please confirm the value (a missing or extra zero?).",
    ),
    ("VALUE-009", ""): (
        "Values that are {bases}, not replacement values",
        "Please give the replacement cost of these buildings and contents, or confirm the basis of the values.",
    ),
    ("LINK-002", "same_key"): ("Two locations share one location number", "Please confirm the location numbers."),
    ("LINK-002", "same_content"): (
        "Locations that look like duplicates of others",
        "Please confirm whether they are distinct buildings.",
    ),
}
# Missing characteristics, asked in one question: rule -> (what to give, examples).
MISSING = {
    "WHAT-001": ("construction type", "wood frame, masonry, reinforced concrete, steel..."),
    "WHAT-002": ("use of the building", "office, warehouse, school..."),
    "WHAT-003": ("number of storeys above ground", ""),
    "WHAT-006": ("year built, and of any major renovation", ""),
    "WHAT-008": ("floor area", "square feet or square metres"),
    "WHAT-012": ("{details}", ""),
}
SECONDARY = {
    "wind": ("roof covering", "roof shape", "roof age", "window protection (shutters, impact glass)"),
    "hail": ("roof covering", "roof age"),
    "flood": ("basement", "height of the ground floor above street level", "flood defences"),
    "earthquake": ("foundation type", "seismic retrofit"),
    "fire": ("sprinklers and the share of the floor area they protect",),
}
CORE_SECONDARY = {"wind": ("roof covering",), "hail": ("roof covering",)}  # asked when the perils are not known
PERILS = (
    "Which perils does the programme cover: fire, windstorm, flood, earthquake...? The schedule does not say, and the "
    "building details we need depend on them."
)
BASIS = "On what basis are these values given: replacement cost, market value, actual cash value...?"
VALUE_WORDS = {"BuildingTIV": "building", "ContentsTIV": "contents", "BITIV": "business interruption",
               "OtherTIV": "other structures"}  # fmt: skip
BASES = {
    "market": "market values",
    "actual_cash": "actual cash values",
    "limit": "policy limits",
    "agreed": "agreed values",
}
NOT_ASKED = {("WHAT-002", "generic"), ("WHAT-003", "band")}  # reported, not worth a question


def _pct(share: float) -> str:
    pct = 100 * share
    return "<0.1" if 0 < pct < 0.1 else f"{pct:.1f}"


def _n(n: int, word: str = "location") -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def from_guards(findings: list[Finding]) -> list[dict[str, Any]]:
    """The schedule's own figures: guard findings on the file that survived the plan's revision."""
    out = []
    for f in sorted(findings, key=lambda f: -f.amount):
        if f.guard not in FILE_ASK or f.kind == "plan":
            continue
        what, ask = FILE_ASK[f.guard]
        out.append({"ref": f.guard, "title": FILE_TITLE[f.guard], "detail": f"{f.message} {ask}",
                    "text": f"{what}. {f.message} {ask}", "locations": [], "cells": list(f.cells), "tiv_pct": None,
                    "impact": f.amount})  # fmt: skip
    return out


def _unquoted(ex: Extraction) -> list[tuple[int, Cite]]:
    """The cells of the plan's questions whose quote is not in the cell."""
    return [(i, c) for i, q in enumerate(ex.plan.questions) for c in q.cells
            if parse.normalise(c.quote) not in parse.normalise(ref_text(c.cell, ex.grid))]  # fmt: skip


def check(ex: Extraction) -> list[Finding]:
    """A plan question quoting what its cell does not say is a plan error, like a fact's quote."""
    return [Finding("quotes", "plan", f"questions[{i}]: '{c.quote}' is not in {c.cell}.", [c.cell])
            for i, c in _unquoted(ex)]  # fmt: skip


def from_plan(ex: Extraction) -> list[dict[str, Any]]:
    """The plan's own questions on this file, in its order; one whose quotes do not hold is not asked."""
    bad = {i for i, _ in _unquoted(ex)}
    return [{"ref": "plan", "title": q.title, "detail": q.text, "text": q.text, "locations": [],
             "cells": [c.cell for c in q.cells], "tiv_pct": None, "impact": 0.0}
            for i, q in enumerate(ex.plan.questions) if i not in bad]  # fmt: skip


def _ask(rule: str, sub: str) -> tuple[str, str] | None:
    return ASK.get((rule, sub)) or ASK.get((rule, ""))


def from_report(report: dict[str, Any], locations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Questions from the quality report and the value bases. locations: per row, {"number", "name", "tiv",
    "unknown_basis": [TIV fields whose basis is unknown]}, in file order."""
    index = {loc["number"]: i for i, loc in enumerate(locations)}
    total = sum(loc["tiv"] for loc in locations) or 1.0
    issues = [x for x in report["issues"] if (x["rule"], x["sub"]) not in NOT_ASKED]
    perils_assumed = "assumed" in (report.get("provenance") or {}).get("LocPerilsCovered", {})
    secondary = CORE_SECONDARY if perils_assumed else SECONDARY

    def rows(found: list[dict[str, Any]]) -> list[int]:
        return sorted({index[n] for x in found for n in x["location_numbers"] if n in index})

    def largest(found_rows: list[int]) -> str:
        top = sorted(found_rows, key=lambda i: -locations[i]["tiv"])[:MAX_LARGEST]
        if len(found_rows) <= MAX_LARGEST or not top or locations[top[0]]["tiv"] <= 0:
            return ""
        named = ", ".join(f"{locations[i]['number']}{' ' + locations[i]['name'] if locations[i]['name'] else ''}"
                          f" ({_pct(locations[i]['tiv'] / total)}%)" for i in top)  # fmt: skip
        return f" The largest: {named}."

    def ask(ref: str, r: list[int], title: str, detail: str, impact: float, lead: str | None = None) -> dict[str, Any]:
        """detail: the question under its title in the report; the e-mail's text is `lead` (the title) then detail."""
        share = sum(locations[i]["tiv"] for i in r) / total
        detail = detail.format(pct=_pct(share)) + largest(r)
        return {"ref": ref, "title": title, "detail": detail, "text": f"{title + ':' if lead is None else lead} {detail}".strip(), "cells": [],
                "tiv_pct": round(100 * share, 1), "locations": [locations[i]["number"] for i in r], "impact": impact}  # fmt: skip

    def question(
        ref: str, found: list[dict[str, Any]], title: str, detail: str, lead: str | None = None
    ) -> dict[str, Any]:
        return ask(ref, rows(found), title, detail, sum(x["impact"] for x in found), lead)

    first: list[dict[str, Any]] = []
    if perils_assumed:
        first.append({"ref": "perils", "title": "Perils covered", "detail": PERILS, "text": PERILS, "locations": [],
                      "cells": [], "tiv_pct": None, "impact": 0.0})  # fmt: skip
    missing = [x for x in issues if x["rule"] in MISSING and (x["rule"] != "WHAT-012" or x["sub"] in secondary)]
    if missing:
        items = []
        for rule, (what, examples) in MISSING.items():
            found = [x for x in missing if x["rule"] == rule]
            if not found:
                continue
            details = ", ".join(dict.fromkeys(s for x in found for s in secondary.get(x["sub"], ())))
            items.append(
                f"{what.format(details=details)}: {_n(len(rows(found)))}" + (f" ({examples})" if examples else "")
            )
        n = len(rows(missing))
        detail = f"{_n(n)}, {{pct}}% of the insured value. Please give, for each building: " + "; ".join(items) + "."
        refs = ", ".join(dict.fromkeys(x["rule"] for x in missing))
        first.append(
            question(refs, missing, "Building characteristics missing", detail, "Building characteristics missing on")
        )
    out = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for x in issues:
        if x["rule"] in MISSING or not (a := _ask(x["rule"], x["sub"])):
            continue
        groups.setdefault(a, []).append(x)
    for (problem, request), found in groups.items():
        what = problem.replace("{bases}", " or ".join(BASES[x["sub"]] for x in found if x["sub"] in BASES))
        out.append(
            question(found[0]["rule"], found, what, f"{_n(len(rows(found)))}, {{pct}}% of the insured value. {request}")
        )
    unknown = [i for i, loc in enumerate(locations) if loc.get("unknown_basis")]
    if unknown:
        kinds = " and ".join(dict.fromkeys(VALUE_WORDS[f] for i in unknown for f in locations[i]["unknown_basis"]))
        share = sum(locations[i]["tiv"] for i in unknown) / total
        detail = f"{BASIS} The schedule does not say, for the {kinds} values: {_n(len(unknown))}, {{pct}}% of the insured value."
        out.append(
            ask("value basis", unknown, "Basis of values", detail, share / 3, "")
        )  # ranked as a VALUE-009 finding: warning, weight 2
    out.sort(key=lambda q: -q["impact"])
    return first + out


def numbering(workbook: bool) -> dict[str, Any]:
    """The request to keep location numbers, for a schedule that has none."""
    if workbook:
        text = ("Your schedule has no location numbers. We have numbered the locations in a new column "
                f'"{NUMBER_HEADER}" of the annotated workbook: please keep this column in the next versions, so that '
                "each location keeps its number.")  # fmt: skip
    else:
        text = "Your schedule has no location numbers. Please number the locations in the next versions."
    return {
        "ref": "location numbers",
        "title": "Location numbers",
        "detail": text,
        "text": text,
        "locations": [],
        "cells": [],
        "tiv_pct": None,
        "impact": 0.0,
    }


def select(file_questions: list[dict[str, Any]], rule_questions: list[dict[str, Any]],
           last: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """The schedule's own figures first, then the rules, then `last`; at most MAX_QUESTIONS in all."""
    last = last or []
    return [*file_questions, *rule_questions][: MAX_QUESTIONS - len(last)] + last


def _short_cells(cells: list[str]) -> list[str]:
    """ "Sheet!F51" -> "F51" when every cell is on one sheet."""
    sheets = {c.rpartition("!")[0] for c in cells}
    return [c.rpartition("!")[2] for c in cells] if len(sheets) == 1 else cells


def email(questions: list[dict[str, Any]], insured: str | None = None) -> str:
    """A plain-text draft in English; Claude writes the message in the broker's language."""
    lines = [f"Subject: Questions on the schedule of values{' of ' + insured if insured else ''}", "", "Hello,", "",
             "Before we can model this schedule, could you help with the points below?", ""]  # fmt: skip
    for i, q in enumerate(questions, start=1):
        lines.append(f"{i}. {q['text']}")
        where, label = (_short_cells(q["cells"]), "Cells") if q["cells"] else (q["locations"], "Locations")
        if where:
            more = f" (+{len(where) - MAX_LISTED})" if len(where) > MAX_LISTED else ""
            lines.append(f"   {label}: {', '.join(str(w) for w in where[:MAX_LISTED])}{more}")
        lines.append("")
    return "\n".join([*lines, "Thank you."]) + "\n"
