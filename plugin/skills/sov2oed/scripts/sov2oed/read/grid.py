"""Raw access to a file, whatever its format: spreadsheet, CSV or PDF.

A `Grid` holds every sheet (or PDF page) with cell values, formulas, comments, merged ranges and
hidden state. It never interprets anything: the reading plan says what the cells mean.

Untrusted input: openpyxl reads values only (no macros, no external links); pdfplumber reads the
text layer only. Work is bounded by what the file holds, never by its dimensions (one stray cell at
XFD1048576 costs one cell), and capped before the expensive step runs. A workbook is pre-scanned before
openpyxl loads it: uncompressed member sizes (`MAX_XLSX_MEMBER_BYTES`, `MAX_XLSX_BYTES`), stored cells per
sheet (`MAX_SHEET_CELLS`), cells covered by merged ranges in the whole workbook (`MAX_MERGED_CELLS`, since
openpyxl creates a cell for every cell of a merged range) and the length of one text (`MAX_CELL_CHARS`). A
CSV is capped by size (`MAX_CSV_BYTES`) and cells. A PDF page's decoded content (`MAX_PAGE_STREAM_BYTES`)
and glyphs (`MAX_PAGE_CHARS`, `MAX_PDF_CHARS`) are counted before pdfplumber lays the page out, and
`MAX_PDF_PAGES` caps the pages. Past a cap, `load` raises ValueError.
"""

from __future__ import annotations

import bisect
import csv
import io
import re
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from openpyxl.utils import column_index_from_string, get_column_letter

MAX_SHEET_CELLS = 5_000_000  # cells per sheet: stored in the sheet XML (pre-scan), then non-empty
MAX_MERGED_CELLS = 1_000_000  # cells covered by merged ranges, in the whole workbook
MAX_XLSX_MEMBER_BYTES = 512_000_000  # uncompressed size of one member of a workbook (sheet XML, strings)
MAX_XLSX_BYTES = 1_024_000_000  # uncompressed size of the whole workbook
MAX_CELL_CHARS = 1_000_000  # characters in one cell text (Excel itself stores at most 32,767)
MAX_CSV_BYTES = 200_000_000  # size of a CSV file
MAX_PDF_PAGES = 2_000
MAX_PAGE_STREAM_BYTES = 10_000_000  # decoded content stream of one PDF page (each glyph needs a byte)
MAX_PAGE_CHARS = 200_000  # characters on one PDF page
MAX_PDF_CHARS = 5_000_000  # characters in the whole PDF

_MAX_ROW, _MAX_COL = 1_048_576, 16_384  # the extent of a sheet, for a merged range open on one side ("A:A")


@dataclass
class Cell:
    value: object = None
    formula: str | None = None
    comment: str | None = None


@dataclass
class Word:
    text: str
    x0: float
    x1: float


@dataclass
class Line:
    """A text line of a PDF page, with its words and their x positions."""

    no: int  # 1-based line number on the page
    text: str
    words: list[Word] = field(default_factory=list)
    top: float = 0.0


@dataclass
class Sheet:
    name: str  # sheet name, or "p3" for PDF page 3
    kind: str  # "xlsx" | "csv" | "pdf_page"
    hidden: bool = False
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)  # (row, col), 1-based
    merged: list[str] = field(default_factory=list)  # "A1:D1"
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    lines: list[Line] = field(default_factory=list)  # PDF only
    width: float = 0.0  # PDF page width (points)

    @property
    def max_row(self) -> int:
        if self.kind == "pdf_page":
            return len(self.lines)
        return max((r for r, _ in self.cells), default=0)

    @property
    def max_col(self) -> int:
        return max((c for _, c in self.cells), default=0)

    def value(self, row: int, col: int | str):
        c = self.cells.get((row, col_index(col)))
        return None if c is None else c.value

    def cell(self, row: int, col: int | str) -> Cell:
        return self.cells.get((row, col_index(col))) or Cell()

    def row_values(self, row: int, max_col: int | None = None) -> list:
        n = max_col or self.max_col
        return [self.value(row, c) for c in range(1, n + 1)]

    def ref(self, row: int, col: int | str | None = None) -> str:
        """Evidence reference: "Sheet!C12", "Sheet!12", or "p3:l12" for PDF lines."""
        if self.kind == "pdf_page":
            return f"{self.name}:l{row}"
        return f"{self.name}!{col_letter(col)}{row}" if col is not None else f"{self.name}!{row}"


@dataclass
class Grid:
    path: Path
    kind: str  # "xlsx" | "csv" | "pdf"
    sheets: dict[str, Sheet] = field(default_factory=dict)

    def sheet(self, name: str) -> Sheet:
        return self.sheets[name]

    def pages(self, numbers: list[int]) -> list[Sheet]:
        return [self.sheets[f"p{n}"] for n in numbers if f"p{n}" in self.sheets]


def col_index(col: int | str) -> int:
    return col if isinstance(col, int) else column_index_from_string(col.upper())


def col_letter(col: int | str) -> str:
    return col.upper() if isinstance(col, str) else get_column_letter(col)


# --- loaders ---------------------------------------------------------------------------------


def load(path: str | Path) -> Grid:
    path = Path(path)
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xlsm"):
        return _load_xlsx(path)
    if suf in (".csv", ".txt"):
        return _load_csv(path)
    if suf == ".pdf":
        return _load_pdf(path)
    raise ValueError(f"unsupported file type: {path.suffix} (xlsx, xlsm, csv, pdf)")


class _CappedReader:
    """A file object that refuses to give more than `cap` bytes: a ZIP member's declared size can lie."""

    def __init__(self, f, cap: int, name: str):
        self.f, self.cap, self.name, self.read_bytes = f, cap, name, 0

    def read(self, n: int = -1) -> bytes:
        data = self.f.read(n)
        self.read_bytes += len(data)
        if self.read_bytes > self.cap:
            raise ValueError(f"{self.name}: more than {self.cap:,} bytes uncompressed")
        return data


def _merge_size(ref: str | None, where: str) -> int:
    """Cells covered by one merged range. A range open on one side ("A:A", "1:1") covers the sheet's extent
    on that side; a ref that cannot be read is refused (openpyxl would fail on it)."""
    from openpyxl.utils import range_boundaries

    try:
        c1, r1, c2, r2 = range_boundaries(ref or "")
    except (TypeError, ValueError):
        raise ValueError(f"{where}: merged range {str(ref)[:40]!r} cannot be read") from None
    c1, r1 = c1 or 1, r1 or 1
    c2, r2 = c2 or _MAX_COL, r2 or _MAX_ROW
    return max(0, c2 - c1 + 1) * max(0, r2 - r1 + 1)


def _check_merges(path: Path) -> None:
    """Pre-scan a workbook before openpyxl loads it, so that a small file cannot make it allocate gigabytes:
    uncompressed member sizes, stored cells per sheet, cells covered by merged ranges in the whole workbook
    (openpyxl creates a MergedCell for every cell of a range, so one "A1:XFD1048576" would exhaust memory)
    and the length of each text. Every element is dropped once read, so the scan holds one at a time."""
    import zipfile
    from xml.etree.ElementTree import iterparse

    covered = 0
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        for info in infos:
            if info.file_size > MAX_XLSX_MEMBER_BYTES:
                raise ValueError(f"{info.filename}: more than {MAX_XLSX_MEMBER_BYTES:,} bytes uncompressed")
        if sum(i.file_size for i in infos) > MAX_XLSX_BYTES:
            raise ValueError(f"workbook: more than {MAX_XLSX_BYTES:,} bytes uncompressed")
        for info in infos:
            name = info.filename
            is_sheet = name.startswith("xl/worksheets/") and name.endswith(".xml")
            if not (is_sheet or name == "xl/sharedStrings.xml"):
                continue
            cells = 0
            stack: list = []
            with z.open(info) as raw:
                for event, el in iterparse(_CappedReader(raw, MAX_XLSX_MEMBER_BYTES, name), events=("start", "end")):
                    if event == "start":
                        stack.append(el)
                        continue
                    stack.pop()
                    tag = el.tag.rsplit("}", 1)[-1]
                    if el.text is not None and len(el.text) > MAX_CELL_CHARS:
                        raise ValueError(f"{name}: a text is longer than {MAX_CELL_CHARS:,} characters")
                    if is_sheet and tag == "c":
                        cells += 1
                        if cells > MAX_SHEET_CELLS:
                            raise ValueError(f"{name}: sheet stores more than {MAX_SHEET_CELLS:,} cells")
                    elif is_sheet and tag == "mergeCell":
                        covered += _merge_size(el.get("ref"), name)
                        if covered > MAX_MERGED_CELLS:
                            raise ValueError(f"{name}: merged ranges cover more than {MAX_MERGED_CELLS:,} cells")
                    if stack:
                        stack[-1].remove(el)  # nothing accumulates under the root


def _load_xlsx(path: Path) -> Grid:
    import openpyxl

    _check_merges(path)
    values = openpyxl.load_workbook(path, data_only=True)  # cached values
    formulas = openpyxl.load_workbook(path, data_only=False)  # formulas, for the guards
    grid = Grid(path, "xlsx")
    for ws in values.worksheets:
        wf = formulas[ws.title]
        sh = Sheet(
            ws.title, "xlsx", hidden=ws.sheet_state != "visible", merged=[str(r) for r in ws.merged_cells.ranges]
        )
        sh.hidden_rows = {r for r, d in ws.row_dimensions.items() if d.hidden}
        # A column dimension may stand for a range (min..max), as grouped columns are saved.
        sh.hidden_cols = {
            i
            for c, d in ws.column_dimensions.items()
            if d.hidden
            for i in range(d.min or column_index_from_string(c), (d.max or column_index_from_string(c)) + 1)
        }
        # Only the cells stored in the file: ws.iter_rows() and ws.cell() would create every cell of the
        # bounding box (A1 to the farthest formatted cell), which a single stray cell makes huge.
        stored_values, stored_formulas = ws._cells, wf._cells
        for key in sorted(stored_values.keys() | stored_formulas.keys()):
            c, cf = stored_values.get(key), stored_formulas.get(key)
            value = None if c is None else c.value
            f = None if cf is None else cf.value
            formula = f if isinstance(f, str) and f.startswith("=") else None
            comment = c.comment.text if c is not None and getattr(c, "comment", None) else None
            if value is None and formula is None and comment is None:
                continue
            if len(sh.cells) >= MAX_SHEET_CELLS:
                raise ValueError(f"sheet '{ws.title}' has more than {MAX_SHEET_CELLS:,} non-empty cells")
            sh.cells[key] = Cell(value, formula, comment)
        grid.sheets[ws.title] = sh
    return grid


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


def _load_csv(path: Path) -> Grid:
    size = path.stat().st_size
    if size > MAX_CSV_BYTES:
        raise ValueError(f"CSV file has {size:,} bytes, more than {MAX_CSV_BYTES:,}")
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    delim = _sniff_delimiter(text[:20000])
    sh = Sheet(path.stem, "csv")
    for r, row in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delim), start=1):
        for c, v in enumerate(row, start=1):
            if v != "":
                if len(sh.cells) >= MAX_SHEET_CELLS:
                    raise ValueError(f"CSV file has more than {MAX_SHEET_CELLS:,} non-empty cells")
                sh.cells[(r, c)] = Cell(v)
    return Grid(path, "csv", {sh.name: sh})


# Word assembly works on characters, not on pdfplumber words: report generators pad right-aligned
# numbers with space glyphs drawn over the digits, so "35" came out as "3 5". Tolerances are in
# em (font size), so they hold from 5 pt footnotes to 12 pt titles.
_WORD_GAP = 0.1  # em: a wider gap between two glyphs ends a word (word spaces are ~0.25 em)
_SPACE_SLACK = 0.1  # em: a space glyph ends a word only if it fits in the gap between two glyphs
_LINE_TOL = 0.45  # em: glyphs whose vertical centres differ by less share a line
_DUP_TOL = 1.0  # points: the same glyph drawn twice this close (fake bold) is read once
_OVERLAP = 0.12  # em: a glyph overlapping the previous one by more starts another word


@dataclass
class _Glyph:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    font: str
    seq: int  # position in the content stream

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


def _load_pdf(path: Path) -> Grid:
    import pdfplumber

    grid = Grid(path, "pdf")
    total = 0
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has {len(pdf.pages):,} pages, more than {MAX_PDF_PAGES:,}")
        for i, page in enumerate(pdf.pages, start=1):
            try:
                _check_page_cost(pdf, page, i, min(MAX_PAGE_CHARS, MAX_PDF_CHARS - total))
                chars = page.chars
                total += len(chars)
                if len(chars) > MAX_PAGE_CHARS:
                    raise ValueError(f"PDF page {i} has {len(chars):,} characters, more than {MAX_PAGE_CHARS:,}")
                if total > MAX_PDF_CHARS:
                    raise ValueError(f"PDF has more than {MAX_PDF_CHARS:,} characters")
                grid.sheets[f"p{i}"] = Sheet(f"p{i}", "pdf_page", lines=_page_lines(chars), width=float(page.width))
            finally:
                page.close()  # pdfplumber keeps every page's layout objects until closed
    return grid


def _decoded_size(stream, cap: int) -> int:
    """Decoded size of a PDF stream, at most cap + 1: a FlateDecode stream is inflated in steps, so a
    compression bomb is refused before it is expanded in memory."""
    import zlib

    from pdfminer.pdftypes import LITERALS_FLATE_DECODE

    if stream.data is not None:
        return len(stream.data)
    filters = stream.get_filters()
    if (
        stream.rawdata is not None
        and not stream.decipher
        and len(filters) == 1
        and filters[0][0] in LITERALS_FLATE_DECODE
    ):
        d, n = zlib.decompressobj(), 0
        try:
            chunk = d.decompress(stream.rawdata, cap + 1)
            while chunk:
                n += len(chunk)
                if n > cap:
                    return n
                chunk = d.decompress(d.unconsumed_tail, cap + 1 - n) if d.unconsumed_tail else b""
        except zlib.error:
            pass  # pdfminer reads a corrupted stream its own way
        else:
            return n
    return len(stream.get_data())


class _TooManyGlyphs(Exception):
    pass


def _check_page_cost(pdf, page, number: int, max_glyphs: int) -> None:
    """Refuse a PDF page before pdfplumber lays it out (which builds an object per glyph): its decoded
    content streams must fit MAX_PAGE_STREAM_BYTES, and a counting pass that builds nothing stops at
    `max_glyphs` + 1 glyphs, Form XObjects included."""
    from pdfminer.pdfdevice import PDFTextDevice
    from pdfminer.pdfinterp import PDFPageInterpreter
    from pdfminer.pdftypes import stream_value

    size = 0
    for obj in page.page_obj.contents:
        try:
            stream = stream_value(obj)
        except Exception:  # noqa: BLE001 - pdfminer skips what is not a stream
            continue
        size += _decoded_size(stream, MAX_PAGE_STREAM_BYTES - size)
        if size > MAX_PAGE_STREAM_BYTES:
            raise ValueError(f"PDF page {number} has more than {MAX_PAGE_STREAM_BYTES:,} bytes of content")

    class Counter(PDFTextDevice):
        n = 0

        def render_char(self, *args, **kwargs) -> float:
            self.n += 1
            if self.n > max_glyphs:
                raise _TooManyGlyphs
            return 0

    try:
        PDFPageInterpreter(pdf.rsrcmgr, Counter(pdf.rsrcmgr)).process_page(page.page_obj)
    except _TooManyGlyphs:
        if max_glyphs < MAX_PAGE_CHARS:
            raise ValueError(f"PDF has more than {MAX_PDF_CHARS:,} characters") from None
        raise ValueError(f"PDF page {number} has more than {MAX_PAGE_CHARS:,} characters") from None


def _glyphs(chars: list[dict]) -> tuple[list[_Glyph], list[_Glyph]]:
    """Visible glyphs and space glyphs, in stream order, duplicates removed."""
    glyphs, spaces = [], []
    seen: dict[tuple[str, int, int], list[_Glyph]] = {}
    for seq, c in enumerate(chars):
        text = c.get("text") or ""
        if not text or not c.get("upright", True):
            continue  # rotated text (vertical headers, stamps) belongs to no table row
        top, bottom = float(c["top"]), float(c["bottom"])
        size = float(c.get("size") or 0) or (bottom - top) or 1.0
        g = _Glyph(text, float(c["x0"]), float(c["x1"]), top, bottom, size, str(c.get("fontname", "")), seq)
        if text.isspace():
            spaces.append(g)
            continue
        kx, ky = round(g.x0), round(g.top)
        tol = min(_DUP_TOL, 0.5 * (g.x1 - g.x0))
        if any(
            abs(o.x0 - g.x0) <= tol and abs(o.top - g.top) <= _DUP_TOL
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for o in seen.get((text, kx + dx, ky + dy), ())
        ):
            continue
        seen.setdefault((text, kx, ky), []).append(g)
        glyphs.append(g)
    return glyphs, spaces


def _page_lines(chars: list[dict]) -> list[Line]:
    glyphs, spaces = _glyphs(chars)
    rows: list[tuple[float, float, list[_Glyph]]] = []  # (anchor mid, anchor size, glyphs)
    for g in sorted(glyphs, key=lambda g: (g.mid, g.x0, g.seq)):
        if rows and g.mid - rows[-1][0] <= _LINE_TOL * max(rows[-1][1], g.size):
            rows[-1][2].append(g)
        else:
            rows.append((g.mid, g.size, [g]))
    anchors = [r[0] for r in rows]
    row_spaces: list[list[_Glyph]] = [[] for _ in rows]
    for s in spaces:
        i = bisect.bisect_left(anchors, s.mid)
        near = [j for j in (i - 1, i) if 0 <= j < len(rows)]
        if not near:
            continue
        j = min(near, key=lambda j: (abs(anchors[j] - s.mid), j))
        if abs(anchors[j] - s.mid) <= _LINE_TOL * max(rows[j][1], s.size) and s.x1 - s.x0 >= 0.1 * s.size:
            row_spaces[j].append(s)
    broken = _overlapping_fonts(rows)
    lines: list[Line] = []
    for (_, _, gs), sp in zip(rows, row_spaces, strict=True):
        words = _words(gs, sp, broken)
        lines.append(Line(len(lines) + 1, " ".join(w.text for w in words), words, min(g.top for g in gs)))
    return lines


def _overlapping_fonts(rows: list[tuple[float, float, list[_Glyph]]]) -> set[tuple[str, float]]:
    """Fonts whose glyph boxes overlap as a rule (wrong widths in the file): median gap < -0.05 em."""
    gaps: dict[tuple[str, float], list[float]] = {}
    for _, _, gs in rows:
        seq = sorted(gs, key=lambda g: g.seq)
        for a, b in pairwise(seq):
            if a.font == b.font and a.size == b.size and b.x0 > a.x0 and b.x0 - a.x1 <= _WORD_GAP * a.size:
                gaps.setdefault((a.font, a.size), []).append((b.x0 - a.x1) / a.size)
    return {k for k, v in gaps.items() if sorted(v)[len(v) // 2] < -_WORD_GAP / 2}


def _space_between(spaces: list[tuple[float, float]], left: float, right: float, slack: float) -> bool:
    lo, hi = left - slack, right + slack
    i = bisect.bisect_left(spaces, (lo, float("-inf")))
    while i < len(spaces) and spaces[i][0] <= hi:
        if spaces[i][1] <= hi:
            return True
        i += 1
    return False


def _advances(p: _Glyph, g: _Glyph) -> bool:
    """g is drawn to the right of p (a zero-width glyph, such as some "€", does not move the pen)."""
    return g.x0 > p.x0 or (p.x1 <= p.x0 and g.x0 >= p.x0)


def _words(glyphs: list[_Glyph], spaces: list[_Glyph], broken: set[tuple[str, float]]) -> list[Word]:
    """Glyphs of one line to words.

    Runs are first chained in stream order, each glyph advancing to the right of the previous one,
    so text overflowing a cell and drawn over its neighbour stays two words. A run ends at a gap
    wider than _WORD_GAP, at a space glyph lying in the gap or drawn in between in the stream, and
    at a glyph that suddenly overlaps a normally spaced run. Runs that touch are then merged, for
    generators that draw a word in pieces, unless a space or an overlap split them.
    """
    spans = sorted((s.x0, s.x1) for s in spaces)
    stream = sorted([(g.seq, 0, g) for g in glyphs] + [(s.seq, 1, s) for s in spaces], key=lambda t: t[0])
    runs: list[list[_Glyph]] = []
    hard: list[bool] = []  # run split from the previous one on purpose
    pending: list[_Glyph] = []  # space glyphs drawn since the last glyph
    for _, is_space, g in stream:
        if is_space:
            pending.append(g)
            continue
        if runs:
            run = runs[-1]
            p = run[-1]
            em = max(p.size, g.size)
            gap = g.x0 - p.x1
            spaced = any(p.x0 < s.x0 and s.x1 <= g.x1 for s in pending) or _space_between(
                spans, p.x1, g.x0, _SPACE_SLACK * em
            )
            # Fonts with wrong widths overlap on every glyph; elsewhere a sudden overlap is the
            # next cell's text drawn over this one.
            sudden = (
                gap < -_OVERLAP * em
                and len(run) >= 2
                and (p.font, p.size) not in broken
                and run[-1].x0 - run[-2].x1 >= -_WORD_GAP / 2 * em
            )
            if _advances(p, g) and gap <= _WORD_GAP * em and not spaced and not sudden:
                run.append(g)
                pending = []
                continue
            hard.append(spaced or sudden)
        else:
            hard.append(False)
        runs.append([g])
        pending = []
    order = sorted(range(len(runs)), key=lambda i: (runs[i][0].x0, max(g.x1 for g in runs[i]), runs[i][0].seq))
    merged: list[list[_Glyph]] = []
    for i in order:
        r = runs[i]
        if merged and not hard[i]:
            last = merged[-1]
            right = max(g.x1 for g in last)
            em = max(last[-1].size, r[0].size)
            gap = r[0].x0 - right
            if (
                -_WORD_GAP * em <= gap <= _WORD_GAP * em
                and _advances(last[-1], r[0])
                and not _space_between(spans, right, r[0].x0, _SPACE_SLACK * em)
            ):
                last.extend(r)
                continue
        merged.append(list(r))
    words = [
        Word(
            "".join(g.text for g in r),
            min(g.x0 for g in r),
            max(g.x1 for g in r),
        )
        for r in merged
    ]
    words.sort(key=lambda w: (w.x0, w.x1, w.text))
    return words


# --- PDF rows ---------------------------------------------------------------------------------
# A PDF table is read from a rows file: a CSV that Claude's script writes from the page lines, one row per
# location, its first column naming the lines the row was read from ("p3:l12+p3:l13", "p3:l12-l14"). The guards
# check every value against those lines.

_LINES = re.compile(r"p(\d+):l(\d+)(?:-l(\d+))?")


def line_keys(text: object) -> list[tuple[int, int]] | None:
    """ "p3:l12+p3:l14-l15" -> [(3, 12), (3, 14), (3, 15)]; None if the text is not a list of PDF lines."""
    out: list[tuple[int, int]] = []
    for token in re.split(r"[+,;]", str(text or "")):
        m = _LINES.fullmatch(token.strip())
        if not m:
            return None
        out.extend((int(m[1]), n) for n in range(int(m[2]), int(m[3] or m[2]) + 1))
    return out or None


def load_rows(path: Path) -> Sheet:
    """A rows file, as the sheet a plan's table reads; its name is the file name."""
    sheet = _load_csv(path).sheets[path.stem]
    sheet.name, sheet.kind = path.name, "rows"
    return sheet


def lines_data(grid: Grid, pages: list[int] | None = None) -> list[dict]:
    """Every line of the PDF pages, for a script to group into rows: ref, page, line, text, words [x0, x1, text]."""
    out = []
    for name, sh in grid.sheets.items():
        if sh.kind != "pdf_page" or (pages and int(name[1:]) not in pages):
            continue
        for ln in sh.lines:
            words = [[round(w.x0, 1), round(w.x1, 1), w.text] for w in ln.words]
            out.append(
                {"ref": f"{name}:l{ln.no}", "page": int(name[1:]), "line": ln.no, "text": ln.text, "words": words}
            )
    return out
