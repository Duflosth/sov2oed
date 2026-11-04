from pathlib import Path

import openpyxl
import pytest

from sov2oed.extract import extract
from sov2oed.guards import check
from sov2oed.plan import Plan
from sov2oed.read.grid import load

PLAN = """
plan: 1
file: {name: harbour.xlsx, sha256: "0"}
country: {value: US, assumed: true}
currency: {value: USD, assumed: true}
tables:
  - id: t1
    sheet: Schedule
    header: [1]
    rows: "2-7"
    totals: ["8"]
    columns:
      A: {field: StreetAddress}
      B: {field: LocName}
      C: {field: BuildingTIV}
      D: {field: LocLimit1Building, basis: limit}
      E: {field: [ContentsTIV, LocLimit3Contents], basis: limit}
      F: {field: row_total}
"""


@pytest.fixture
def harbour(tmp_path: Path) -> Path:
    """A fictional wind schedule with the defects found in real ones: a SUM that stops short, a formula column with
    typed and blank rows, an amount worked out by hand, a hidden sheet of values, a cell that is not a number."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append(["Premises", "Occupancy", "Replacement Cost", "Wind Bldg", "Wind Cnts", "Wind TIV"])
    data = [
        ("1 Pier Road", "Harbour Office", 500000, 400000, 50000),
        ("2 Pier Road", "Boat Store", 300000, 240000, 20000),
        ("3 Pier Road", "Fuel Dock", 200000, 160000, "=10000+5000"),
        ("4 Pier Road", "Workshop", 100000, 80000, 10000),
        ("5 Pier Road", "Crane", "TBC", 0, 0),
        ("6 Pier Road", "Ticket Hut", 50000, 40000, 5000),
    ]
    for i, (a, b, c, d, e) in enumerate(data, start=2):
        ws.append([a, b, c, d, e, f"=SUM(D{i}:E{i})"])
    ws["F5"] = None  # blank in the formula column
    ws["F6"] = 12345  # typed in the formula column
    ws.append(["TOTALS", None, 1200000, "=SUM(D2:D7)", "=SUM(E2:E7)", "=SUM(F2:F5)"])
    hidden = wb.create_sheet("Old values")
    hidden.append(["1 Pier Road", 450000])
    hidden.sheet_state = "hidden"
    path = tmp_path / "harbour.xlsx"
    wb.save(path)
    return path


def findings(path: Path, plan_text: str = PLAN) -> dict[str, list[str]]:
    grid = load(path)
    sheet = grid.sheets["Schedule"]
    cached = {"D8": 920000, "E8": 100000, "F8": 1015000, "E4": 15000, "F2": 450000, "F3": 260000, "F7": 45000}
    for ref, value in cached.items():  # what Excel would have cached; openpyxl writes formulas only
        sheet.cells[(int(ref[1:]), ord(ref[0]) - 64)].value = value
    out: dict[str, list[str]] = {}
    for f in check(extract(Plan.read(plan_text), grid)):
        out.setdefault(f.guard, []).append(f.message)
    return out


def test_total_that_does_not_add_up(harbour):
    totals = findings(harbour)["totals"]
    assert any("Schedule!C8 prints 1,200,000; the rows read add up to 1,150,000" in m for m in totals)


def test_sum_that_stops_short(harbour):
    assert any(
        "F8 is =SUM(F2:F5): it adds rows 2-5, the table holds rows 2-7" in m for m in findings(harbour)["totals"]
    )


def test_formula_column_with_blank_and_typed_rows(harbour):
    formulas = findings(harbour)["formulas"]
    assert any("Column F (Wind TIV) is =SUM(D:E)" in m and "F5-F6" in m and "77,655" in m for m in formulas)
    assert any("E4 is =10000+5000: an amount worked out by hand" in m for m in formulas)


def test_unreadable_value_and_forgotten_hidden_sheet(harbour):
    found = findings(harbour)
    assert any("'TBC' (1)" in m for m in found["unreadable"])
    assert any("hidden sheet 'Old values'" in m for m in found["sheets"])
    listed = PLAN.replace("tables:", "other_sheets: {Old values: last year's values}\ntables:")
    assert "sheets" not in findings(harbour, listed)


def test_rows_outside_the_table_and_quotes(harbour):
    text = PLAN.replace('rows: "2-7"', 'rows: "2-6"')
    text += 'facts:\n  - {cell: "Schedule!B3", field: FlexiLocComment, value: "store of 2 boats", quote: "Boat Shed"}\n'
    found = findings(harbour, text)
    assert any("rows 7 hold values" in m for m in found["rows"])
    assert any("'Boat Shed' is not in Schedule!B3" in m for m in found["quotes"])
    assert any("2 in the value is not in the quote" in m for m in found["quotes"])


def test_a_clean_reading_has_no_plan_finding(town_xlsx):
    from tests.test_convert import town_plan

    grid = load(town_xlsx)
    assert [f.message for f in check(extract(town_plan(town_xlsx), grid)) if f.kind == "plan"] == []


def _findings(path: Path, plan_text: str, cached: dict[str, float] | None = None) -> list:
    grid = load(path)
    sheet = next(iter(grid.sheets.values()))
    for ref, value in (cached or {}).items():  # what Excel would have cached
        sheet.cells[(int(ref[1:]), ord(ref[0]) - 64)].value = value
    return check(extract(Plan.read(plan_text), grid))


SECTIONS = """
plan: 1
file: {name: campuses.xlsx, sha256: "0"}
country: {value: US, assumed: true}
currency: {value: USD, assumed: true}
tables:
  - id: t1
    sheet: Schedule
    header: [1]
    rows: "2-11"
    skip: {"2": section heading, "7": section heading}
    totals: ["6", "10", "11"]
    columns:
      A: {field: StreetAddress}
      B: {field: LocName}
      C: {field: BuildingTIV}
      D: {field: ContentsTIV}
"""


@pytest.fixture
def campuses(tmp_path: Path) -> Path:
    """A fictional schedule in two sections, each closed by a subtotal (a SUM of its own rows), then a grand total."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append(["Address", "Building", "Building Value", "Contents"])
    ws.append(["NORTH CAMPUS"])
    ws.append(["1 Elm St", "Classrooms", 400000, 40000])
    ws.append(["1 Elm St", "Gym", 200000, 20000])
    ws.append(["1 Elm St", "Library", 100000, 10000])
    ws.append(["Subtotal North", None, "=SUM(C3:C5)", 70000])
    ws.append(["SOUTH CAMPUS"])
    ws.append(["9 Oak Ave", "Main Building", 300000, 30000])
    ws.append(["9 Oak Ave", "Portable", 50000, 5000])
    ws.append(["Subtotal South", None, "=SUM(C8:C9)", 35000])
    ws.append(["GRAND TOTAL", None, "=SUM(C3:C5)+SUM(C8:C9)", 105000])
    path = tmp_path / "campuses.xlsx"
    wb.save(path)
    return path


def test_each_subtotal_closes_its_own_section(campuses):
    found = _findings(campuses, SECTIONS, {"C6": 700000, "C10": 350000, "C11": 1050000})
    assert [f.message for f in found if f.guard == "totals"] == []


def test_a_wrong_subtotal_is_compared_with_its_section(campuses):
    found = _findings(campuses, SECTIONS, {"C6": 700000, "C10": 360000, "C11": 1050000})
    totals = [f for f in found if f.guard == "totals"]
    assert [f.cells for f in totals] == [["Schedule!C10"]]
    assert "the rows of its section (Schedule!8 to Schedule!9) add up to 350,000" in totals[0].message
    assert totals[0].kind == "file"


SECOND_TABLE = """  - id: t2
    sheet: Schedule
    rows: "6-7"
    columns:
      A: {field: StreetAddress}
      C: {field: BuildingTIV}
"""


def test_rows_read_by_two_tables_are_a_plan_error(harbour):
    grid = load(harbour)
    overlap = [f for f in check(extract(Plan.read(PLAN + SECOND_TABLE), grid)) if f.guard == "overlap"]
    assert [(f.kind, f.message) for f in overlap] == [("plan", "Tables t1 and t2 both read 2 row(s), from Schedule!6.")]


ROW_TOTALS = """
plan: 1
file: {name: depots.csv, sha256: "0"}
country: {value: US, assumed: true}
currency: {value: USD, assumed: true}
tables:
  - id: t1
    sheet: depots
    header: [1]
    rows: "2-5"
    columns:
      A: {field: StreetAddress}
      B: {field: BuildingTIV}
      C: {field: ContentsTIV}
      D: {field: row_total}
"""


def test_a_printed_row_total_above_its_amounts(tmp_path):
    path = tmp_path / "depots.csv"
    path.write_text(
        "Address,Building,Contents,Total\n"
        '2 Dock Rd,"500,000","50,000","550,000"\n'
        '4 Dock Rd,"300,000","30,000","350,000"\n'  # 20,000 above building + contents
        '6 Dock Rd,"200,000",,"200,000"\n'
        '8 Dock Rd,"100,000","10,000","110,000"\n'
    )
    found = [f for f in _findings(path, ROW_TOTALS) if f.guard == "row_totals"]
    assert [(f.kind, f.cells, f.amount) for f in found] == [("file", ["depots!D3"], 20000)]
    assert "depots!D3 prints 350,000, 20,000 above Building + Contents (330,000)" in found[0].message


def test_a_row_total_that_adds_a_column_not_read_is_not_compared(tmp_path):
    path = tmp_path / "depots.csv"
    path.write_text(
        "Address,Building,Contents,Total\n"
        '2 Dock Rd,"500,000","50,000","600,000"\n'
        '4 Dock Rd,"300,000","30,000","380,000"\n'
        '6 Dock Rd,"200,000","20,000","220,000"\n'
    )
    assert [f for f in _findings(path, ROW_TOTALS.replace('"2-5"', '"2-4"')) if f.guard == "row_totals"] == []


def test_a_formula_row_total_is_left_to_the_formula_check(harbour):
    assert "row_totals" not in findings(harbour)


def test_a_total_that_adds_another_column():
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    grid = load(fixtures / "sov_us_cedar_isd.xlsx")
    plan = Plan.load(fixtures / "sov_us_cedar_isd.plan.yaml")
    totals = [f.message for f in check(extract(plan, grid)) if f.guard == "totals"]
    assert len(totals) == 1 and "P30 adds column Q (Total TIV), not column P (Contents Value)." in totals[0]


def test_an_amount_with_spaces_between_thousands_counts_as_printed():
    from sov2oed.guards import _AMOUNT

    assert [bool(_AMOUNT.search(t)) for t in ["Atelier 2 593", "TOTAL 24 958", "1.250.000,00", "Loc 3 bldg 1 1987"]] == [
        True, True, True, False]  # fmt: skip
