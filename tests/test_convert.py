import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from sov2oed import guards
from sov2oed.extract import extract
from sov2oed.oed.write import write
from sov2oed.plan import Plan, problems, sha256
from sov2oed.read.grid import load, load_rows

TOWN_PLAN = """
plan: 1
file: {{name: brackley.xlsx, sha256: "{sha}"}}
country: {{value: GB, cell: "Town of Brackley Schedule!A1", quote: Statement of values}}
currency: {{value: GBP, assumed: true}}
insured: {{cell: "sheet:Town of Brackley Schedule", quote: Town of Brackley}}
other_sheets: {{Legend: construction legend}}
tables:
  - id: t1
    sheet: Town of Brackley Schedule
    header: [2]
    rows: "3-7"
    totals: ["8"]
    columns:
      A: {{field: StreetAddress}}
      B: {{field: [LocName, OccupancyCode]}}
      C: {{field: YearBuilt}}
      D: {{field: NumberOfStoreys}}
      E: {{field: BuildingTIV, basis: replacement}}
      F: {{field: ContentsTIV}}
      G: {{field: ConstructionCode}}
    non_building: {{column: B, labels: [Playground Equip]}}
codes:
  OccupancyCode: {{map: {{Fire Station: 1220, Library: 1214, Playground Equip: 1108, Town Hall: 1210, Pump House: 1301}}}}
  ConstructionCode: {{map: {{JM: 5108, Frame: 5050}}}}
defaults:
  City: {{value: Brackley, cell: "sheet:Town of Brackley Schedule", quote: Brackley}}
facts:
  - {{cell: "Town of Brackley Schedule!A3", field: FlexiLocComment, value: "Flood policy: 10-12 Mill Lane", quote: "Flood policy: 10-12 Mill Lane"}}
"""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def town_plan(path: Path) -> Plan:
    return Plan.read(TOWN_PLAN.format(sha=sha256(path)))


def test_plan_converts_every_row(town_xlsx, tmp_path):
    grid, plan = load(town_xlsx), town_plan(town_xlsx)
    assert problems(plan, grid) == []
    files = write(extract(plan, grid), tmp_path / "out")
    rows = read_csv(files["location"])
    assert [r["LocNumber"] for r in rows] == ["3", "4", "5", "6", "7"]
    first, library, playground, hall, pump = rows
    assert first["OccupancyCode"] == "1220" and first["OrgOccupancyCode"] == "Fire Station"
    assert first["FlexiLocComment"] == "Flood policy: 10-12 Mill Lane" and first["City"] == "Brackley"
    assert library["YearBuilt"] == "1960" and hall["NumberOfStoreys"] == "3" and hall["BuildingTIV"] == "1900000"
    assert playground["BuildingTIV"] == "0" and playground["OtherTIV"] == "40000"
    assert pump["YearBuilt"] == "" and pump["BuildingTIV"] == "0"  # "n/k" and a zero value state nothing
    assert first["LocPerilsCovered"] == "AA1" and first["AccNumber"] == "Town of Brackley"


def test_provenance_names_cell_and_status(town_xlsx, tmp_path):
    files = write(extract(town_plan(town_xlsx), load(town_xlsx)), tmp_path / "out")
    prov = {(r["LocNumber"], r["OEDField"]): r for r in read_csv(files["provenance"])}
    assert prov[("4", "YearBuilt")]["Provenance"] == "declared" and "approximate" in prov[("4", "YearBuilt")]["Note"]
    assert prov[("3", "OccupancyCode")]["Provenance"] == "derived"
    assert prov[("3", "BuildingTIV")]["Source"] == "Town of Brackley Schedule!E3"
    assert "basis: replacement" in prov[("3", "BuildingTIV")]["Note"]
    assert prov[("3", "LocPerilsCovered")]["Provenance"] == "assumed"
    assert ("7", "YearBuilt") not in prov


def test_same_file_and_plan_give_the_same_bytes(town_xlsx, tmp_path):
    for out in ("a", "b"):
        write(extract(town_plan(town_xlsx), load(town_xlsx)), tmp_path / out)
    for name in ("location.csv", "account.csv", "provenance.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_text_that_looks_like_a_formula_is_inert(town_xlsx, tmp_path):
    plan = town_plan(town_xlsx)
    plan.facts[0].value = "=HYPERLINK(1)"
    files = write(extract(plan, load(town_xlsx)), tmp_path / "out")
    assert "'=HYPERLINK(1)" in files["location"].read_text()


def test_problems_name_every_wrong_reference(town_xlsx):
    text = TOWN_PLAN.format(sha=sha256(town_xlsx))
    text = text.replace("sheet: Town of Brackley Schedule\n", "sheet: Town Schedule\n", 1)
    assert any("no sheet 'Town Schedule'" in p for p in problems(Plan.read(text), load(town_xlsx)))
    text = (
        TOWN_PLAN.format(sha=sha256(town_xlsx))
        .replace("JM: 5108", "JM: 9999")
        .replace("{field: YearBuilt}", "{field: YearBuild}")
    )
    found = problems(Plan.read(text), load(town_xlsx))
    assert any("9999 is not an OED code" in p for p in found)
    assert any("'YearBuild' is not an OED Location field" in p for p in found)


def test_a_stated_value_needs_its_cell_and_quote(town_xlsx):
    text = TOWN_PLAN.format(sha=sha256(town_xlsx)).replace("{value: GBP, assumed: true}", "{value: GBP}")
    with pytest.raises(ValidationError, match="needs a cell and a quote"):
        Plan.read(text)


PDF_PLAN = """
plan: 1
file: {name: brackley.pdf, sha256: "0"}
country: {value: US, assumed: true}
currency: {value: USD, assumed: true}
tables:
  - id: t1
    rows_file: brackley.rows.csv
    header: [1]
    rows: "2-4"
    totals: ["5"]
    columns:
      B: {field: LocNumber}
      C: {field: LocName}
      D: {field: ConstructionCode}
      E: {field: BuildingTIV}
    sentinels: {E: ["-"]}
codes:
  ConstructionCode: {map: {JM: 5108, FR: 5050}}
"""
# What a script writes from the page lines: one row per location, the lines it was read from in column A. The
# glued "PUMPSTATIONJM" is split, and the wrapped library is joined.
PDF_ROWS = """lines,ID,Description,Constr,Value
p1:l3,1,PUMPSTATION,JM,"125,000"
p1:l4-l5,2,LIBRARY ANNEX WING,FR,"640,000"
p2:l2,3,DEPOT,JM,-
p2:l3,,TOTAL,,"765,000"
"""


def _with_rows(grid, tmp_path, text=PDF_ROWS):
    path = tmp_path / "brackley.rows.csv"
    path.write_text(text, encoding="utf-8")
    grid.sheets[path.name] = load_rows(path)
    return grid


def test_a_pdf_is_read_from_its_rows_file(town_pdf, tmp_path):
    plan, grid = Plan.read(PDF_PLAN), _with_rows(town_pdf, tmp_path)
    assert problems(plan, grid) == []
    ex = extract(plan, grid)
    assert [loc.id for loc in ex.locations] == ["p1-3", "p1-4", "p2-2"]
    pump, library, depot = ex.locations
    assert pump.get("LocName") == "PUMPSTATION" and pump.get("ConstructionCode") == 5108
    assert library.get("LocName") == "LIBRARY ANNEX WING" and library.values["BuildingTIV"].cell == "p1:l4-l5"
    assert depot.values["BuildingTIV"].note == "'-' means not known"
    assert guards.check(ex) == []  # every value printed on its lines, the total reconciles, no line left out
    rows = read_csv(write(ex, tmp_path)["location"])
    assert [r["LocNumber"] for r in rows] == ["1", "2", "3"]


def test_a_rows_file_is_checked_against_the_pdf(town_pdf, tmp_path):
    rows = PDF_ROWS.replace('p1:l4-l5,2,LIBRARY ANNEX WING,FR,"640,000"', 'p1:l4,2,LIBRARY ANNEX,FR,"641,000"')
    ex = extract(Plan.read(PDF_PLAN), _with_rows(town_pdf, tmp_path, rows))
    found = [f.message for f in guards.check(ex) if f.guard == "sources"]
    assert any("not printed on the lines of their row" in m and "brackley.rows.csv!E3" in m for m in found)
    assert any("on no row" in m and "p1:l5" in m for m in found)  # the wrapped line with the value


def test_a_pdf_table_needs_a_rows_file(town_pdf):
    plan = Plan.read(PDF_PLAN.replace("rows_file: brackley.rows.csv", "sheet: p1"))
    assert any("read from a rows_file" in p for p in problems(plan, town_pdf))
