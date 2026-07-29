# trove.scot MCP Server

MCP server for Scotland's historic environment — 313K+ heritage records (castles, monuments, listed buildings, archaeological sites) via [Historic Environment Scotland](https://www.historicenvironment.scot)'s open ArcGIS API, the backend for [trove.scot](https://www.trove.scot). No auth required.

## Tools

- **search_heritage(term, sitetype?, council?, broadclass?, limit=50)** — Search Scotland's National Record of the Historic Environment (Canmore). Filter by name, with optional site-type, council-area, and broad-classification filters. Returns site names, types, locations (lat/lon), and trove.scot links.
- **get_heritage_by_id(canmore_id)** — Full details of a specific site by its Canmore ID (the number at the end of a `trove.scot/place/{id}` URL, e.g. `52068` for Edinburgh Castle). Returns the complete record with lat/lon and a trove.scot link.
- **count_heritage(term?, sitetype?, council?)** — Count how many sites match a search without fetching the records. Useful to check whether a query is too broad (results cap at 1000) before searching.
- **heritage_near(lat, lon, radius_km=1.0, term?)** — Find heritage sites near a geographic point (WGS84 lat/lon). Builds a bounding box around the point and returns up to 50 sites sorted nearest-first, each with a `distance_km` field. Optional name filter.
- **search_listed_buildings(term?, category?, local_authority?, limit=50)** — Search Scotland's 67K+ listed buildings. Filter by name/address, listing grade (`A`, `B`, or `C` — A is highest significance), and local authority. Returns building name/address, category grade, date designated, location (lat/lon), and a link.
- **search_scheduled_monuments(term?, local_authority?, limit=50)** — Search Scotland's scheduled monuments — nationally important archaeological sites protected by law. Filter by name and local authority. Returns monument name, class, area, location (lat/lon), and a link.
- **list_properties_in_care(local_authority?, term?, limit=100)** — List the castles, abbeys, standing stones, and other monuments that HES manages on behalf of the nation (e.g. Edinburgh Castle, Melrose Abbey, Callanish Stones). Filter by name or local authority.

## Features

- No API key required — uses HES's free, open ArcGIS REST API
- Automatic retry with exponential backoff (the ArcGIS server can be slow under load — timeouts, 502s, and 503s are retried)
- British National Grid → WGS84 coordinate conversion built in — every result includes `lat`/`lon` (the API only returns BNG eastings/northings)
- Count-before-fetch with explicit truncation warnings — the API caps results with no pagination, so the tools tell you when a query was cut off and how to narrow it
- trove.scot deep links on every record (`https://www.trove.scot/place/{id}`)

## Install

```bash
cd trove-scot-mcp
pip install -e .
```

## Configure (Hermes)

Add to your Hermes `config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  trove-scot:
    command: /mnt/HC_Volume_105667182/kimbo/.hermes/hermes-agent/venv/bin/python3
    args: ["-m", "trove_scot_mcp.server"]
```

Or for Claude Desktop / other MCP clients:

```json
{
  "mcpServers": {
    "trove-scot": {
      "command": "python3",
      "args": ["-m", "trove_scot_mcp.server"]
    }
  }
}
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## API Notes

- **Source:** Historic Environment Scotland ArcGIS REST API at `https://inspire.hes.scot/arcgis/rest/services` — the backend for trove.scot. No auth required.
- **Datasets:** Canmore / National Record of the Historic Environment (313K+ sites), plus designation layers for listed buildings (67K+), scheduled monuments, and properties in care.
- **Case handling:** Text data in Canmore is stored UPPERCASE and the server's `LIKE` is case-sensitive — but this server handles it for you (it wraps columns in `UPPER()` and uppercases your term). Just search normally, e.g. `term="castle"`.
- **Result caps (no pagination):** The API silently caps a single query at **1000** (Canmore), **5000** (listed buildings), and **10000** (scheduled monuments). There is no way to page beyond this. The tools count first and set a `truncated` flag plus a `note` when a query exceeds the cap — narrow your query with filters (sitetype, council, category, local authority) for complete results.
- **Coordinates:** The API returns British National Grid (OSGB36 / EPSG:27700) eastings/northings, not lat/lon. This server converts them to WGS84 `lat`/`lon` automatically (~5 m accuracy — plenty for locating sites on a map).
- **Reliability:** The ArcGIS server can be slow under load. The client uses a 20 s timeout and retries transient failures (timeouts, connection errors, HTTP 502/503) automatically with backoff (1 s, 2 s). If it reports the service as unavailable, wait a moment and try again.
- **Data licence:** Open Government Licence (per the API records).
