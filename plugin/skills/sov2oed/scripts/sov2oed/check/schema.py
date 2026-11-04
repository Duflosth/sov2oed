"""The OED schema checks: what ods_tools, the reference OED library, would refuse or silently change.

Required fields, numbers that are not numbers, values outside the spec range, codes not in their list, country
and area codes, peril codes, currencies, locations whose account is not in the account file. Each check flags
location rows (an account-file problem flags every location of that account); rule LINK-009 scores them.
Values outside the range of a field another rule owns (coordinates, storeys, year, TIVs) are left to that rule.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..oed import spec
from .load import Portfolio

OWNED_ELSEWHERE = {
    "Latitude",
    "Longitude",
    "NumberOfStoreys",
    "YearBuilt",
    "BuildingTIV",
    "OtherTIV",
    "ContentsTIV",
    "BITIV",
}
OWN_CODE_RULES = {"OccupancyCode", "ConstructionCode"}  # WHAT-002 and WHAT-001
PERIL_FIELDS = {"Loc": ("LocPerilsCovered", "LocPeril"), "Acc": ("PolPerilsCovered", "PolPeril", "AccPeril")}
NON_MONETARY = {"XXX", "XTS", "XAU", "XAG", "XPD", "XPT"}  # ISO 4217 codes that cannot denominate a sum insured
_INT_TYPES = ("int", "tinyint", "smallint", "bigint", "bit")


@dataclass
class Schema:
    flags: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))  # sub-code -> location rows
    lost: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))  # field -> rows not readable
    details: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))  # sub-code -> what was found


def _number(text: str, f: spec.Field) -> float | None:
    """The value, or None when it is not a number of the field's type (2.5 storeys, "1,000")."""
    try:
        n = float(text)
    except ValueError:
        return None
    if f.data_type.lower().startswith(_INT_TYPES) and not n.is_integer():
        return None
    return n


def account_key(row: dict[str, str]) -> tuple[str, str]:
    return row.get("PortNumber", ""), row.get("AccNumber", "")


def check(p: Portfolio) -> Schema:
    out = Schema()
    locs = p.locations
    by_account: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, row in enumerate(locs):
        by_account[account_key(row)].append(i)
    present = set(p.columns)

    def flag(code: str, rows: Iterable[int], detail: str) -> None:
        rows = set(rows)
        if rows:
            out.flags[code].update(rows)
            if detail not in out.details[code]:
                out.details[code].append(detail)

    for req in spec.fields("Loc").values():
        if req.required and req.name not in present:
            flag("required_fields", range(len(locs)), f"{req.name} column missing")
    for i, row in enumerate(locs):
        for name, text in row.items():
            if (f := spec.field(name)) is None:
                continue
            if f.numeric:
                n = _number(text, f)
                if n is None:
                    out.lost[f.name].add(i)
                    if f.name not in ("Latitude", "Longitude"):
                        flag("not_a_number", [i], f"{f.name} '{text[:30]}'")
                    continue
                lo, hi = f.valid_range
                if f.name not in OWNED_ELSEWHERE and ((lo is not None and n < lo) or (hi is not None and n > hi)):
                    flag("valid_values", [i], f"{f.name} {text} outside {lo}..{hi}")
                if f.name in spec.codes() and f.name not in OWN_CODE_RULES and not spec.is_code(f.name, int(n)):
                    flag("invalid_code", [i], f"{f.name} {text} is not an OED code")
        for f in spec.fields("Loc").values():
            if f.required and f.name in present and not row.get(f.name):
                flag("required_fields", [i], f"{f.name} blank")
        country, area = row.get("CountryCode", ""), row.get("AreaCode", "")
        if country and country not in spec.countries():
            flag("invalid_country", [i], f"CountryCode {country}")
        elif area and country in spec.areas() and area not in spec.areas()[country]:
            flag("invalid_area", [i], f"AreaCode {area} in {country}")
        for name in PERIL_FIELDS["Loc"]:
            if (perils := row.get(name)) and any(t.strip() not in spec.perils() for t in perils.split(";")):
                flag("perils", [i], f"{name} {perils}")
        cur = row.get("LocCurrency", "").upper()
        if cur and (cur not in spec.currencies() or cur in NON_MONETARY):
            flag("currency", [i], f"LocCurrency {cur}")
    if p.has_account:
        accounts = {account_key(a) for a in p.accounts}
        flag("orphan_location", [i for k, rows in by_account.items() if k not in accounts for i in rows],
             "location whose account is not in the account file")  # fmt: skip
        for a in p.accounts:
            rows = by_account.get(account_key(a), [])
            for f in spec.fields("Acc").values():
                if f.required and not a.get(f.name):
                    flag("required_fields", rows, f"account {f.name} blank")
            for name in PERIL_FIELDS["Acc"]:
                if (perils := a.get(name)) and any(t.strip() not in spec.perils() for t in perils.split(";")):
                    flag("perils", rows, f"{name} {perils}")
            cur = a.get("AccCurrency", "").upper()
            if cur and (cur not in spec.currencies() or cur in NON_MONETARY):
                flag("currency", rows, f"AccCurrency {cur}")
    if p.unknown_columns:
        out.details["unknown_column"] = list(p.unknown_columns)
    return out
