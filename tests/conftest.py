from pathlib import Path

import openpyxl
import pytest
from openpyxl.comments import Comment

from sov2oed.read.grid import Grid, Line, Sheet, Word


@pytest.fixture
def town_xlsx(tmp_path: Path) -> Path:
    """A fictional town schedule: a title row, a header, five rows, a total row, a legend sheet."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Town of Brackley Schedule"
    ws.append(["Statement of values 2026"])
    ws.append(["Address", "Description", "Year", "Stories", "Bldg Value", "Contents", "Construction"])
    rows = [
        ["12 Mill Lane", "Fire Station", 1978, 2, 1200000, 150000, "JM"],
        ["4 Quarry Road", "Library", "c. 1960", 1, 850000, 90000, "Frame"],
        ["4 Quarry Road", "Playground Equip", None, None, 40000, 0, None],
        ["7 Bell Street", "Town Hall", 1901, "R+2", "1,900,000", 300000, "JM"],
        ["Water Tower Hill", "Pump House", "n/k", 1, 0, 25000, "Frame"],
    ]
    for r in rows:
        ws.append(r)
    ws.append(["TOTAL", None, None, None, 3990000, 565000])
    ws["A3"].comment = Comment("Flood policy: 10-12 Mill Lane", "broker")
    legend = wb.create_sheet("Legend")
    legend.append(["JM", "Joisted masonry"])
    path = tmp_path / "brackley.xlsx"
    wb.save(path)
    return path


def _line(no: int, words: list[tuple[str, float]]) -> Line:
    return Line(no, " ".join(t for t, _ in words), [Word(t, x, x + 6 * len(t)) for t, x in words])


@pytest.fixture
def town_pdf() -> Grid:
    """Two PDF pages. Location 2 wraps: its value is printed on the next line (p1:l5)."""
    p1 = Sheet("p1", "pdf_page", lines=[
        _line(1, [("SCHEDULE", 50), ("OF", 110), ("VALUES", 130)]),
        _line(2, [("ID", 50), ("Description", 100), ("Constr", 172), ("Value", 300)]),
        _line(3, [("1", 50), ("PUMPSTATIONJM", 100), ("125,000", 300)]),
        _line(4, [("2", 50), ("LIBRARY", 100), ("ANNEX", 148), ("FR", 172)]),
        _line(5, [("WING", 100), ("640,000", 300)]),
    ])  # fmt: skip
    p2 = Sheet("p2", "pdf_page", lines=[
        _line(1, [("Page", 50), ("2", 80)]),
        _line(2, [("3", 50), ("DEPOT", 100), ("JM", 172), ("-", 300)]),
        _line(3, [("TOTAL", 50), ("765,000", 300)]),
    ])  # fmt: skip
    return Grid(Path("brackley.pdf"), "pdf", {"p1": p1, "p2": p2})
