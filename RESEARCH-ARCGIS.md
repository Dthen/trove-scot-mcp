# HES ArcGIS REST API — Research Findings (RESEARCH-ARCGIS.md)

Research spike to resolve specific unknowns before building an MCP server around the
Historic Environment Scotland ArcGIS REST API.

- **Base API:** `https://inspire.hes.scot/arcgis/rest/services` (no auth, append `f=json`)
- **ArcGIS version:** 10.81 — folders: `CANMORE`, `HES`, `INSPIRE`
- **Main dataset:** `CANMORE/Canmore_Points/MapServer`, layer **0** = "Terrestrial"
- **Live record count (layer 0):** **313,456** (via `returnCountOnly`)
- All tests run with `curl --max-time 20`. Every URL below was executed for real.

> **HEADLINE:** The two "blockers" in the prior RESEARCH.md were misdiagnosed.
> 1. `outFields=*` works perfectly. The HTTP 400 "Pagination is not supported" is
>    triggered by **`resultRecordCount` / `resultOffset`**, NOT by `outFields`.
> 2. Text search via `LIKE` works fine — but the data is stored in **UPPERCASE** and
>    `LIKE` is **case-sensitive**, so `'%castle%'` returns 0 while `'%CASTLE%'` returns 7,098.

---

## 1. outFields — ✅ WORKS (the prior "HTTP 400" was actually the pagination param)

The earlier finding conflated two things. `outFields=*` returns HTTP 200 with full data.
The 400 "Pagination is not supported" only appears when `resultRecordCount`/`resultOffset`
are present (see §3).

| Test | URL (query part) | Result |
|------|------------------|--------|
| outFields=* (valid id) | `?where=CANMOREID=52068&outFields=*&returnGeometry=false&f=json` | ✅ HTTP 200, all 22 fields returned |
| Named fields | `?where=CANMOREID=311&outFields=NMRSNAME,SITETYPE,CANMOREID,COUNCIL&returnGeometry=false&f=json` | ✅ works |
| Single field | `?where=CANMOREID=311&outFields=NMRSNAME&returnGeometry=false&f=json` | ✅ works |
| outFields=* with bad id | `?where=CANMOREID=1&outFields=*&f=json` | HTTP 200 but `features:[]` (id 1 doesn't exist — valid IDs start ~311). **This empty result is what likely looked like a failure before.** |

**Answer:** Use `outFields=*` freely. Always pair with `returnGeometry=false` unless you
actually need the multipoint geometry (keeps payloads small). The exact error message when
pagination params are used is: `{"error":{"code":400,"message":"Pagination is not supported.","details":[]}}`.

Full Edinburgh Castle record (CANMOREID 52068) via `outFields=*`:
```json
{
 "FID": 48773, "CANMOREID": 52068, "SITENUMBER": "NT27SE 1.00",
 "NMRSNAME": "EDINBURGH CASTLE", "ALTNAME": "EDINBURGH CASTLE, GENERAL",
 "BROADCLASS": "DEFENCE, MONUMENT (BY FORM)", "SITETYPE": "CASTLE (MEDIEVAL)",
 "COUNTY": "MIDLOTHIAN", "COUNCIL": "EDINBURGH, CITY OF",
 "PARISH": "EDINBURGH (EDINBURGH, CITY OF)", "ARCHITECTU": "Y", "ARCHAEOLOG": "Y",
 "FORM": " ", "GRIDREF": "NT 25112 73497", "ACCURACY": "NGR given to the nearest 1m",
 "URL": "https://www.trove.scot/place/52068",
 "ENTRYDATE": 650073600000, "LASTUPDATE": 1330905600000,
 "XCOORD": 325112, "YCOORD": 673497,
 "COMPILER": "Historic Environment Scotland", "LICENCE": "Open Government Licence"
}
```
Note: `URL` = `https://www.trove.scot/place/{CANMOREID}` (handy deep link). Dates are epoch-ms.

---

## 2. Text search — ✅ WORKS (data is UPPERCASE; LIKE is case-sensitive)

The prior "0 rows" was a case-sensitivity trap. NMRSNAME/SITETYPE/etc. are stored in
**uppercase**. `LIKE` is case-sensitive on this server, so lowercase patterns match nothing.

| WHERE clause | Count | Verdict |
|--------------|-------|---------|
| `NMRSNAME LIKE '%CASTLE%'` | **7,098** | ✅ works (uppercase pattern) |
| `UPPER(NMRSNAME) LIKE '%CASTLE%'` | **7,102** | ✅ works, slightly more (catches mixed-case) |
| `NMRSNAME LIKE '%castle%'` | **0** | ❌ lowercase pattern → 0 (case-sensitive) |
| `UPPER(NMRSNAME) LIKE '%EDINBURGH%'` | **17,125** | ✅ works |
| `NMRSNAME = 'Edinburgh Castle'` | **0** | ❌ exact match fails (wrong case) |
| `NMRSNAME = 'EDINBURGH CASTLE'` | **4** | ✅ exact match works in uppercase |
| `UPPER(NMRSNAME) LIKE '%EDINBURGH CASTLE%'` | **94** | ✅ works |
| `SITETYPE LIKE '%castle%'` | **0** | ❌ lowercase |
| `SITETYPE LIKE '%CASTLE%'` | **911** | ✅ uppercase works |

**Recommended pattern:** always wrap the column in `UPPER()` and uppercase the pattern:
`UPPER(NMRSNAME) LIKE '%<TERM>%'`. This is robust regardless of source casing. Combine
terms with `AND`/`OR`. You can also search `SITETYPE`, `ALTNAME`, `COUNTY`, `COUNCIL`, `PARISH`.

Sample (UPPER(NMRSNAME) LIKE '%EDINBURGH CASTLE%'):
```
52068  EDINBURGH CASTLE                              CASTLE (MEDIEVAL)      EDINBURGH, CITY OF
52069  EDINBURGH CASTLE, MONS MEG                    CANNON (15TH CENTURY)  EDINBURGH, CITY OF
52070  EDINBURGH CASTLE, HALF MOON BATTERY           BATTERY (16TH CENTURY) EDINBURGH, CITY OF
```

### Listed_Buildings layer — text search also works (mixed-case data!)
Unlike Canmore, `HES/Listed_Buildings` stores **mixed-case** titles, so `UPPER()` is still
the safe choice but plain `LIKE '%Edinburgh%'` also returns hits there.
| WHERE (Listed_Buildings layer 0) | Count |
|----------------------------------|-------|
| `ENT_TITLE LIKE '%CASTLE%'` | 0 (ENT_TITLE casing differs) |
| `UPPER(DES_TITLE) LIKE '%CASTLE%'` | **2,214** ✅ |
| `DES_TITLE LIKE '%Edinburgh%'` | 571 ✅ (mixed case works here) |
| `UPPER(DES_TITLE) LIKE '%EDINBURGH%'` | 802 ✅ |
| `CATEGORY = 'A'` | 6,485 ✅ |

---

## 3. Limiting results — ❌ NO pagination, ⚠️ implicit 1000-cap

| Test | Result |
|------|--------|
| `?where=1=1&resultRecordCount=5&...` | ❌ HTTP 400 "Pagination is not supported." |
| `?where=1=1&resultOffset=5&resultRecordCount=5&...` | ❌ HTTP 400 "Pagination is not supported." |

**`resultRecordCount` alone (even without offset) triggers the error.** This server has
`supportsPagination = false`. There is **no way to ask for "top N"**.

What you get instead: the server returns up to **`maxRecordCount`** features (1000 for
Canmore_Points layer 0) and silently truncates anything beyond that. So a broad query
returns "the first 1000 by FID order" — not configurable, not pageable.

**Workarounds for the MCP server:**
- **Narrow the `where` clause** so the true result set is < 1000 (e.g. add `AND COUNCIL=...`,
  `AND SITETYPE LIKE ...`). Use `returnCountOnly=true` first to check the count.
- **Page manually via CANMOREID ranges:** `where=CANMOREID > <lastSeen> AND ...` ordered by id
  (no ORDER BY support, but FID/CANMOREID roughly ascend). Fetch a page, take the max
  CANMOREID, repeat. This is the reliable way to walk large result sets.
- **Spatial windowing** (see §5) to chunk by geography.

---

## 4. returnGeometry & returnCountOnly — ✅ both work

| Test | Result |
|------|--------|
| `?where=1=1&returnCountOnly=true&f=json` | ✅ `{"count":313456}` — fast, no features |
| `?where=...&returnGeometry=false&outFields=...` | ✅ works; drops the multipoint geometry, smaller payload |

`returnCountOnly` is ideal for a "how many match?" pre-check before fetching.
Geometry type is `esriGeometryMultipoint`; default `returnGeometry=true` includes coords,
but `XCOORD`/`YCOORD` attribute fields already carry the position, so geometry is usually
redundant — prefer `returnGeometry=false`.

---

## 5. Spatial / bbox query — ✅ WORKS (great for "near me" tools)

```
?geometry=-3.3,55.9,-3.1,56.0&geometryType=esriGeometryEnvelope&inSR=4326
 &spatialRel=esriSpatialRelIntersects&outFields=NMRSNAME,CANMOREID&returnGeometry=false&f=json
```
✅ Returns features in the Edinburgh-area bbox (hit the 1000 cap → busy area). Sample:
```
EDINBURGH, 1 RANDOLPH LANE                          257331
EDINBURGH, CANONGATE, MORAY HOUSE, QUEEN MARY'S THORN  52132
EDINBURGH, GRANTON HARBOUR                           52051
```
- `inSR=4326` = input bbox is WGS84 lon/lat; server reprojects to its native BNG internally.
- Combine spatial with `where` and `outFields` freely.
- Still subject to the 1000 cap → for dense areas, subdivide the bbox or add `where` filters.
- For "near a point", send a small envelope around the lon/lat (or use `geometryType=esriGeometryPoint`
  with a `distance`/buffer — envelope is simplest and confirmed working).

---

## 6. Field values / domains

No coded-value domains on any field (all free-text/`domain=None`). Enumerate values with
`returnDistinctValues=true` — **BUT** this only dedupes within the first maxRecordCount page
(see caveat). Confirmed working:
`?where=1=1&outFields=BROADCLASS&returnGeometry=false&returnDistinctValues=true&f=json`

### BROADCLASS
A **comma-joined multi-tag** string (a site can have several). 150 distinct combinations seen
in the first page. Root categories include:
`AGRICULTURE AND SUBSISTENCE`, `ARMOUR AND WEAPONS`, `CIVIL`, `COMMEMORATIVE`, `COMMERCIAL`,
`CONTAINER`, `CURRENCY`, `DEFENCE`, `DOMESTIC`, `DRESS AND PERSONAL ACCESSORIES`,
`GARDENS PARKS AND URBAN SPACES`, `INDUSTRIAL`, `MARITIME`, `MARITIME CRAFT`, `MEASUREMENT`,
`MONUMENT (BY FORM)`, `RECREATIONAL`, `RELIGION OR RITUAL`, `RELIGIOUS RITUAL AND FUNERARY`,
`TOOLS AND EQUIPMENT`, `TRANSPORT`, `UNASSIGNED`, `WATER SUPPLY AND DRAINAGE`.
→ Filter with `BROADCLASS LIKE '%DEFENCE%'` (substring, since values are combined).

### COUNCIL — ⚠️ distinct query is misleading
`returnDistinctValues` on COUNCIL returned only 3 (`HIGHLAND, ORKNEY ISLANDS, SHETLAND ISLANDS`)
— **that is just what's in the first FID-sorted 1000-page**, NOT the full set. Proof that more
exist via targeted counts/LIKE:
| Council (exact / pattern) | Count |
|---------------------------|-------|
| `COUNCIL = 'EDINBURGH, CITY OF'` | 18,416 |
| `COUNCIL = 'FIFE'` | 15,979 |
| `COUNCIL LIKE '%GLASGOW%'` → `GLASGOW, CITY OF` | 10,859 |
| `COUNCIL LIKE '%ABERDEEN%'` | 33,494 |
| `COUNCIL LIKE '%ARGYLL%'` | 19,276 |
| `COUNCIL = 'HIGHLAND'` | (present) |

**Naming convention:** cities use `"<NAME>, CITY OF"` (e.g. `EDINBURGH, CITY OF`,
`GLASGOW, CITY OF`); council areas use the plain name (`HIGHLAND`, `FIFE`, `ARGYLL AND BUTE`).
→ For an MCP "filter by council" tool, **match with `COUNCIL LIKE '%<TERM>%'` (uppercased)**
rather than exact equality, to absorb the `, CITY OF` suffix. Do NOT trust `returnDistinctValues`
to enumerate councils.

---

## 7. HES designation layers — ✅ better behaved than Canmore (higher caps)

All keyless, JSON, wkid=27700 (BNG). Text search works (use `UPPER(col) LIKE '%TERM%'`).

### HES/Listed_Buildings/MapServer
- Layers: **0** = "Listed Buildings by Category" (points), **1** = "Listed Buildings boundaries" (polygons)
- **Count (layer 0): 67,477** · **maxRecordCount = 5000** (much higher than Canmore!)
- Fields: `FID, ENT_REF, ENT_SEQ, ENT_TITLE, PRECISION, ACCURACY, X, Y, CREATED, UPDATED,
  COMPILER, DES_REF, DES_TITLE, DES_TYPE, DESIGNATED, AMENDED, LINK, LEGISLATIO, CATEGORY,
  GROUPCAT, CLASS, PARBUR, LOCAL_AUTH, NAT_PARK`
- Key fields: **ENT_TITLE / DES_TITLE** (name+address), **CATEGORY** (A / B / C listing grade),
  **DESIGNATED** (epoch-ms date listed), **LOCAL_AUTH** (council, e.g. "Edinburgh"), **LINK**.
- Sample: `{'DES_TITLE':'Duke of York Statue, Edinburgh Castle Esplanade, Edinburgh','CATEGORY':'B','LOCAL_AUTH':'Edinburgh'}`

### HES/Scheduled_Monuments/MapServer — layer 0
- **maxRecordCount = 10000** (highest)
- Fields: `FID, CREATED, UPDATED, PRECISION, ACCURACY, CAP_SCALE, X, Y, COMPILER, DES_REF,
  DES_TITLE, DES_TYPE, DESIGNATED, AMENDED, LINK, LEGISLATIO, CLASS, PARISH, CATEGORY, AREA,
  LOCAL_AUTH, Shape_Leng, Shape_Area`
- Key: **DES_TITLE** (name), **CLASS**, **CATEGORY**, **AREA**, **LOCAL_AUTH**, **DESIGNATED**, **LINK**.

### HES/Properties_in_care_points/MapServer — layer 0 ("Properties in care")
- **maxRecordCount = 1000**
- Fields: `Shape, FID, PIC_ID, PIC_NAME, X, Y, LINK, LOCAL_AUTH`
- Key: **PIC_NAME**, **LINK**, **LOCAL_AUTH**. Small, simple layer (HES-managed sites).

**Verdict:** designation layers support `outFields` + text search just as well as Canmore and
have *higher* result caps (5000–10000), so pagination pain is reduced. Their schemas are richer
for "designation" questions (listing grade, date designated, legal class).

---

## 8. Coordinates — British National Grid (OSGB36), NOT WGS84

- Layer `spatialReference`: **`{"wkid":27700,"latestWkid":27700}`** = **British National Grid**.
- `XCOORD` / `YCOORD` are **BNG eastings/northings** (metres). Edinburgh Castle:
  `XCOORD=325112, YCOORD=673497` ↔ `GRIDREF="NT 25112 73497"` (they match).
- **To get lat/lon you must convert OSGB36 → WGS84** (e.g. `pyproj`
  `Transformer.from_crs(27700, 4326)`, or the `osgb36`/`gps` grid transform). Do NOT treat
  XCOORD/YCOORD as lon/lat.
- For **input** geometry (bbox/point queries) you can pass WGS84 with `inSR=4326` and the
  server reprojects (§5). For **output**, either convert XCOORD/YCOORD yourself, or request
  geometry reprojected by adding `outSR=4326` (returns geometry in WGS84) — but since
  `returnGeometry=false` is preferred, convert the attribute coords in the MCP server.

---

## Layer metadata — CANMORE/Canmore_Points layer 0 ("Terrestrial")

- `geometryType`: `esriGeometryMultipoint`
- `maxRecordCount`: **1000**
- `capabilities`: `Map,Query,Data`
- `supportsStatistics`: **False** · `supportsPagination`: not advertised (effectively false)
- `spatialReference`: **wkid 27700 (BNG)**

| Field | Type | Len | Notes |
|-------|------|-----|-------|
| FID | OID | – | object id |
| Shape | Geometry | – | multipoint (BNG) |
| CANMOREID | Integer | – | **stable primary key**; deep link = trove.scot/place/{id} |
| SITENUMBER | String | 56 | e.g. "NT27SE 1.00" |
| NMRSNAME | String | 254 | **primary name (UPPERCASE)** — main search field |
| ALTNAME | String | 254 | alternate names |
| BROADCLASS | String | 254 | comma-joined broad categories |
| SITETYPE | String | 254 | e.g. "CASTLE (MEDIEVAL)" — good search field |
| COUNTY | String | 30 | historic county |
| COUNCIL | String | 42 | council area; cities = "<NAME>, CITY OF" |
| PARISH | String | 41 | |
| ARCHITECTU | String | 1 | "Y"/"N" flag |
| ARCHAEOLOG | String | 1 | "Y"/"N" flag |
| FORM | String | 7 | |
| GRIDREF | String | 20 | BNG grid ref string |
| ACCURACY | String | 29 | positional accuracy note |
| URL | String | 69 | trove.scot/place/{CANMOREID} |
| ENTRYDATE | Date | – | epoch-ms |
| LASTUPDATE | Date | – | epoch-ms |
| XCOORD | Double | – | **BNG easting** |
| YCOORD | Double | – | **BNG northing** |
| COMPILER | String | 254 | |
| LICENCE | String | 254 | "Open Government Licence" |

(Layer 1 "Maritime" has an identical schema.)

---

## ✅ WHAT WORKS (reliable patterns)

1. **Get by CANMOREID:** `?where=CANMOREID=<id>&outFields=*&returnGeometry=false&f=json`
2. **Text search (name/type/place):** `?where=UPPER(NMRSNAME) LIKE '%<TERM>%'\
   &outFields=CANMOREID,NMRSNAME,SITETYPE,COUNCIL&returnGeometry=false&f=json`\
   (also `SITETYPE`, `ALTNAME`, `COUNTY`, `PARISH`; combine with `AND`/`OR`)
3. **Count first:** `?where=<...>&returnCountOnly=true&f=json`
4. **Spatial bbox (WGS84 in):** `?geometry=minLon,minLat,maxLon,maxLat\
   &geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects\
   &outFields=...&returnGeometry=false&f=json`
5. **outFields=* / named / single** — all fine.
6. **returnGeometry=false**, **returnCountOnly=true**, **returnDistinctValues=true** (within first page).
7. **Council filter:** `UPPER(COUNCIL) LIKE '%<TERM>%'` (absorbs ", CITY OF").
8. **Designation layers** (Listed_Buildings, Scheduled_Monuments, Properties_in_care) — same
   query patterns, richer designation fields, higher caps (5000/10000/1000).

## ❌ WHAT DOESN'T WORK (confirmed limitations)

1. **Pagination:** `resultRecordCount` and/or `resultOffset` → HTTP 400
   `"Pagination is not supported."` (even `resultRecordCount` alone). No "top N".
2. **Results silently cap at maxRecordCount** (1000 Canmore, 5000 LB, 10000 SAM). Broad
   queries truncate without error — always count first.
3. **Case-sensitive LIKE on lowercase patterns** → 0 rows. Data is UPPERCASE (Canmore) /
   mixed (LB). Always `UPPER(col) LIKE '%UPPERCASE%'`.
4. **Exact `=` on names** usually fails unless you match the stored casing exactly
   (`'EDINBURGH CASTLE'` works, `'Edinburgh Castle'` = 0). Prefer `UPPER(...) LIKE`.
5. **No server-side statistics / ORDER BY** (`supportsStatistics=false`).
6. **`returnDistinctValues` only sees the first page** → cannot enumerate all COUNCIL/BROADCLASS
   values this way (it returned 3 councils when 32 exist).
7. **XCOORD/YCOORD are BNG (27700), not lat/lon** — must convert.

---

## Recommended MCP tools (realistic, given what actually works)

**Canmore (NRHE) — `CANMORE/Canmore_Points/MapServer/0`:**
1. `canmore_get_by_id(canmore_id)` — full record via `where=CANMOREID=n&outFields=*`.
   Convert XCOORD/YCOORD → lat/lon; expose `URL` as a clickable trove.scot link.
2. `canmore_search(term, sitetype=None, council=None, broadclass=None, limit≈50)` —
   build `UPPER(NMRSNAME) LIKE '%TERM%'` (+ optional `AND UPPER(SITETYPE) LIKE ...`,
   `AND UPPER(COUNCIL) LIKE ...`). Run `returnCountOnly` first; if count > 1000, tell the
   user to narrow the query (or auto-add filters). Return concise fields.
3. `canmore_count(term/filters)` — `returnCountOnly` "how many match" helper.
4. `canmore_near(lon, lat, radius_km, term=None)` — build a WGS84 envelope around the point
   (`inSR=4326`, `esriSpatialRelIntersects`), optional name filter. Convert results to lat/lon
   and sort client-side by distance.
5. `canmore_in_bbox(minLon,minLat,maxLon,maxLat, term=None)` — raw bbox query.
6. `canmore_list_councils()` / `canmore_list_broadclasses()` — return a **hardcoded/curated**
   list (do NOT rely on `returnDistinctValues`); or fetch via targeted `LIKE` counts.

**Designations — separate tools or a `dataset` param:**
7. `listed_buildings_search(term, category=None, local_auth=None)` — `HES/Listed_Buildings/MapServer/0`,
   search `UPPER(DES_TITLE) LIKE`, filter `CATEGORY` (A/B/C) and `LOCAL_AUTH`. Returns grade + date designated.
8. `scheduled_monuments_search(term, class=None)` — `HES/Scheduled_Monuments/MapServer/0`.
9. `properties_in_care_search(term=None)` — `HES/Properties_in_care_points/MapServer/0` (small; can list all).

**Shared infra for the server:**
- A single `query(layer, where, out_fields, geometry=None, count_only=False)` helper that
  URL-encodes, appends `f=json`, sets `returnGeometry=false`, and enforces `--max-time 20` + 1–2 retries.
- A BNG→WGS84 converter (pyproj EPSG:27700→4326) applied to XCOORD/YCOORD (and to X/Y on the
  designation layers, which are also BNG).
- Always **count before fetching**; surface truncation ("showing first 1000 of N — narrow your query").
- Uppercase all `LIKE` terms and wrap columns in `UPPER()`.

**Not feasible (don't build these):** server-side pagination/"next page", "top N most recent",
server-side sorting, full enumeration of field values via the API, fuzzy/full-text ranking
(the SQL `LIKE` is substring-only). For ranked full-text search over all 313k names, build a
**local SQLite/FTS index from the bulk shapefile zip** (`inspire.hes.scot/AtomService/DATA/Canmore_Points.zip`)
as a complement — see RESEARCH.md.
