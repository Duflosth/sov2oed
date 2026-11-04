"""End to end on the two fictional schedules of fixtures/, with the plans written from `inspect` as in the skill."""

import csv
import json
from pathlib import Path

import openpyxl
import pytest

from sov2oed import cli, pipeline

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCHEDULES = {"sov_us_cedar_isd": "sov_us_cedar_isd.xlsx", "sov_us_gulfmoor": "sov_us_gulfmoor.pdf"}


def convert(name, out):
    return pipeline.convert(FIXTURES / SCHEDULES[name], FIXTURES / f"{name}.plan.yaml", out, as_of=2026)


@pytest.mark.parametrize("name", SCHEDULES)
def test_the_plan_holds(name):
    report = pipeline.decide(FIXTURES / SCHEDULES[name], FIXTURES / f"{name}.plan.yaml")
    assert report["ok"] and not [f for f in report["findings"] if f["kind"] == "plan"]


def test_cedar_isd(tmp_path):
    result = convert("sov_us_cedar_isd", tmp_path)
    assert result.summary["locations"] == 24
    assert result.summary["values"] == {"BuildingTIV": 76_052_000, "ContentsTIV": 7_605_200, "OtherTIV": 292_000}
    qs = json.loads(result.files["report_json"].read_text())["questions"]
    # The schedule's own figures first: P30 adds up column Q, and Q16 is typed over the =O+P formula.
    assert [q["cells"] for q in qs[:2]] == [["Property SOV!P30"], ["Property SOV!Q16"]]
    assert "=SUM(Q6:Q29)" in qs[0]["text"]
    assert not any("use of the building" in q["text"] for q in qs)  # the fences and bleachers are not buildings
    # Then the plan's own questions on this file, then the perils, which the schedule does not state.
    assert [q["ref"] for q in qs[2:5]] == ["plan", "plan", "perils"]
    assert qs[2]["cells"] == ["Property SOV!J10", "Property SOV!T10"]  # built 2005, roof replaced 1998
    assert not any("seismic" in q["text"] or "flood defences" in q["text"] for q in qs)
    rows = list(csv.DictReader(result.files["location"].open()))
    assert rows[0]["LocNumber"] == "1-1" and rows[0]["ConstructionCode"] == "5108"
    assert "CEDAR CROSSING INDEPENDENT SCHOOL DISTRICT" in result.files["questions"].read_text().splitlines()[0]
    ws = openpyxl.load_workbook(result.files["workbook"])["Property SOV"]
    assert ws["Q16"].comment is not None  # the schedule has location numbers: no column added
    assert all(c.value != "Location no." for c in ws[5])


def test_gulfmoor(tmp_path):
    result = convert("sov_us_gulfmoor", tmp_path)
    assert result.summary["locations"] == 61
    assert result.summary["values"] == {"BuildingTIV": 127_116_000, "ContentsTIV": 19_599_000, "OtherTIV": 1_136_950}
    reading = json.loads(result.files["conversion"].read_text())
    # the subtotal and the grand total match the lines read; one row prints a total its amounts do not reach
    assert [(f["guard"], f["kind"], f["cells"]) for f in reading["findings"]] == [("row_totals", "file", ["p2:l15"])]
    rows = {r["LocNumber"]: r for r in csv.DictReader(result.files["location"].open())}
    assert rows["3-1"]["ConstructionCode"] == "5201"  # NC: non-combustible, not "not communicated"
    assert rows["41-1"]["OtherTIV"] == "168000" and rows["41-1"]["BuildingTIV"] == "0"
    qs = json.loads(result.files["report_json"].read_text())["questions"]
    asked = {q["text"].partition(":")[0]: q["locations"] for q in qs}
    assert asked["No street address"] == ["18-1"]  # TBD
    assert asked["Locations that look like duplicates of others"] == ["40-1"]  # the same barn as 6-1
    assert asked["A year or a number of storeys that cannot be right"] == ["20-1"]  # built in 2028
    assert "9-2" in asked["Value per square foot or metre out of the usual range"]  # 1,150 sq ft at 9,850,000


@pytest.mark.parametrize("name", SCHEDULES)
def test_same_file_same_plan_same_bytes(name, tmp_path):
    a, b = convert(name, tmp_path / "a"), convert(name, tmp_path / "b")
    for key in a.files:
        assert a.files[key].read_bytes() == b.files[key].read_bytes(), key


def test_the_gulfmoor_rows_file_comes_from_its_script(tmp_path):
    """The rows file is reproducible: the PDF and its script give the same bytes."""
    rows = tmp_path / "sov_us_gulfmoor.rows.csv"
    script = FIXTURES.parent / "sov2oed" / "data" / "rows_example.py"
    assert cli.main(["rows", str(FIXTURES / "sov_us_gulfmoor.pdf"), str(script), "--out", str(rows)]) == 0
    assert rows.read_bytes() == (FIXTURES / "sov_us_gulfmoor.rows.csv").read_bytes()
    assert rows.with_suffix(".py").read_text(encoding="utf-8") == script.read_text(encoding="utf-8")
