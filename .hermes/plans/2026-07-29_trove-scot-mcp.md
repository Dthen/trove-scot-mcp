# trove.scot MCP Server — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** MCP server wrapping the Historic Environment Scotland ArcGIS REST API — search Scotland's 313K+ heritage records (castles, monuments, listed buildings, archaeological sites) with no auth.

**Architecture:** Python FastMCP. Shared ArcGIS query helper (retry + timeout + URL-encoding). BNG→WGS84 coordinate conversion. Count-before-fetch with truncation signals. Uppercase LIKE patterns.

**Tech Stack:** Python 3.11, `mcp` SDK (FastMCP), `httpx`. Pure-Python OSGB36→WGS84 transform (avoid heavy pyproj dep).

**API base:** `https://inspire.hes.scot/arcgis/rest/services` (no auth, `f=json`)

---

### Task 1: Scaffolding + ArcGIS client

**Objective:** Project structure + the core query client with coordinate conversion.

**Files:**
- `pyproject.toml` (name trove-scot-mcp, deps mcp>=1.0 + httpx>=0.27, src layout, [dev] extras)
- `src/trove_scot_mcp/__init__.py`
- `src/trove_scot_mcp/server.py` (FastMCP skeleton: `mcp = FastMCP("trove-scot", instructions=...)`)
- `src/trove_scot_mcp/client.py` — `HesClient` class:
  - `query(layer, where, out_fields="*", geometry=None, count_only=False, distinct=False)` — builds the ArcGIS query URL, URL-encodes, sets `returnGeometry=false`, `f=json`; retries on 502/503/timeout (2 retries, backoff); 20s timeout
  - `count(layer, where)` — `returnCountOnly=true`
  - BNG→WGS84: `bng_to_wgs84(easting, northing) -> (lat, lon)` pure-Python Helmert transform (OSGB36→WGS84, ~5m accuracy is fine for heritage sites). Document accuracy.
  - Result parser: extract `features[].attributes`, convert XCOORD/YCOORD (or X/Y) to lat/lon
- `tests/test_client.py` (MockTransport: query building, retry, count, BNG conversion against known point — Edinburgh Castle 325112/673497 ≈ 55.9486/-1.9486)
- `tests/test_server.py` (smoke)

**Verify:** `pip install -e .`, `python -c "from trove_scot_mcp.server import mcp; print(mcp.name)"`, `pytest -v`
**Commit:** scaffolding + client

### Task 2: Canmore tools

**Objective:** The core NRHE heritage search tools.

**Tools in server.py:**
1. `search_heritage(term, sitetype=None, council=None, broadclass=None, limit=50)` — `UPPER(NMRSNAME) LIKE '%TERM%'` + optional filters; count first; if count>1000 return truncation note; return concise records with lat/lon + trove.scot URL
2. `get_heritage_by_id(canmore_id)` — full record via `CANMOREID=n`, with lat/lon + URL
3. `count_heritage(term, sitetype=None, council=None)` — how many match
4. `heritage_near(lat, lon, radius_km=1, term=None)` — WGS84 envelope around point, sort by distance client-side

**Tests:** query construction (UPPER/LIKE), count-first logic, truncation flag, near-point bbox math, error handling
**Commit:** Canmore tools

### Task 3: Designation tools

**Objective:** Listed buildings, scheduled monuments, properties in care.

**Tools:**
5. `search_listed_buildings(term=None, category=None, local_authority=None, limit=50)` — `HES/Listed_Buildings/MapServer/0`, `UPPER(DES_TITLE) LIKE`, filter CATEGORY (A/B/C) + LOCAL_AUTH; returns grade + date designated + lat/lon
6. `search_scheduled_monuments(term=None, local_authority=None, limit=50)` — `HES/Scheduled_Monuments/MapServer/0`
7. `list_properties_in_care(local_authority=None)` — `HES/Properties_in_care_points/MapServer/0` (small layer, HES-managed castles/abbeys)

**Tests:** per-layer query construction, category filter, error handling
**Commit:** designation tools

### Task 4: README + integration test

**Objective:** Docs + end-to-end verification against live API.

- README (tools, install, Hermes/Claude config, API notes incl. the uppercase-LIKE and 1000-cap quirks)
- Live smoke test each tool (tolerate ArcGIS slowness)
**Commit:** docs
