"""Read OED files as they are: location.csv, account.csv, and the provenance file when there is one.

Values stay text as written, under their OED names (headers are matched case-insensitively, flexi fields by
prefix); `number` reads one when a rule needs it. A column that is not an OED field is kept aside for the schema
check. The provenance file, written by the conversion, gives each value's status, cell and value basis.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from ..oed import spec

MAX_ROWS = 500_000
MAX_BYTES = 200 * 1024 * 1024
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class Provenance:
    status: str  # declared | derived | assumed
    source: str
    note: str


@dataclass
class Portfolio:
    locations: list[dict[str, str]]
    accounts: list[dict[str, str]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)  # location columns, as OED names, in file order
    unknown_columns: list[str] = field(default_factory=list)  # location columns that are not OED fields
    account_unknown_columns: list[str] = field(default_factory=list)
    provenance: dict[tuple[str, str], Provenance] = field(default_factory=dict)  # (LocNumber, field)
    has_account: bool = False

    def basis(self, i: int, name: str) -> str | None:
        """The value basis the conversion recorded for one row's amount ("replacement", "limit"...)."""
        p = self.provenance.get((self.locations[i].get("LocNumber", ""), name))
        if p is None:
            return None
        for part in p.note.split("; "):
            if part.startswith("basis: "):
                return part[7:]
        return None


def _read(path: Path) -> tuple[list[str], list[list[str]]]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} has more than {MAX_BYTES // 2**20} MB")
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        raise ValueError(f"{path.name} is empty")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"{path.name} has more than {MAX_ROWS:,} rows")
    return rows[0], rows[1:]


def _value(text: str) -> str:
    """The value as written; the apostrophe that keeps a formula-like text inert in a spreadsheet is dropped."""
    text = text.strip()
    return text[1:] if text.startswith("'") and text[1:].startswith(_FORMULA_START) else text


def _table(path: Path, file: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    header, rows = _read(path)
    names, unknown = [], []
    for h in header:
        f = spec.field(h, file)
        names.append(f.name if f else h.strip())
        if f is None:
            unknown.append(h.strip())
    records = [
        {n: _value(v) for n, v in zip(names, row, strict=False) if v.strip() != ""}
        for row in rows
        if any(c.strip() for c in row)
    ]
    return records, names, unknown


def load(location: str | Path, account: str | Path | None = None, provenance: str | Path | None = None) -> Portfolio:
    locations, columns, unknown = _table(Path(location), "Loc")
    p = Portfolio(locations, columns=columns, unknown_columns=unknown)
    if account is not None:
        p.accounts, _, p.account_unknown_columns = _table(Path(account), "Acc")
        p.has_account = True
    if provenance is not None:
        _, rows = _read(Path(provenance))
        for r in rows:
            if len(r) >= 7:
                p.provenance[(r[2], r[3])] = Provenance(r[4], r[5], r[6])
    return p


def number(row: dict[str, str], name: str) -> float | None:
    """A numeric OED value, or None when blank or not a number (the schema check reports the latter)."""
    text = row.get(name)
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None
