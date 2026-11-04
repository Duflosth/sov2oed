"""Render the README images from a real conversion of a fictional schedule: docs/img/report.png and docs/img/workbook.png.

The workbook image is an HTML rendering of the annotated workbook's cells, fills and comments (a spreadsheet
application is not needed); the report image is the HTML report itself. A Chromium-based browser takes the
screenshots in headless mode.

Usage: python tools/readme_images.py --browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"""

from __future__ import annotations

import argparse
import html
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from sov2oed import pipeline

ROOT = Path(__file__).resolve().parents[1]
STEM = "sov_us_cedar_isd"
SHEET = "Property SOV"
ROWS = [5, *range(12, 18), 28, 29, 30]  # header, rows around Q16, the last rows and the total
COLS = ["A", "B", "D", "H", "I", "J", "K", "N", "O", "P", "Q"]


def _workbook_html(path: Path) -> str:
    wb = load_workbook(path)
    ws = wb[SHEET]
    head = "".join(f"<th>{c}</th>" for c in COLS)
    body, notes = [], []
    for r in ROWS:
        cells = []
        for c in COLS:
            cell = ws[f"{c}{r}"]
            value = "" if cell.value is None else cell.value
            if isinstance(value, float):
                value = f"{value:,.0f}"
            elif isinstance(value, int) and value > 9999:
                value = f"{value:,}"
            fill = cell.fill.fgColor.rgb if cell.comment else None
            style = f' style="background:#{str(fill)[-6:]}"' if fill and str(fill) != "00000000" else ""
            mark = '<span class="tri"></span>' if cell.comment else ""
            cells.append(f"<td{style}>{mark}{html.escape(str(value))}</td>")
            if cell.comment and len(notes) < 3 and cell.coordinate in ("P30", "Q16", "O16"):
                notes.append(f"<b>{cell.coordinate}</b> {html.escape(cell.comment.text.splitlines()[0])}")
        if r == 28:
            body.append(f"<tr><th>…</th>{'<td></td>' * len(COLS)}</tr>")
        body.append(f"<tr><th>{r}</th>{''.join(cells)}</tr>")
    questions = [row for row in wb["Questions"].iter_rows(min_row=2, max_row=5, values_only=True)]
    qrows = "".join(
        f"<tr><td>{q[0]}</td><td>{html.escape(str(q[1]))}</td><td>{'–' if q[3] is None else str(q[3]) + '%'}</td></tr>"
        for q in questions
    )
    return f"""<!doctype html><meta charset="utf-8"><style>
body{{font:12px -apple-system,Segoe UI,sans-serif;margin:16px;color:#1f2328;background:#fff}}
table{{border-collapse:collapse;margin-bottom:14px}} td,th{{border:1px solid #d0d7de;padding:3px 6px;white-space:nowrap;
max-width:160px;overflow:hidden;text-overflow:ellipsis;position:relative}} th{{background:#f3f4f6;font-weight:500}}
.tri{{position:absolute;top:0;right:0;border-top:7px solid #d1242f;border-left:7px solid transparent}}
h3{{font-size:13px;margin:10px 0 6px}} .note{{color:#57606a;margin:2px 0}} .q td{{white-space:normal;max-width:760px}}
</style><h3>{html.escape(path.name)} · sheet “{SHEET}”: doubtful cells highlighted and commented</h3>
<table><tr><th></th>{head}</tr>{"".join(body)}</table>
{"".join(f'<p class="note">{n}</p>' for n in notes)}
<h3>Sheet “Questions”: the schedule’s own figures first, then by share of insured value</h3><table class="q">{qrows}</table>"""


def _shot(browser: str, page: Path, out: Path, size: str) -> None:
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={size}",
            f"--screenshot={out}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", required=True, help="path to a Chromium-based browser executable")
    args = ap.parse_args()
    img = ROOT / "docs" / "img"
    img.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        sov = ROOT / "fixtures" / f"{STEM}.xlsx"
        result = pipeline.convert(sov, ROOT / "fixtures" / f"{STEM}.plan.yaml", Path(tmp), as_of=2026)
        _shot(args.browser, result.files["report_html"], img / "report.png", "1440,2330")
        page = Path(tmp) / "workbook.html"
        page.write_text(_workbook_html(result.files["workbook"]), encoding="utf-8")
        _shot(args.browser, page, img / "workbook.png", "1100,620")
    print(f"wrote {img / 'report.png'} and {img / 'workbook.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
