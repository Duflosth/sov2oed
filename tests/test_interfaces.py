import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from sov2oed.check.rules import TITLES
from sov2oed.plan import Plan

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_server_stays_in_its_folder(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError

    from sov2oed import mcp_server

    monkeypatch.setenv("SOV2OED_ROOT", str(tmp_path))
    with pytest.raises(ToolError, match="outside the allowed folder"):
        mcp_server.check("../etc/passwd.csv")
    with pytest.raises(ToolError, match="not found"):
        mcp_server.inspect("missing.xlsx")


def test_mcp_tools_convert_a_schedule(town_xlsx, monkeypatch):
    pytest.importorskip("mcp")
    from sov2oed import mcp_server
    from sov2oed.plan import sha256
    from tests.test_convert import TOWN_PLAN

    monkeypatch.setenv("SOV2OED_ROOT", str(town_xlsx.parent))
    assert "Town of Brackley Schedule" in mcp_server.inspect(town_xlsx.name)
    report = mcp_server.decide(town_xlsx.name, TOWN_PLAN.format(sha=sha256(town_xlsx)))
    assert report["ok"] and report["plan_path"] == "sov2oed-out/brackley.plan.yaml"
    result = mcp_server.convert(town_xlsx.name, report["plan_path"])
    assert result["files"]["location"] == "sov2oed-out/location.csv"


def test_skill_zip_runs_without_the_package_installed(town_xlsx, tmp_path):
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_skill.py"), "--out", str(tmp_path / "skill.zip")], check=True
    )
    with zipfile.ZipFile(tmp_path / "skill.zip") as z:
        z.extractall(tmp_path / "unzipped")
    cli = tmp_path / "unzipped" / "sov2oed" / "scripts" / "sov2oed_cli.py"
    out = subprocess.run(
        [sys.executable, "-I", str(cli), "inspect", str(town_xlsx)], capture_output=True, text=True, cwd=tmp_path
    )
    assert out.returncode == 0, out.stderr
    assert "Town of Brackley Schedule" in out.stdout
    env = subprocess.run([sys.executable, "-I", str(cli.parent / "check_env.py")], capture_output=True, text=True)
    assert json.loads(env.stdout)["python_ok"]


def test_the_plugin_folder_is_up_to_date_and_runs_on_its_own(town_xlsx, tmp_path):
    check = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_skill.py"), "--check"], capture_output=True, text=True
    )
    assert check.returncode == 0, check.stdout
    shutil.copytree(ROOT / "plugin", tmp_path / "plugin")
    cli = tmp_path / "plugin" / "skills" / "sov2oed" / "scripts" / "sov2oed_cli.py"
    out = subprocess.run([sys.executable, "-I", str(cli), "inspect", str(town_xlsx)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Town of Brackley Schedule" in out.stdout


def test_the_example_plan_is_valid():
    plan = Plan.load(ROOT / "sov2oed" / "data" / "plan_example.yaml")
    assert plan.tables[0].columns["H"].basis == "replacement"


def test_every_rule_is_documented():
    text = (ROOT / "RULES.md").read_text(encoding="utf-8")
    assert all(f"`{rule}`" in text for rule in TITLES)


def test_no_research_file_in_the_repository():
    """The research corpus never goes in the repository: no file may have the digest of one of its files."""
    corpus = set((ROOT / "tests" / "data" / "corpus.sha256").read_text().split())
    tracked = [
        p for p in ROOT.rglob("*") if p.is_file() and not {".git", ".venv", "dist"} & set(p.relative_to(ROOT).parts)
    ]
    leaked = [
        p for p in tracked if p.stat().st_size < 50_000_000 and hashlib.sha256(p.read_bytes()).hexdigest() in corpus
    ]
    assert leaked == []
