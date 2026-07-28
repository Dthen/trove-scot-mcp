# trove.scot MCP Server

MCP server for [trove.scot](https://www.trove.scot) — Historic Environment Scotland's portal for Scotland's historic environment. 3M+ records covering 5,000 years: listed buildings, scheduled monuments, battlefields, archaeological sites, properties in care, and archival images.

## Why

- Zero existing MCP coverage — nobody has touched Scottish heritage data
- Incredibly rich data: 320,000+ archaeological sites, 1.3M archive items, all listed buildings, scheduled monuments, battlefields, conservation areas
- Replaced Canmore/SCRAN in Feb 2025 — this is now *the* canonical source for Scotland's historic environment

## API Notes

- **Auth:** Unknown — no documented public API
- **No official REST API documented.** However:
  - The website search at trove.scot/search must talk to *some* backend — needs investigation
  - Bulk data downloads available (GIS shapefiles, CSVs) for statutory datasets
  - Spatial data via spatialdata.gov.scot (OGC WFS/WMS endpoints)
- **Possible approaches:**
  1. **Discover the undocumented API** — inspect network traffic from the website search, likely JSON endpoints behind the frontend
  2. **Bulk download → local SQLite** — download CSVs/GIS data, build a local queryable index
  3. **WFS endpoints** — OGC-standard spatial data queries (XML-heavy but works)

## Available Bulk Datasets

- Listed buildings (statutory addresses + supplementary info)
- Scheduled monuments
- Gardens and designed landscapes
- Battlefields (Inventory of Historic Battlefields)
- Historic Marine Protected Areas
- Conservation areas
- World Heritage Sites
- Properties in care (castles, abbeys, standing stones)
- National Record of the Historic Environment (320K+ sites, 1.3M archive items)
- Historic Landuse Assessment
- Scottish Radiocarbon Index (5,000+ radiocarbon dates)
- Buildings at Risk Register

## Rough Tool Ideas

- `search_places(query, area?)` — search the NRHE catalogue
- `get_place(id)` — full details for a historic site/building
- `listed_buildings(area?, category?)` — search listed buildings
- `scheduled_monuments(area?)` — scheduled monument records
- `battlefields()` — inventory of historic battlefields
- `properties_in_care()` — HES-managed castles, abbeys, stones
- `buildings_at_risk(area?)` — threatened historic buildings
- `map_search(lat, lon, radius?)` — find historic sites near a location

## Status

⚠️ **Before proceeding:** This needs proper research and planning before any code is written. Use the `plan` skill for a thorough execution plan and `subagent-driven-development` for implementation. Research first, build second.

### Research TODO
- [ ] **Priority:** Inspect trove.scot website network traffic to discover undocumented API endpoints
- [ ] Check if the search backend is Elasticsearch, Solr, or something else
- [ ] Test discovered endpoints for stability and rate limits
- [ ] Download and inspect bulk data formats (CSV, shapefile structure)
- [ ] Investigate spatialdata.gov.scot WFS endpoints for HES layers
- [ ] Check if the old Canmore API endpoints still work (redirected?)
- [ ] Evaluate approach: undocumented API vs. local SQLite index vs. WFS
- [ ] Decide: TypeScript or Python?
