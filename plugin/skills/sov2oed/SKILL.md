---
name: sov2oed
description: Turns a property statement of values (SOV: Excel, CSV or PDF schedule of insured locations) into OED exposure files (Oasis Open Exposure Data location, account and provenance CSVs), and checks the quality of any OED file. Use when the user shares a SOV, a schedule of locations or an OED file and wants it converted to OED, validated, scored, or wants questions for the broker.
license: Apache-2.0 (see LICENSE.txt)
---

# sov2oed

Two jobs:

- **Convert a schedule** (xlsx, xlsm, csv, pdf with a text layer). You look at the file and write a **reading
  plan**: which rows form each table, which column feeds which OED field, which label means which OED code. Code
  reads every value, checks the reading against the whole file, and writes the OED files, a quality report,
  questions for the broker and the client's workbook annotated.
- **Check OED files** received from someone else: the quality report and the questions only.

Run every command as `python3 scripts/sov2oed_cli.py` from this skill's folder, with paths to the user's files and
an output folder `OUT` (for example `/mnt/user-data/outputs/sov2oed`). Nothing calls the network.

First run `python3 scripts/check_env.py`. If `ok` is false, run the `install` command it prints when the network
allows it; otherwise tell the user which packages are missing, and stop.

## Path A: convert a schedule

1. **Look at the file.** `inspect FILE` lists the sheets (hidden ones included) or pages with a preview. Then:
   - `inspect FILE --sheet NAME --rows 1-60` prints rows with column letters, formulas `{=SUM(D2:E2)}` and comments;
   - `inspect FILE --sheet NAME --labels C --rows 2-64` counts the distinct values of a column;
   - `inspect FILE --page 3 [--lines 1-30]` prints a PDF page with the x position of every word;
   - `inspect --codes OccupancyCode` (or ConstructionCode, RoofCover...) prints an OED code list.
   Look for what a preview hides: where each table starts and ends, total and subtotal rows, the unit of the
   amounts (a title or a footnote: "values in thousands"), the basis of the values (replacement cost, actual cash
   value, a policy limit), rows that are not buildings (playground equipment, signs, generators), hidden sheets,
   rare labels in coded columns.
2. **A PDF: write its rows file.** Write a short Python script, `OUT/STEM.rows.py`, defining `rows(lines)`: it
   gets every line of the PDF as `{"ref": "p3:l12", "page": 3, "line": 12, "text": ..., "words": [[x0, x1,
   text], ...]}` and returns the header, then one row per location (and one per total line): column A naming the
   PDF lines the row was read from (`p3:l12`, `p3:l12-l13` for a location over two lines), then one column per
   field of the schedule, each value copied as printed. Join the lines of a location that wraps; leave out headers
   and footers. The script only computes: no file, no network, imports such as `re` and `bisect` only.
   `rows FILE OUT/STEM.rows.py --out OUT/STEM.rows.csv` runs it; `inspect --example rows` prints such a script.
   Code checks every value against its lines, and that no line printing an amount is left out. Spreadsheets and
   CSV files need no rows file.
3. **Write the plan**, `OUT/STEM.plan.yaml`, following the guide that `inspect --guide` prints. `inspect --example plan` is a
   complete plan for a fictional schedule. The plan holds structure and codes only: every value you state yourself
   (the country, a fact taken from a comment, a multiplier) names its cell and quotes it exactly, or says
   `assumed: true`.
   While you look, note what only a reading of this file shows and the broker alone can settle: two insureds in
   one schedule, a row whose total is not its building plus contents, two rows that may be one building, flood
   amounts that are policy limits, policy dates already past, a building marked vacant. Write them in the plan's
   `questions` (at most 5), each with the cells it is about and their exact quotes. Not what the schedule
   already states, nor what code asks itself (totals, formulas, missing characteristics, perils, value basis),
   nor what sov2oed or a cat team does (placing addresses on the map, the OED format).
4. **Check it.** `decide FILE OUT/STEM.plan.yaml`. It prints the reading: rows read and skipped per table, the
   values read, and the findings.
   - `problems`: a reference that does not hold (a sheet name, a column, a code). Fix the plan.
   - findings of kind `plan`: the reading is incomplete (rows that look like locations but are not read, a label
     with no code, a sheet not accounted for, a quote not in its cell). Fix the plan.
   - findings of kind `file`: the schedule itself is in doubt (a printed total its rows do not reach, a formula
     column typed over by hand). Keep them: they become questions for the broker. Never bend the plan to make a
     total match.
   Run decide again after a fix, **at most twice**.
5. **Convert.** `convert FILE OUT/STEM.plan.yaml --out OUT`. The rows file and its script stay with the outputs:
   they reproduce the reading.

## Path B: check OED files

`check location.csv [--account account.csv] [--provenance provenance.csv] --out OUT`

## What to give the user

The command prints a summary. Tell the user, briefly: the locations read and the values read; whether the printed
totals reconcile (the `totals` findings); the grade and the score; the three largest issues with their share of
the insured value; what was assumed. Then the files:

| File | What it is |
|---|---|
| `location.csv`, `account.csv` | OED 5.0 files, ready for an OED import |
| `provenance.csv` | for each location and OED field: declared, derived or assumed, the source cell, the value basis |
| `report.html`, `report.json` | the quality report: the grade, the figures to correct, issues ranked by the share of insured value they concern, the questions, and where each value comes from |
| `STEM.annotated.xlsx` | the client's own workbook, doubtful cells coloured and commented, a "Questions" sheet |
| `questions.txt` | the questions for the broker, in English: write the message from them (below) |
| `conversion.json` | how the file was read: rows read and skipped, values, findings, what was assumed |

Never present a value the file does not state as a fact: perils, for example, are all perils (AA1) unless the
plan cites where the file states them.

## The message to the broker

`questions.txt` holds the questions, at most ten, in this order: the schedule's own figures (a total that does not
add up, a formula typed over), then your questions from the plan, then which perils the programme covers when the
file does not say, then the missing building characteristics in one question (the core ones while the perils are
not known), then the rest, largest share of the insured value first. Code fixes each point, its cells or
locations and its share of the insured value; it drops a plan question whose quote is not in its cell. When the
user wants the message to the broker, write it from `questions.txt`:
- in the broker's language: the language of the schedule, unless the user says otherwise;
- every question, in the same order, with its cells and locations; add none, drop none;
- in the words of a property schedule, never an OED field name or code (not "ConstructionCode", not "WW2");
- short: a greeting, the numbered questions, a line saying the annotated workbook is attached.

## When something fails

| Message | What to do |
|---|---|
| `check_env.py`: `python_ok` false | sov2oed needs Python 3.11 or later: run the commands with a newer `python3`. |
| `No module named 'sov2oed'` | Run through `scripts/sov2oed_cli.py`, not `python -m sov2oed`. |
| `invalid plan: ...` | A key, a type or a missing value: the message names it. `inspect --plan-schema` gives the schema. |
| `needs a cell and a quote, or assumed: true` | A value you state must cite where the file says it, or say it is assumed. |
| `the plan was written for another file` | `file.sha256` must be the file's: `shasum -a 256 FILE`. |
| `label(s) of ... have no code` | Add every label listed to `codes`; `inspect --labels` gives them all. |
| `is not in` (a quote) | A quote is the exact words of its cell (`Sheet!K12`, `p3:l17` on a PDF, `sheet:<name>`). |
| `is an OED field name` | A question in `questions` says it in the words of the schedule: "year built", not YearBuilt. |
| `rows script refused` | The script opens a file or imports a module it may not: it gets the lines as data. |
| `not printed on the lines of their row` | The script took a value from another line, or reformatted it: copy it as printed. |
| `print an amount and are on no row` | A wrapped location: join that line to its row in the script, or give it a row and skip it with a reason. |
| `.xls` or `.ods` file | Not readable here: ask the user to save it as .xlsx. |
| A scanned PDF (`no text`) | No OCR here: ask for the original spreadsheet or a PDF with a text layer. |
