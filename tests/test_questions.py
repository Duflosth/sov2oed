"""The broker questions: the plan's own questions checked against their cells, the order, the perils, the basis."""

import json

import pytest
from pydantic import ValidationError

from sov2oed import pipeline, questions
from sov2oed.plan import Plan, problems, sha256
from sov2oed.read.grid import load
from sov2oed.report import title
from tests.test_convert import TOWN_PLAN

SHEET = "Town of Brackley Schedule"
ASKED = f"""
questions:
  - text: The Library and the playground are both at 4 Quarry Road. Is the playground on the library's site?
    cells:
      - {{cell: "{SHEET}!A4", quote: "4 Quarry Road"}}
      - {{cell: "{SHEET}!B5", quote: "Playground Equip"}}
"""
PERILS = f"""
account:
  perils: {{value: WW2, cell: "{SHEET}!A1", quote: "Statement of values"}}
"""


def plan_text(path, extra="", plan=TOWN_PLAN):
    return plan.format(sha=sha256(path)) + extra


def asked(path, out, extra="", plan=TOWN_PLAN):
    result = pipeline.convert(path, plan_text(path, extra, plan), out, as_of=2026)
    return json.loads(result.files["report_json"].read_text())["questions"]


def test_a_plan_question_is_asked_after_the_schedule_figures(town_xlsx, tmp_path):
    qs = asked(town_xlsx, tmp_path, ASKED)
    assert [q["ref"] for q in qs][:2] == ["plan", "perils"]
    assert qs[2]["text"].startswith("Building characteristics missing")
    assert qs[0]["cells"] == [f"{SHEET}!A4", f"{SHEET}!B5"]
    assert pipeline.decide(town_xlsx, plan_text(town_xlsx, ASKED))["ok"]


def test_a_quote_not_in_its_cell_is_a_plan_error_and_not_asked(town_xlsx, tmp_path):
    wrong = ASKED.replace('quote: "4 Quarry Road"', 'quote: "5 Quarry Road"')
    report = pipeline.decide(town_xlsx, plan_text(town_xlsx, wrong))
    assert not report["ok"]
    assert [f["message"] for f in report["findings"] if f["kind"] == "plan"] == [
        f"questions[0]: '5 Quarry Road' is not in {SHEET}!A4."
    ]
    assert "plan" not in [q["ref"] for q in asked(town_xlsx, tmp_path, wrong)]


def test_a_question_cites_cells_of_the_file_in_the_words_of_a_schedule(town_xlsx):
    grid = load(town_xlsx)
    bad = ASKED.replace(f"{SHEET}!B5", "Nowhere!B5").replace("Is the playground", "Is its OccupancyCode")
    assert problems(Plan.read(plan_text(town_xlsx, bad)), grid) == [
        "questions[0]: OccupancyCode is an OED field name; use the words of the schedule",
        "questions[0]: 'Nowhere!B5': no sheet 'Nowhere'",
    ]


def test_at_most_five_plan_questions_each_with_a_cell(town_xlsx):
    six = "questions:\n" + "".join(ASKED.split("questions:\n")[1] for _ in range(6))
    with pytest.raises(ValidationError, match="at most 5"):
        Plan.read(plan_text(town_xlsx, six))
    with pytest.raises(ValidationError):
        Plan.read(plan_text(town_xlsx, "questions:\n  - {text: 'One building or two?', cells: []}\n"))


def test_perils_not_stated_ask_them_and_only_the_core_characteristics(town_xlsx, tmp_path):
    qs = asked(town_xlsx, tmp_path)
    assert qs[0]["ref"] == "perils"
    missing = qs[1]["text"]
    assert "roof covering" in missing and "floor area" in missing
    assert not any(word in missing for word in ("window protection", "seismic", "flood defences", "sprinklers"))


def test_perils_stated_ask_the_details_of_those_perils(town_xlsx, tmp_path):
    qs = asked(town_xlsx, tmp_path, PERILS)
    assert "perils" not in [q["ref"] for q in qs]
    missing = next(q["text"] for q in qs if q["ref"].startswith("WHAT"))
    assert "window protection" in missing  # windstorm without storm surge: the wind details only
    assert not any(word in missing for word in ("seismic", "flood defences", "sprinklers"))


def test_an_unknown_value_basis_is_asked(town_xlsx, tmp_path):
    plan = TOWN_PLAN.replace("F: {{field: ContentsTIV}}", "F: {{field: ContentsTIV, basis: unknown}}")
    q = next(q for q in asked(town_xlsx, tmp_path, plan=plan) if q["ref"] == "value basis")
    assert q["text"].startswith("On what basis are these values given: replacement cost, market value")
    assert "for the contents values: 4 locations" in q["text"]
    assert q["locations"] == ["3", "4", "6", "7"]  # the playground has no contents value


def test_at_most_ten_questions_the_location_numbers_last():
    many = [{"ref": f"q{i}", "text": "", "cells": [], "locations": [], "tiv_pct": None, "impact": 0.0}
            for i in range(12)]  # fmt: skip
    qs = questions.select(many[:8], many[8:], [questions.numbering(True)])
    assert len(qs) == 10 and [q["ref"] for q in qs[-2:]] == ["q8", "location numbers"]


def test_the_secondary_attributes_rule_names_its_peril():
    assert title("WHAT-012", "earthquake") == "Earthquake attributes unknown"
    assert title("WHAT-001", "missing") == "Construction unknown"
