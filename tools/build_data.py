"""Build the reference tables bundled in ``sov2oed/data`` from public sources.

This script is run by maintainers, never by sov2oed at runtime. It reads files
already downloaded by hand (see ``sov2oed/data/SOURCE.md`` for the URLs) and
writes small, deterministic CSV files.

Usage::

    python tools/build_data.py --download-dir /path/to/downloads
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "sov2oed" / "data"
MAX_BOXES_PER_COUNTRY = 10

# Micro-states and territories absent from Natural Earth 1:50m admin-0, with
# hand-made boxes (min_lat, max_lat, min_lon, max_lon). Coordinates are rounded
# public geographic facts; each box is deliberately generous.
HAND_BOXES: dict[str, list[tuple[float, float, float, float]]] = {
    "GI": [(36.10, 36.16, -5.37, -5.33)],  # Gibraltar
    "BV": [(-54.47, -54.38, 3.28, 3.45)],  # Bouvet Island
    "TK": [(-9.45, -8.53, -172.53, -171.18)],  # Tokelau
    "BQ": [
        (12.0, 12.32, -68.45, -68.18),
        (17.47, 17.65, -63.25, -62.95),
    ],  # Bonaire, Saba, St Eustatius
    "GP": [(15.83, 16.52, -61.81, -61.0)],  # Guadeloupe
    "MQ": [(14.38, 14.88, -61.24, -60.81)],  # Martinique
    "GF": [(2.11, 5.75, -54.6, -51.6)],  # French Guiana
    "RE": [(-21.39, -20.87, 55.21, 55.84)],  # Reunion
    "YT": [(-13.0, -12.63, 45.01, 45.3)],  # Mayotte
    "SJ": [(74.3, 80.9, 10.4, 33.6), (70.8, 71.2, -9.1, -7.9)],  # Svalbard and Jan Mayen
    "TV": [(-10.8, -5.6, 176.0, 179.9)],  # Tuvalu
    "CX": [(-10.58, -10.41, 105.52, 105.72)],  # Christmas Island
    "CC": [(-12.21, -11.82, 96.81, 96.93)],  # Cocos (Keeling) Islands
    "UM": [(19.27, 19.32, 166.59, 166.66), (28.19, 28.23, -177.42, -177.32)],  # Wake, Midway
    # OED-defined codes for the Caribbean Netherlands
    "XB": [(12.0, 12.32, -68.45, -68.18)],  # Bonaire
    "XE": [(17.46, 17.54, -63.01, -62.93)],  # St. Eustatius
    "XS": [(17.6, 17.66, -63.27, -63.2)],  # Saba
}


def bbox(ring_sets: list[list[list[float]]]) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) of the outer ring of a polygon."""
    outer = ring_sets[0]
    lons = [p[0] for p in outer]
    lats = [p[1] for p in outer]
    return (min(lats), max(lats), min(lons), max(lons))


def area(b: tuple[float, float, float, float]) -> float:
    return (b[1] - b[0]) * (b[3] - b[2])


def union(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]))


def reduce_boxes(boxes: list[tuple[float, float, float, float]], limit: int) -> list[tuple[float, float, float, float]]:
    """Greedily merge the pair of boxes whose union adds the least area."""
    boxes = list(boxes)
    while len(boxes) > limit:
        best: tuple[float, int, int] | None = None
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                u = union(boxes[i], boxes[j])
                cost = area(u) - area(boxes[i]) - area(boxes[j])
                # Never merge across the antimeridian: the union would span the globe.
                if u[3] - u[2] > 180:
                    continue
                if best is None or cost < best[0]:
                    best = (cost, i, j)
        if best is None:
            break
        _, i, j = best
        merged = union(boxes[i], boxes[j])
        boxes = [b for k, b in enumerate(boxes) if k not in (i, j)] + [merged]
    return sorted(boxes)


def build_country_boxes(geojson: Path) -> None:
    data = json.loads(geojson.read_text(encoding="utf-8"))
    per_country: dict[str, list[tuple[float, float, float, float]]] = {}
    for feature in data["features"]:
        props = feature["properties"]
        iso2 = props["ISO_A2_EH"]
        if not iso2 or iso2 == "-99":
            continue
        geom = feature["geometry"]
        polygons = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        per_country.setdefault(iso2, []).extend(bbox(p) for p in polygons)
    for iso2, boxes in HAND_BOXES.items():
        per_country.setdefault(iso2, []).extend(boxes)
    out = DATA_DIR / "country_boxes.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["CountryCode", "min_lat", "max_lat", "min_lon", "max_lon"])
        for iso2 in sorted(per_country):
            for b in reduce_boxes(per_country[iso2], MAX_BOXES_PER_COUNTRY):
                writer.writerow([iso2, *(f"{v:.4f}" for v in b)])
    print(f"{out}: {len(per_country)} countries")


def build_iso4217(xml_path: Path) -> None:
    root = ET.parse(xml_path).getroot()
    published = root.attrib.get("Pblshd", "")
    codes: dict[str, str] = {}
    for entry in root.iter("CcyNtry"):
        code = entry.findtext("Ccy")
        name = entry.findtext("CcyNm")
        if code and name:
            codes[code] = name
    out = DATA_DIR / "iso4217.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["code", "name", "list_published"])
        for code in sorted(codes):
            writer.writerow([code, codes[code], published])
    print(f"{out}: {len(codes)} codes, list published {published}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-dir", type=Path, required=True)
    args = parser.parse_args()
    d: Path = args.download_dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(d / "OtherValues.csv", DATA_DIR / "OtherValues.csv")
    build_country_boxes(d / "ne_50m_admin_0_countries.geojson")
    build_iso4217(d / "list-one.xml")


if __name__ == "__main__":
    main()
