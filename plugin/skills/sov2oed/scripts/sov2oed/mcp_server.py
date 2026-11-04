"""MCP server (mcp 2.x, stdio): inspect, rows, decide, convert, check.

Each tool reads files under one allowed folder (SOV2OED_ROOT, default the working directory) and writes its
outputs under an out_dir inside it. Nothing calls the network or a model: the host model looks at the file with
inspect, writes the reading plan, and the tools check it and convert.

    pip install "sov2oed[mcp]"
    claude mcp add sov2oed -- python -P -m sov2oed.mcp_server     # in Claude Code; other clients run the same command
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import pipeline
from .plan import Plan
from .read import grid as grid_mod, inspect as views

INSTRUCTIONS = (
    "sov2oed turns a statement of values (xlsx, csv or PDF) into OED location, account and provenance files, "
    "checks their quality and writes the broker questions. For a schedule: look at it with inspect, write a reading "
    "plan (inspect with guide=guide says how, plan_schema=true gives its schema), pass it to decide (fix and retry at most twice), then "
    "call convert. A PDF table is read from a rows file: write a script defining rows(lines) that groups the page "
    "lines into rows (inspect with guide=rows gives an example) and pass it to rows. For OED files received from someone "
    "else: call check. Paths are under the server's folder."
)
server = MCPServer("sov2oed", instructions=INSTRUCTIONS)


def _root() -> Path:
    return Path(os.environ.get("SOV2OED_ROOT") or Path.cwd()).resolve()


def _inside(p: str, what: str, suffixes: tuple[str, ...] | None = None, *, must_exist: bool = True) -> Path:
    """A path inside the allowed folder, or an error: the server never reads or writes outside it."""
    root = _root()
    path = (root / p).resolve()
    if not path.is_relative_to(root):
        raise ToolError(f"{what} {p!r} is outside the allowed folder {root}")
    if must_exist and not path.is_file():
        raise ToolError(f"{what} {p!r} not found")
    if suffixes and path.suffix.lower() not in suffixes:
        raise ToolError(f"{what} {p!r}: unsupported file type (expected {', '.join(suffixes)})")
    return path


def _files(result: pipeline.Result) -> dict[str, str]:
    root = _root()
    return {k: v.resolve().relative_to(root).as_posix() for k, v in result.files.items()}


def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except pipeline.PlanError as e:
        return {"ok": False, "problems": e.problems}
    except pipeline.user_errors() as e:
        raise ToolError(pipeline.explain(e)) from e


@server.tool()
def inspect(
    path: Annotated[str | None, Field(description="the schedule: .xlsx, .xlsm, .csv or .pdf")] = None,
    sheet: Annotated[str | None, Field(description="a sheet to print rows of")] = None,
    rows: Annotated[str | None, Field(description="rows of the sheet: 1-40,52")] = None,
    labels: Annotated[str | None, Field(description="a column of the sheet: its distinct values")] = None,
    page: Annotated[int | None, Field(description="a PDF page to print with word positions")] = None,
    lines: Annotated[str | None, Field(description="lines of the page: 1-30")] = None,
    codes: Annotated[str | None, Field(description="an OED code list: OccupancyCode, ConstructionCode...")] = None,
    plan_schema: Annotated[bool, Field(description="the JSON schema of a reading plan")] = False,
    guide: Annotated[str | None, Field(description="guide: how to write a plan; plan or rows: an example")] = None,
) -> Any:
    """Look at a schedule before writing its reading plan; with no option, the list of sheets or pages."""
    if plan_schema:
        return Plan.model_json_schema()
    if guide:
        return _run(views.guide, guide)
    if codes:
        return views.codes(codes)
    if path is None:
        raise ToolError("inspect needs a path, codes or plan_schema")
    sov = _inside(path, "schedule", pipeline.SCHEDULE_SUFFIXES)
    g = _run(grid_mod.load, sov)
    if page:
        return views.page(g, page, lines)
    if sheet and labels:
        return views.labels(g, sheet, labels, rows or f"1-{g.sheets[sheet].max_row}")
    if sheet:
        return views.rows(g, sheet, rows or "1-40")
    return views.overview(g)


@server.tool()
def rows(
    path: Annotated[str, Field(description="the PDF schedule")],
    script: Annotated[
        str, Field(description="Python source defining rows(lines): the header, then one row per location")
    ],
    pages: Annotated[str | None, Field(description="pages to pass: 2-10 (default all)")] = None,
    out_dir: Annotated[
        str, Field(description="folder for <stem>.rows.csv and the script, next to the plan")
    ] = "sov2oed-out",
) -> dict[str, Any]:
    """Run the script on the PDF's lines and write the rows file the plan reads (rows_file: <stem>.rows.csv)."""
    sov = _inside(path, "schedule", (".pdf",))
    out = _inside(out_dir, "out_dir", must_exist=False)
    result = dict(_run(pipeline.write_rows, sov, script, out / f"{sov.stem}.rows.csv", pages))
    root = _root()
    result["rows_path"] = Path(result["rows_path"]).resolve().relative_to(root).as_posix()
    result["script_path"] = Path(result["script_path"]).resolve().relative_to(root).as_posix()
    return result


@server.tool()
def decide(
    path: Annotated[str, Field(description="the schedule")],
    plan: Annotated[str, Field(description="the reading plan, as YAML text")],
    out_dir: Annotated[str, Field(description="folder for <stem>.plan.yaml, where rows files are too")] = "sov2oed-out",
) -> dict[str, Any]:
    """Check the plan against the file. ok false: fix the plan and call again (at most twice)."""
    sov = _inside(path, "schedule", pipeline.SCHEDULE_SUFFIXES)
    parsed = _run(Plan.read, plan)
    out = _inside(out_dir, "out_dir", must_exist=False)
    report = dict(_run(pipeline.decide, sov, parsed, out))
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{sov.stem}.plan.yaml"
    target.write_text(parsed.to_yaml(), encoding="utf-8")
    report["plan_path"] = target.relative_to(_root()).as_posix()
    return report


@server.tool()
def convert(
    path: Annotated[str, Field(description="the schedule")],
    plan_path: Annotated[str, Field(description="the plan.yaml written by decide")],
    out_dir: Annotated[str, Field(description="folder for the outputs")] = "sov2oed-out",
) -> dict[str, Any]:
    """OED location, account and provenance files, quality report, questions and annotated workbook."""
    sov = _inside(path, "schedule", pipeline.SCHEDULE_SUFFIXES)
    plan = _inside(plan_path, "plan", (".yaml", ".yml"))
    out = _inside(out_dir, "out_dir", must_exist=False)
    result = _run(pipeline.convert, sov, plan, out)
    return result if isinstance(result, dict) else {"summary": result.summary, "files": _files(result)}


@server.tool()
def check(
    location_path: Annotated[str, Field(description="OED location file (.csv)")],
    account_path: Annotated[str | None, Field(description="OED account file (.csv)")] = None,
    provenance_path: Annotated[str | None, Field(description="provenance side file (.csv)")] = None,
    out_dir: Annotated[str, Field(description="folder for the outputs")] = "sov2oed-out",
) -> dict[str, Any]:
    """Quality report and questions for OED files received from someone else."""
    loc = _inside(location_path, "location file", (".csv",))
    acc = _inside(account_path, "account file", (".csv",)) if account_path else None
    prov = _inside(provenance_path, "provenance file", (".csv",)) if provenance_path else None
    out = _inside(out_dir, "out_dir", must_exist=False)
    result = _run(pipeline.check, loc, out, acc, prov)
    return {"summary": result.summary, "files": _files(result)}


def main() -> None:
    server.run("stdio")  # stdout carries the protocol: nothing else may print there


if __name__ == "__main__":
    main()
