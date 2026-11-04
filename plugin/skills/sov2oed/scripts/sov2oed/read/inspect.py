"""What Claude looks at to write the reading plan: the file as it is, sheet by sheet and page by page.

Every view is plain text. `overview` lists the sheets or pages; `rows` prints a range of a sheet with its column
letters, formulas and comments; `page` prints a PDF page line by line with the x position of every word, to
write the script that groups lines into rows; `labels` counts the distinct values of a column (of a sheet or of a
rows file), which is what the code lists are written from; `codes` prints an OED code list.
"""

from __future__ import annotations

from collections import Counter

from .. import parse
from ..oed import spec
from ..plan import sheet_rows
from .grid import Grid, Sheet, col_index, col_letter

CELL_WIDTH = 28  # characters of a cell shown in a row view


def _short(text: str, width: int = CELL_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def overview(grid: Grid, preview: int = 8) -> str:
    out = [f"{grid.path.name}: {grid.kind}, {len(grid.sheets)} {'pages' if grid.kind == 'pdf' else 'sheets'}"]
    for name, sh in grid.sheets.items():
        if sh.kind == "pdf_page":
            texts = [ln.text for ln in sh.lines if ln.text.strip()]
            out.append(f"\n[{name}] {len(sh.lines)} lines" + ("" if texts else " (no text: scanned or empty)"))
            out += [f"  l{ln.no}: {_short(ln.text, 110)}" for ln in sh.lines[:preview]]
            continue
        numbers = sum(isinstance(c.value, (int, float)) and not isinstance(c.value, bool) for c in sh.cells.values())
        formulas = sum(c.formula is not None for c in sh.cells.values())
        comments = sum(c.comment is not None for c in sh.cells.values())
        flags = [f for f, on in (("HIDDEN", sh.hidden), (f"{len(sh.hidden_rows)} hidden rows", sh.hidden_rows),
                                 (f"{len(sh.hidden_cols)} hidden columns", sh.hidden_cols)) if on]  # fmt: skip
        out.append(
            f"\n[{name}] rows 1-{sh.max_row}, columns A-{col_letter(max(sh.max_col, 1))}: {len(sh.cells)} cells, "
            f"{numbers} numbers, {formulas} formulas, {comments} comments, {len(sh.merged)} merged ranges"
            + (f" ({', '.join(flags)})" if flags else "")
        )
        out.append(rows(grid, name, f"1-{min(sh.max_row, preview)}"))
    return "\n".join(out)


def _cell_text(sh: Sheet, r: int, c: int, width: int) -> str:
    """The value cut to `width`, then its formula and its comment, each cut on its own."""
    cell = sh.cell(r, c)
    parts = [_short(parse.text_of(cell.value), width)] if cell.value is not None else []
    if cell.formula:
        parts.append("{" + _short(cell.formula, width) + "}")
    if cell.comment:
        parts.append(f"[comment: {_short(cell.comment, 80)}]")
    return " ".join(parts)


def rows(grid: Grid, sheet: str, spec_text: str = "1-40", columns: str | None = None, width: int = CELL_WIDTH) -> str:
    """Rows of a sheet, one line each: "12 | A: 4 Mill Lane | C: 1,200,000 {=SUM(D12:E12)}"; blank cells left out."""
    sh = grid.sheets[sheet]
    if columns:
        first, _, last = columns.partition("-")
        cols = range(col_index(first), col_index(last or first) + 1)
    else:
        cols = range(1, sh.max_col + 1)
    out = []
    for r in sheet_rows(spec_text):
        if r > sh.max_row:
            break
        cells = [f"{col_letter(c)}: {t}" for c in cols if (t := _cell_text(sh, r, c, width))]
        hidden = " (hidden)" if r in sh.hidden_rows else ""
        out.append(f"{r}{hidden} | " + " | ".join(cells) if cells else f"{r}{hidden} |")
    return "\n".join(out)


def page(grid: Grid, number: int, lines: str | None = None) -> str:
    """A PDF page, one line each with every word at its x position: "l12 | 50 TELEMETRYNC | 300 125,000"."""
    sh = grid.sheets[f"p{number}"]
    wanted = set(sheet_rows(lines)) if lines else None
    out = [f"page {number}: {len(sh.lines)} lines, width {sh.width:.0f}"]
    for ln in sh.lines:
        if wanted is not None and ln.no not in wanted:
            continue
        out.append(f"l{ln.no} | " + " | ".join(f"{w.x0:.0f} {w.text}" for w in ln.words))
    return "\n".join(out)


def labels(grid: Grid, sheet: str, column: str, spec_text: str) -> str:
    """The distinct values of a column over rows, most frequent first: what a code list is written from."""
    sh = grid.sheets[sheet]
    counts = Counter(parse.text_of(sh.value(r, column)) for r in sheet_rows(spec_text))
    counts.pop("", None)
    return "\n".join(f"{n:>5}  {label}" for label, n in counts.most_common())


GUIDE = {"guide": "plan_guide.md", "plan": "plan_example.yaml", "rows": "rows_example.py"}


def guide(name: str = "guide") -> str:
    """The reading plan guide, or an example: a plan for a fictional schedule, a rows script for a PDF."""
    from importlib.resources import files

    if name not in GUIDE:
        raise ValueError(f"no example '{name}': plan or rows")
    return files("sov2oed").joinpath("data", GUIDE[name]).read_text(encoding="utf-8")


def codes(field_name: str) -> str:
    """An OED code list, "code  label" per line."""
    table = spec.codes().get(field_name)
    if table is None:
        return f"{field_name} is not a coded OED field. Coded fields: {', '.join(sorted(spec.codes()))}"
    return "\n".join(f"{code:>5}  {label}" for code, label in sorted(table.items(), key=lambda kv: int(kv[0])))
