import json

from sov2oed.cli import main
from sov2oed.plan import sha256
from tests.test_convert import TOWN_PLAN


def write_plan(town_xlsx, tmp_path, text=None):
    path = tmp_path / "plan.yaml"
    path.write_text(text or TOWN_PLAN.format(sha=sha256(town_xlsx)), encoding="utf-8")
    return path


def test_inspect_views(town_xlsx, capsys):
    assert main(["inspect", str(town_xlsx)]) == 0
    out = capsys.readouterr().out
    assert "[Town of Brackley Schedule] rows 1-8" in out and "[comment: Flood policy" in out
    assert (
        main(["inspect", str(town_xlsx), "--sheet", "Town of Brackley Schedule", "--labels", "G", "--rows", "3-7"]) == 0
    )
    assert capsys.readouterr().out.splitlines() == ["    2  JM", "    2  Frame"]
    assert main(["inspect", "--codes", "ConstructionCode"]) == 0
    assert "5108" in capsys.readouterr().out


def test_decide_and_convert(town_xlsx, tmp_path, capsys):
    plan = write_plan(town_xlsx, tmp_path)
    assert main(["decide", str(town_xlsx), str(plan)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["locations"] == 5 and report["values"]["OtherTIV"] == 40000
    assert main(["convert", str(town_xlsx), str(plan), "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "location.csv").is_file() and (tmp_path / "out" / "conversion.json").is_file()


def test_plan_for_another_file_is_refused(town_xlsx, tmp_path, capsys):
    plan = write_plan(town_xlsx, tmp_path, TOWN_PLAN.format(sha="0" * 64))
    assert main(["convert", str(town_xlsx), str(plan), "--out", str(tmp_path / "out")]) == 1
    assert "written for another file" in capsys.readouterr().out


def test_bad_input_exits_2(tmp_path, capsys):
    assert main(["inspect", str(tmp_path / "missing.xlsx")]) == 2
    assert "sov2oed: error" in capsys.readouterr().err
