"""The steps the skill, the MCP server and the command line share: rows (a PDF), decide, convert, check.

`decide` reads a plan against the file and reports what does not hold: wrong references, then the guards'
findings. `convert` does the same, then writes the OED files, the reading log (conversion.json), the quality
report, the broker questions and the annotated workbook. `check` grades OED files received from someone else.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import annotate, guards, parse, questions, report, sandbox
from .check import load as oed_load, rules, schema, score
from .extract import ASSUMED, Extraction, extract
from .oed import write as oed_write
from .plan import Plan, problems, sha256, sheet_rows
from .read import grid as grid_mod

SCHEDULE_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".txt", ".pdf")


class PlanError(ValueError):
    """The plan does not hold for the file: the conversion is refused, the list says why."""

    def __init__(self, found: list[str]):
        super().__init__("the plan does not hold for this file: " + "; ".join(found[:10]))
        self.problems = found


def user_errors() -> tuple[type[BaseException], ...]:
    """Exceptions a bad input raises (missing or unreadable file, malformed YAML, invalid plan)."""
    import zipfile

    import yaml
    from pydantic import ValidationError

    out: list[type[BaseException]] = [OSError, ValueError, yaml.YAMLError, ValidationError, zipfile.BadZipFile]
    try:
        from pdfplumber.utils.exceptions import PdfminerException

        out.append(PdfminerException)
    except ImportError:
        pass
    return tuple(out)


def explain(e: BaseException) -> str:
    """One line on what is wrong with the input."""
    import yaml
    from pydantic import ValidationError

    if isinstance(e, ValidationError):
        errs = e.errors(include_url=False, include_context=False)
        parts = [f"{'.'.join(str(x) for x in err['loc']) or 'top level'}: {err['msg']}" for err in errs[:8]]
        return "invalid plan: " + "; ".join(parts) + (f" (and {len(errs) - 8} more)" if len(errs) > 8 else "")
    if isinstance(e, yaml.YAMLError):
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        return f"malformed YAML{where}: {getattr(e, 'problem', None) or type(e).__name__}"
    if isinstance(e, OSError) and e.filename:
        return f"{e.strerror or type(e).__name__}: {e.filename}"
    if type(e).__name__ == "BadZipFile":
        return f"not a readable xlsx workbook ({e})"
    if type(e).__name__ == "PdfminerException":
        return f"not a readable PDF ({e})"
    return str(e) or type(e).__name__


def _plan(plan: Plan | str | Path, base: Path | None) -> tuple[Plan, Path]:
    """The plan, and the folder its rows files are in: the plan's own folder, else `base`, else the working one."""
    if isinstance(plan, Plan):
        return plan, base or Path.cwd()
    text = str(plan)
    if "\n" in text:
        return Plan.read(text), base or Path.cwd()
    return Plan.load(text), base or Path(text).resolve().parent


def _read(path: str | Path, plan: Plan, base: Path) -> tuple[grid_mod.Grid, list[str]]:
    """The file, with the rows files of its PDF tables as sheets, and what does not hold."""
    grid = grid_mod.load(path)
    found = []
    if plan.file.sha256 != sha256(grid.path):
        found.append(f"the plan was written for another file (sha256 {plan.file.sha256[:12]}…), not {grid.path.name}")
    for t in plan.tables:
        if t.rows_file is None or t.rows_file in grid.sheets:
            continue
        rows = base / t.rows_file
        if not rows.is_file():
            found.append(f"table {t.id}: rows file '{t.rows_file}' not found in {base}")
            continue
        grid.sheets[t.rows_file] = grid_mod.load_rows(rows)
    return grid, found + problems(plan, grid)


def write_rows(path: str | Path, script: str, out: str | Path, pages: str | None = None) -> dict[str, Any]:
    """Run a rows script on the PDF's lines and write the rows file, the script next to it (STEM.rows.py)."""
    grid = grid_mod.load(path)
    if grid.kind != "pdf":
        raise ValueError(f"{grid.path.name} is not a PDF: a plan reads its sheets directly, with no rows file")
    rows = sandbox.run(script, grid_mod.lines_data(grid, sheet_rows(pages) if pages else None))
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    kept = target.with_suffix(".py")
    if not kept.is_file() or kept.read_text(encoding="utf-8") != script:
        kept.write_text(script, encoding="utf-8")
    return {"rows_path": str(target), "script_path": str(kept), "rows": len(rows) - 1, "first_rows": rows[:6]}


def _reading(ex: Extraction, found: list[guards.Finding]) -> dict[str, Any]:
    """The reading log: rows read and skipped per table, the values read, what was assumed, the guards' findings."""
    tables = []
    for t in ex.plan.tables:
        locs = [loc for loc in ex.locations if loc.table == t.id]
        skipped = Counter(why for tid, _, why in ex.skipped if tid == t.id)
        tables.append({"id": t.id, "source": t.source, "rows_in_range": len(ex.rows[t.id]),
                       "locations": len(locs), "skipped": dict(sorted(skipped.items()))})  # fmt: skip
    values: dict[str, float] = {}
    for loc in ex.locations:
        for name in ("BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV"):
            target = "OtherTIV" if name == "BuildingTIV" and not loc.building else name
            if (v := loc.get(name)) is not None:
                values[target] = parse.fsum([values.get(target, 0.0), v])
    assumed = Counter(name for loc in ex.locations for name, v in loc.values.items() if v.status == ASSUMED)
    plan = ex.plan
    stated_assumed = [label for label, s in (("country", plan.country), ("currency", plan.currency),
                                              ("perils", plan.account.perils)) if s.assumed]  # fmt: skip
    return {
        "file": ex.grid.path.name,
        "sha256": plan.file.sha256,
        "locations": len(ex.locations),
        "tables": tables,
        "values": {k: round(v, 2) for k, v in values.items()},
        "assumed": {"file": stated_assumed, "fields": dict(sorted(assumed.items()))},
        "findings": [asdict(f) for f in found],
        "notes": plan.notes,
    }


def decide(path: str | Path, plan: Plan | str | Path, base: Path | None = None) -> dict[str, Any]:
    """Check a plan against the file. "ok" is true when every reference holds and no guard found a plan error."""
    p, folder = _plan(plan, base)
    grid, found = _read(path, p, folder)
    if found:
        return {"ok": False, "problems": found}
    ex = extract(p, grid)
    findings = guards.check(ex) + questions.check(ex)
    report = _reading(ex, findings)
    report["ok"] = not any(f.kind == "plan" for f in findings)
    return report


@dataclass
class Result:
    files: dict[str, Path]
    summary: dict[str, Any]


def _write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _grade(location: Path, account: Path | None, provenance: Path | None, as_of: int | None,
           reading: dict[str, Any] | None) -> tuple[oed_load.Portfolio, dict[str, Any]]:  # fmt: skip
    p = oed_load.load(location, account, provenance)
    sc = schema.check(p)
    results = rules.run(p, sc, as_of or dt.date.today().year)
    ctx = rules.Context(p, sc)
    s = score.score(p, ctx, results)
    tiv: dict[str, float] = {}
    for row in p.locations:
        for f in rules.TIV:
            tiv[f] = parse.fsum([tiv.get(f, 0.0), oed_load.number(row, f) or 0.0])
    return p, report.build(p, s, results, {k: v for k, v in tiv.items() if v}, reading)


def _locations(p: oed_load.Portfolio) -> list[dict[str, Any]]:
    return [{"number": r.get("LocNumber", str(i + 1)), "name": r.get("LocName") or r.get("StreetAddress", ""),
             "tiv": sum(oed_load.number(r, f) or 0.0 for f in rules.TIV),
             "unknown_basis": [f for f in rules.TIV if p.basis(i, f) == "unknown" and (oed_load.number(r, f) or 0) > 0]}
            for i, r in enumerate(p.locations)]  # fmt: skip


def _deliver(
    folder: Path, rep: dict[str, Any], qs: list[dict[str, Any]], insured: str | None, title: str
) -> dict[str, Path]:
    files = {
        "report_json": folder / "report.json",
        "report_html": folder / "report.html",
        "questions": folder / "questions.txt",
    }
    files["report_json"].write_text(report.to_json({**rep, "questions": qs}), encoding="utf-8")
    files["report_html"].write_text(report.to_html({**rep, "questions": qs}, title, insured), encoding="utf-8")
    files["questions"].write_text(questions.email(qs, insured), encoding="utf-8")
    return files


def convert(path: str | Path, plan: Plan | str | Path, out: str | Path, as_of: int | None = None,
            base: Path | None = None) -> Result:  # fmt: skip
    """Read the file with the plan and write the OED files, the reading log, the report, questions and workbook."""
    p, rows_dir = _plan(plan, base)
    grid, found = _read(path, p, rows_dir)
    if found:
        raise PlanError(found)
    ex = extract(p, grid)
    findings = guards.check(ex) + questions.check(ex)
    folder = Path(out)
    files = oed_write.write(ex, folder)
    reading = _reading(ex, findings)
    for name in dict.fromkeys(t.rows_file for t in p.tables if t.rows_file):
        src, kept = rows_dir / name, folder / name  # the rows file goes with the plan: both reproduce the reading
        if src.resolve() != kept.resolve():
            shutil.copyfile(src, kept)
            if (script := src.with_suffix(".py")).is_file():
                shutil.copyfile(script, kept.with_suffix(".py"))
        files[f"rows:{name}"] = kept
        reading.setdefault("rows_files", {})[name] = sha256(kept)
    reading["removed_sheets"] = annotate.removed_sheets(ex)  # hidden sheets not read: not in the annotated copy
    files["conversion"] = _write_json(folder / "conversion.json", reading)
    files["plan"] = folder / "plan.yaml"
    files["plan"].write_text(p.to_yaml(), encoding="utf-8")
    portfolio, rep = _grade(files["location"], files["account"], files["provenance"], as_of, reading)
    workbook = grid.kind == "xlsx"
    last = [questions.numbering(workbook)] if annotate.numbers_generated(ex) else []
    asked = [*questions.from_guards(findings), *questions.from_plan(ex)]
    qs = questions.select(asked, questions.from_report(rep, _locations(portfolio)), last)
    insured = (p.insured.value or p.insured.quote) if p.insured else None
    files.update(_deliver(folder, rep, qs, insured, f"OED quality report: {grid.path.name}"))
    files["workbook"] = annotate.write(ex, qs, folder / f"{grid.path.stem}.annotated.xlsx")
    return Result(files, {"locations": len(ex.locations), "values": reading["values"], "score": rep["score"],
                          "grade": rep["grade"], "questions": len(qs), "plan_findings": sum(f.kind == "plan" for f in findings),
                          "top_issues": [f"{x['title']}: {x['label']} ({x['locations']} locations)" for x in rep["issues"][:3]]})  # fmt: skip


def check(location: str | Path, out: str | Path, account: str | Path | None = None,
          provenance: str | Path | None = None, as_of: int | None = None) -> Result:  # fmt: skip
    """Grade OED files received from someone else: report and questions for whoever sent them."""
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    portfolio, rep = _grade(
        Path(location), Path(account) if account else None, Path(provenance) if provenance else None, as_of, None
    )
    qs = questions.select([], questions.from_report(rep, _locations(portfolio)))
    files = _deliver(folder, rep, qs, None, f"OED quality report: {Path(location).name}")
    return Result(files, {"locations": rep["locations"], "score": rep["score"], "grade": rep["grade"],
                          "top_issues": [f"{x['title']}: {x['label']} ({x['locations']} locations)" for x in rep["issues"][:3]]})  # fmt: skip
