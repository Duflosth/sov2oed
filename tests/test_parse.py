import datetime as dt

import pytest

from sov2oed import parse
from sov2oed.parse import DECLARED, UNCERTAIN, UNKNOWN


@pytest.mark.parametrize(
    ("raw", "decimal", "expected"),
    [
        ("$1,250,000", ".", 1250000.0),
        ("1 250,5", ",", 1250.5),
        ("1,250", ",", 1.25),
        ("250.000", ",", 250000.0),
        ("250.000", ".", 250.0),
        ("1.250.000", ".", 1250000.0),
        ("£2.8M", ".", 2800000.0),
        ("n/k", ".", None),
        ("as last year + inflation", ".", None),
    ],
)
def test_parse_number(raw, decimal, expected):
    assert parse.parse_number(raw, decimal) == expected


def test_amount_zero_and_abbreviation():
    assert parse.parse_amount(0)[1] == UNKNOWN
    value, status, note = parse.parse_amount("$2.8M")
    assert (value, status) == (2800000.0, DECLARED) and "precision lost" in note
    assert parse.parse_amount("12", multiplier=1000)[0] == 12000.0


@pytest.mark.parametrize(
    ("raw", "year", "status"),
    [
        (1985, 1985, DECLARED),
        ("1985.0", 1985, DECLARED),
        (dt.date(1972, 1, 1), 1972, DECLARED),
        ("c. 1960", 1960, UNCERTAIN),
        ("1960s", 1960, UNCERTAIN),
        ("Pre 1919", 1919, UNCERTAIN),
        ("1985/2004", 1985, DECLARED),
        ("1700", None, UNKNOWN),
        ("old", None, UNKNOWN),
    ],
)
def test_parse_year(raw, year, status):
    value, got, _ = parse.parse_year(raw, 2100)
    assert (value, got) == (year, status)


@pytest.mark.parametrize(("raw", "storeys"), [("3", 3), ("R+2", 3), ("RDC", 1), ("2-3", 3), ("4 floors", 4), (0, None)])
def test_parse_storeys(raw, storeys):
    assert parse.parse_storeys(raw)[0] == storeys


def test_postcodes_and_states():
    assert parse.parse_postcode(7302, "US")[0] == "07302"
    assert parse.parse_postcode("g31  4pd", "GB")[0] == "G31 4PD"
    assert parse.parse_area_code("Tennessee", "US")[0] == "TN"
    assert parse.parse_area_code("Atlantis", "US")[1] == UNKNOWN


def test_dates_follow_the_decimal_mark():
    assert parse.parse_date("03/04/2025", ".")[0] == "2025-03-04"
    assert parse.parse_date("03/04/2025", ",")[0] == "2025-04-03"


def test_blank_values():
    assert all(parse.blank(v) for v in (None, "", " N/A ", "TBD", "—", "à préciser"))
    assert not parse.blank("0")
