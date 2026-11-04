"""Command line: sov2oed inspect | rows | decide | convert | check | auto.

    sov2oed inspect FILE                                   sheets or pages, with a preview of each
    sov2oed inspect FILE --sheet NAME --rows 1-40          rows with column letters, formulas, comments
    sov2oed inspect FILE --page 3 [--lines 1-20]          a PDF page with the x position of every word
    sov2oed inspect FILE --sheet NAME --labels B --rows 2-64   distinct values of a column
    sov2oed inspect --codes OccupancyCode                  an OED code list
    sov2oed inspect --plan-schema                          the JSON schema of a reading plan
    sov2oed inspect --guide | --example plan|rows          how to write a plan; a plan, a PDF rows script
    sov2oed rows FILE.pdf SCRIPT.py --out STEM.rows.csv   a PDF: run the script that groups its lines into rows
    sov2oed decide FILE PLAN.yaml                          the plan checked against the file
    sov2oed convert FILE PLAN.yaml --out OUT/              OED files, reading log, report, questions
    sov2oed check location.csv [--account account.csv] [--provenance provenance.csv] --out OUT/
    sov2oed auto FILE --out OUT/ [--llm anthropic:claude-opus-5-5]   a model does the steps above on its own

Exit codes: 0 done, 1 the plan does not hold (decide, convert), 2 bad input. Only auto calls the network: it sends
the schedule to the model's provider.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, pipeline
from .plan import Plan
from .read import grid as grid_mod, inspect as views


def _print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _inspect(args: argparse.Namespace) -> int:
    if args.plan_schema:
        _print_json(Plan.model_json_schema())
        return 0
    if args.codes:
        print(views.codes(args.codes))
        return 0
    if args.guide or args.example:
        print(views.guide(args.example or "guide"))
        return 0
    if not args.file:
        raise ValueError("inspect needs a FILE, --codes, --guide, --example or --plan-schema")
    g = grid_mod.load(args.file)
    if args.page:
        print(views.page(g, args.page, args.lines))
    elif args.sheet and args.labels:
        print(views.labels(g, args.sheet, args.labels, args.rows or f"1-{g.sheets[args.sheet].max_row}"))
    elif args.sheet:
        print(views.rows(g, args.sheet, args.rows or "1-40", args.columns))
    else:
        print(views.overview(g))
    return 0


def _rows(args: argparse.Namespace) -> int:
    with open(args.script, encoding="utf-8") as f:
        script = f.read()
    _print_json(pipeline.write_rows(args.file, script, args.out, args.pages))
    return 0


def _decide(args: argparse.Namespace) -> int:
    report = pipeline.decide(args.file, args.plan)
    _print_json(report)
    return 0 if report["ok"] else 1


def _convert(args: argparse.Namespace) -> int:
    try:
        result = pipeline.convert(args.file, args.plan, args.out)
    except pipeline.PlanError as e:
        _print_json({"ok": False, "problems": e.problems})
        return 1
    _print_json({"files": {k: str(v) for k, v in result.files.items()}, **result.summary})
    return 0


def _check(args: argparse.Namespace) -> int:
    result = pipeline.check(args.location, args.out, args.account, args.provenance)
    _print_json({"files": {k: str(v) for k, v in result.files.items()}, **result.summary})
    return 0


def _auto(args: argparse.Namespace) -> int:
    from . import agent

    _print_json(agent.run(args.file, args.out, agent.model(args.llm, args.base_url)))
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sov2oed", description="Statement of values to OED, and OED quality checks.")
    p.add_argument("--version", action="version", version=f"sov2oed {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("inspect", help="look at a file before writing its reading plan")
    i.add_argument("file", nargs="?")
    i.add_argument("--sheet")
    i.add_argument("--rows", help="rows of a sheet: 1-40,52")
    i.add_argument("--columns", help="columns of a sheet: A-H")
    i.add_argument("--labels", metavar="COLUMN", help="distinct values of this column of --sheet")
    i.add_argument("--page", type=int)
    i.add_argument("--lines", help="lines of --page: 1-20")
    i.add_argument("--codes", metavar="FIELD", help="an OED code list: OccupancyCode, ConstructionCode, RoofCover...")
    i.add_argument("--plan-schema", action="store_true")
    i.add_argument("--guide", action="store_true", help="how to write a reading plan")
    i.add_argument("--example", choices=["plan", "rows"], help="a plan for a fictional schedule, a PDF rows script")
    i.set_defaults(func=_inspect)
    n = sub.add_parser("rows", help="a PDF: run the script that groups its lines into rows, write the rows file")
    n.add_argument("file")
    n.add_argument("script", help="a Python file defining rows(lines)")
    n.add_argument("--pages", help="pages: 2-10")
    n.add_argument("--out", required=True, help="the rows file: STEM.rows.csv next to the plan")
    n.set_defaults(func=_rows)
    d = sub.add_parser("decide", help="check a reading plan against the file")
    d.add_argument("file")
    d.add_argument("plan")
    d.set_defaults(func=_decide)
    c = sub.add_parser("convert", help="convert the file with its reading plan")
    c.add_argument("file")
    c.add_argument("plan")
    c.add_argument("--out", required=True)
    c.set_defaults(func=_convert)
    k = sub.add_parser("check", help="grade OED files received from someone else")
    k.add_argument("location")
    k.add_argument("--account")
    k.add_argument("--provenance")
    k.add_argument("--out", required=True)
    k.set_defaults(func=_check)
    a = sub.add_parser("auto", help="a model reads the file, writes the plan and converts, with no assistant")
    a.add_argument("file")
    a.add_argument("--out", required=True)
    a.add_argument("--llm", default="anthropic:claude-opus-5-5", help="provider:model, anthropic or openai")
    a.add_argument("--base-url", help="an OpenAI-compatible endpoint (Azure OpenAI, a local server)")
    a.set_defaults(func=_auto)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code: int = args.func(args)
        return code
    except pipeline.user_errors() as e:
        print(f"sov2oed: error: {pipeline.explain(e)}", file=sys.stderr)
        return 2
    except (ImportError, RuntimeError) as e:  # auto: a provider SDK not installed, the model stopped
        print(f"sov2oed: error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
