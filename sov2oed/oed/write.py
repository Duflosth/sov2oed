"""Write the locations as OED 5.0 files: location.csv, account.csv, and provenance.csv beside them.

A field is written only when the file states it: unknown values stay blank, so an OED default is never written as
if declared. The broker's own labels go to OrgOccupancyCode and OrgConstructionCode. A row that is not a building
(playground equipment, a generator) is written with its value as OtherTIV and its limit as LocLimit2Other. The
perils are those the plan cites; with none, AA1 (all perils), marked assumed in the provenance file.

The CSV files are often opened in a spreadsheet: a text that starts with = + - @ would run there as a formula, so
it is written with a leading apostrophe.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

from .. import parse
from ..extract import ASSUMED, Extraction, Location
from . import spec

ACC_NUMBER_MAX = 40
FLEXI_MAX = 40  # FlexiLocZZZ is varchar(40)
AREA_UNIT = {"sqft": 11, "sqm": 12}  # OED FloorAreaUnit (OtherValues: Units)
REQUIRED = ("PortNumber", "AccNumber", "LocNumber", "CountryCode", "LocPerilsCovered", "LocCurrency")
TIV = ("BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV")
LIMIT_TYPES = {
    "LocLimit1Building": "LocLimitType1Building",
    "LocLimit2Other": "LocLimitType2Other",
    "LocLimit3Contents": "LocLimitType3Contents",
    "LocLimit4BI": "LocLimitType4BI",
    "LocLimit6All": "LocLimitType6All",
}
NOT_A_BUILDING = {"BuildingTIV": "OtherTIV", "LocLimit1Building": "LocLimit2Other"}
ORIGINAL = {"OccupancyCode": "OrgOccupancyCode", "ConstructionCode": "OrgConstructionCode"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
PROVENANCE = {parse.DECLARED: "declared", parse.UNCERTAIN: "declared", "derived": "derived", ASSUMED: "assumed"}


def acc_number(ex: Extraction) -> str:
    """The insured's name when it fits in AccNumber, else its start and a hash; never a name silently cut."""
    s = ex.plan.insured
    name = " ".join(str((s.value or s.quote) if s else "").split())
    if not name:
        return "1"
    if len(name) <= ACC_NUMBER_MAX:
        return name
    return f"{name[:31].rstrip()}~{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"


def perils(ex: Extraction) -> str:
    return str(ex.plan.account.perils.value)


def _typed(name: str, value: Any) -> Any:
    info = spec.field(name)
    kind = info.kind if info else "text"
    if kind in ("code", "int", "year", "storeys"):
        return int(value)
    if kind in ("money", "float"):
        return float(value)
    text = " ".join(str(value).split())
    return text[:FLEXI_MAX] if name.startswith("FlexiLoc") else text


def location_row(loc: Location, ex: Extraction) -> dict[str, Any]:
    plan = ex.plan
    row: dict[str, Any] = {
        "PortNumber": "1",
        "AccNumber": acc_number(ex),
        "LocNumber": loc.get("LocNumber") or loc.id,
        "CountryCode": loc.get("CountryCode") or plan.country.value,
        "LocPerilsCovered": perils(ex),
        "LocCurrency": plan.currency.value,
    }
    for name, v in loc.values.items():
        if v.status == parse.UNKNOWN or v.value is None or name in ("LocNumber", "CountryCode"):
            continue
        row[NOT_A_BUILDING.get(name, name) if not loc.building else name] = _typed(name, v.value)
    table = next(t for t in plan.tables if t.id == loc.table)
    for name, org in ORIGINAL.items():
        cols = [col for col, c in table.columns.items() if name in c.fields]
        label = " ".join(parse.text_of(loc.raw.get(col)) for col in cols).strip()
        if label:
            row[org] = label[:100]
    if "FloorArea" in row and table.area_unit:
        row["FloorAreaUnit"] = AREA_UNIT[table.area_unit]
    limits = [col for col in LIMIT_TYPES if col in row]
    if limits:  # OED requires the peril and the type of a location limit: every covered peril, an amount
        row["LocPeril"] = row["LocPerilsCovered"]
        for col in limits:
            row[LIMIT_TYPES[col]] = 0
    for col in TIV:
        row.setdefault(col, 0.0)
    return row


def _columns(rows: list[dict[str, Any]], ex: Extraction) -> list[str]:
    """Required keys, TIVs, then the plan's fields in its order, then what the writer adds."""
    order = [*REQUIRED, *TIV]
    for t in ex.plan.tables:
        for c in t.columns.values():
            order += [f for f in c.fields if f not in order]
    order += [f.field for f in ex.plan.facts if f.field not in order]
    order += [f for f in ex.plan.defaults if f not in order]
    order += [*NOT_A_BUILDING.values(), *ORIGINAL.values(), "FloorAreaUnit", "LocPeril", *LIMIT_TYPES.values()]
    present = {k for r in rows for k in r}
    return [c for c in dict.fromkeys(order) if c in present] + sorted(present - set(order))


def provenance_rows(ex: Extraction) -> list[dict[str, Any]]:
    """One row per location and OED field written: how the value was obtained, and the cell it comes from."""
    acc = acc_number(ex)
    p = ex.plan.account.perils
    out = []
    for loc in ex.locations:
        number = loc.get("LocNumber") or loc.id
        base = {"PortNumber": "1", "AccNumber": acc, "LocNumber": number}
        out.append({**base, "OEDField": "LocPerilsCovered", "Provenance": "assumed" if p.assumed else "derived",
                    "Source": p.cell or "", "Note": "no peril stated: all perils" if p.assumed else f"quoted: {p.quote}"})  # fmt: skip
        if "LocNumber" not in loc.values:
            out.append({**base, "OEDField": "LocNumber", "Provenance": "derived", "Source": loc.ref,
                        "Note": "row of the location in the file"})  # fmt: skip
        for name, v in loc.values.items():
            if v.status == parse.UNKNOWN or v.value is None:
                continue
            target = NOT_A_BUILDING.get(name, name) if not loc.building else name
            note = "; ".join(x for x in (v.note, f"basis: {loc.basis[name]}" if name in loc.basis else None) if x)
            out.append(
                {**base, "OEDField": target, "Provenance": PROVENANCE[v.status], "Source": v.cell or "", "Note": note}
            )
    return out


def account_row(ex: Extraction) -> dict[str, Any]:
    plan = ex.plan
    row: dict[str, Any] = {
        "PortNumber": "1",
        "AccNumber": acc_number(ex),
        "AccCurrency": plan.currency.value,
        "PolNumber": "1",
        "PolPerilsCovered": perils(ex),
        "PolPeril": perils(ex),
    }
    if plan.insured:
        row["AccName"] = " ".join(str(plan.insured.value or plan.insured.quote).split())[:100]
    for term in plan.account.terms:
        info = spec.field(term.field, "Acc")
        row[info.name if info else term.field] = term.value
    return row


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return str(v)


def write_csv(rows: list[dict[str, Any]], columns: list[str], path: Path) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        w.writerows([_cell(r.get(c)) for c in columns] for r in rows)
    return path


def write(ex: Extraction, folder: str | Path) -> dict[str, Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = [location_row(loc, ex) for loc in ex.locations]
    acc = account_row(ex)
    prov = provenance_rows(ex)
    return {
        "location": write_csv(rows, _columns(rows, ex) if rows else [*REQUIRED, *TIV], folder / "location.csv"),
        "account": write_csv([acc], list(acc), folder / "account.csv"),
        "provenance": write_csv(prov, ["PortNumber", "AccNumber", "LocNumber", "OEDField", "Provenance", "Source", "Note"], folder / "provenance.csv"),
    }  # fmt: skip
