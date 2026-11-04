"""Read one cell as a typed value, the way values arrive in schedules: "$1,250,000", "1 250,5", "R+2", "c. 1960".

Every parser returns (value, status, note). The status is "declared" when the cell states the value,
"uncertain" when it states it loosely (an age band, a storey range) and "unknown" when it states nothing
usable; the note says why, for the provenance file.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

DECLARED, UNCERTAIN, UNKNOWN = "declared", "uncertain", "unknown"
Parsed = tuple[Any, str, str | None]

BLANKS = {
    "",
    "-",
    "--",
    "—",
    "–",
    "?",
    "n/a",
    "na",
    "n.a.",
    "n/k",
    "nk",
    "nc",
    "n.c.",
    "n/c",
    "nr",
    "#n/a",
    "tbd",
    "tba",
    "unk",
    "unknown",
    "not known",
    "none provided",
    "to be determined",
    "inconnu",
    "inconnue",
    "à préciser",
    "a preciser",
    "non renseigné",
    "non renseigne",
}
MIN_YEAR = 1800
_WS = re.compile(r"\s+")


def text_of(v: Any) -> str:
    """The cell as a person reads it: 1985.0 -> "1985", dates in ISO, text stripped."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time() else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v).strip()


def normalise(s: str) -> str:
    """Case-folded, whitespace-collapsed, typographic quotes made straight: for quotes and label lookups."""
    s = str(s).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", s.replace(" ", " ").replace(" ", " ")).strip().casefold()


def blank(v: Any) -> bool:
    return v is None or normalise(text_of(v)) in BLANKS


# Numbers ---------------------------------------------------------------------------------------------------------

_SPACES = re.compile(r"[\s  ']")
_SYMBOLS = re.compile(r"[$€£]|USD|EUR|GBP", re.I)
_SUFFIX = {"k": 1e3, "m": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9}


def parse_number(v: Any, decimal: str = ".") -> float | None:
    """A float, or None when the cell is not a number.

    `decimal` settles the ambiguous cases: "1,250" is 1250 with "." and 1.25 with ","; "250.000" is 250 with "."
    and 250000 with "," (a dot followed by exactly three digits is a thousands separator there).
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _SPACES.sub("", _SYMBOLS.sub("", str(v)).strip())
    if not s:
        return None
    mult = 1.0
    m = re.fullmatch(r"(-?[\d.,]+)(k|m|mm|bn|b)", s, re.I)
    if m:
        s, mult = m.group(1), _SUFFIX[m.group(2).lower()]
    if not re.fullmatch(r"-?[\d.,]+", s) or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(",", ".")
    elif "," in s:
        thousands = re.fullmatch(r"-?\d{1,3}(,\d{3})+", s)
        s = s.replace(",", "") if thousands and decimal == "." else s.replace(",", ".")
    elif s.count(".") > 1 or (decimal == "," and re.fullmatch(r"-?[1-9]\d{0,2}\.\d{3}", s)):
        s = s.replace(".", "")
    try:
        return float(s) * mult
    except ValueError:
        return None


def fsum(values: Any) -> float | int:
    """A sum with the same bits on every Python: integers stay exact, any float makes it math.fsum."""
    vals = list(values)
    if all(isinstance(v, int) for v in vals):
        return sum(vals)
    return math.fsum(vals)


_ABBREVIATED = re.compile(r"\d\s*(k|m|mm|bn)\s*$", re.I)


def parse_amount(v: Any, decimal: str = ".", multiplier: float = 1.0) -> Parsed:
    """A money or measure cell -> float after the multiplier, rounded to the cent. Zero or less is "unknown"."""
    if blank(v):
        return None, UNKNOWN, None
    n = parse_number(v, decimal)
    if n is None:
        return None, UNKNOWN, f"unreadable number '{text_of(v)}'"
    value = round(n * multiplier, 2)
    note = None
    if isinstance(v, str) and _ABBREVIATED.search(v.strip()):
        note = f"abbreviated amount '{v.strip()}': precision lost in the schedule"
    if value <= 0:
        return value, UNKNOWN, note or (f"negative amount '{text_of(v)}'" if value < 0 else "zero")
    return value, DECLARED, note


def parse_float(v: Any, decimal: str = ".") -> Parsed:
    """A plain number (a coordinate): no multiplier, zero allowed."""
    if blank(v):
        return None, UNKNOWN, None
    n = parse_number(v, decimal)
    return (n, DECLARED, None) if n is not None else (None, UNKNOWN, f"unreadable number '{text_of(v)}'")


def parse_percent(v: Any, decimal: str = ".") -> Parsed:
    """ "50%", "50", 0.5 -> 50.0. A bare number at most 1 is read as a proportion."""
    if blank(v):
        return None, UNKNOWN, None
    s = text_of(v)
    n = parse_number(s.replace("%", ""), decimal)
    if n is None:
        return None, UNKNOWN, f"unreadable percentage '{s}'"
    pct = round(n if "%" in s or n > 1 else n * 100, 4)
    if not 0 <= pct <= 100:
        return pct, UNKNOWN, f"percentage '{s}' out of 0-100"
    return pct, DECLARED, None


# Years, storeys, dates -------------------------------------------------------------------------------------------

_APPROX = re.compile(
    r"(?:c\.?|ca\.?|circa|approx\.?|vers|env\.?|~)\s*(\d{4})|(\d{4})\s*'?s|(\d{4})\s*\(?(?:approx|est)\.?\)?", re.I
)
_BAND = re.compile(r"(pre(?![a-z])-?|before|avant|<|post(?![a-z])-?|after|since|depuis|apr[eè]s|>)\s*(\d{4})", re.I)
_FIRST_OF = re.compile(r"(\d{4})(?:\.0)?\s*(?:[/\-–;,&(]|reno|rénov|renov).*", re.I)


def _year(y: int, effective_year: int, status: str = DECLARED, note: str | None = None) -> Parsed:
    if MIN_YEAR <= y <= effective_year:
        return y, status, note
    return None, UNKNOWN, f"year {y} out of range {MIN_YEAR}-{effective_year}"


def parse_year(v: Any, effective_year: int) -> Parsed:
    """1985, "1985.0", a date, "c. 1960", "1960s", "Pre 1919"; "1985/2004" or "1985 reno 2004" gives 1985."""
    if blank(v):
        return None, UNKNOWN, None
    if isinstance(v, (dt.date, dt.datetime)):
        return _year(v.year, effective_year)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if float(v).is_integer():
            return _year(int(v), effective_year)
        return None, UNKNOWN, f"unreadable year '{text_of(v)}'"
    s = text_of(v)
    if re.fullmatch(r"\d{4}(?:\.0)?", s):
        return _year(int(s[:4]), effective_year)
    date, status, _ = parse_date(s)
    if date and status == DECLARED:
        return _year(int(date[:4]), effective_year)
    if b := _BAND.fullmatch(s):
        return _year(int(b.group(2)), effective_year, UNCERTAIN, f"year band '{s}': {b.group(2)} kept")
    if a := _APPROX.fullmatch(s):
        y = int(next(g for g in a.groups() if g))
        return _year(y, effective_year, UNCERTAIN, f"approximate year '{s}'")
    if f := _FIRST_OF.fullmatch(s):
        return _year(int(f.group(1)), effective_year, DECLARED, f"first year of '{s}' kept")
    return None, UNKNOWN, f"unreadable year '{s}'"


_STOREY_WORDS = r"(?:FLOORS?|STOR(?:E?Y|IES|EYS)|NIVEAUX?|ÉTAGES?|ETAGES?|LEVELS?)"


def parse_storeys(v: Any) -> Parsed:
    """ "3", "2-3", "R+2", "RDC", "3 floors" -> storeys above ground."""
    if blank(v):
        return None, UNKNOWN, None
    s = text_of(v).upper().replace(" ", "")
    n = parse_number(s)
    if n is not None:
        if n >= 1:
            note = None if float(n).is_integer() else f"fractional storeys '{text_of(v)}', integer part kept"
            return int(n), DECLARED if note is None else UNCERTAIN, note
        return None, UNKNOWN, f"storeys '{text_of(v)}' below 1"
    if m := re.fullmatch(r"(\d+)[-–/](\d+)", s):
        return int(m.group(2)), UNCERTAIN, f"storey range '{text_of(v)}', upper value kept"
    if s.startswith("RDC") or s == "R":
        return 1, DECLARED, None
    if m := re.search(r"R\+(\d+)", s):
        return int(m.group(1)) + 1, DECLARED, None
    if (m := re.fullmatch(rf"(\d+){_STOREY_WORDS}", s)) and int(m.group(1)) >= 1:
        return int(m.group(1)), DECLARED, None
    return None, UNKNOWN, f"unreadable storeys '{text_of(v)}'"


def parse_date(v: Any, decimal: str = ".") -> Parsed:
    """A date cell, ISO text, "12/03/2025" (month first in files with a decimal point, else day first) -> ISO."""
    if blank(v):
        return None, UNKNOWN, None
    if isinstance(v, dt.datetime):
        return v.date().isoformat(), DECLARED, None
    if isinstance(v, dt.date):
        return v.isoformat(), DECLARED, None
    s = text_of(v)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s):
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)), s)
    if m := re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})", s):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + (2000 if y < 70 else 1900) if y < 100 else y
        month, day = (a, b) if decimal == "." else (b, a)
        return _iso(y, month, day, s)
    return None, UNKNOWN, f"unreadable date '{s}'"


def _iso(y: int, month: int, day: int, s: str) -> Parsed:
    try:
        return dt.date(y, month, day).isoformat(), DECLARED, None
    except ValueError:
        return None, UNKNOWN, f"unreadable date '{s}'"


# Places ----------------------------------------------------------------------------------------------------------


def parse_postcode(v: Any, country: str) -> Parsed:
    """Postcode as text; US and French postcodes that lost their leading zero are padded."""
    if blank(v):
        return None, UNKNOWN, None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer():
        pc = str(int(v))
    else:
        pc = text_of(v).removesuffix(".0")
    if country in ("US", "FR") and pc.isdigit() and len(pc) < 5:
        return pc.zfill(5), DECLARED, f"postcode '{pc}' padded to {pc.zfill(5)}"
    if country == "GB":
        pc = _WS.sub(" ", pc.upper())
    return pc, DECLARED, None


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico",
}  # fmt: skip
_STATE_NAMES = {normalise(n): c for c, n in US_STATES.items()}


def parse_area_code(v: Any, country: str) -> Parsed:
    """A US state written as its code or its name -> its code; elsewhere the text as it is."""
    if blank(v):
        return None, UNKNOWN, None
    s = " ".join(text_of(v).split())
    if country != "US":
        return s, DECLARED, None
    code = s.upper() if s.upper() in US_STATES else _STATE_NAMES.get(normalise(s))
    return (code, DECLARED, None) if code else (None, UNKNOWN, f"'{s}' is not a US state")


def parse_text(v: Any) -> Parsed:
    if blank(v):
        return None, UNKNOWN, None
    return " ".join(text_of(v).split()), DECLARED, None
