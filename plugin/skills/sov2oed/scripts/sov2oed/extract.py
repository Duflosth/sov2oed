"""Apply a reading plan to the file: every row of every table becomes a location, every value keeps its cell.

A value's status says how it was obtained: "declared" (the cell states it), "uncertain" (it states it loosely),
"derived" (a label coded through the plan's code list, a fact quoted from a comment, a value stated once for the
file), "assumed" (no cell states it) or "unknown" (the cell is blank or unreadable). Nothing is filled in that the
file does not state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import parse
from .oed import spec
from .plan import PSEUDO_FIELDS, Plan, RowKey, Table, split_cell, table_rows
from .read.grid import Grid, line_keys

DERIVED, ASSUMED = "derived", "assumed"
YEAR_LIMIT = 2100  # years after the current one are reported by the impossible-values rule, not dropped here
TEXT_JOIN = {"FlexiLocComment": "; ", "LocNumber": "-"}


@dataclass
class Value:
    value: Any
    status: str
    cell: str | None = None
    note: str | None = None


@dataclass
class Location:
    id: str  # "45", "p3-12"; prefixed with the table id when the plan has several tables: "t2-45"
    table: str
    row: RowKey
    ref: str  # "Sheet!45", "p3:l12+p3:l13"
    values: dict[str, Value] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)  # column -> cell as read
    basis: dict[str, str] = field(default_factory=dict)  # money field -> what its column measures
    building: bool = True

    def get(self, name: str) -> Any:
        v = self.values.get(name)
        return None if v is None or v.status == parse.UNKNOWN else v.value


@dataclass
class Extraction:
    plan: Plan
    grid: Grid
    locations: list[Location]
    skipped: list[tuple[str, RowKey, str]]  # (table id, row, why)
    rows: dict[str, list[RowKey]]  # table id -> every row of its range, read or skipped


def _lines(grid: Grid, key: RowKey) -> str | None:
    """The PDF lines a row of a rows file was read from, as written in its column A."""
    sheet = grid.sheets[key[0]]
    return parse.text_of(sheet.value(key[1], 1)) if sheet.kind == "rows" else None


def cell_ref(grid: Grid, key: RowKey, col: str) -> str:
    """Where a value is: "Sheet!C12", or the PDF lines of its row."""
    return _lines(grid, key) or f"{key[0]}!{col}{key[1]}"


def row_ref(grid: Grid, key: RowKey) -> str:
    return _lines(grid, key) or f"{key[0]}!{key[1]}"


def row_name(grid: Grid, key: RowKey) -> str:
    """A location's number when the file has none: the row, or the first PDF line ("p3-12")."""
    keys = line_keys(_lines(grid, key))
    return f"p{keys[0][0]}-{keys[0][1]}" if keys else str(key[1])


def read_row(t: Table, grid: Grid, key: RowKey) -> dict[str, Any]:
    """Column -> cell value."""
    sheet = grid.sheets[key[0]]
    return {col: sheet.value(key[1], col) for col in t.columns}


def _parse(name: str, raw: Any, t: Table, plan: Plan) -> tuple[Any, str, str | None]:
    info = spec.field(name)
    assert info is not None
    kind, country = info.kind, str(plan.country.value)
    if kind == "code":
        label = parse.normalise(parse.text_of(raw))
        labels = plan.codes[name].map if name in plan.codes else {}
        mapping = {parse.normalise(k): v for k, v in labels.items()}
        if label in mapping:  # before the blank words: "NC" is non-combustible in a construction column
            return mapping[label], DERIVED, None
        if parse.blank(raw):
            return None, parse.UNKNOWN, None
        return None, parse.UNKNOWN, f"label '{parse.text_of(raw)}' has no {name} in the plan"
    if kind == "money":
        return parse.parse_amount(raw, t.decimal, float(t.multiplier.value))
    if kind == "year":
        return parse.parse_year(raw, YEAR_LIMIT)
    if kind == "storeys":
        return parse.parse_storeys(raw)
    if kind == "postcode":
        return parse.parse_postcode(raw, country)
    if kind == "area":
        return parse.parse_area_code(raw, country)
    if kind == "date":
        return parse.parse_date(raw, t.decimal)
    if kind in ("float", "int"):
        v, status, note = parse.parse_float(raw, t.decimal)
        if kind == "int" and v is not None:
            return (
                (int(v), status, note)
                if float(v).is_integer()
                else (None, parse.UNKNOWN, f"'{raw}' is not a whole number")
            )
        return v, status, note
    return parse.parse_text(raw)


def _combine(name: str, old: Value, new: Value) -> Value:
    """Two columns feeding one field: texts are joined, amounts added, otherwise the first stated value is kept."""
    if old.status == parse.UNKNOWN:
        return new
    if new.status == parse.UNKNOWN:
        return old
    info = spec.field(name)
    cell = f"{old.cell}+{new.cell}"
    if info and info.kind == "text":
        return Value(f"{old.value}{TEXT_JOIN.get(name, ' ')}{new.value}", old.status, cell)
    if info and info.kind == "money":
        return Value(parse.fsum([old.value, new.value]), old.status, cell, "sum of two columns")
    return old


def _non_building(t: Table, grid: Grid, plan: Plan) -> set[RowKey]:
    nb = t.non_building
    if nb is None:
        return set()
    out = set(table_rows(t, grid, nb.rows)) if nb.rows else set()
    if nb.column:
        labels = {parse.normalise(x) for x in nb.labels}
        for key in table_rows(t, grid, t.rows):
            if parse.normalise(parse.text_of(read_row(t, grid, key).get(nb.column))) in labels:
                out.add(key)
    return out


def fact_location(locations: list[Location], grid: Grid, cell: str) -> Location | None:
    """The location a fact's cell belongs to: on its row, or among the PDF lines of its row."""
    if m := re.fullmatch(r"p(\d+):l(\d+)", cell):
        line = (int(m[1]), int(m[2]))
        return next((x for x in locations if line in (line_keys(_lines(grid, x.row)) or [])), None)
    parts = split_cell(cell)
    return next((x for x in locations if parts and x.row == (parts[0], parts[2])), None)


def extract(plan: Plan, grid: Grid) -> Extraction:
    locations: list[Location] = []
    skipped: list[tuple[str, RowKey, str]] = []
    all_rows: dict[str, list[RowKey]] = {}
    prefix = len(plan.tables) > 1
    for t in plan.tables:
        rows = table_rows(t, grid, t.rows)
        all_rows[t.id] = rows
        skip = {k: why for spec_text, why in t.skip.items() for k in table_rows(t, grid, spec_text)}
        skip.update({k: "total" for spec_text in t.totals for k in table_rows(t, grid, spec_text)})
        non_building = _non_building(t, grid, plan)
        sentinels = {col: {parse.normalise(v) for v in vals} for col, vals in t.sentinels.items()}
        placeholders = {col: {parse.normalise(v) for v in vals} for col, vals in t.placeholders.items()}
        for key in rows:
            if key in skip:
                skipped.append((t.id, key, skip[key]))
                continue
            raw = read_row(t, grid, key)
            if all(parse.blank(raw.get(col)) for col in t.columns):
                skipped.append((t.id, key, "blank"))
                continue
            name = row_name(grid, key)
            loc = Location(f"{t.id}-{name}" if prefix else name, t.id, key, row_ref(grid, key), raw=raw)
            loc.building = key not in non_building
            for col, c in t.columns.items():
                v = raw.get(col)
                for f in c.fields:
                    if f in PSEUDO_FIELDS:
                        continue
                    if col in sentinels and parse.normalise(parse.text_of(v)) in sentinels[col]:
                        new = Value(
                            None, parse.UNKNOWN, cell_ref(grid, key, col), f"'{parse.text_of(v)}' means not known"
                        )
                    else:
                        value, status, note = _parse(f, v, t, plan)
                        new = Value(value, status, cell_ref(grid, key, col), note)
                        if col in placeholders and parse.normalise(parse.text_of(v)) in placeholders[col]:
                            kept = spec.kind(f) == "money"
                            new.status = parse.UNCERTAIN if kept and status != parse.UNKNOWN else parse.UNKNOWN
                            new.note = f"'{parse.text_of(v)}' is a placeholder" + (
                                ", kept in the value" if kept else ""
                            )
                    loc.values[f] = _combine(f, loc.values[f], new) if f in loc.values else new
                    if c.basis:
                        loc.basis[f] = c.basis
            locations.append(loc)
    for fact in plan.facts:
        target = fact_location(locations, grid, fact.cell)
        if target is None:
            continue  # reported by the guards: a fact outside every table
        stated = Value(fact.value, DERIVED, fact.cell, f"quoted: {fact.quote}")
        old = target.values.get(fact.field)
        target.values[fact.field] = _combine(fact.field, old, stated) if old else stated
    for f, s in plan.defaults.items():
        for loc in locations:
            if loc.get(f) is None:
                loc.values[f] = Value(s.value, ASSUMED if s.assumed else DERIVED, s.cell, "stated once for the file")
    return Extraction(plan, grid, locations, skipped, all_rows)
