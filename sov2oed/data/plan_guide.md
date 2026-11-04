# The reading plan

A YAML file that says how to read one schedule. Code reads every value from it; the same file and the same plan
give the same OED files. `inspect --plan-schema` prints its JSON schema.

## What the plan states, and what it never states

- **Structure:** which sheet or pages, which rows, which rows to skip and why, which rows are totals.
- **Mapping:** which column feeds which OED Location field (`StreetAddress`, `BuildingTIV`, `YearBuilt`...).
- **Codes:** which OED code each label of a coded column means (`"Fire Station": 1220`).
- **Values stated once** (the country, the currency, the insured, a city written only in the sheet name, the
  perils, a multiplier): each with `cell` and `quote`, the exact words of that cell, or `assumed: true`.

Never copy an amount, an address or a name from the file into the plan: refer to the column, the row, the cell.

## Keys

| Key | What it holds |
|---|---|
| `file` | `name` and `sha256` of the file (`shasum -a 256 FILE`) |
| `country`, `currency` | `value` (ISO code) with `cell` and `quote`, or `assumed: true` |
| `insured` | `cell` and `quote` of the insured's name (becomes AccNumber and AccName) |
| `other_sheets` | every sheet (or page range `p4-p6`) not read, with what it is: `legend`, `summary`, `auto schedule`... |
| `tables` | one entry per table: below |
| `codes` | `{OED field: {map: {label: code}}}`: every label of every coded column |
| `defaults` | a Location field stated once for every row: `{City: {value, cell, quote}}` |
| `facts` | a value the file states in words for one location: `{cell, field, value, quote}` |
| `account` | `perils` (`{value: WW1, cell, quote}`; absent means AA1, assumed, and the broker is asked) and `terms` (`AccLimit6All`...) |
| `questions` | at most 5 points of this file to ask the broker, each with `text` and `cells`: below |
| `notes` | what you saw and did not express |

## A table

| Key | What it holds |
|---|---|
| `id` | `t1`, `t2`... |
| `sheet` or `rows_file` | a sheet name, or for a PDF the rows file next to the plan (`STEM.rows.csv`) |
| `header` | header row numbers |
| `rows` | `"2-64"`: rows of the sheet or of the rows file |
| `skip` | `{"68-69": signature, "30": section heading}`: rows inside `rows` that are not locations |
| `totals` | `["65"]`: rows printing totals of the table's columns; code checks them against the rows read |
| `decimal` | `"."` (1,250.50) or `","` (1 250,50 and 250.000 for 250000) |
| `multiplier` | `{value: 1000, cell, quote}` when amounts are in thousands; 1 by default |
| `area_unit` | `sqft` or `sqm`, required when FloorArea is mapped |
| `columns` | `{A: {field: StreetAddress}, C: {field: BuildingTIV, basis: replacement}}`; a list of fields reads one column into several (`[LocName, OccupancyCode]`: the name, and its code through `codes`); two columns into one text field are joined (house number and street) |
| `sentinels` | `{C: ["0", "TBC"]}`: values that mean "not known" |
| `placeholders` | `{C: ["100000"]}`: stand-in values; kept in an amount, marked to confirm |
| `non_building` | `{rows: "29-34,37"}` or `{column: B, labels: [Playground Equip]}`: their values go to OtherTIV |

`field` may also be `row_total` (a printed sum of the row's amounts, checked) or `ignore`.

`basis` says what a money column measures: `replacement`, `market`, `actual_cash`, `agreed`, `limit` (a policy
limit, not a value) or `unknown`. A basis other than replacement is asked; one stated as market, actual cash,
agreed or limit also lowers the score.

## PDF tables

A PDF table is read from its rows file, a CSV that `sov2oed rows FILE.pdf SCRIPT.py --out STEM.rows.csv` writes: your
script defines `rows(lines)` and returns the header, then one row per location or total. Column A names the PDF
lines of each row, the other columns hold the values as printed. The plan reads it like a sheet: `rows_file: STEM.rows.csv`,
`header: [1]`, `rows`, `totals`, `columns` by letter from B. Two tables may read one rows file (buildings, then
other structures). Code checks that every value of a row read is printed on its lines, and that no line of those
pages printing an amount is left out of the rows file. A fact on a PDF line (`p3:l14`) belongs to the location
whose row names that line.

## References to cells

`Sheet!B12` (quote the sheet name if it has spaces at the ends: `'Sheet '!B12`), `p3:l12` (a PDF line),
`sheet:<name>` (the sheet name itself states it: "Cedar Crossing ISD Property Schedule"), `file:<name>` (the file name).
A quote must be the exact words of what it names; a cell's comment counts as part of the cell.

## Questions to the broker

Code asks what it can find itself: totals that do not add up, formulas typed over, missing characteristics,
values off their range, which perils are covered when the file does not say, a value basis you marked `unknown`.
Add in `questions` what only a reading of this file shows, at most 5: two insureds in one schedule, a row whose
total is not its building plus contents, two rows that may be one building, flood amounts that are policy limits,
policy dates already past, a building marked vacant.

```yaml
questions:
  - title: Town Hall listed twice
    text: Rows 8 and 9 are both the Town Hall at 1 Harbour Street. One building or two?
    cells:
      - {cell: "Schedule!C8", quote: "Town Hall"}
      - {cell: "Schedule!C9", quote: "Old Town Hall"}
```

- `text`: the question, in English, in the words of a property schedule: never an OED field name or code
  (not "BuildingTIV", not "WW2"). Claude translates the message to the broker.
- `title`: a few words naming the point, shown above the question in the report.
- `cells`: every cell the question is about, each with its exact quote, as for a fact (`p3:l12` on a PDF).
  A quote not in its cell is a plan error, and the question is not asked.
- Never ask what the schedule already states, nor what sov2oed or a cat team does itself: placing an address on
  the map, coordinates, the OED format, peril codes.
