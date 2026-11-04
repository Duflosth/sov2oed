import json

import openpyxl

from sov2oed import pipeline
from sov2oed.plan import sha256
from tests.test_convert import TOWN_PLAN


def convert(town_xlsx, out):
    return pipeline.convert(town_xlsx, TOWN_PLAN.format(sha=sha256(town_xlsx)), out, as_of=2026)


def test_convert_writes_every_deliverable(town_xlsx, tmp_path):
    result = convert(town_xlsx, tmp_path / "out")
    assert set(result.files) == {"location", "account", "provenance", "conversion", "plan", "report_json",
                                 "report_html", "questions", "workbook"}  # fmt: skip
    report = json.loads(result.files["report_json"].read_text())
    assert report["grade"] in "ABCDE" and report["locations"] == 5
    assert report["questions"][-1]["ref"] == "location numbers"
    page = result.files["report_html"].read_text()
    assert "<title>OED quality report: brackley.xlsx</title>" in page
    assert len(report["matrix"]["rows"]) == 5 and all(len(r["cells"]) == 12 for r in report["matrix"]["rows"])
    assert "Where each value comes from" in page and "<b>Location numbers</b>" in page  # questions carry a title


def test_annotated_workbook(town_xlsx, tmp_path):
    wb = openpyxl.load_workbook(convert(town_xlsx, tmp_path / "out").files["workbook"])
    assert wb.sheetnames == ["Town of Brackley Schedule", "Legend", "Questions", "Legend (2)"]
    ws = wb["Town of Brackley Schedule"]
    assert ws["H2"].value == "Location no." and ws["H3"].value == "3" and ws["H7"].value == "7"
    assert ws["C7"].comment is not None and "YearBuilt" in ws["C7"].comment.text  # "n/k"
    assert ws["A3"].comment.text.startswith("Flood policy")  # the broker's own comment stays first
    assert wb["Questions"]["B2"].value


def test_same_inputs_same_bytes(town_xlsx, tmp_path):
    a, b = convert(town_xlsx, tmp_path / "a"), convert(town_xlsx, tmp_path / "b")
    for key in a.files:
        assert a.files[key].read_bytes() == b.files[key].read_bytes(), key


def test_check_alone(town_xlsx, tmp_path):
    converted = convert(town_xlsx, tmp_path / "out").files
    result = pipeline.check(converted["location"], tmp_path / "check", account=converted["account"], as_of=2026)
    assert result.summary["locations"] == 5 and result.files["questions"].is_file()
