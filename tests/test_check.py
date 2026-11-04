import csv
from pathlib import Path

import pytest

from sov2oed.check import rules, schema, score
from sov2oed.check.load import load

HEADER = ["PortNumber", "AccNumber", "LocNumber", "CountryCode", "LocPerilsCovered", "LocCurrency", "Latitude",
          "Longitude", "StreetAddress", "PostalCode", "OccupancyCode", "ConstructionCode", "YearBuilt",
          "NumberOfStoreys", "FloorArea", "FloorAreaUnit", "BuildingTIV", "ContentsTIV"]  # fmt: skip
GOOD = ["1", "A1", "{n}", "GB", "WW1", "GBP", "51.5", "-0.12", "{n} Kings Road", "SW3 4LX", "1100", "5100", "1990",
        "3", "1000", "12", "2000000", "100000"]  # fmt: skip


def write(tmp_path: Path, rows: list[dict[str, str]], name: str = "location.csv") -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER + sorted({k for r in rows for k in r} - set(HEADER)))
        w.writeheader()
        w.writerows(rows)
    return path


def portfolio(tmp_path: Path, changes: dict[int, dict[str, str]], n: int = 12) -> list[rules.Result]:
    rows = [{h: v.format(n=i + 1) for h, v in zip(HEADER, GOOD, strict=True)} for i in range(n)]
    for i, change in changes.items():
        rows[i].update(change)
    p = load(write(tmp_path, rows))
    return rules.run(p, schema.check(p), 2026)


def flagged(results: list[rules.Result], rule: str, sub: str) -> list[int]:
    return sorted(i for r in results if r.rule == rule and r.sub == sub for i in r.rows)


def test_a_clean_portfolio_scores_a(tmp_path):
    rows = [{h: v.format(n=i + 1) for h, v in zip(HEADER, GOOD, strict=True)} for i in range(12)]
    for i, r in enumerate(rows):
        r["YearBuilt"], r["OccupancyCode"] = str(1950 + 3 * i), ["1102", "1104", "1101"][i % 3]
        r["BuildingTIV"], r["ContentsTIV"] = str(1_500_000 + 37_000 * i), str(90_000 + 1_100 * i)
        r["RoofCover"], r["RoofGeometry"], r["FirstFloorHeight"], r["Basement"] = "1", "1", "1.2", "2"
        r["FoundationType"], r["SprinklerType"], r["SoftStory"] = "2", "2", "1"
    p = load(write(tmp_path, rows))
    sc = schema.check(p)
    s = score.score(p, rules.Context(p, sc), rules.run(p, sc, 2026))
    assert s.grade == "A", [(x.rule, x.sub, len(x.rows)) for x in s.issues]


@pytest.mark.parametrize(
    ("change", "rule", "sub"),
    [
        ({"Latitude": "", "Longitude": ""}, "WHERE-001", "no_coordinates"),
        ({"Latitude": "0", "Longitude": "0"}, "WHERE-002", "null_island"),
        ({"Latitude": "-0.12", "Longitude": "51.5"}, "WHERE-002", "swapped"),
        ({"Latitude": "40.7", "Longitude": "-74.0"}, "WHERE-002", "outside_country"),
        ({"ConstructionCode": "5000"}, "WHAT-001", "unknown"),
        ({"OccupancyCode": ""}, "WHAT-002", "missing"),
        ({"NumberOfStoreys": "0"}, "WHAT-003", "missing"),
        ({"YearBuilt": ""}, "WHAT-006", ""),
        ({"YearBuilt": "2090"}, "WHAT-007", "future"),
        ({"YearBuilt": "-5"}, "WHAT-007", "too_old"),
        ({"NumberOfStoreys": "250"}, "WHAT-007", "storeys_above_max"),
        ({"FloorArea": ""}, "WHAT-008", "missing"),
        ({"BuildingTIV": "0", "ContentsTIV": "0"}, "VALUE-001", "zero_or_missing"),
        ({"BuildingTIV": "2000"}, "VALUE-002", "too_low"),
        ({"LocNumber": "2"}, "LINK-002", "same_key"),
        ({"CountryCode": "UK"}, "LINK-009", "invalid_country"),
        ({"LocPerilsCovered": "WIND"}, "LINK-009", "perils"),
        ({"BuildingTIV": "1,000,000"}, "LINK-009", "not_a_number"),
    ],
)
def test_each_rule_flags_its_case(tmp_path, change, rule, sub):
    assert 0 in flagged(portfolio(tmp_path, {0: change}), rule, sub)


def test_value_100_times_the_median(tmp_path):
    assert flagged(portfolio(tmp_path, {0: {"BuildingTIV": "300000000"}}), "VALUE-006", "") == [0]


def test_text_in_a_number_field_leaves_the_rules_on_that_field(tmp_path):
    results = portfolio(tmp_path, {0: {"YearBuilt": "c. 1990"}})
    assert 0 in flagged(results, "LINK-009", "not_a_number") and 0 not in flagged(results, "WHAT-006", "")


def test_basis_from_the_provenance_file(tmp_path):
    rows = [{h: v.format(n=i + 1) for h, v in zip(HEADER, GOOD, strict=True)} for i in range(3)]
    loc = write(tmp_path, rows)
    prov = tmp_path / "provenance.csv"
    prov.write_text("PortNumber,AccNumber,LocNumber,OEDField,Provenance,Source,Note\n"
                    "1,A1,2,BuildingTIV,declared,S!C3,basis: limit\n1,A1,3,BuildingTIV,declared,S!C4,basis: replacement\n")  # fmt: skip
    p = load(loc, provenance=prov)
    results = rules.run(p, schema.check(p), 2026)
    assert flagged(results, "VALUE-009", "limit") == [1]
