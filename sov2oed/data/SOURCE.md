# Bundled reference data

sov2oed never downloads anything at runtime. The files below were built once by
`tools/build_data.py` from public sources downloaded by hand on 25 Oct 2026.
To rebuild them, download the same files and run:

```
python tools/build_data.py --download-dir /path/to/downloads
```

| File | Source | Licence | SHA-256 |
|---|---|---|---|
| `OpenExposureData_5.0.0Spec.json` | OED specification 5.0.0 as shipped in ods_tools 5.0.9 (`ods_tools/data/OpenExposureData_5.0.0Spec.json`), https://github.com/OasisLMF/ODS_Tools | CC0 1.0 (OED schema) | `8bb716671960c535246c333a23792015a02cbfe410d5027572031e5a30cf21d0` |
| `OtherValues.csv` | ODS_OpenExposureData, `OpenExposureData/OtherValues.csv` at commit `07fc27ae38c8e73111e1bd05f53740cd488ba733`: https://raw.githubusercontent.com/OasisLMF/ODS_OpenExposureData/07fc27ae38c8e73111e1bd05f53740cd488ba733/OpenExposureData/OtherValues.csv | CC0 1.0 (repository README: documentation and schema are CC0) | `4e5a13488d295fc71897b2e4c672f50c660176928b64527a1a1251545773586a` |
| `country_boxes.csv` | Natural Earth 1:50m Admin 0 - Countries, v5.1.2, `geojson/ne_50m_admin_0_countries.geojson` from https://github.com/nvkelso/natural-earth-vector (tag v5.1.2), plus a hand-made table of micro-states and territories missing at that scale (see `HAND_BOXES` in the build script) | Public domain (Natural Earth terms of use) | `90a2b029e7f68e98640f68ab84177fe0f8148aa4d8cd959409333a4f1d8f73e5` |
| `iso4217.csv` | ISO 4217 List One as published by SIX on behalf of ISO: https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml (list dated 17 Sep 2026) | No licence stated; codes and names used as reference facts | `dda7fe826e1824b42a4033eb8fdf5a59ef1bc0bfbda40fc8382d84597bc58409` |

## How the country boxes are derived

- The country key is `ISO_A2_EH`, not `ISO_A2`: in v5.1.2, `ISO_A2` is `-99` for France, Norway and Kosovo, among others. Features without any ISO code (Somaliland, N. Cyprus, Siachen Glacier) are skipped.
- Each polygon part gives one box. Natural Earth already splits geometries at ±180°, so the Aleutians, Russia, Fiji and New Zealand keep boxes on both sides.
- Boxes are then merged greedily, always merging the pair whose union adds the least area, until each country has at most 10 boxes (v0 judgment). Merges never cross the antimeridian.
- The rules add a margin (0.5° by default, v0 judgment) at runtime.
- Offshore pseudo-countries (A1 … W5), the Caspian Sea code C2 and XW have no box: rows coded with them are "not evaluated" by WHERE-003 and WHERE-004.

Boxes are deliberately lenient. They catch gross errors (a swap, a point on another continent, a wrong country code), not a point a few kilometres across a border.
