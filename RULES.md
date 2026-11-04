# Rules

Sixteen rules check an OED location file, in four families. Each flags rows, by sub-code, with a severity
(critical, warning, info) and a weight from 0 to 3 (how much the field matters for a loss model). The score
weighs every flagged row by its insured value: [docs/scoring.md](docs/scoring.md).

The thresholds are judgments of version 1 unless a source is given. They are constants at the top of
[`sov2oed/check/rules.py`](sov2oed/check/rules.py), published so that they can be challenged.

| Rule | Title | Sub-codes: severity, weight |
|---|---|---|
| `WHERE-001` | Location cannot be placed | address is a placeholder (various, TBD): warning 2<br>no coordinates and no address below the country: critical 2<br>no coordinates and no street address, only a city or postcode: warning 2<br>no coordinates, an address to geocode: warning 2 |
| `WHERE-002` | Invalid coordinates | coordinates that are not numbers: critical 3<br>coordinates 0, 0: critical 3<br>coordinates out of range: critical 3<br>latitude and longitude swapped: critical 3<br>point outside the stated country: critical 3 |
| `WHAT-001` | Construction unknown | blank: warning 3<br>an OED unknown code: warning 3<br>not an OED code: warning 3 |
| `WHAT-002` | Occupancy unknown | blank: warning 2<br>an OED unknown code: warning 2<br>not an OED code: warning 2<br>a code for a whole category: info 1 |
| `WHAT-003` | Number of storeys missing | blank: warning 2<br>a height band, not a count: info 2 |
| `WHAT-006` | Year built missing | blank or 0: warning 2 |
| `WHAT-007` | Impossible year or storeys | placeholder year (1, 99, 999, 9999): critical 2<br>year before 1000: critical 2<br>year after this year: critical 2<br>roof or upgrade year before the build year or in the future: critical 2<br>more than 170 storeys: critical 2<br>storeys below -3: critical 2<br>99 storeys on many rows: warning 2 |
| `WHAT-008` | Floor area missing | blank: info 1<br>area too small or too large: warning 1 |
| `WHAT-011` | Default or repeated values | ConstructionCode: one value on most rows: warning 2<br>YearBuilt: one value on most rows: warning 2<br>FloorArea: one value on most rows: warning 2<br>RoofCover: one value on most rows: warning 2<br>an amount of one repeated digit (999999): warning 2<br>the same values at many addresses: warning 2<br>round amounts on most of the value: info 2 |
| `WHAT-012` | Peril attributes unknown (the report names the peril: "Wind attributes unknown") | wind: roof covering and shape both unknown (tropical wind: anchorage, deck, windows too): info 2<br>flood: first floor height, basement and flood defences all unknown: info 2<br>earthquake: soft storey, foundation type and retrofit all unknown: info 2<br>fire: sprinkler type and share sprinklered both unknown: info 2<br>hail: roof covering unknown: info 2 |
| `VALUE-001` | Zero or missing value | no insured value: critical 3<br>a negative value: critical 3 |
| `VALUE-002` | Value per area out of range | below 100 per m²: warning 2<br>above 20,000 per m²: warning 2 |
| `VALUE-006` | Value 100 times off comparable buildings | 100 times off the median of comparable buildings: warning 2 |
| `VALUE-009` | Value basis is not a replacement value | actual cash value (depreciated): warning 2<br>agreed value: warning 2<br>market value: warning 2<br>a policy limit, not a value: warning 2 |
| `LINK-002` | Duplicate locations | same location number twice: critical 3<br>same row under two numbers: warning 2 |
| `LINK-009` | OED schema | required OED field blank or missing: critical 2<br>text in a number field: critical 2<br>country code not in OED: critical 3<br>area code not valid for the country: info 1<br>peril code not in OED: critical 3<br>currency code not valid: critical 3<br>location without its account: critical 3<br>value outside the OED range: warning 1<br>code not in its OED list: warning 1 |

## What each rule evaluates

**Where.**
- `WHERE-001` evaluates every row. An address is a placeholder when the street, name or city is one of
  "various", "all locations", "unscheduled", "blanket", "TBD", "to be advised", "divers", "tous sites", "à préciser".
- `WHERE-002` evaluates rows with coordinates. A point is outside its country when it falls in none of the
  country's boxes (Natural Earth 1:50m admin-0, public domain), widened by 0.5°; offshore OED countries are not
  checked. Swapped: a latitude above 90 with a longitude within 90, or a point that lands in its country once swapped.

**What.** Rules on building characteristics evaluate buildings: rows whose construction code is a building class
(5000-5399, 5951, 5959, or not an OED code), and not rows with OtherTIV only (signs, fences, equipment).
- `WHAT-001` unknown codes are the OED construction codes named "Unknown" (5000, 5400, 5550, 5600...), 5304
  (unknown glass) excepted. Source: OED 5.0.0 construction list.
- `WHAT-002` unknown codes are 1000, 2000 and 3000 (source: OED 5.0.0 occupancy list); generic codes describe a
  whole category (1050, 1100, 1150, 1256, 1305, 1400, 2651).
- `WHAT-003` leaves out aggregate rows (IsAggregate 1, or more than one building).
- `WHAT-006` and `WHAT-008`: blank, or the OED default (0).
- `WHAT-007`: placeholder years 1, 99, 999, 9999; a year before 1000 (negative years included); a year after the
  current one, except construction projects (PercentComplete below 1 or a CompletionDate); a roof or upgrade year
  before the build year or in the future; more than 170 storeys (source: CTBUH, accessed 24 Oct 2026, Burj
  Khalifa 163 floors above ground, Jeddah Tower 167); fewer than -3 (the OED range); 99 storeys on at least 3 rows
  and more than 1% of them.
- `WHAT-008` implausible: below 10 m² with a building value, above 1,000,000 m² on a non-aggregate row.
- `WHAT-011`, on a file of 20 rows or more with more than one site: one construction class (5050, 5100, 5150,
  5200), year, roof cover or floor area on at least 80% of the rows, spread over 3 kinds of occupancy or more (5
  addresses for floor areas); an amount of one digit repeated 6 times or more (999999); the same building,
  contents and BI values at 5 addresses or more with different occupancies or constructions; round amounts (two
  significant digits) on 80% of the value with fewer distinct values than a tenth of the rows.
- `WHAT-012`, for each peril the row covers (no valid peril counts as all): every secondary attribute unknown.
  Tropical wind: RoofCover, RoofGeometry, RoofAnchorage, RoofDeckAttachment, WindowProtection; other wind:
  RoofCover, RoofGeometry; flood: FirstFloorHeight, Basement, FloodDefenseHeight; earthquake: SoftStory,
  FoundationType, Retrofit; fire: SprinklerType, PercentSprinklered; hail: RoofCover. It forms its own family.

**Value.**
- `VALUE-001`: all four TIVs blank or 0, or one negative. A building value of 0 alone is legitimate (a tenant).
- `VALUE-002`: building value per m² outside 100 to 20,000 (USD, EUR, GBP, CHF; other currencies are not
  evaluated). Outer bounds only: they catch an amount 1,000 times off, not a mispriced building.
- `VALUE-006`: a row's value (per m² when the area is known) at least 100 times above or below the median of at
  least 10 comparable rows (same account, kind of occupancy, currency).
- `VALUE-009`: an amount the conversion recorded as a market value, an actual cash value, a policy limit or an
  agreed value (in `provenance.csv`). Not evaluated on OED files without a provenance file.

**Link.**
- `LINK-002`: one location key (PortNumber, AccNumber, LocNumber) on several rows; or the same address,
  coordinates, building id, name, codes and values under two location numbers.
- `LINK-009`: what ods_tools, the reference OED library, would refuse or silently change: required fields, text
  in number fields (those rows leave the rules on that field), values outside the OED range of fields no other
  rule owns, codes not in their OtherValues list, country and area codes, peril codes, ISO 4217 currencies
  (XXX, XTS and the metals excluded), locations whose account is not in the account file.
