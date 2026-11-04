"""The broker's own workbook, annotated: doubtful cells coloured and commented, and the questions to send back.

For an xlsx input the broker's file is copied with its formatting, less its hidden sheets the plan does not read:
the copy goes back to the broker and on to insurers, and a hidden sheet may hold someone else's data. A formula
reading a removed sheet keeps its cached value, and a defined name or a validation list pointing into one goes.
Every cell a doubtful value comes from gets a
fill and a comment, and two sheets are added: "Questions" and "Legend". When the schedule has no location numbers,
a "Location no." column gives ours, right of everything the sheet holds, so the broker can keep them in the next
version. For a csv or pdf input there is no workbook to annotate: a new one holds the locations read, coloured
the same way. The same inputs give the same bytes (fixed zip timestamps and document dates).

Untrusted input: openpyxl reads the workbook without running anything; macros are never written back (an .xlsm
is saved as .xlsx). Every cell written here is text or a number, never a formula; a text a spreadsheet would read
as a formula is stored as text with the quote prefix style. The broker's cells keep their values: only their
fill and comment change.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.comments import Comment
from openpyxl.formula.tokenizer import Token, Tokenizer, TokenizerError
from openpyxl.reader.excel import ExcelReader
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.worksheet import Worksheet

from . import parse
from .extract import ASSUMED, DERIVED, Extraction, Location, Value
from .oed import spec
from .plan import split_cell
from .read.grid import col_index

AUTHOR = "sov2oed"
NUMBER_HEADER = "Location no."
CLASSES = ("unknown", "to_confirm", "from_text")  # most serious first: a cell takes the first that applies
FILLS = {"unknown": "FFD27F", "to_confirm": "FFF2A8", "from_text": "BDD7EE"}
LEGEND = {
    "unknown": "Missing: blank, not readable, or a label with no code",
    "to_confirm": "To confirm: a loose or stand-in value, or a cell a question is about",
    "from_text": "Read from text: a fact quoted from a note or a comment",
}
NOT_MARKED = {"FlexiLocComment", "LocName", "StreetAddress"}  # free text: a blank is no gap
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_FIXED_ISO = "1980-01-01T00:00:00Z"
_W3CDTF = re.compile(r"[0-9][0-9T:.+\-Z]{3,39}")
_HEADER = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")
Marks = dict[tuple[str, int, int], tuple[str, list[str]]]
HIDDEN = ("hidden", "veryHidden")


def _class(name: str, v: Value) -> str | None:
    if name in NOT_MARKED or v.status == ASSUMED:
        return None
    if v.status == parse.UNKNOWN or (spec.kind(name) == "code" and spec.is_unknown(name, v.value)):
        return "unknown"
    if v.status == parse.UNCERTAIN:
        return "to_confirm"
    if v.status == DERIVED and v.note and v.note.startswith("quoted: "):
        return "from_text"
    return None


def _line(loc: Location, name: str, v: Value) -> str:
    shown = "" if v.value is None or spec.kind(name) == "code" else parse.text_of(v.value)[:40]
    what = {"unknown": "missing", "uncertain": "to confirm", DERIVED: "read from text"}.get(v.status, v.status)
    return f"Location {loc.id} · {name}: " + (f"{shown}, {what}" if shown else what) + (f". {v.note}" if v.note else "")


def _keys(ref: str | None) -> list[tuple[str, int, int]]:
    """A cell reference -> [(sheet, row, column)]; "sheet:<name>" marks the sheet's first cell."""
    if not ref:
        return []
    if ref.startswith("sheet:"):
        return [(ref[6:], 1, 1)]
    out = []
    for part in ref.split("+"):  # two columns feeding one field: "S!A2+S!B2"
        if cell := split_cell(part):
            out.append((cell[0], cell[2], col_index(cell[1])))
    return out


NON_BUILDING_MARKED = {"BuildingTIV", "StreetAddress", "City", "PostalCode"}


def marks(ex: Extraction) -> Marks:
    """(sheet, row, column) -> (class, comment lines), from every doubtful value of every location."""
    out: Marks = {}
    for loc in ex.locations:
        for name in sorted(loc.values):
            v = loc.values[name]
            cls = _class(name, v)
            if cls is None or (v.note == "stated once for the file"):
                continue
            if cls == "unknown" and not loc.building and name not in NON_BUILDING_MARKED:
                continue  # a fence has no year built: the rules skip it, so does the workbook
            for key in _keys(v.cell):
                _add(out, key, cls, _line(loc, name, v))
    return out


def _add(out: Marks, key: tuple[str, int, int], cls: str, line: str) -> None:
    prev, lines = out.get(key, (cls, []))
    out[key] = (min(prev, cls, key=CLASSES.index), lines if line in lines else [*lines, line])


def with_questions(cells: Marks, questions: list[dict[str, Any]]) -> Marks:
    """Add the cells a question about the schedule's own figures points to (a total that does not add up)."""
    out = dict(cells)
    for i, q in enumerate(questions, start=1):
        for ref in q.get("cells") or []:
            for key in _keys(ref):
                _add(out, key, "to_confirm", f"Question {i} to the broker: see the Questions sheet")
    return out


def _formula_like(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(FORMULA_START)


def _text_only(ws: Worksheet, row: int | None = None) -> None:
    """Keep every text written here as text, shown as written."""
    for cells in [ws[row]] if row is not None else ws.iter_rows():
        for c in cells:
            if c.data_type == "f":
                c.data_type = "s"
            if c.data_type == "s" and _formula_like(c.value):
                c.quotePrefix = True


def _apply(ws: Worksheet, row: int, col: int, cls: str, lines: list[str]) -> None:
    cell = ws.cell(row=row, column=col)
    if isinstance(cell, MergedCell):  # paint a merged range through its top-left cell
        rng = next((r for r in ws.merged_cells.ranges if cell.coordinate in r), None)
        if rng is None:
            return
        cell = ws.cell(row=rng.min_row, column=rng.min_col)
    cell.fill = PatternFill("solid", fgColor=FILLS[cls])
    text = "\n".join(lines)
    if cell.comment is not None:
        text = f"{cell.comment.text}\n---\n{text}"  # the broker's own comment stays first
    comment = Comment(text, AUTHOR)
    comment.width, comment.height = 320, min(60 + 30 * text.count("\n"), 400)
    cell.comment = comment


def numbers_generated(ex: Extraction) -> bool:
    """The schedule has no location numbers: ours come from where each location is in the file."""
    return not any("LocNumber" in c.fields for t in ex.plan.tables for c in t.columns.values())


def _last_column(ws: Worksheet) -> int:
    return max((c.column for row in ws.iter_rows() for c in row if c.value is not None), default=0)


def _number_column(wb: Workbook, ex: Extraction) -> int:
    """Our location numbers in a new column right of everything the sheet holds; nothing existing moves."""
    n = 0
    for t in ex.plan.tables:
        if t.sheet is None or t.sheet not in wb.sheetnames:
            continue
        ws = wb[t.sheet]
        col = _last_column(ws) + 1
        if t.header:
            head = ws.cell(row=max(t.header), column=col)
            head.value, head.font = NUMBER_HEADER, _HEADER
        for loc in ex.locations:
            if loc.table == t.id:
                cell = ws.cell(row=loc.row[1], column=col)
                cell.value, cell.number_format = loc.id, "@"
                n += 1
    return n


def _unique_title(wb: Workbook, title: str) -> str:
    names, n = set(wb.sheetnames), 2
    if title not in names:
        return title
    while f"{title} ({n})" in names:
        n += 1
    return f"{title} ({n})"


def _questions_sheet(wb: Workbook, questions: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(_unique_title(wb, "Questions"))
    ws.append(["#", "Question", "Locations or cells", "Share of TIV (%)", "Ref"])
    for i, q in enumerate(questions, start=1):
        where = q.get("locations") or q.get("cells") or []
        shown = ", ".join(str(x) for x in where[:20]) + (f" (+{len(where) - 20})" if len(where) > 20 else "")
        ws.append([i, q.get("text", ""), shown, q.get("tiv_pct"), q.get("ref", "")])
    _text_only(ws)
    for c in ws[1]:
        c.font = _HEADER
    for col, width in zip("ABCDE", (4, 90, 32, 10, 22), strict=True):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = _WRAP
    ws.freeze_panes = "A2"


def _states(path: Path) -> dict[str, str]:
    """Sheet name -> state, chart sheets included (openpyxl loads a hidden chart sheet as visible)."""
    reader = ExcelReader(path, read_only=True, keep_links=False)
    try:
        reader.read_manifest()
        reader.read_workbook()
        return {s.name: s.state or "visible" for s in reader.parser.sheets}
    finally:
        reader.archive.close()


def removed_sheets(ex: Extraction) -> list[str]:
    """The hidden and very hidden sheets no table of the plan reads: left out of the annotated workbook."""
    if ex.grid.path.suffix.lower() not in (".xlsx", ".xlsm"):
        return []
    read = {t.sheet for t in ex.plan.tables if t.sheet is not None}
    return [name for name, state in _states(ex.grid.path).items() if state in HIDDEN and name not in read]


def _reads(formula: str, sheets: set[str], names: set[str]) -> bool:
    """The formula reads one of `sheets` or `names` (casefolded). A 3-D reference ("Jan:Mar!A1") or a formula
    that cannot be read counts as reading them."""
    try:
        tokens = Tokenizer(formula if formula.startswith("=") else "=" + formula).items
    except TokenizerError:
        return True
    for t in tokens:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        where = re.sub(r"^\[\d+\]", "", t.value.rpartition("!")[0])  # "[1]Sheet": an external workbook
        if len(where) > 1 and where[0] == where[-1] == "'":
            where = where[1:-1].replace("''", "'")
        if ":" in where or where.casefold() in sheets:
            return True
        if not where and t.value.split("[")[0].casefold() in names:  # a defined name, or a table: "T1[Value]"
            return True
    return False


def _drop_hidden(wb: Workbook, src: Path, gone: list[str]) -> None:
    """Remove the sheets `gone`; formulas reading them keep their cached value, names into them go."""
    sheets = {g.casefold() for g in gone}
    removed = [ws for ws in wb._sheets if ws.title.casefold() in sheets]
    kept = [ws for ws in wb.worksheets if ws.title.casefold() not in sheets]
    names = {t.casefold() for ws in removed if isinstance(ws, Worksheet) for t in ws.tables}
    for scope in [wb.defined_names, *(ws.defined_names for ws in kept)]:
        for name, dn in list(scope.items()):
            if dn.value and _reads(dn.value, sheets, names):
                names.add(name.casefold())
                del scope[name]
    cached: Workbook | None = None
    for ws in kept:
        for c in ws._cells.values():  # only the stored cells: iter_rows would fill the whole bounding box
            text = c.value.text if isinstance(c.value, ArrayFormula) else c.value
            if c.data_type == "f" and isinstance(text, str) and _reads(text, sheets, names):
                cached = cached or load_workbook(src, data_only=True, keep_links=False)
                c.value = cached[ws.title][c.coordinate].value
                if _formula_like(c.value):
                    c.data_type, c.quotePrefix = "s", True
        dvs = ws.data_validations.dataValidation
        dvs[:] = [dv for dv in dvs if not any(f and _reads(f, sheets, names) for f in (dv.formula1, dv.formula2))]
    active = wb.active
    for ws in removed:
        wb.remove(ws)
    if active in wb._sheets and active.sheet_state == "visible":
        wb.active = active


def _legend_sheet(wb: Workbook, n_marked: int, source: str, removed: list[str]) -> None:
    ws = wb.create_sheet(_unique_title(wb, "Legend"))
    ws.append(["Colour", "Meaning"])
    for c in ws[1]:
        c.font = _HEADER
    for cls in CLASSES:
        ws.append(["", LEGEND[cls]])
        ws.cell(row=ws.max_row, column=1).fill = PatternFill("solid", fgColor=FILLS[cls])
    ws.append([])
    ws.append(["", f"{n_marked} cells coloured. Each carries a comment by {AUTHOR}: location, field, status, note."])
    ws.append(["", f"Source: {source}."])
    if removed:
        ws.append(["", f"Hidden sheets not read, removed from this copy: {', '.join(removed)}"])
    _text_only(ws)
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 90


def _locations_sheet(wb: Workbook, ex: Extraction) -> int:
    """For a csv or pdf input: the locations read, one row each, coloured by status. Returns the cells marked."""
    ws = wb.active
    ws.title = "Locations"
    fields = list(dict.fromkeys(f for loc in ex.locations for f in loc.values))
    ws.append(["Location no.", "Source", *fields])
    for c in ws[1]:
        c.font = _HEADER
    n = 0
    for r, loc in enumerate(ex.locations, start=2):
        ws.append([loc.id, loc.ref, *[_cell_value(loc.values.get(f)) for f in fields]])
        _text_only(ws, r)
        for k, f in enumerate(fields, start=3):
            v = loc.values.get(f)
            if v is not None and (cls := _class(f, v)):
                _apply(ws, r, k, cls, [_line(loc, f, v) + (f" [{v.cell}]" if v.cell else "")])
                n += 1
    ws.freeze_panes = "B2"
    for k in range(1, len(fields) + 3):
        ws.column_dimensions[get_column_letter(k)].width = 16
    return n


def _cell_value(v: Value | None) -> Any:
    if v is None or v.value is None:
        return None
    return v.value if isinstance(v.value, (int, float, str)) else str(v.value)


def _source_dates(path: Path) -> tuple[str, str]:
    """The broker file's own created and modified dates (openpyxl would stamp "now")."""
    try:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile):
        return _FIXED_ISO, _FIXED_ISO
    core = re.sub(r"<!--.*?-->", "", core, flags=re.S)
    found = [re.search(rf"<dcterms:{t}[^>]*>([^<]+)</dcterms:{t}>", core) for t in ("created", "modified")]
    created, modified = (
        m.group(1).strip() if m and _W3CDTF.fullmatch(m.group(1).strip()) else _FIXED_ISO for m in found
    )
    return created, modified


def _keep_tags(value: str) -> Callable[[re.Match[str]], str]:
    """A replacement that keeps the matched tags around `value` (never read as a template)."""
    return lambda m: m.group(1) + value + m.group(2)


def _deterministic_save(wb: Workbook, out_path: Path, created: str, modified: str) -> None:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with zipfile.ZipFile(buf) as src, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                for tag, value in (("created", created), ("modified", modified)):
                    text = re.sub(rf"(<dcterms:{tag}[^>]*>)[^<]*(</dcterms:{tag}>)", _keep_tags(value), text)
                data = text.encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=_FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            dst.writestr(zi, data)


def write(ex: Extraction, questions: list[dict[str, Any]], out_path: str | Path) -> Path:
    """Write the annotated workbook to `out_path` (forced to .xlsx) and return its path."""
    src, out = ex.grid.path, Path(out_path).with_suffix(".xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    removed = removed_sheets(ex)
    if src.suffix.lower() in (".xlsx", ".xlsm"):
        wb = load_workbook(src, keep_vba=False, keep_links=False)
        _drop_hidden(wb, src, removed)
        created, modified = _source_dates(src)
        n = 0
        for (sheet, row, col), (cls, lines) in sorted(with_questions(marks(ex), questions).items()):
            if sheet in wb.sheetnames:
                _apply(wb[sheet], row, col, cls, lines)
                n += 1
        if numbers_generated(ex):
            _number_column(wb, ex)
    else:
        wb = Workbook()
        created = modified = _FIXED_ISO
        n = _locations_sheet(wb, ex)
    _questions_sheet(wb, questions)
    _legend_sheet(wb, n, ex.plan.file.name, removed)
    _deterministic_save(wb, out, created, modified)
    return out
