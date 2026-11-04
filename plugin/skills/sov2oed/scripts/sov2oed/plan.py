"""The reading plan: what Claude writes after looking at the file, and the only input of the conversion besides it.

A plan holds structure and classification only: which rows form each table, which column feeds which OED field,
which label means which OED code. Code reads every value. A value the plan states itself (the country, a fact
taken from a comment, a multiplier) comes with the cell it is read from and the exact quote, or is marked assumed.
So does each point of this file the plan asks the broker (`questions`): it cites the cells it is about.

`problems` checks every reference of a plan against the file before anything is read.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .oed import spec
from .read.grid import Grid, Sheet, col_index

VERSION = 1
Basis = Literal["replacement", "market", "actual_cash", "agreed", "limit", "unknown"]
PSEUDO_FIELDS = ("ignore", "row_total")  # a column read for no OED field, or a printed sum of the row's values
MAX_QUESTIONS = 5  # the plan's own questions to the broker
_CAMEL = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]*)+\b")  # YearBuilt, BuildingTIV: an OED field name


class _Loader(yaml.SafeLoader):
    """YAML for hand-written plans: only true/false are booleans (a column may be named "No"), dates stay text."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sourced(_Model):
    """A value the plan states, with where the file states it ("Sheet!B2", "p3:l12", "sheet:<name>", "file:<name>")."""

    value: Any = None
    cell: str | None = None
    quote: str | None = None
    assumed: bool = False

    @model_validator(mode="after")
    def _evidence(self) -> Sourced:
        if not self.assumed and not (self.cell and self.quote):
            raise ValueError("needs a cell and a quote, or assumed: true")
        return self


class FileId(_Model):
    name: str
    sha256: str


class Column(_Model):
    field: str | list[str]
    basis: Basis | None = None  # what a money column measures: a value basis, or a limit

    @property
    def fields(self) -> list[str]:
        return [self.field] if isinstance(self.field, str) else list(self.field)


class NonBuilding(_Model):
    """Rows that are not buildings (playground equipment, a generator): their values go to OtherTIV."""

    rows: str | None = None
    column: str | None = None
    labels: list[str] = []


class Table(_Model):
    id: str
    sheet: str | None = None
    rows_file: str | None = None  # a PDF table: the CSV a script wrote from the page lines, next to the plan
    header: list[int] = []
    rows: str  # "2-64": rows of the sheet or of the rows file
    skip: dict[str, str] = {}  # rows inside `rows` that are not locations -> why
    totals: list[str] = []  # rows printing totals of the table's columns: "65"
    decimal: Literal[".", ","] = "."
    multiplier: Sourced = Sourced(value=1, assumed=True)
    area_unit: Literal["sqft", "sqm"] | None = None
    columns: dict[str, Column]
    sentinels: dict[str, list[str]] = {}  # column -> values that mean "not known" ("0", "TBC")
    placeholders: dict[
        str, list[str]
    ] = {}  # column -> stand-in values (100,000 on every row, year 1985): kept in an amount, not known otherwise
    non_building: NonBuilding | None = None

    @model_validator(mode="after")
    def _source(self) -> Table:
        if (self.sheet is None) == (self.rows_file is None):
            raise ValueError("a table has a sheet or a rows_file, not both")
        if self.multiplier.value != 1 and self.multiplier.assumed:
            raise ValueError("a multiplier other than 1 needs a cell and a quote")
        return self

    @property
    def source(self) -> str:
        """The sheet the table reads: a sheet of the workbook, or the rows file."""
        if self.sheet is not None:
            return self.sheet
        assert self.rows_file is not None
        return self.rows_file


class CodeMap(_Model):
    map: dict[str, int]  # label as written -> OED code


class Fact(_Model):
    """A value the file states in words, for the location of that cell: "Flood: 713-719 30th St" -> FlexiLocComment."""

    cell: str
    field: str
    value: Any
    quote: str


class Cite(_Model):
    cell: str
    quote: str = Field(min_length=1)


class Question(_Model):
    """A point of this file only the broker can settle, in the words of a schedule, with the cells it is about."""

    text: str = Field(min_length=1)
    cells: list[Cite] = Field(min_length=1)
    title: str = Field(default="", max_length=60)  # a few words heading the question in the report


class Term(_Model):
    """An account value: AccPeril, a limit or a deductible, the account name."""

    field: str
    value: Any
    cell: str | None = None
    quote: str | None = None
    assumed: bool = False


class Account(_Model):
    perils: Sourced = Sourced(value="AA1", assumed=True)
    terms: list[Term] = []


class Plan(_Model):
    plan: int = VERSION
    file: FileId
    country: Sourced
    currency: Sourced
    insured: Sourced | None = None
    other_sheets: dict[str, str] = {}  # every sheet not read, with what it is
    tables: list[Table] = []  # none when the file holds no schedule (a summary, a questionnaire)
    codes: dict[str, CodeMap] = {}
    defaults: dict[str, Sourced] = {}  # a Location field stated once for every row (the city in the sheet name)
    facts: list[Fact] = []
    account: Account = Account()
    questions: list[Question] = Field(default=[], max_length=MAX_QUESTIONS)
    notes: list[str] = []

    @classmethod
    def read(cls, text: str) -> Plan:
        return cls.model_validate(yaml.load(text, Loader=_Loader))

    @classmethod
    def load(cls, path: str | Path) -> Plan:
        return cls.read(Path(path).read_text(encoding="utf-8"))

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json", exclude_defaults=True)
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110)


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# Row specifications ----------------------------------------------------------------------------------------------

RowKey = tuple[str, int]  # (sheet name or "p3", row or line number)
_PDF_POINT = re.compile(r"p(\d+):l(\d+)")


def sheet_rows(spec_text: str) -> list[int]:
    """ "2-64,70" -> [2, ..., 64, 70]."""
    out: list[int] = []
    for token in filter(None, (t.strip() for t in spec_text.split(","))):
        a, _, b = token.partition("-")
        if not (a.isdigit() and (not b or b.isdigit())):
            raise ValueError(f"'{token}' is not a row or a range of rows")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def page_numbers(spec_text: str) -> list[int]:
    return sheet_rows(spec_text)


def table_rows(t: Table, grid: Grid, spec_text: str) -> list[RowKey]:
    return [(t.source, r) for r in sheet_rows(spec_text)]


# Reference checks ------------------------------------------------------------------------------------------------


def split_cell(ref: str) -> tuple[str, str, int] | None:
    """ "Sheet!B12" or "'Sheet name '!B12" -> (sheet, "B", 12); None if it is not a cell reference."""
    sheet, _, cell = ref.rpartition("!")
    if len(sheet) > 1 and sheet[0] == sheet[-1] == "'":
        sheet = sheet[1:-1]
    m = re.fullmatch(r"([A-Za-z]{1,3})(\d+)", cell)
    return (sheet, m[1].upper(), int(m[2])) if sheet and m else None


def _ref_exists(ref: str, grid: Grid) -> str | None:
    """None if the reference names something in the file, else what is wrong."""
    if ref.startswith("file:"):
        return None if ref[5:] and ref[5:] in grid.path.name else f"'{ref}': the file is named {grid.path.name}"
    if ref.startswith("sheet:"):
        return None if ref[6:] in grid.sheets else f"'{ref}': no sheet of that name"
    if m := _PDF_POINT.fullmatch(ref):
        page = grid.sheets.get(f"p{m[1]}")
        if page is None or not 1 <= int(m[2]) <= len(page.lines):
            return f"'{ref}': no such page and line"
        return None
    parts = split_cell(ref)
    if parts is None:
        return f"'{ref}' is not a cell (Sheet!B12, p3:l12, sheet:<name> or file:<name>)"
    if parts[0] not in grid.sheets:
        return f"'{ref}': no sheet '{parts[0]}'"
    return None


def ref_text(ref: str, grid: Grid) -> str:
    """The text a reference shows: the cell, the PDF line, the sheet name or the file name."""
    if ref.startswith("file:"):
        return grid.path.name
    if ref.startswith("sheet:"):
        return ref[6:]
    if m := _PDF_POINT.fullmatch(ref):
        return grid.sheets[f"p{m[1]}"].lines[int(m[2]) - 1].text
    parts = split_cell(ref)
    assert parts
    c = grid.sheets[parts[0]].cell(parts[2], parts[1])
    return " ".join(str(x) for x in (c.value, c.comment) if x is not None)


def _sheet_of(t: Table, grid: Grid) -> list[Sheet]:
    return [grid.sheets[t.source]] if t.source in grid.sheets else []


def problems(plan: Plan, grid: Grid) -> list[str]:
    """Every reference of the plan that does not hold in the file: sheets, rows, columns, OED fields and codes."""
    out: list[str] = []
    if plan.country.value not in spec.countries():
        out.append(f"country '{plan.country.value}' is not an OED country code")
    if plan.currency.value not in spec.currencies():
        out.append(f"currency '{plan.currency.value}' is not an ISO 4217 code")
    for name in plan.other_sheets:
        if name in grid.sheets:
            continue
        pages = re.fullmatch(r"p(\d+)(?:-p(\d+))?", name) if grid.kind == "pdf" else None
        if not pages or not all(f"p{n}" in grid.sheets for n in range(int(pages[1]), int(pages[2] or pages[1]) + 1)):
            out.append(
                f"other_sheets: no sheet '{name}'" + (" (pages are written p4 or p4-p6)" if grid.kind == "pdf" else "")
            )
    for t in plan.tables:
        where = f"table {t.id}"
        if grid.kind == "pdf" and t.rows_file is None:
            out.append(f"{where}: a PDF table is read from a rows_file, written by a script from `lines`")
            continue
        if t.rows_file is not None and grid.kind != "pdf":
            out.append(f"{where}: rows_file is for a PDF; read the sheet itself")
            continue
        if t.source not in grid.sheets:
            names = [n for n, sh in grid.sheets.items() if sh.kind != "pdf_page"]
            out.append(f"{where}: no sheet '{t.source}'" + (f" (sheets: {', '.join(names)})" if t.sheet else ""))
            continue
        try:
            rows = table_rows(t, grid, t.rows)
            for spec_text in [*t.skip, *t.totals]:
                table_rows(t, grid, spec_text)
            if t.non_building and t.non_building.rows:
                table_rows(t, grid, t.non_building.rows)
        except ValueError as e:
            out.append(f"{where}: {e}")
            continue
        if not rows:
            out.append(f"{where}: no rows")
        sheets = _sheet_of(t, grid)
        if sheets and rows and rows[-1][1] > sheets[0].max_row:
            out.append(f"{where}: row {rows[-1][1]} is past the last row of the sheet ({sheets[0].max_row})")
        for col, c in t.columns.items():
            if not re.fullmatch(r"[A-Za-z]{1,3}", col):
                out.append(f"{where}: column '{col}' is not a column letter")
            elif t.rows_file is not None and col_index(col) == 1:
                out.append(f"{where}: column A of a rows file names the PDF lines; map the columns after it")
            elif sheets and col_index(col) > sheets[0].max_col:
                out.append(f"{where}: column {col} is past the last column of the sheet")
            for f in c.fields:
                if f in PSEUDO_FIELDS:
                    continue
                info = spec.field(f)
                if info is None:
                    out.append(f"{where}: column {col}: '{f}' is not an OED Location field")
                elif c.basis and info.kind != "money":
                    out.append(f"{where}: column {col}: a basis is for money fields, {f} is not one")
        if any("FloorArea" in c.fields for c in t.columns.values()) and not t.area_unit:
            out.append(f"{where}: FloorArea is mapped, area_unit is not given")
        for col in [
            *t.sentinels,
            *t.placeholders,
            *([t.non_building.column] if t.non_building and t.non_building.column else []),
        ]:
            if col not in t.columns:
                out.append(f"{where}: column {col} is used but not mapped in columns")
        if t.multiplier.cell and (err := _ref_exists(t.multiplier.cell, grid)):
            out.append(f"{where}: multiplier: {err}")
    for f, m in plan.codes.items():
        if f not in spec.codes():
            out.append(f"codes: '{f}' is not a coded OED field")
            continue
        out += [
            f"codes: {f} '{label}' -> {code} is not an OED code"
            for label, code in m.map.items()
            if not spec.is_code(f, code)
        ]
    for i, fact in enumerate(plan.facts):
        if spec.field(fact.field) is None:
            out.append(f"facts[{i}]: '{fact.field}' is not an OED Location field")
        if err := _ref_exists(fact.cell, grid):
            out.append(f"facts[{i}]: {err}")
    for i, q in enumerate(plan.questions):
        if named := [w for w in _CAMEL.findall(q.text) if spec.field(w) or spec.field(w, "Acc")]:
            out.append(f"questions[{i}]: {', '.join(named)} is an OED field name; use the words of the schedule")
        out += [f"questions[{i}]: {err}" for c in q.cells if (err := _ref_exists(c.cell, grid))]
    for f in plan.defaults:
        if spec.field(f) is None:
            out.append(f"defaults: '{f}' is not an OED Location field")
    for i, term in enumerate(plan.account.terms):
        if spec.field(term.field, "Acc") is None:
            out.append(f"account.terms[{i}]: '{term.field}' is not an OED Account field")
    if not set(spec.expand_perils(str(plan.account.perils.value))):
        out.append(f"account.perils: '{plan.account.perils.value}' is not an OED peril code")
    for label, s in _sourced(plan):
        if s.cell and (err := _ref_exists(s.cell, grid)):
            out.append(f"{label}: {err}")
    return out


def _sourced(plan: Plan) -> list[tuple[str, Sourced]]:
    out = [("country", plan.country), ("currency", plan.currency), ("account.perils", plan.account.perils)]
    out += [("insured", plan.insured)] if plan.insured else []
    out += [(f"defaults.{f}", s) for f, s in plan.defaults.items()]
    return out
