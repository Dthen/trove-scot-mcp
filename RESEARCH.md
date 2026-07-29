# trove.scot / Historic Environment Scotland — Research Findings

## Summary

### 🔑 THE BIG DISCOVERY: Open ArcGIS REST API (no auth, no WAF)

The trove.scot website itself is behind Azure WAF + a JS bot-challenge (curl gets 403), and its live search backend was **down during testing** ("Search may be intermittently unavailable due to increased bot attack activity"). But HES runs a fully open **ArcGIS Server REST API** at:

```
https://inspire.hes.scot/arcgis/rest/services?f=json
```

- ArcGIS version 10.81, folders: **CANMORE**, **HES**, **INSPIRE**
- **CANMORE/Canmore_Points** (MapServer) = the National Record of the Historic Environment. Layer 0 "Terrestrial" + Layer 1 "Maritime". **Live count query returned 313,424 terrestrial records.**
- **CANMORE/Scottish_Radiocarbon_Index**
- **HES/** services: Battlefields_Inventory_Boundary, Conservation_Areas, Gardens_and_Designed_Landscapes, HES_Designations, Historic_Marine_Protected_Areas, Listed_Buildings, Properties_in_care_points, Scheduled_Monuments, World_Heritage_Sites

**Working query pattern** (returns JSON, no key needed):
```
https://inspire.hes.scot/arcgis/rest/services/CANMORE/Canmore_Points/MapServer/0/query?where=1=1&returnCountOnly=true&f=json
```

**Canmore_Points schema** (both layers identical): FID(OID), Shape(geometry), CANMOREID(int), SITENUMBER, NMRSNAME, ALTNAME, BROADCLASS, SITETYPE, COUNTY, COUNCIL, PARISH, ARCHITECTU, ARCHAEOLOG, FORM, GRIDREF, ACCURACY, URL, ENTRYDATE(date), LASTUPDATE(date), XCOORD(double), YCOORD(double), COMPILER, LICENCE. `maxRecordCount=1000`.

**Caveats found:**
- `outFields=*` and `resultOffset`/`resultRecordCount` pagination → **HTTP 400 "Pagination is not supported."** Must page via `where` clauses on CANMOREID ranges or spatial queries. Named-field outFields may work; needs verification.
- A `LIKE '%CASTLE%'` on NMRSNAME returned 0 rows — text search via SQL `where` is unreliable; prefer exact/`upper()` filters or spatial `geometry` queries. Each MapServer also exposes **WFSServer** and **WMSServer** endpoints (e.g. `.../Listed_Buildings/MapServer/WFSServer?request=GetCapabilities&service=WFS`).

## trove.scot undocumented endpoints (found in JS bundles, but search backend down)

- Next.js app (buildId `uB6MKogB6ZLqdXe0KQSE1`), server-rendered (RSC).
- `/api/download-results?q=...&page_type=All&viewmode=grid` → returns `text/csv` (Content-Disposition attachment). Worked (HTTP 200) but returned **0 bytes** because search backend was down.
- `/api/sas-image`, `/api/scran-image/{id}` — image proxies.
- Image/Canmore backend host: **`i.rcahms.gov.uk`** (config var `CN`), path `/canmore/x`.
- `/search/map` — server-rendered map page.

## Bulk downloads (INSPIRE Atom feed — direct zips work, no WAF)

Feed: `https://inspire.hes.scot/AtomService/HES_AtomService.atom.en.xml` (HTTP 200). Server is Microsoft-IIS/8.5. Direct files confirmed downloadable (e.g. `lb_scotland.zip` = 6.5 MB, shapefiles). Datasets:

- Listed Buildings → inspire.hes.scot/AtomService/DATA/lb_scotland.zip
- Scheduled Monuments → .../DATA/sam_scotland.zip
- Gardens & Designed Landscapes → .../DATA/gdl_scotland.zip
- Battlefields → .../DATA/battlefields_scotland.zip
- Historic Marine Protected Areas → .../DATA/HMPA_Scotland.zip
- Conservation Areas → .../DATA/ca_scotland.zip
- World Heritage Sites → .../DATA/WHS.zip
- Properties in Care → .../DATA/pic.zip
- NRHE (Canmore) → .../DATA/Canmore_Points.zip + trove.scot/media/5mapdvkk/nrhe_areas.zip
- Historic Landuse Assessment → external: hlamap.org.uk/data-download
- Scottish Radiocarbon Index → via spatialdata.gov.scot geonetwork
- Buildings at Risk Register → barr.org.uk (separate)

## spatialdata.gov.scot

GeoNetwork catalog works: `https://spatialdata.gov.scot/geonetwork/srv/api/records/{uuid}` returns JSON metadata (used to find the ArcGIS endpoints). The `/geonetwork/srv/api/records?q=` search is broken ("Not implemented in ES"). HES metadata records reference the ArcGIS WFS/WMS servers above.

## Old Canmore

canmore.org.uk is retired; the successor data lives in the **CANMORE** ArcGIS folder and `i.rcahms.gov.uk` image host.

## Recommended MCP design

**Primary: wrap the open ArcGIS REST API** (`inspire.hes.scot/arcgis/rest/services`). It's live, keyless, JSON, has 313k+ NRHE records + all designation layers, and supports spatial queries — ideal for MCP tools (search by name/type/county, get-by-CANMOREID, bbox/nearby queries). Handle the no-pagination limit by querying on CANMOREID ranges / `where` filters / geometry. **Secondary: local SQLite from bulk shapefile zips** for offline/full-text search (the live SQL text search is weak). WFS is available but ArcGIS REST JSON is simpler. Avoid depending on trove.scot's own `/api/*` (WAF-gated + flaky).

## Issues

- Azure WAF blocks all curl to www.trove.scot (403); browser also hit a bot challenge (passed once).
- trove.scot live search backend down during session → couldn't capture real search XHR or CSV sample rows.
- Subagent hit tool-iteration limit before writing this file; saved by parent agent from summary.
