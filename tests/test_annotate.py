"""The annotated workbook leaves out the hidden sheets the plan does not read: it goes back to the broker and on."""

import zipfile
from pathlib import Path

import openpyxl
import pytest
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from sov2oed import annotate
from sov2oed.extract import extract
from sov2oed.plan import Plan, sha256
from sov2oed.read.grid import load

PLAN = """
plan: 1
file: {{name: harrow.xlsx, sha256: "{sha}"}}
country: {{value: GB, assumed: true}}
currency: {{value: GBP, assumed: true}}
other_sheets: {{Notes: broker notes, Other stock: another landlord's homes, Lookup: drop-down lists}}
tables:
  - id: t1
    sheet: {sheet}
    header: [1]
    rows: "2-3"
    columns:
      A: {{field: StreetAddress}}
      B: {{field: BuildingTIV}}
"""
CACHED = {"Notes": {"B1": "910000", "B2": "910000", "B3": "1250000"}}  # what Excel saved with each formula


@pytest.fixture
def harrow_xlsx(tmp_path: Path) -> Path:
    """A fictional housing schedule: a table, a visible notes sheet, a hidden sheet of another landlord's homes,
    a very hidden list sheet, and formulas and names reading the hidden one."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append(["Address", "Value"])
    ws.append(["1 Elm Row", 500000])
    ws.append(["3 Elm Row", 750000])
    dv = DataValidation(type="list", formula1="Lookup!$A$1:$A$2")
    dv.add("C2:C3")
    ws.add_data_validation(dv)
    notes = wb.create_sheet("Notes")
    notes.append(["Other stock total", "=SUM('Other stock'!B2:B4)"])
    notes.append(["Same, by name", "=SUM(OtherValues)"])
    notes.append(["Our total", "=SUM(Schedule!B2:B3)"])
    other = wb.create_sheet("Other stock")
    other.append(["Address", "Value"])
    for row in (["2 Ash Close", 300000], ["4 Ash Close", 410000], ["6 Ash Close", 200000]):
        other.append(row)
    other.sheet_state = "hidden"
    lookup = wb.create_sheet("Lookup")
    lookup.append(["Flat"])
    lookup.append(["House"])
    lookup.sheet_state = "veryHidden"
    wb.defined_names["OtherValues"] = DefinedName("OtherValues", attr_text="'Other stock'!$B$2:$B$4")
    wb.defined_names["OurValues"] = DefinedName("OurValues", attr_text="Schedule!$B$2:$B$3")
    path = tmp_path / "harrow.xlsx"
    wb.save(path)
    _cache(path, "xl/worksheets/sheet2.xml", CACHED["Notes"])
    return path


def _cache(path: Path, member: str, values: dict[str, str]) -> None:
    """Write the cached value of formula cells, as Excel does (openpyxl saves formulas with none)."""
    with zipfile.ZipFile(path) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    xml = parts[member].decode()
    for ref, v in values.items():
        head = f'<c r="{ref}">'
        start = xml.index(head)
        xml = xml[:start] + xml[start:].replace("<v />", f"<v>{v}</v>", 1)
    parts[member] = xml.encode()
    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)


def _annotate(path: Path, out: Path, sheet: str = "Schedule") -> tuple[Path, list[str]]:
    plan = Plan.read(PLAN.format(sha=sha256(path), sheet=sheet))
    ex = extract(plan, load(path))
    return annotate.write(ex, [], out), annotate.removed_sheets(ex)


def test_hidden_sheets_not_read_are_removed(harrow_xlsx, tmp_path):
    out, removed = _annotate(harrow_xlsx, tmp_path / "a.xlsx")
    assert removed == ["Other stock", "Lookup"]
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Schedule", "Notes", "Questions", "Legend"]  # the visible sheet not read stays
    legend = [c.value for c in wb["Legend"]["B"]]
    assert "Hidden sheets not read, removed from this copy: Other stock, Lookup" in legend
    with zipfile.ZipFile(out) as z:
        text = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    assert "Ash Close" not in text and "410000" not in text and "Other stock'!" not in text


def test_formulas_and_names_into_a_removed_sheet(harrow_xlsx, tmp_path):
    wb = openpyxl.load_workbook(_annotate(harrow_xlsx, tmp_path / "a.xlsx")[0])
    notes = wb["Notes"]
    assert notes["B1"].value == 910000 and notes["B1"].data_type == "n"  # the cached value, not #REF!
    assert notes["B2"].value == 910000  # through a name defined on the removed sheet
    assert notes["B3"].value == "=SUM(Schedule!B2:B3)"  # a formula reading kept sheets stays a formula
    assert set(wb.defined_names) == {"OurValues"}
    assert wb["Schedule"].data_validations.dataValidation == []  # its list was on the very hidden sheet


def test_a_hidden_sheet_the_plan_reads_stays(harrow_xlsx, tmp_path):
    out, removed = _annotate(harrow_xlsx, tmp_path / "a.xlsx", sheet="Other stock")
    assert removed == ["Lookup"]
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Schedule", "Notes", "Other stock", "Questions", "Legend"]
    assert wb["Other stock"].sheet_state == "hidden"
    assert wb["Notes"]["B1"].value == "=SUM('Other stock'!B2:B4)"


def test_same_file_and_plan_give_the_same_bytes(harrow_xlsx, tmp_path):
    a, _ = _annotate(harrow_xlsx, tmp_path / "a.xlsx")
    b, _ = _annotate(harrow_xlsx, tmp_path / "b.xlsx")
    assert a.read_bytes() == b.read_bytes()


def test_no_hidden_sheet_no_line(town_xlsx, tmp_path):
    from tests.test_convert import town_plan

    ex = extract(town_plan(town_xlsx), load(town_xlsx))
    assert annotate.removed_sheets(ex) == []
    legend = openpyxl.load_workbook(annotate.write(ex, [], tmp_path / "t.xlsx"))["Legend (2)"]
    assert not any("removed from this copy" in str(c.value) for c in legend["B"])
