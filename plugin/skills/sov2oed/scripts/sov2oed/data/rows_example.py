"""The rows script written for the fictional sov_us_gulfmoor.pdf: the page lines in, the rows file out.

`rows(lines)` gets every line of the PDF, each a dict {"ref": "p1:l7", "page": 1, "line": 7, "text": ...,
"words": [[x0, x1, text], ...]}, and returns the header, then one row per location (and per total line), column A
naming the lines the row was read from. The script only computes: no file, no import beyond a few pure modules.

One line per location here. When a location wraps over several lines, join them (a line with no location number
continues the one above, or the amounts sit on the line before it) and name every line: "p3:l12-l13" or
"p3:l12+p3:l14".

    sov2oed rows sov_us_gulfmoor.pdf rows_example.py --out sov_us_gulfmoor.rows.csv
"""

import bisect

# Column starts, from `inspect --page 1`: a word belongs to the column its left edge falls in.
CUTS = [40, 60, 120, 240, 345, 405, 432, 460, 505, 535, 585, 660, 730]
NAMES = ["Loc", "Bldg", "Department", "Description", "Address", "City", "Zip", "Year", "Constr", "SqFt", "Stories",
         "Building", "Contents", "Total"]  # fmt: skip


def cells(line):
    out = {}
    for x0, _, text in line["words"]:
        name = NAMES[bisect.bisect_right(CUTS, x0)]
        out[name] = f"{out[name]} {text}" if name in out else text
    return out


def rows(lines):
    out = [["lines", *NAMES]]
    for line in lines:
        c = cells(line)
        is_location = c.get("Loc", "").isdigit() and c.get("Bldg", "").isdigit()
        is_total = line["text"].startswith(("Subtotal", "GRAND TOTAL"))
        if is_location or is_total:
            out.append([line["ref"], *(c.get(n, "") for n in NAMES)])
    return out
