"""The OED 5.0 specification, read from the files bundled in sov2oed/data: fields, code lists, perils, currencies.

Nothing here invents a field or a code; every list comes from the OED spec JSON, OtherValues.csv (both CC0, Oasis
LMF), the ISO 4217 list or the country bounding boxes.
"""

from __future__ import annotations

import csv
import functools
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any

VERSION = "5.0.0"
FILES = {"location": "Loc", "account": "Acc"}
# Codes that mean "unknown": a field coded this way carries no information.
UNKNOWN_CODES = {"OccupancyCode": 1000, "ConstructionCode": 5000}
_NUMERIC = ("int", "float", "decimal", "tinyint", "smallint", "bigint", "bit")


@dataclass(frozen=True)
class Field:
    name: str
    file: str  # "Loc" | "Acc"
    data_type: str
    default: str
    required: bool  # "R" for property
    valid_range: tuple[float | None, float | None] = (None, None)  # (min, max) from "Valid value range"

    @property
    def numeric(self) -> bool:
        return self.data_type.lower().startswith(_NUMERIC)

    @property
    def kind(self) -> str:
        """How a schedule cell is read into this field."""
        n = self.name
        if n in codes():
            return "code"
        if n in ("YearBuilt", "YearUpgraded", "RoofYearBuilt"):
            return "year"
        if n == "NumberOfStoreys":
            return "storeys"
        if n == "PostalCode":
            return "postcode"
        if n == "AreaCode":
            return "area"
        if self.data_type in ("date", "smalldatetime"):
            return "date"
        if self.data_type.startswith(("float", "decimal")):
            return "money" if n.endswith("TIV") or "Limit" in n or "Ded" in n else "float"
        if self.numeric:
            return "int"
        return "text"


def _data(name: str) -> str:
    return resources.files("sov2oed.data").joinpath(name).read_text("utf-8-sig")


@functools.cache
def schema() -> dict[str, Any]:
    return json.loads(_data(f"OpenExposureData_{VERSION}Spec.json"))


@functools.cache
def fields(file: str) -> dict[str, Field]:
    """{lower-case name: Field} of one file, "Loc" or "Acc"."""
    return {
        key: Field(
            name=info["Input Field Name"],
            file=file,
            data_type=str(info.get("Data Type", "")),
            default=str(info.get("Default", "n/a")),
            required=str(info.get("Property field status", "")) == "R",
            valid_range=_range(info.get("Valid value range")),
        )
        for key, info in schema()["input_fields"][file].items()
    }


def _range(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0].get("min"), value[0].get("max")
    return None, None


def field(name: str, file: str = "Loc") -> Field | None:
    """The OED field of that name, case-insensitive; a flexi field (FlexiLocXX) matches its prefix."""
    table = fields(file)
    key = name.strip().lower()
    if key in table:
        return table[key]
    for i in range(1, len(key)):
        for suffix in ("xx", "zzz"):
            if key[:-i] + suffix in table:
                f = table[key[:-i] + suffix]
                return Field(name, f.file, f.data_type, f.default, f.required, f.valid_range)
    return None


@functools.cache
def other_values() -> dict[str, dict[str, str]]:
    """{category: {code: description}} from OtherValues.csv."""
    out: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(_data("OtherValues.csv").splitlines()):
        out.setdefault(row["Category"], {})[row["Code"].strip()] = row["CodeDescription"].strip()
    return out


@functools.cache
def codes() -> dict[str, dict[str, str]]:
    """{coded Location field: {code: label}}: occupancy and construction from the spec, the others from OtherValues."""
    s = schema()
    out = {
        name: {
            c: ", ".join(p for p in (v.get("Category", "").strip(), v.get("Name", "").strip()) if p)
            for c, v in s[key].items()
        }
        for name, key in (("OccupancyCode", "occupancy"), ("ConstructionCode", "construction"))
    }
    values = other_values()
    for f in fields("Loc").values():
        if f.name not in out and f.name in values and f.numeric:
            out[f.name] = dict(values[f.name])
    return out


def is_code(field_name: str, code: Any) -> bool:
    return str(code) in codes().get(field_name, {})


def is_unknown(field_name: str, code: Any) -> bool:
    if code is None:
        return True
    return int(code) == UNKNOWN_CODES.get(field_name, 0)


@functools.cache
def countries() -> frozenset[str]:
    return frozenset(schema()["country"])


@functools.cache
def areas() -> dict[str, frozenset[str]]:
    """{country: its OED AreaCodes}, for the countries the spec lists areas for."""
    return {c: frozenset(a) for c, a in schema()["area"].items()}


@functools.cache
def offshore_countries() -> frozenset[str]:
    return frozenset(c for c, v in schema()["country"].items() if v.get("Inland") == "No")


@functools.cache
def currencies() -> frozenset[str]:
    return frozenset(row["code"] for row in csv.DictReader(_data("iso4217.csv").splitlines()))


@functools.cache
def country_boxes() -> dict[str, list[tuple[float, float, float, float]]]:
    """{country: [(min_lat, max_lat, min_lon, max_lon), ...]}: one box per landmass (Natural Earth 1:50m)."""
    out: dict[str, list[tuple[float, float, float, float]]] = {}
    for r in csv.DictReader(_data("country_boxes.csv").splitlines()):
        box = (float(r["min_lat"]), float(r["max_lat"]), float(r["min_lon"]), float(r["max_lon"]))
        out.setdefault(r["CountryCode"], []).append(box)
    return out


def perils() -> dict[str, str]:
    """{peril code: description}, single and grouped."""
    return {k: v["Peril Description"] for k, v in schema()["perils"]["info"].items()}


@functools.cache
def expand_perils(perils_text: str) -> frozenset[str]:
    """ "WW1;QQ1" -> the elementary perils they cover; unknown codes are dropped (the schema check reports them)."""
    covered = schema()["perils"]["covered"]
    out: set[str] = set()
    for token in perils_text.split(";"):
        out.update(covered.get(token.strip(), []))
    return frozenset(out)


def kind(name: str) -> str | None:
    """How a schedule cell is read into this Location field; None for a pseudo-field or an unknown name."""
    f = field(name)
    return f.kind if f else None
