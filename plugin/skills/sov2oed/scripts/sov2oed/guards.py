"""Checks of a reading against the whole file, run after extraction and before anything is written.

Each guard answers one question a real schedule raised: do the rows add up to the printed totals (each section to
its subtotal), does each row add up to its printed row total, is a formula column broken, is every row and every
sheet accounted for, has every label a code, is every quote in its cell, is a row read twice, is a number cell
readable, did a PDF line cut cleanly.

A finding is "plan" when the reading plan is incomplete or wrong (Claude fixes it), "file" when the schedule
itself is in doubt (it becomes a question for the broker if it survives the plan's revision).
"""

from __future__ import annotations

import ast
import operator
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import parse
from .extract import Extraction, cell_ref, fact_location, read_row, row_ref
from .oed import spec
from .plan import Column, RowKey, Table, ref_text, sheet_rows, table_rows
from .read.grid import Cell, Sheet, col_index, line_keys

TOLERANCE = 1.0  # currency units: a printed total within this of the rows' sum reconciles
FORMULA_SHARE = 0.5  # a column whose usual formula fills at least this share of its rows is a formula column
MIN_FORMULAS = 3
_SUM = re.compile(r"SUM\(\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)\)", re.I)
_REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")


@dataclass
class Finding:
    guard: str  # totals | row_totals | formulas | rows | sheets | codes | quotes | overlap | unreadable | sources
    kind: str  # "plan" | "file"
    message: str
    cells: list[str] = field(default_factory=list)
    amount: float = 0.0  # money concerned, to rank the questions
    hint: str = ""  # what to change in the plan if the plan, not the file, is the cause


def check(ex: Extraction) -> list[Finding]:
    out: list[Finding] = []
    for guard in (_totals, _row_totals, _formulas, _rows, _sheets, _codes, _quotes, _overlap, _unreadable, _sources):
        out += guard(ex)
    return out


_NONE = Column(field="ignore")


def _money_columns(t: Table) -> list[str]:
    return [col for col, c in t.columns.items() if any(f == "row_total" or spec.kind(f) == "money" for f in c.fields)]


def _amount(v: object, t: Table) -> float | None:
    n = parse.parse_number(v, t.decimal)
    return None if n is None else n * float(t.multiplier.value)


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x - round(x)) < 0.005 else f"{x:,.2f}"


# 1. Totals -------------------------------------------------------------------------------------------------------


def _header(sheet: Sheet, t: Table, col: str) -> str:
    """A column's header text, or its letter. A rows file has its header on row 1 even when a table names none."""
    rows = t.header or ([1] if sheet.kind == "rows" else [])
    return " ".join(parse.text_of(sheet.value(h, col)) for h in rows).strip() or col


def _grand(sheet: Sheet, t: Table, rows: list[int], cols: list[str]) -> int:
    """The grand total among a table's total rows: the one printing the sum of the others (two at least), else the
    last."""
    printed = {r: {c: a for c in cols if (a := _amount(sheet.value(r, c), t)) is not None} for r in rows}
    for r in rows:
        others = [x for x in rows if x != r]
        if (
            len(others) >= 2
            and printed[r]
            and all(
                abs(v - parse.fsum([printed[x].get(c, 0.0) for x in others])) <= TOLERANCE * len(others)
                for c, v in printed[r].items()
            )
        ):
            return r
    return rows[-1]


def _totals(ex: Extraction) -> list[Finding]:
    """Each printed total against the rows it closes, in every table that names it (two tables may share one total
    row). A subtotal closes the rows read since the total above it; the grand total closes the whole table."""
    out: list[Finding] = []
    by_total: dict[RowKey, list[Table]] = defaultdict(list)
    for t in ex.plan.tables:
        for spec_text in t.totals:
            for key in table_rows(t, ex.grid, spec_text):
                by_total[key].append(t)
    for key, tables in by_total.items():
        t = tables[0]
        sheet = ex.grid.sheets[key[0]]
        ids = {x.id for x in tables}
        locs = [loc for loc in ex.locations if loc.table in ids]
        cols = list(dict.fromkeys(col for x in tables for col in _money_columns(x)))
        totals = sorted(k[1] for k, ts in by_total.items() if k[0] == key[0] and ids & {x.id for x in ts})
        above = max((r for r in totals if r < key[1]), default=0)
        section = sorted((loc for loc in locs if above < loc.row[1] < key[1]), key=lambda loc: loc.row[1])
        scope = "the rows read"
        if section and len(section) < len(locs) and key[1] != _grand(sheet, t, totals, cols):
            locs = section
            scope = f"the rows of its section ({section[0].ref} to {section[-1].ref})"
        sums = {col: parse.fsum([a for loc in locs if (a := _amount(loc.raw.get(col), t)) is not None]) for col in cols}
        grand = parse.fsum(
            sums[col] for col in cols if all("row_total" not in x.columns.get(col, _NONE).fields for x in tables)
        )
        read_rows = sorted(loc.row[1] for loc in locs)
        printed_row = read_row(t, ex.grid, key)
        for col in cols:
            printed = _amount(printed_row.get(col), t)
            if printed is None:
                continue
            ref = cell_ref(ex.grid, key, col)
            formula = sheet.cell(key[1], col).formula
            if abs(printed - sums[col]) > TOLERANCE and abs(printed - grand) > TOLERANCE:
                gap = sums[col] - printed
                shown = f" ({formula})" if formula else ""
                msg = f"{ref}{shown} prints {_fmt(printed)}; {scope} add up to {_fmt(sums[col])}"
                msg += f" ({'+' if gap > 0 else ''}{_fmt(gap)}, {gap / printed:+.2%})." if printed else "."
                m = _SUM.search(formula or "")
                if m and m[1].upper() == m[3].upper() != col.upper():  # P30 =SUM(Q6:Q29)
                    other = m[1].upper()
                    msg += (
                        f" {col}{key[1]} adds column {other} ({_header(sheet, t, other)}), "
                        f"not column {col} ({_header(sheet, t, col)})."
                    )
                hint = "If rows are missing from the plan, fix `rows`; otherwise the schedule does not add up."
                out.append(Finding("totals", "file", msg, [ref], abs(gap), hint))
            out += _short_sum(t, ex, key, col, ref, read_rows, "section" if locs is section else "table")
    return out


def _short_sum(
    t: Table, ex: Extraction, key: RowKey, col: str, ref: str, read_rows: list[int], scope: str
) -> list[Finding]:
    """A SUM in a total cell that leaves out rows of its table, or of its section, that hold a value."""
    if ex.grid.sheets[key[0]].kind != "xlsx" or not read_rows:
        return []
    formula = ex.grid.sheets[key[0]].cell(key[1], col).formula or ""
    ranges = [(int(m[2]), int(m[4])) for m in _SUM.finditer(formula) if m[1].upper() == m[3].upper() == col.upper()]
    if not ranges:
        return []

    def added(r: int) -> bool:
        return any(a <= r <= b for a, b in ranges)

    sheet = ex.grid.sheets[key[0]]
    left_out = [r for r in read_rows if not added(r) and _amount(sheet.value(r, col), t)]
    if not left_out and added(read_rows[0]) and added(read_rows[-1]):
        return []
    missed = parse.fsum([_amount(sheet.value(r, col), t) or 0.0 for r in left_out])
    rows = f"rows {read_rows[0]}-{read_rows[-1]}"
    adds = " and ".join(f"{a}-{b}" for a, b in ranges)
    msg = f"{ref} is {formula}: it adds rows {adds}, the {scope} holds {rows}"
    msg += (
        f"; it leaves out {_fmt(missed)} in {col}{left_out[0]}" + ("..." if len(left_out) > 1 else "")
        if left_out
        else ""
    )
    msg += "."
    return [Finding("totals", "file", msg, [ref], missed)]


# 2. Formula columns ----------------------------------------------------------------------------------------------


def _pattern(formula: str, row: int) -> str:
    """A formula with its same-sheet row numbers made relative: "=SUM(D12:E12)" at row 12 -> "=SUM(D[0]:E[0])"."""
    return _REF.sub(lambda m: f"{m[2]}{m[4] if m[3] else f'[{int(m[4]) - row}]'}", formula.upper())


def _evaluate(pattern: str, sheet: Sheet, row: int, t: Table) -> float | None:
    """The value of a formula made of same-row cells, numbers, + - * / and SUM of a same-row range, else None."""

    def cell(col: str | int) -> str:
        return repr(_amount(sheet.value(row, col), t) or 0.0)

    body = re.sub(
        r"SUM\(([A-Z]{1,3})\[0\]:([A-Z]{1,3})\[0\]\)",
        lambda m: "(" + "+".join(cell(c) for c in range(col_index(m[1]), col_index(m[2]) + 1)) + ")",
        pattern.removeprefix("="),
    )
    body = re.sub(r"([A-Z]{1,3})\[0\]", lambda m: cell(m[1]), body)
    body = re.sub(
        r"\b([A-Z]{1,3})(\d+)\b", lambda m: repr(_amount(sheet.value(int(m[2]), m[1]), t) or 0.0), body
    )  # $Y$1
    try:
        return _arithmetic(ast.parse(body, mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _arithmetic(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_arithmetic(node.left), _arithmetic(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_arithmetic(node.operand)
    raise ValueError("not arithmetic")


def _usual(cells: dict[int, Cell]) -> tuple[str, int] | None:
    """A column's usual formula and the rows holding it, when it fills enough of the column to be the column's rule."""
    patterns = Counter(_pattern(x.formula, r) for r, x in cells.items() if x.formula)
    if not patterns:
        return None
    usual, n = patterns.most_common(1)[0]
    filled = sum(1 for x in cells.values() if x.value is not None or x.formula)
    return None if n < MIN_FORMULAS or n < FORMULA_SHARE * filled else (usual, n)


def _formulas(ex: Extraction) -> list[Finding]:
    out: list[Finding] = []
    for t in ex.plan.tables:
        if t.sheet is None:
            continue
        sheet = ex.grid.sheets[t.sheet]
        rows = [loc.row[1] for loc in ex.locations if loc.table == t.id]
        money = set(_money_columns(t))
        for col in t.columns:
            c = col_index(col)
            cells = {r: sheet.cell(r, c) for r in rows}
            if col in money:
                for r, x in cells.items():
                    if (
                        x.formula
                        and re.fullmatch(r"=[\d.+\-*/() ]+", x.formula)
                        and re.search(r"\d[+\-*/]\d", x.formula)
                    ):
                        out.append(Finding("formulas", "file", f"{col}{r} is {x.formula}: an amount worked out by hand.", [f"{t.sheet}!{col}{r}"]))  # fmt: skip
            if (rule := _usual(cells)) is None:
                continue
            usual, n = rule
            odd = [r for r, x in cells.items() if not x.formula or _pattern(x.formula, r) != usual]
            if not odd:
                continue
            header = _header(sheet, t, col)
            usual_text = re.sub(r"\[0\]", "", usual)
            if _evaluate(usual, sheet, odd[0], t) is not None:
                wrong = [
                    (r, e, _amount(cells[r].value, t) or 0.0)
                    for r in odd
                    if (e := _evaluate(usual, sheet, r, t)) is not None
                ]
                wrong = [w for w in wrong if abs(w[1] - w[2]) > TOLERANCE]
                if wrong:
                    gap = parse.fsum([e - v for _, e, v in wrong])
                    out.append(Finding("formulas", "file", (
                        f"Column {col} ({header}) is {usual_text} on {n} rows, but {_rows_text([r for r, _, _ in wrong], col)} "
                        f"{'is' if len(wrong) == 1 else 'are'} blank or typed and differ{'s' if len(wrong) == 1 else ''} "
                        f"from that formula by {_fmt(gap)}{' in all' if len(wrong) > 1 else ''}."
                    ), [f"{t.sheet}!{col}{r}" for r, _, _ in wrong], abs(gap)))  # fmt: skip
            else:
                typed = [r for r in odd if cells[r].value is not None and not cells[r].formula]
                if typed:
                    out.append(Finding("formulas", "file", (
                        f"Column {col} ({header}) is a formula on {n} rows, but {_rows_text(typed, col)} "
                        f"{'holds a typed value' if len(typed) == 1 else 'hold typed values'} instead."
                    ), [f"{t.sheet}!{col}{r}" for r in typed]))  # fmt: skip
    return out


def _rows_text(rows: list[int], col: str = "") -> str:
    """[51, 52, 53, 60] -> "51-53, 60" (or "F51-F53, F60")."""
    out, run = [], [rows[0]] if rows else []
    for r in rows[1:]:
        if r == run[-1] + 1:
            run.append(r)
        else:
            out.append(run)
            run = [r]
    if run:
        out.append(run)
    return ", ".join(f"{col}{a[0]}" + (f"-{col}{a[-1]}" if len(a) > 1 else "") for a in out)


# 3. Rows and 4. sheets -------------------------------------------------------------------------------------------


def _rows(ex: Extraction) -> list[Finding]:
    """Rows that look like locations but are neither read nor skipped; rows skipped as blank that hold values."""
    out: list[Finding] = []
    claimed: dict[str, set[int]] = defaultdict(set)
    for t in ex.plan.tables:
        claimed[t.source].update(t.header)
        for spec_text in [t.rows, *t.skip, *t.totals]:
            claimed[t.source].update(r for _, r in table_rows(t, ex.grid, spec_text))
    for t in ex.plan.tables:
        sheet = ex.grid.sheets[t.source]
        first = 2 if sheet.kind == "rows" else 1  # column A of a rows file names the PDF lines
        money = _money_columns(t)
        looks = [
            r
            for r in range(1, sheet.max_row + 1)
            if r not in claimed[t.source]
            and any(_amount(sheet.value(r, col), t) for col in money)
            and sum(not parse.blank(sheet.value(r, col)) for col in t.columns) >= 2
        ]
        if looks:
            out.append(Finding("rows", "plan", (
                f"Sheet '{t.source}': rows {_rows_text(looks)} hold values in the table's columns but are neither "
                "read nor skipped. Add them to `rows`, or to `skip` or `totals` with a reason."
            ), [f"{t.source}!{r}" for r in looks]))  # fmt: skip
        blank = [k[1] for tid, k, why in ex.skipped if tid == t.id and why == "blank"]
        hidden = [r for r in blank if any(not parse.blank(v) for v in sheet.row_values(r)[first - 1 :])]
        if hidden:
            out.append(Finding("rows", "plan", (
                f"Sheet '{t.source}': rows {_rows_text(hidden)} are blank in the mapped columns but hold values in "
                "other columns. Map those columns, or skip the rows with a reason."
            ), [f"{t.source}!{r}" for r in hidden]))  # fmt: skip
    return out


def _sheets(ex: Extraction) -> list[Finding]:
    """Every sheet or page with content is read by a table or listed in other_sheets, hidden ones included."""
    read = {t.source for t in ex.plan.tables}
    for t in ex.plan.tables:
        if t.rows_file is not None:
            read.update(f"p{page}" for page, _ in _claimed_lines(ex.grid.sheets[t.source]))
    listed: set[str] = set()
    for name in ex.plan.other_sheets:
        if m := re.fullmatch(r"p(\d+)(?:-p(\d+))?", name):
            listed.update(f"p{n}" for n in range(int(m[1]), int(m[2] or m[1]) + 1))
        listed.add(name)
    out = []
    for name, sheet in ex.grid.sheets.items():
        if name in read or name in listed:
            continue
        has_content = (
            any(line.text.strip() for line in sheet.lines)
            if sheet.kind == "pdf_page"
            else any(isinstance(c.value, (int, float)) and not isinstance(c.value, bool) for c in sheet.cells.values())
        )
        if has_content:
            what = "page" if sheet.kind == "pdf_page" else ("hidden sheet" if sheet.hidden else "sheet")
            out.append(Finding("sheets", "plan", (
                f"The {what} '{name}' holds {'text' if sheet.kind == 'pdf_page' else 'numbers'} and is neither read nor "
                "listed in other_sheets. Read it as a table, or list it with what it is."
            ), [f"sheet:{name}"]))  # fmt: skip
    return out


# 5. Codes and 6. quotes ------------------------------------------------------------------------------------------


def _codes(ex: Extraction) -> list[Finding]:
    missing: dict[str, Counter[str]] = defaultdict(Counter)
    cells: dict[str, list[str]] = defaultdict(list)
    for loc in ex.locations:
        for name, v in loc.values.items():
            if v.note and v.note.startswith("label '") and "has no" in v.note:
                label = v.note.split("'")[1]
                missing[name][label] += 1
                cells[name].append(v.cell or loc.ref)
    return [
        Finding(
            "codes",
            "plan",
            (
                f"{len(labels)} label(s) of {name} have no code in `codes`: "
                + "; ".join(f'"{label}" ({n} rows)' for label, n in labels.most_common(20))
                + (" ..." if len(labels) > 20 else "")
            ),
            cells[name][:50],
        )
        for name, labels in missing.items()
    ]


def _quote_in(quote: str, ref: str, ex: Extraction) -> bool:
    return parse.normalise(quote) in parse.normalise(ref_text(ref, ex.grid))


def _quotes(ex: Extraction) -> list[Finding]:
    out = []
    plan = ex.plan
    stated = [("country", plan.country), ("currency", plan.currency), ("account.perils", plan.account.perils)]
    stated += [("insured", plan.insured)] if plan.insured else []
    stated += [(f"defaults.{f}", s) for f, s in plan.defaults.items()]
    stated += [(f"table {t.id} multiplier", t.multiplier) for t in plan.tables]
    for label, s in stated:
        if s is not None and s.cell and s.quote and not _quote_in(s.quote, s.cell, ex):
            out.append(Finding("quotes", "plan", f"{label}: '{s.quote}' is not in {s.cell}.", [s.cell]))
    for i, fact in enumerate(plan.facts):
        if not _quote_in(fact.quote, fact.cell, ex):
            out.append(Finding("quotes", "plan", f"facts[{i}]: '{fact.quote}' is not in {fact.cell}.", [fact.cell]))
        numbers = re.findall(r"\d+(?:[.,]\d+)?", str(fact.value))
        if spec.kind(fact.field) not in (None, "code") and (absent := [n for n in numbers if n not in fact.quote]):
            out.append(Finding("quotes", "plan", f"facts[{i}]: {', '.join(absent)} in the value is not in the quote.", [fact.cell]))  # fmt: skip
        if fact_location(ex.locations, ex.grid, fact.cell) is None:
            out.append(Finding("quotes", "plan", f"facts[{i}]: {fact.cell} is not on a row read as a location.", [fact.cell]))  # fmt: skip
    return out


# 7. Rows read twice ----------------------------------------------------------------------------------------------


def _overlap(ex: Extraction) -> list[Finding]:
    """The same rows read by two tables: the reading counts them twice. A schedule listing a building twice is the
    duplicates rule's (LINK-002), not a reading error."""
    seen: dict[RowKey, str] = {}
    twice: dict[tuple[str, str], list[RowKey]] = defaultdict(list)
    for loc in ex.locations:
        if loc.row in seen and seen[loc.row] != loc.table:
            twice[(seen[loc.row], loc.table)].append(loc.row)
        seen.setdefault(loc.row, loc.table)
    return [
        Finding("overlap", "plan", f"Tables {a} and {b} both read {len(rows)} row(s), from {rows[0][0]}!{rows[0][1]}.",
                [row_ref(ex.grid, k) for k in rows[:50]], hint="Read each row in one table: fix `rows` or `skip`.")
        for (a, b), rows in twice.items()
    ]  # fmt: skip


# 8. Unreadable cells ---------------------------------------------------------------------------------------------


def _unreadable(ex: Extraction) -> list[Finding]:
    """Cells of a number column that state something but not a number ("n/k", "as last year + inflation")."""
    found: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for loc in ex.locations:
        for name, v in loc.values.items():
            if v.status == parse.UNKNOWN and v.note and v.note.startswith("unreadable"):
                found[(loc.table, name)].append((v.cell or loc.ref, v.note.split("'", 1)[1].rstrip("'")))
    out = []
    for (tid, name), items in found.items():
        texts = Counter(text for _, text in items)
        out.append(Finding("unreadable", "file", (
            f"{len(items)} cell(s) of {name} in table {tid} are not readable as a value: "
            + "; ".join(f"'{text}' ({n})" for text, n in texts.most_common(5))
            + ". They are read as not known."
        ), [c for c, _ in items]))  # fmt: skip
    return out


# 9. PDF rows ----------------------------------------------------------------------------------------------------

_AMOUNT = re.compile(
    r"(?<![\d.,])\d{1,3}(?:[,.\u00a0\u202f ]\d{3})+(?:[.,]\d{1,2})?(?!\d)"
)  # 1,250, 1.250.000,00, 2 593


def _claimed_lines(sheet: Sheet) -> set[tuple[int, int]]:
    """Every PDF line named in column A of a rows file, its header row aside."""
    return {k for r in range(2, sheet.max_row + 1) for k in line_keys(sheet.value(r, 1)) or []}


def _printed(value: object, text: str, decimal: str) -> bool:
    """Whether a value of a rows file is printed on its lines: each number as a number, each word as written."""
    words = text.split()
    for token in parse.text_of(value).split():
        n = parse.parse_number(token, decimal) if re.search(r"\d", token) else None
        if n is not None:
            if not any(parse.parse_number(w, decimal) == n for w in words):
                return False
        elif parse.normalise(token) not in parse.normalise(text):
            return False
    return True


def _sources(ex: Extraction) -> list[Finding]:
    """A rows file against the PDF: its lines exist, its values are printed on them, no line with an amount is left
    out. What Claude's script did is checked here, not trusted."""
    out, seen = [], set()
    for t in ex.plan.tables:
        sheet = ex.grid.sheets[t.source]
        if sheet.kind != "rows":
            continue
        first = t.source not in seen  # what concerns the whole rows file is reported once, not per table
        seen.add(t.source)
        pages = {int(n[1:]): sh for n, sh in ex.grid.sheets.items() if sh.kind == "pdf_page"}
        bad_refs, not_printed = [], []
        for r in range(2, sheet.max_row + 1):
            keys = line_keys(sheet.value(r, 1))
            if keys is None or any(p not in pages or not 1 <= n <= len(pages[p].lines) for p, n in keys):
                bad_refs.append(f"{t.source}!A{r}")
                continue
            if r not in set(sheet_rows(t.rows)) | {x for spec_text in t.totals for x in sheet_rows(spec_text)}:
                continue
            text = " ".join(pages[p].lines[n - 1].text for p, n in keys)
            not_printed += [f"{t.source}!{col}{r}" for col in t.columns
                            if not parse.blank(v := sheet.value(r, col)) and not _printed(v, text, t.decimal)]  # fmt: skip
        if bad_refs and first:
            out.append(Finding("sources", "plan", (
                f"{t.source}: {len(bad_refs)} row(s) whose column A is not a list of lines of this PDF "
                f"(p3:l12, p3:l12-l14, p3:l12+p4:l1), e.g. {bad_refs[0]}."
            ), bad_refs[:50], hint="Write the lines each row was read from in column A."))  # fmt: skip
        if not_printed:
            out.append(Finding("sources", "plan", (
                f"{t.source}: {len(not_printed)} value(s) are not printed on the lines of their row, e.g. "
                f"{not_printed[0]}. A value must be copied as printed, from the lines named in column A."
            ), not_printed[:50], hint="Fix the script: a value taken from another line, or reformatted."))  # fmt: skip
        claimed = _claimed_lines(sheet)
        left_out = [f"p{p}:l{ln.no}" for p in sorted({p for p, _ in claimed}) for ln in pages.get(p, Sheet("", "")).lines
                    if (p, ln.no) not in claimed and _AMOUNT.search(ln.text)]  # fmt: skip
        if left_out and first:
            out.append(Finding("sources", "plan", (
                f"{t.source}: {len(left_out)} line(s) of these pages print an amount and are on no row, e.g. "
                f"{', '.join(left_out[:5])}. A location's figures on a line of its own (a wrapped row) are lost."
            ), left_out[:50], hint="Join the line to its location's row, or give it a row of its own and skip it with a reason."))  # fmt: skip
    return out


# 10. Row totals --------------------------------------------------------------------------------------------------


def _row_totals(ex: Extraction) -> list[Finding]:
    """A printed row total against the sum of the row's amounts read, on every source. A formula column of an xlsx,
    and any cell holding a formula, is left to the formula check. A total that matches on fewer than half its rows
    adds something else (a column not read) and is not compared."""
    out = []
    for t in ex.plan.tables:
        sheet = ex.grid.sheets[t.source]
        locs = [loc for loc in ex.locations if loc.table == t.id]
        parts = [col for col in _money_columns(t) if "row_total" not in t.columns[col].fields]
        for col in (c for c, x in t.columns.items() if "row_total" in x.fields):
            if not parts or _usual({loc.row[1]: sheet.cell(loc.row[1], col) for loc in locs}):
                continue
            checked: list[tuple[str, float, float]] = []  # (cell, printed, sum of the amounts)
            for loc in locs:
                printed = _amount(loc.raw.get(col), t)
                amounts = [_amount(loc.raw.get(p), t) for p in parts]
                if printed is None or sheet.cell(loc.row[1], col).formula:
                    continue
                if any(a is None and not parse.blank(loc.raw.get(p)) for a, p in zip(amounts, parts, strict=True)):
                    continue  # an unreadable amount, reported as such
                checked.append((cell_ref(ex.grid, loc.row, col), printed, parse.fsum([a or 0.0 for a in amounts])))
            wrong = [w for w in checked if abs(w[1] - w[2]) > TOLERANCE]
            if not wrong or len(checked) - len(wrong) < FORMULA_SHARE * len(checked):
                continue
            names = " + ".join(_header(sheet, t, p) for p in parts)
            listed = "; ".join(
                f"{ref} prints {_fmt(p)}, {_fmt(p - s)} {'above' if p > s else 'below'} {names} ({_fmt(s)})"
                for ref, p, s in wrong[:5]
            )
            gap = parse.fsum([abs(p - s) for _, p, s in wrong])
            out.append(Finding("row_totals", "file", (
                f"The row total ({_header(sheet, t, col)}) is {names} on {len(checked) - len(wrong)} rows, but not on "
                f"{len(wrong)} row{'' if len(wrong) == 1 else 's'}: {listed}{' ...' if len(wrong) > 5 else ''}."
            ), [ref for ref, _, _ in wrong], gap))  # fmt: skip
    return out
