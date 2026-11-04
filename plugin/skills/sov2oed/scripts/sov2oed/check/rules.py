"""The quality rules: 16 checks of an OED location file, in four families, each flagging rows.

Every threshold below is a judgment of version 1 unless a source is given; they are published to be challenged.
A rule says which rows it evaluated and which it flags, by sub-code; `score` weighs them by insured value.
Rows whose field is not a number (the schema check's "not_a_number") leave the rules on that field.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..oed import spec
from .load import Portfolio, number
from .schema import Schema, account_key

CRITICAL, WARNING, INFO = "critical", "warning", "info"
TIV = ("BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV")
BLANKS = {"", "n/a", "na", "#n/a", "tbd", "tba", "-", "--", "?", "null", "none", "nan"}
# OED construction codes of buildings (the rest are vehicles, equipment, crops, marine...): 5000-5399, 5951, 5959.
BUILDING_CONSTRUCTION = ((5000, 5399), (5951, 5951), (5959, 5959))
# Source: OED 5.0.0 construction list, codes whose name contains "Unknown" (5304, unknown glass, excepted).
UNKNOWN_CONSTRUCTION = {5000, 5400, 5550, 5600, 5650, 5850, 5870, 6000, 6100, 6150, 7000, 7500, 8000, 8010, 8030,
                        8050, 8070, 8090, 8100, 8110, 8130, 8300, 8400}  # fmt: skip
UNKNOWN_OCCUPANCY = {1000, 2000, 3000}  # Source: OED 5.0.0 occupancy list (Unknown, IFM Unknown, Offshore Unknown)
GENERIC_OCCUPANCY = {1050, 1100, 1150, 1256, 1305, 1400, 2651}  # a code for a whole category
CATEGORY_CONSTRUCTION = {5050, 5100, 5150, 5200}  # frame, masonry, reinforced concrete, steel: class-level codes
PLACEHOLDER_ADDRESS = {"various", "all locations", "unscheduled", "blanket", "tbd", "tba", "to be advised", "divers",
                       "tous sites", "à préciser", "a preciser", "non communiqué"}  # fmt: skip
BOX_MARGIN_DEG = 0.5  # country boxes (Natural Earth 1:50m admin-0) are widened by this on every side
PLACEHOLDER_YEARS = {1, 99, 999, 9999}
MIN_YEAR = 1000
MAX_STOREYS = 170  # Source: CTBUH (accessed 24 Oct 2026): Burj Khalifa 163 floors above ground, Jeddah Tower 167
STOREY_SENTINEL, SENTINEL_MIN_ROWS, SENTINEL_MIN_SHARE = 99, 3, 0.01
AREA_M2 = {11: 0.09290304, 12: 1.0, 13: 4046.8564224, 14: 10000.0}  # FloorAreaUnit -> m²; blank is 11 (sq ft)
MIN_AREA_M2, MAX_AREA_M2 = 10, 1_000_000
VALUE_PER_M2 = {"USD": (100, 20000), "EUR": (100, 20000), "GBP": (100, 20000), "CHF": (100, 20000)}  # outer bounds
OUTLIER_LOG10, OUTLIER_MIN_GROUP = 2, 10  # 100 times the median of at least 10 comparable rows
DEFAULT_MIN_ROWS, DEFAULT_MIN_SHARE, DEFAULT_MIN_GROUPS, DEFAULT_MIN_ADDRESSES = 20, 0.8, 3, 5
REPEATED_DIGITS, TRIPLET_MIN_ADDRESSES = 6, 5
ROUND_MAX_DIGITS, ROUND_MIN_ROWS, ROUND_MIN_SHARE, ROUND_MAX_DISTINCT = 2, 20, 0.8, 0.1
NOT_REPLACEMENT = {"market", "actual_cash", "limit", "agreed"}
# Secondary attributes per peril (OED field sets); a peril is flagged when every field of its set is unknown.
SECONDARY = {
    "wind_tc": ("RoofCover", "RoofGeometry", "RoofAnchorage", "RoofDeckAttachment", "WindowProtection"),
    "wind": ("RoofCover", "RoofGeometry"),
    "flood": ("FirstFloorHeight", "Basement", "FloodDefenseHeight"),
    "earthquake": ("SoftStory", "FoundationType", "Retrofit"),
    "fire": ("SprinklerType", "PercentSprinklered"),
    "hail": ("RoofCover",),
}
PERIL_CODES = {"wind": {"WTC", "WEC", "XSL", "XTD", "ZST"}, "flood": {"ORF", "OSF", "WSS"},
               "earthquake": {"QEQ", "QLS", "QLF", "QTS"}, "fire": {"BFR", "BBF", "BSK"}, "hail": {"XHL"}}  # fmt: skip
BROAD_GROUPS = ((1050, 1099, "residential"), (1100, 1149, "commercial"), (1150, 1199, "industrial"),
                (2001, 2799, "industrial"), (1200, 1249, "public"), (1250, 1349, "transport and utilities"),
                (1350, 1399, "agriculture"))  # fmt: skip

TITLES = {
    "WHERE-001": "Location cannot be placed",
    "WHERE-002": "Invalid coordinates",
    "WHAT-001": "Construction unknown",
    "WHAT-002": "Occupancy unknown",
    "WHAT-003": "Number of storeys missing",
    "WHAT-006": "Year built missing",
    "WHAT-007": "Impossible year or storeys",
    "WHAT-008": "Floor area missing",
    "WHAT-011": "Default or repeated values",
    "WHAT-012": "Peril attributes unknown",  # the report names the peril of each sub-code
    "VALUE-001": "Zero or missing value",
    "VALUE-002": "Value per area out of range",
    "VALUE-006": "Value 100 times off comparable buildings",
    "VALUE-009": "Value basis is not a replacement value",
    "LINK-002": "Duplicate locations",
    "LINK-009": "OED schema",
}
# (rule, sub-code) -> (severity, weight 0-3). One weight per sub-code, how much it matters for a loss model: the
# mean, rounded, of the five peril weights of sov2oed 0.x (wind, flood, earthquake, fire, hail).
SEVERITY: dict[tuple[str, str], tuple[str, int]] = {
    ("WHERE-001", "placeholder_text"): (WARNING, 2), ("WHERE-001", "country_level_only"): (CRITICAL, 2),
    ("WHERE-001", "no_street"): (WARNING, 2), ("WHERE-001", "no_coordinates"): (WARNING, 2),
    **{("WHERE-002", s): (CRITICAL, 3) for s in ("not_a_number", "null_island", "out_of_range", "swapped", "outside_country")},
    **{("WHAT-001", s): (WARNING, 3) for s in ("missing", "unknown", "invalid")},
    **{("WHAT-002", s): (WARNING, 2) for s in ("missing", "unknown", "invalid")}, ("WHAT-002", "generic"): (INFO, 1),
    ("WHAT-003", "missing"): (WARNING, 2), ("WHAT-003", "band"): (INFO, 2),
    ("WHAT-006", ""): (WARNING, 2),
    **{("WHAT-007", s): (CRITICAL, 2) for s in ("placeholder", "too_old", "future", "secondary_year", "storeys_above_max", "storeys_below_min")},
    ("WHAT-007", "storeys_sentinel"): (WARNING, 2),
    ("WHAT-008", "missing"): (INFO, 1), ("WHAT-008", "implausible"): (WARNING, 1),
    **{("WHAT-011", s): (WARNING, 2) for s in ("ConstructionCode", "YearBuilt", "FloorArea", "RoofCover", "repeated_digit", "repeated_triplet")},
    ("WHAT-011", "round_share"): (INFO, 2),
    **{("WHAT-012", s): (INFO, 2) for s in ("wind", "flood", "earthquake", "fire", "hail")},
    **{("VALUE-001", s): (CRITICAL, 3) for s in ("zero_or_missing", "negative")},
    ("VALUE-002", "too_low"): (WARNING, 2), ("VALUE-002", "too_high"): (WARNING, 2),
    ("VALUE-006", ""): (WARNING, 2),
    **{("VALUE-009", s): (WARNING, 2) for s in NOT_REPLACEMENT},
    ("LINK-002", "same_key"): (CRITICAL, 3), ("LINK-002", "same_content"): (WARNING, 2),
    ("LINK-009", "required_fields"): (CRITICAL, 2), ("LINK-009", "not_a_number"): (CRITICAL, 2),
    ("LINK-009", "invalid_country"): (CRITICAL, 3), ("LINK-009", "invalid_area"): (INFO, 1),
    ("LINK-009", "perils"): (CRITICAL, 3), ("LINK-009", "currency"): (CRITICAL, 3),
    ("LINK-009", "orphan_location"): (CRITICAL, 3), ("LINK-009", "valid_values"): (WARNING, 1),
    ("LINK-009", "invalid_code"): (WARNING, 1),
}  # fmt: skip


@dataclass
class Result:
    rule: str
    sub: str
    rows: set[int]
    evaluated: set[int]
    details: list[str] = field(default_factory=list)

    @property
    def family(self) -> str:
        return "secondary" if self.rule == "WHAT-012" else self.rule.split("-")[0]

    @property
    def severity(self) -> str:
        return SEVERITY[(self.rule, self.sub)][0]

    @property
    def weight(self) -> int:
        return SEVERITY[(self.rule, self.sub)][1]


class Context:
    """What several rules read: numbers, codes, which rows are buildings, perils, addresses, areas."""

    def __init__(self, p: Portfolio, schema: Schema):
        self.p, self.schema = p, schema
        self.rows = p.locations
        self.all = set(range(len(self.rows)))
        self.tiv = [sum(self.num(i, f) or 0.0 for f in TIV) for i in self.all_ordered]
        self.other_only = {
            i for i in self.all if (self.num(i, "OtherTIV") or 0) > 0 and self.tiv[i] == self.num(i, "OtherTIV")
        }
        self.building = {i for i in self.all if self._building_code(i) and i not in self.other_only}
        self.aggregate = {
            i for i in self.all if self.num(i, "IsAggregate") == 1 or (self.num(i, "NumberOfBuildings") or 0) > 1
        }

    @property
    def all_ordered(self) -> range:
        return range(len(self.rows))

    def text(self, i: int, name: str) -> str:
        return self.rows[i].get(name, "")

    def blank(self, i: int, name: str) -> bool:
        return self.text(i, name).strip().lower() in BLANKS

    def num(self, i: int, name: str) -> float | None:
        return None if i in self.schema.lost.get(name, ()) else number(self.rows[i], name)

    def code(self, i: int, name: str) -> int | None:
        n = self.num(i, name)
        return int(n) if n is not None and n.is_integer() else None

    def unknown(self, i: int, name: str) -> bool:
        """Blank, or the OED default of the field (0 or -999): the file says nothing."""
        n = self.num(i, name)
        return self.blank(i, name) or n in (0, -999)

    def lost(self, *names: str) -> set[int]:
        return set().union(*(self.schema.lost.get(n, set()) for n in names))

    def _building_code(self, i: int) -> bool:
        c = self.code(i, "ConstructionCode")
        c = 5000 if c is None else c
        return any(lo <= c <= hi for lo, hi in BUILDING_CONSTRUCTION) or not spec.is_code("ConstructionCode", c)

    def covers(self, i: int, peril: str) -> bool:
        """Whether the row's perils include this family; a row with no valid perils counts for all."""
        codes = spec.expand_perils(self.text(i, "LocPerilsCovered"))
        return not codes or bool(codes & PERIL_CODES[peril])

    def area_m2(self, i: int) -> float | None:
        a = self.num(i, "FloorArea")
        unit = self.code(i, "FloorAreaUnit")
        factor = AREA_M2.get(11 if unit is None and self.blank(i, "FloorAreaUnit") else unit or -1)
        return a * factor if a and a > 0 and factor else None

    def group(self, i: int) -> str:
        c = self.code(i, "OccupancyCode") or 1000
        return next((name for lo, hi, name in BROAD_GROUPS if lo <= c <= hi), "other")

    def name(self, i: int) -> str:
        """How a detail names a row: its location number, else its row in the file."""
        return f"location {n}" if (n := self.text(i, "LocNumber")) else f"row {i + 1}"

    def address(self, i: int) -> str:
        parts = [self.text(i, f) for f in ("PostalCode", "City", "StreetAddress")]
        if not any(p.strip().lower() not in BLANKS for p in parts):
            return ""
        key = "|".join([self.text(i, "CountryCode"), *parts]).lower()
        return re.sub(r"\s+", " ", re.sub(r"[^\w|]+", " ", key)).strip()


def _results(
    rule: str, evaluated: set[int], subs: dict[str, set[int]], details: dict[str, list[str]] | None = None
) -> list[Result]:
    return [Result(rule, s, rows & evaluated, evaluated, (details or {}).get(s, [])) for s, rows in subs.items()]


# WHERE -----------------------------------------------------------------------------------------------------------


def where_001(c: Context) -> list[Result]:
    placeholder = {
        i
        for i in c.all
        if any(c.text(i, f).strip().lower() in PLACEHOLDER_ADDRESS for f in ("StreetAddress", "LocName", "City"))
    }
    no_coords = {i for i in c.all if c.blank(i, "Latitude") or c.blank(i, "Longitude")}
    address = {i for i in c.all if any(not c.blank(i, f) for f in ("StreetAddress", "PostalCode", "City", "AreaCode"))}
    no_street = {i for i in no_coords & address if c.blank(i, "StreetAddress")} - placeholder
    return _results("WHERE-001", c.all, {
        "placeholder_text": placeholder,
        "country_level_only": no_coords - address - placeholder,
        "no_street": no_street,
        "no_coordinates": (no_coords & address) - placeholder - no_street,
    })  # fmt: skip


def _inside(country: str, lat: float, lon: float) -> bool | None:
    boxes = spec.country_boxes().get(country)
    if not boxes:
        return None
    m = BOX_MARGIN_DEG
    return any(
        lo_lat - m <= lat <= hi_lat + m and lo_lon - m <= lon <= hi_lon + m for lo_lat, hi_lat, lo_lon, hi_lon in boxes
    )


def where_002(c: Context) -> list[Result]:
    evaluated = {i for i in c.all if not (c.blank(i, "Latitude") and c.blank(i, "Longitude"))}
    subs: dict[str, set[int]] = defaultdict(set)
    for i in evaluated:
        lat, lon = c.num(i, "Latitude"), c.num(i, "Longitude")
        if lat is None or lon is None:
            subs["not_a_number"].add(i)
        elif lat == 0 and lon == 0:
            subs["null_island"].add(i)
        elif abs(lat) > 90 and abs(lon) <= 90:
            subs["swapped"].add(i)
        elif abs(lat) > 90 or abs(lon) > 180:
            subs["out_of_range"].add(i)
        else:
            country = c.text(i, "CountryCode").upper()
            if country in spec.offshore_countries() or country in ("XW", ""):
                continue
            inside = _inside(country, lat, lon)
            if inside is False:
                subs["swapped" if _inside(country, lon, lat) else "outside_country"].add(i)
    return _results("WHERE-002", evaluated, dict(subs))


# WHAT ------------------------------------------------------------------------------------------------------------


def _coded(c: Context, name: str, unknown: set[int], evaluated: set[int]) -> dict[str, set[int]]:
    missing = {i for i in evaluated if c.blank(i, name)}
    unk = {i for i in evaluated - missing if c.code(i, name) in unknown}
    invalid = {i for i in evaluated - missing - unk if not spec.is_code(name, c.code(i, name))}
    return {"missing": missing, "unknown": unk, "invalid": invalid}


def what_001(c: Context) -> list[Result]:
    evaluated = c.all - c.lost("ConstructionCode") - c.other_only
    return _results("WHAT-001", evaluated, _coded(c, "ConstructionCode", UNKNOWN_CONSTRUCTION, evaluated))


def what_002(c: Context) -> list[Result]:
    evaluated = c.all - c.lost("OccupancyCode") - c.other_only
    subs = _coded(c, "OccupancyCode", UNKNOWN_OCCUPANCY, evaluated)
    subs["generic"] = {
        i for i in evaluated - set().union(*subs.values()) if c.code(i, "OccupancyCode") in GENERIC_OCCUPANCY
    }
    return _results("WHAT-002", evaluated, subs)


def what_003(c: Context) -> list[Result]:
    evaluated = c.building - c.aggregate - c.lost("NumberOfStoreys")
    return _results("WHAT-003", evaluated, {
        "missing": {i for i in evaluated if c.blank(i, "NumberOfStoreys") or c.num(i, "NumberOfStoreys") == 0},
        "band": {i for i in evaluated if c.num(i, "NumberOfStoreys") in (-1, -2, -3)},
    })  # fmt: skip


def what_006(c: Context) -> list[Result]:
    evaluated = c.building - c.lost("YearBuilt")
    return _results("WHAT-006", evaluated, {"": {i for i in evaluated if c.unknown(i, "YearBuilt")}})


def what_007(c: Context, as_of: int) -> list[Result]:
    years = c.building - c.lost("YearBuilt")
    subs: dict[str, set[int]] = defaultdict(set)
    for i in years:
        y = c.num(i, "YearBuilt")
        if y is None or y == 0:
            continue
        if y in PLACEHOLDER_YEARS:
            subs["placeholder"].add(i)
        elif y < MIN_YEAR:
            subs["too_old"].add(i)
        elif y > as_of and not ((c.num(i, "PercentComplete") or 1) < 1 or not c.blank(i, "CompletionDate")):
            subs["future"].add(i)
        else:
            for other in ("RoofYearBuilt", "YearUpgraded"):
                o = c.num(i, other)
                if o and (o < y or o > as_of):
                    subs["secondary_year"].add(i)
    storeys = c.building - c.lost("NumberOfStoreys")
    for i in storeys:
        s = c.num(i, "NumberOfStoreys")
        if s is not None and s > MAX_STOREYS:
            subs["storeys_above_max"].add(i)
        elif s is not None and s < -3:
            subs["storeys_below_min"].add(i)
    sentinel = {i for i in storeys if c.num(i, "NumberOfStoreys") == STOREY_SENTINEL}
    if len(sentinel) >= SENTINEL_MIN_ROWS and len(sentinel) > SENTINEL_MIN_SHARE * max(len(storeys), 1):
        subs["storeys_sentinel"] = sentinel
    return _results("WHAT-007", years | storeys, dict(subs))


def what_008(c: Context) -> list[Result]:
    evaluated = c.building - c.lost("FloorArea", "FloorAreaUnit")
    missing = {i for i in evaluated if c.unknown(i, "FloorArea")}
    implausible = set()
    for i in evaluated - missing:
        m2 = c.area_m2(i)
        if m2 is not None and (
            (m2 < MIN_AREA_M2 and (c.num(i, "BuildingTIV") or 0) > 0) or (m2 > MAX_AREA_M2 and i not in c.aggregate)
        ):
            implausible.add(i)
    return _results("WHAT-008", evaluated, {"missing": missing, "implausible": implausible})


def _known(c: Context, i: int, name: str, flagged: set[int]) -> float | None:
    if i in flagged:
        return None
    n = c.num(i, name)
    if name == "RoofCover":
        return n if n and spec.is_code("RoofCover", int(n)) else None
    return n if n is not None and n > 0 else None


def what_011(c: Context, earlier: list[Result]) -> list[Result]:
    """One value on most rows of a portfolio that spans many kinds of buildings: a default filled down."""
    flagged: dict[str, set[int]] = defaultdict(set)
    for r in earlier:
        field_name = {
            "WHAT-001": "ConstructionCode",
            "WHAT-006": "YearBuilt",
            "WHAT-007": "YearBuilt",
            "WHAT-008": "FloorArea",
        }.get(r.rule)
        if field_name and not r.sub.startswith("storeys"):
            flagged[field_name] |= r.rows
    subs: dict[str, set[int]] = {}
    details: dict[str, list[str]] = defaultdict(list)
    sites = {c.address(i) or f"row{i}" for i in c.all}
    evaluated: set[int] = set()
    if len(c.rows) >= DEFAULT_MIN_ROWS and len(sites) > 1:
        for name in ("ConstructionCode", "YearBuilt", "FloorArea", "RoofCover"):
            values = {i: v for i in c.all if (v := _known(c, i, name, flagged[name])) is not None}
            evaluated |= set(values)
            if not values:
                continue
            value, n = Counter(values.values()).most_common(1)[0]
            rows = {i for i, v in values.items() if v == value}
            if n / len(c.rows) < DEFAULT_MIN_SHARE or (
                name == "ConstructionCode" and value not in CATEGORY_CONSTRUCTION
            ):
                continue
            spread = (
                len({c.address(i) for i in rows} - {""}) >= DEFAULT_MIN_ADDRESSES
                if name == "FloorArea"
                else len({c.group(i) for i in rows}) >= DEFAULT_MIN_GROUPS
            )
            if spread:
                subs[name] = rows
                details[name].append(f"{name} = {value:g} on {n} rows ({n / len(c.rows):.0%}): suspected default value")
    lost = c.lost(*TIV)
    amounts = c.all - lost
    evaluated |= amounts
    repdigit = {i for i in amounts if any(_repdigit(c.num(i, f)) for f in TIV)}
    triplets: dict[tuple[float, ...], set[int]] = defaultdict(set)
    for i in amounts - repdigit:
        t = tuple(c.num(i, f) or 0.0 for f in ("BuildingTIV", "ContentsTIV", "BITIV"))
        if any(t):
            triplets[t].add(i)
    repeated = set()
    for t, rows in triplets.items():
        addresses = {c.address(i) for i in rows} - {""}
        codes = {c.code(i, "OccupancyCode") for i in rows}, {c.code(i, "ConstructionCode") for i in rows}
        if len(addresses) >= TRIPLET_MIN_ADDRESSES and (len(codes[0]) >= 2 or len(codes[1]) >= 2):
            repeated |= rows
            details["repeated_triplet"].append(f"{t[0]:,.0f} / {t[1]:,.0f} / {t[2]:,.0f} on {len(rows)} rows")
    subs["repeated_digit"], subs["repeated_triplet"] = repdigit, repeated
    if len(amounts) >= ROUND_MIN_ROWS:
        round_rows = {i for i in amounts if c.tiv[i] > 0 and all(_round(c.num(i, f) or 0.0) for f in TIV)}
        total = sum(c.tiv[i] for i in amounts) or 1.0
        distinct = len({c.tiv[i] for i in amounts})
        if sum(c.tiv[i] for i in round_rows) / total >= ROUND_MIN_SHARE and distinct < ROUND_MAX_DISTINCT * len(
            amounts
        ):
            subs["round_share"] = round_rows - repdigit - repeated
            details["round_share"].append(
                f"round amounts hold most of the value; {distinct} distinct values for {len(amounts)} rows"
            )
    return _results("WHAT-011", evaluated, subs, details)


def _repdigit(v: float | None) -> bool:
    if not v or v <= 0 or not float(v).is_integer():
        return False
    s = str(int(v))
    return len(s) >= REPEATED_DIGITS and len(set(s)) == 1


def _round(v: float) -> bool:
    if v == 0:
        return True
    digits = f"{abs(v):.15g}".replace(".", "").lstrip("0").rstrip("0")
    return len(digits) <= ROUND_MAX_DIGITS


def what_012(c: Context) -> list[Result]:
    fields = {f for fs in SECONDARY.values() for f in fs}
    evaluated = c.building - c.lost(*fields)
    subs: dict[str, set[int]] = defaultdict(set)
    for i in evaluated:
        for peril in PERIL_CODES:
            if not c.covers(i, peril):
                continue
            key = (
                "wind_tc"
                if peril == "wind" and "WTC" in spec.expand_perils(c.text(i, "LocPerilsCovered") or "AA1")
                else peril
            )
            if all(c.unknown(i, f) for f in SECONDARY[key]):
                subs[peril].add(i)
    return _results("WHAT-012", evaluated, dict(subs))


# VALUE -----------------------------------------------------------------------------------------------------------


def value_001(c: Context) -> list[Result]:
    evaluated = c.all - c.lost(*TIV)
    zero = {i for i in evaluated if all(not c.num(i, f) for f in TIV)}
    negative = {i for i in evaluated - zero if any((c.num(i, f) or 0) < 0 for f in TIV)}
    return _results("VALUE-001", evaluated, {"zero_or_missing": zero, "negative": negative})


def value_002(c: Context) -> list[Result]:
    evaluated: set[int] = set()
    low: set[int] = set()
    high: set[int] = set()
    details: dict[str, list[str]] = defaultdict(list)
    for i in c.all - c.lost(*TIV, "FloorArea", "FloorAreaUnit") - c.aggregate:
        band = VALUE_PER_M2.get(c.text(i, "LocCurrency").upper())
        b, m2 = c.num(i, "BuildingTIV") or 0.0, c.area_m2(i)
        if not band or b <= 0 or not m2 or c.num(i, "IsTenant") == 1 or not MIN_AREA_M2 <= m2 <= MAX_AREA_M2:
            continue
        evaluated.add(i)
        per_m2 = b / m2
        if per_m2 < band[0] or per_m2 > band[1]:
            (low if per_m2 < band[0] else high).add(i)
            if len(details["too_low" if per_m2 < band[0] else "too_high"]) < 5:
                details["too_low" if per_m2 < band[0] else "too_high"].append(
                    f"{c.name(i)}: {per_m2:,.0f} per m² (band {band[0]:,}-{band[1]:,})"
                )
    return _results("VALUE-002", evaluated, {"too_low": low, "too_high": high}, details)


def value_006(c: Context) -> list[Result]:
    groups: dict[tuple[str, ...], dict[int, float]] = defaultdict(dict)
    for i in c.all - c.lost(*TIV) - c.other_only:
        if c.tiv[i] <= 0:
            continue
        m2 = c.area_m2(i)
        key = ("|".join(account_key(c.rows[i])), c.group(i), c.text(i, "LocCurrency"), "per_m2" if m2 else "total")
        groups[key][i] = c.tiv[i] / m2 if m2 else c.tiv[i]
    evaluated, flagged = set(), set()
    details: list[str] = []
    for values in groups.values():
        if len(values) < OUTLIER_MIN_GROUP:
            continue
        evaluated |= set(values)
        median = statistics.median(values.values())
        for i, v in values.items():
            if abs(math.log10(v / median)) >= OUTLIER_LOG10:
                flagged.add(i)
                if len(details) < 5:
                    details.append(f"{c.name(i)}: {v / median:,.4g} times the median of its group")
    return _results("VALUE-006", evaluated, {"": flagged}, {"": details})


def value_009(c: Context) -> list[Result]:
    """Amounts recorded by the conversion as a market value, an actual cash value, a limit or an agreed value."""
    evaluated, subs = set(), defaultdict(set)
    for i in c.all:
        for f in ("BuildingTIV", "ContentsTIV"):
            basis = c.p.basis(i, f)
            if basis and (c.num(i, f) or 0) > 0:
                evaluated.add(i)
                if basis in NOT_REPLACEMENT:
                    subs[basis].add(i)
    return _results("VALUE-009", evaluated, dict(subs))


# LINK ------------------------------------------------------------------------------------------------------------


def link_002(c: Context) -> list[Result]:
    keys = Counter((*account_key(r), r.get("LocNumber", "")) for r in c.rows)
    same_key = {i for i, r in enumerate(c.rows) if keys[(*account_key(r), r.get("LocNumber", ""))] > 1}
    evaluated = {
        i
        for i in c.all - c.aggregate
        if c.address(i) or (c.num(i, "Latitude") is not None and c.num(i, "Longitude") is not None)
    }
    content: dict[tuple[object, ...], list[int]] = defaultdict(list)
    for i in sorted(evaluated):
        r = c.rows[i]
        content[(*account_key(r), c.address(i), r.get("Latitude"), r.get("Longitude"), r.get("BuildingID"),
                 r.get("LocName", "").lower(), r.get("OccupancyCode"), r.get("ConstructionCode"),
                 *(c.num(i, f) for f in TIV))].append(i)  # fmt: skip
    same_content = set()
    for rows in content.values():
        first = c.rows[rows[0]].get("LocNumber")
        same_content |= {i for i in rows[1:] if c.rows[i].get("LocNumber") != first}
    return [
        Result("LINK-002", "same_key", same_key, c.all),
        Result("LINK-002", "same_content", same_content - same_key, evaluated),
    ]


def link_009(c: Context) -> list[Result]:
    return [
        Result("LINK-009", s, set(rows), c.all, c.schema.details.get(s, [])[:10])
        for s, rows in c.schema.flags.items()
        if (("LINK-009", s) in SEVERITY)
    ]


def run(p: Portfolio, schema: Schema, as_of: int) -> list[Result]:
    c = Context(p, schema)
    out = where_001(c) + where_002(c)
    out += what_001(c) + what_002(c) + what_003(c) + what_006(c) + what_007(c, as_of) + what_008(c)
    out += what_011(c, out) + what_012(c)
    out += value_001(c) + value_002(c) + value_006(c) + value_009(c)
    out += link_002(c) + link_009(c)
    return out
