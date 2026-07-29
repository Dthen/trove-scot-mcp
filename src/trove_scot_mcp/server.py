"""FastMCP server exposing Scotland's historic environment as MCP tools.

Data comes from the Historic Environment Scotland (HES) ArcGIS REST API, the
backend for trove.scot — over 313,000 heritage records (castles, monuments,
listed buildings, archaeological sites) plus designation layers (listed
buildings, scheduled monuments, properties in care).

These tools query the Canmore layer (Scotland's National Record of the Historic
Environment, NRHE) via :class:`trove_scot_mcp.client.HesClient`.

Server quirks handled here (see RESEARCH-ARCGIS.md):
- Text data is UPPERCASE and ``LIKE`` is case-sensitive, so every text filter
  wraps the column in ``UPPER()`` and uppercases the search term.
- There is NO pagination; results silently cap at 1000. We always count first
  and flag truncation so callers know to narrow broad queries.
- ``XCOORD``/``YCOORD`` are British National Grid, converted to lat/lon via
  :func:`trove_scot_mcp.client.enrich_with_latlon`.
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from trove_scot_mcp.client import HesClient, HesError, enrich_with_latlon

# Canmore (NRHE) terrestrial points layer.
CANMORE_LAYER = "CANMORE/Canmore_Points/MapServer/0"

# HES designation layers (the backend for trove.scot's designation datasets).
LISTED_BUILDINGS_LAYER = "HES/Listed_Buildings/MapServer/0"
SCHEDULED_MONUMENTS_LAYER = "HES/Scheduled_Monuments/MapServer/0"
PROPERTIES_IN_CARE_LAYER = "HES/Properties_in_care_points/MapServer/0"

# The server silently caps query results at maxRecordCount (1000 for Canmore)
# with no pagination. Counts above this mean the true result set is larger than
# anything a single fetch can return, so we flag it and ask callers to narrow.
CANMORE_ROW_CAP = 1000
# Designation layers have higher caps than Canmore, so truncation is rarer, but
# broad queries can still exceed them — we flag it the same way.
LISTED_BUILDINGS_ROW_CAP = 5000
SCHEDULED_MONUMENTS_ROW_CAP = 10000
PROPERTIES_IN_CARE_ROW_CAP = 1000

# Concise field set for list/search results (keeps payloads small). Full records
# (get-by-id) use "*" instead.
_SEARCH_FIELDS = (
    "CANMOREID,NMRSNAME,SITETYPE,BROADCLASS,COUNCIL,COUNTY,PARISH,"
    "GRIDREF,URL,XCOORD,YCOORD"
)
_NEAR_FIELDS = "CANMOREID,NMRSNAME,SITETYPE,COUNCIL,GRIDREF,URL,XCOORD,YCOORD"

# Designation-layer field sets (X/Y are BNG coords, enriched to lat/lon).
_LISTED_BUILDING_FIELDS = (
    "DES_TITLE,ENT_TITLE,CATEGORY,DESIGNATED,LOCAL_AUTH,CLASS,PARBUR,LINK,X,Y"
)
_SCHEDULED_MONUMENT_FIELDS = (
    "DES_TITLE,CLASS,CATEGORY,AREA,LOCAL_AUTH,PARISH,DESIGNATED,LINK,X,Y"
)
_PROPERTIES_IN_CARE_FIELDS = "PIC_ID,PIC_NAME,LOCAL_AUTH,LINK,X,Y"


@asynccontextmanager
async def _lifespan(app: FastMCP):
    """Manage the shared client's lifecycle: yield on startup, close on shutdown."""
    try:
        yield
    finally:
        await _client.aclose()


mcp = FastMCP(
    "trove-scot",
    instructions=(
        "Search Scotland's historic environment — 313K+ heritage records "
        "(castles, monuments, listed buildings, archaeological sites) via "
        "Historic Environment Scotland"
    ),
    lifespan=_lifespan,
)

# Single shared client instance (module-level).
_client = HesClient()


# ---------------------------------------------------------------------------
# Query-building helpers
# ---------------------------------------------------------------------------


def _like_term(term: str) -> str:
    """Uppercase a search term and wrap it as a SQL ``LIKE`` wildcard pattern.

    Canmore text data is stored in UPPERCASE and ``LIKE`` is case-sensitive, so
    the pattern must be uppercased to match. ``"castle"`` → ``"'%CASTLE%'"``.
    """
    return f"'%{term.strip().upper()}%'"


def _build_where(
    term: str | None = None,
    sitetype: str | None = None,
    council: str | None = None,
    broadclass: str | None = None,
) -> str:
    """Build an ArcGIS ``where`` clause from a name term and optional filters.

    The name term matches ``NMRSNAME`` (the primary site name). Each optional
    filter is AND-ed in as an ``UPPER(column) LIKE '%TERM%'`` substring match —
    substring (not equality) so that e.g. ``council="edinburgh"`` absorbs the
    ``"EDINBURGH, CITY OF"`` suffix and ``broadclass="defence"`` matches the
    comma-joined multi-tag ``BROADCLASS`` field.

    With no arguments at all, returns ``"1=1"`` (match everything).
    """
    clauses: list[str] = []
    if term:
        clauses.append(f"UPPER(NMRSNAME) LIKE {_like_term(term)}")
    if sitetype:
        clauses.append(f"UPPER(SITETYPE) LIKE {_like_term(sitetype)}")
    if council:
        clauses.append(f"UPPER(COUNCIL) LIKE {_like_term(council)}")
    if broadclass:
        clauses.append(f"UPPER(BROADCLASS) LIKE {_like_term(broadclass)}")
    return " AND ".join(clauses) if clauses else "1=1"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    r = 6371.0  # mean Earth radius, km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_heritage(
    term: str,
    sitetype: str | None = None,
    council: str | None = None,
    broadclass: str | None = None,
    limit: int = 50,
) -> dict[str, Any] | str:
    """Search Scotland's National Record of the Historic Environment (Canmore) — 313K+ heritage sites including castles, monuments, churches, archaeological sites, and historic buildings. Search by name, with optional filters for site type, council area, and broad classification. Returns site names, types, locations (lat/lon), and trove.scot links.

    Args:
        term: Name text to search for (matched case-insensitively against the
            site name, e.g. "Edinburgh Castle", "castle", "standing stone").
        sitetype: Optional site-type filter, substring-matched (e.g. "castle",
            "church", "fort").
        council: Optional council-area filter, substring-matched (e.g.
            "edinburgh", "fife", "glasgow"). Absorbs the ", CITY OF" suffix used
            for cities.
        broadclass: Optional broad-classification filter, substring-matched
            (e.g. "defence", "religious", "domestic").
        limit: Maximum number of sites to return (default 50).

    Returns an object with ``count`` (sites returned), ``total_found`` (total
    matches), ``truncated`` (True when more matched than could be returned), an
    optional ``note`` explaining truncation, and ``sites`` (list of site dicts
    with name, type, classification, council, county, parish, grid reference,
    lat/lon, and a trove.scot URL). Returns a friendly message string when
    nothing matches, or an ``Error:`` string on failure.
    """
    try:
        where = _build_where(term, sitetype=sitetype, council=council, broadclass=broadclass)
        total_found = await _client.count(CANMORE_LAYER, where)
        if total_found == 0:
            return (
                f"No heritage sites matched term={term!r}"
                + (f" with sitetype={sitetype!r}" if sitetype else "")
                + (f" in council={council!r}" if council else "")
                + (f" with broadclass={broadclass!r}" if broadclass else "")
                + ". Try a broader term or fewer filters."
            )

        features = await _client.fetch_features(
            CANMORE_LAYER, where, out_fields=_SEARCH_FIELDS
        )
        for f in features:
            enrich_with_latlon(f)

        truncated = total_found > CANMORE_ROW_CAP or total_found > limit
        sites = features[:limit]
        out: dict[str, Any] = {
            "count": len(sites),
            "total_found": total_found,
            "truncated": truncated,
            "sites": sites,
        }
        if truncated:
            if total_found > CANMORE_ROW_CAP:
                out["note"] = (
                    f"{total_found} sites matched but the API caps a single query at "
                    f"{CANMORE_ROW_CAP} with no pagination, so results are incomplete. "
                    "Narrow the query (add a sitetype, council, or broadclass filter, or "
                    "a more specific term) for complete results."
                )
            else:
                out["note"] = (
                    f"{total_found} sites matched but only the first {len(sites)} were "
                    f"returned (limit={limit}). Raise 'limit' to see more."
                )
        return out
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


@mcp.tool()
async def get_heritage_by_id(canmore_id: int) -> dict[str, Any] | str:
    """Get full details of a specific heritage site by its Canmore ID. Returns the complete record including name, type, classification, location (lat/lon), grid reference, and trove.scot link.

    Args:
        canmore_id: The stable Canmore ID of the site (e.g. 52068 for Edinburgh
            Castle). This is the number at the end of a trove.scot/place/{id} URL.

    Returns the complete record dict (all fields) with lat/lon added, or an
    ``Error:`` string if no site has that ID or the request fails.
    """
    try:
        features = await _client.fetch_features(
            CANMORE_LAYER, f"CANMOREID={int(canmore_id)}", out_fields="*"
        )
        if not features:
            return f"Error: no heritage site found with Canmore ID {canmore_id}"
        record = features[0]
        enrich_with_latlon(record)
        return record
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


@mcp.tool()
async def count_heritage(
    term: str | None = None,
    sitetype: str | None = None,
    council: str | None = None,
) -> dict[str, Any] | str:
    """Count how many heritage sites match a search, without fetching the records. Useful to check if a query is too broad (results cap at 1000) before searching.

    Args:
        term: Optional name text to match against the site name.
        sitetype: Optional site-type filter, substring-matched.
        council: Optional council-area filter, substring-matched.

    With no arguments, counts all records in the Canmore layer. Returns an
    object with ``total_found`` and, when the count exceeds the 1000-per-query
    cap, a ``note`` warning that a search would be truncated. Returns an
    ``Error:`` string on failure.
    """
    try:
        where = _build_where(term, sitetype=sitetype, council=council)
        total_found = await _client.count(CANMORE_LAYER, where)
        out: dict[str, Any] = {"total_found": total_found}
        if total_found > CANMORE_ROW_CAP:
            out["note"] = (
                f"{total_found} matches exceed the {CANMORE_ROW_CAP}-per-query cap — "
                "a search would be truncated. Narrow the query for complete results."
            )
        return out
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


@mcp.tool()
async def heritage_near(
    lat: float,
    lon: float,
    radius_km: float = 1.0,
    term: str | None = None,
) -> dict[str, Any] | str:
    """Find heritage sites near a geographic point (lat/lon in WGS84). Builds a bounding box around the point and returns matching sites sorted by distance. Optional name filter.

    Args:
        lat: Latitude of the centre point in decimal degrees (WGS84).
        lon: Longitude of the centre point in decimal degrees (WGS84).
        radius_km: Search radius in kilometres (default 1.0). Used to build a
            square bounding box around the point.
        term: Optional name text to filter results by (substring match).

    Returns an object with ``count`` and ``sites`` — up to 50 sites sorted
    nearest-first, each with a ``distance_km`` field plus name, type, council,
    grid reference, lat/lon, and trove.scot URL. Returns an ``Error:`` string on
    failure.
    """
    try:
        lat = float(lat)
        lon = float(lon)
        radius_km = float(radius_km)

        # Rough degree deltas for a square envelope around the point. One degree
        # of latitude ≈ 111 km; longitude degrees shrink with cos(latitude).
        lat_delta = radius_km / 111.0
        cos_lat = math.cos(math.radians(lat))
        # Guard against division by ~0 near the poles (not relevant for Scotland
        # but keeps the helper robust).
        lon_delta = radius_km / (111.0 * cos_lat) if cos_lat > 1e-6 else 360.0
        envelope = (
            f"{lon - lon_delta},{lat - lat_delta},"
            f"{lon + lon_delta},{lat + lat_delta}"
        )

        where = f"UPPER(NMRSNAME) LIKE {_like_term(term)}" if term else "1=1"
        features = await _client.fetch_features(
            CANMORE_LAYER,
            where,
            out_fields=_NEAR_FIELDS,
            geometry=envelope,
            geometry_type="esriGeometryEnvelope",
            in_sr=4326,
        )
        for f in features:
            enrich_with_latlon(f)

        # Sort client-side by true great-circle distance; drop records we could
        # not geocode (no lat/lon) since they cannot be ranked.
        geocoded = [f for f in features if "lat" in f and "lon" in f]
        for f in geocoded:
            f["distance_km"] = round(_haversine_km(lat, lon, f["lat"], f["lon"]), 3)
        geocoded.sort(key=lambda f: f["distance_km"])

        sites = geocoded[:50]
        return {"count": len(sites), "sites": sites}
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


@mcp.tool()
async def search_listed_buildings(
    term: str | None = None,
    category: str | None = None,
    local_authority: str | None = None,
    limit: int = 50,
) -> dict[str, Any] | str:
    """Search Scotland's listed buildings — 67K+ buildings of special architectural or historic interest. Filter by name/address, listing category (A, B, or C — A being highest significance), and local authority. Returns building name/address, category grade, date designated, location (lat/lon), and a link.

    Args:
        term: Optional name/address text to search for (matched
            case-insensitively against the building's name and address, e.g.
            "castle", "Edinburgh", "church").
        category: Optional listing grade filter — exactly "A", "B", or "C"
            (case-insensitive). A is the highest significance.
        local_authority: Optional council-area filter, substring-matched
            case-insensitively (e.g. "edinburgh", "fife", "glasgow").
        limit: Maximum number of buildings to return (default 50).

    Returns an object with ``count`` (buildings returned), ``total_found``
    (total matches), ``truncated`` (True when more matched than could be
    returned), an optional ``note`` explaining truncation, and ``buildings``
    (list of dicts with name/address, category grade, date designated, local
    authority, class, lat/lon, and a link). Returns a friendly message string
    when nothing matches, or an ``Error:`` string on failure.
    """
    try:
        clauses: list[str] = []
        if term:
            clauses.append(f"UPPER(DES_TITLE) LIKE {_like_term(term)}")
        if category:
            cat = category.strip().upper()
            if cat not in {"A", "B", "C"}:
                return "Error: category must be A, B, or C"
            clauses.append(f"CATEGORY = '{cat}'")
        if local_authority:
            clauses.append(f"UPPER(LOCAL_AUTH) LIKE {_like_term(local_authority)}")
        where = " AND ".join(clauses) if clauses else "1=1"

        total_found = await _client.count(LISTED_BUILDINGS_LAYER, where)
        if total_found == 0:
            return (
                f"No listed buildings matched term={term!r}"
                + (f" with category={category!r}" if category else "")
                + (f" in local_authority={local_authority!r}" if local_authority else "")
                + ". Try a broader term or fewer filters."
            )

        features = await _client.fetch_features(
            LISTED_BUILDINGS_LAYER, where, out_fields=_LISTED_BUILDING_FIELDS
        )
        for f in features:
            enrich_with_latlon(f)

        truncated = total_found > LISTED_BUILDINGS_ROW_CAP or total_found > limit
        buildings = features[:limit]
        out: dict[str, Any] = {
            "count": len(buildings),
            "total_found": total_found,
            "truncated": truncated,
            "buildings": buildings,
        }
        if truncated:
            if total_found > LISTED_BUILDINGS_ROW_CAP:
                out["note"] = (
                    f"{total_found} buildings matched but the API caps a single query "
                    f"at {LISTED_BUILDINGS_ROW_CAP} with no pagination, so results are "
                    "incomplete. Narrow the query (add a category or local-authority "
                    "filter, or a more specific term) for complete results."
                )
            else:
                out["note"] = (
                    f"{total_found} buildings matched but only the first {len(buildings)} "
                    f"were returned (limit={limit}). Raise 'limit' to see more."
                )
        return out
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


@mcp.tool()
async def search_scheduled_monuments(
    term: str | None = None,
    local_authority: str | None = None,
    limit: int = 50,
) -> dict[str, Any] | str:
    """Search Scotland's scheduled monuments — nationally important archaeological sites and historic monuments protected by law. Filter by name and local authority. Returns monument name, class, area, location (lat/lon), and a link.

    Args:
        term: Optional name text to search for (matched case-insensitively
            against the monument name, e.g. "castle", "broch", "standing stone").
        local_authority: Optional council-area filter, substring-matched
            case-insensitively (e.g. "highland", "orkney", "fife").
        limit: Maximum number of monuments to return (default 50).

    Returns an object with ``count`` (monuments returned), ``total_found``
    (total matches), ``truncated`` (True when more matched than could be
    returned), an optional ``note`` explaining truncation, and ``monuments``
    (list of dicts with name, class, category, area, local authority, parish,
    date designated, lat/lon, and a link). Returns a friendly message string
    when nothing matches, or an ``Error:`` string on failure.
    """
    try:
        clauses: list[str] = []
        if term:
            clauses.append(f"UPPER(DES_TITLE) LIKE {_like_term(term)}")
        if local_authority:
            clauses.append(f"UPPER(LOCAL_AUTH) LIKE {_like_term(local_authority)}")
        where = " AND ".join(clauses) if clauses else "1=1"

        total_found = await _client.count(SCHEDULED_MONUMENTS_LAYER, where)
        if total_found == 0:
            return (
                f"No scheduled monuments matched term={term!r}"
                + (f" in local_authority={local_authority!r}" if local_authority else "")
                + ". Try a broader term or fewer filters."
            )

        features = await _client.fetch_features(
            SCHEDULED_MONUMENTS_LAYER, where, out_fields=_SCHEDULED_MONUMENT_FIELDS
        )
        for f in features:
            enrich_with_latlon(f)

        truncated = total_found > SCHEDULED_MONUMENTS_ROW_CAP or total_found > limit
        monuments = features[:limit]
        out: dict[str, Any] = {
            "count": len(monuments),
            "total_found": total_found,
            "truncated": truncated,
            "monuments": monuments,
        }
        if truncated:
            if total_found > SCHEDULED_MONUMENTS_ROW_CAP:
                out["note"] = (
                    f"{total_found} monuments matched but the API caps a single query "
                    f"at {SCHEDULED_MONUMENTS_ROW_CAP} with no pagination, so results "
                    "are incomplete. Narrow the query (add a local-authority filter or a "
                    "more specific term) for complete results."
                )
            else:
                out["note"] = (
                    f"{total_found} monuments matched but only the first {len(monuments)} "
                    f"were returned (limit={limit}). Raise 'limit' to see more."
                )
        return out
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


@mcp.tool()
async def list_properties_in_care(
    local_authority: str | None = None,
    term: str | None = None,
    limit: int = 100,
) -> dict[str, Any] | str:
    """List Historic Environment Scotland properties in care — the castles, abbeys, standing stones, and other monuments that HES manages on behalf of the nation (e.g. Edinburgh Castle, Melrose Abbey, Callanish Stones). Filter by name or local authority.

    Args:
        local_authority: Optional council-area filter, substring-matched
            case-insensitively (e.g. "edinburgh", "highland", "fife").
        term: Optional name text to search for (matched case-insensitively
            against the property name, e.g. "castle", "abbey", "stones").
        limit: Maximum number of properties to return (default 100 — this is a
            small layer of ~300 HES-managed sites).

    Returns an object with ``count`` (properties returned), ``total_found``
    (total matches), ``truncated`` (True when more matched than could be
    returned), an optional ``note`` explaining truncation, and ``properties``
    (list of dicts with property ID, name, local authority, lat/lon, and a
    link). Returns a friendly message string when nothing matches, or an
    ``Error:`` string on failure.
    """
    try:
        clauses: list[str] = []
        if term:
            clauses.append(f"UPPER(PIC_NAME) LIKE {_like_term(term)}")
        if local_authority:
            clauses.append(f"UPPER(LOCAL_AUTH) LIKE {_like_term(local_authority)}")
        where = " AND ".join(clauses) if clauses else "1=1"

        total_found = await _client.count(PROPERTIES_IN_CARE_LAYER, where)
        if total_found == 0:
            return (
                f"No properties in care matched term={term!r}"
                + (f" in local_authority={local_authority!r}" if local_authority else "")
                + ". Try a broader term or fewer filters."
            )

        features = await _client.fetch_features(
            PROPERTIES_IN_CARE_LAYER, where, out_fields=_PROPERTIES_IN_CARE_FIELDS
        )
        for f in features:
            enrich_with_latlon(f)

        truncated = total_found > PROPERTIES_IN_CARE_ROW_CAP or total_found > limit
        properties = features[:limit]
        out: dict[str, Any] = {
            "count": len(properties),
            "total_found": total_found,
            "truncated": truncated,
            "properties": properties,
        }
        if truncated:
            if total_found > PROPERTIES_IN_CARE_ROW_CAP:
                out["note"] = (
                    f"{total_found} properties matched but the API caps a single query "
                    f"at {PROPERTIES_IN_CARE_ROW_CAP} with no pagination, so results are "
                    "incomplete. Narrow the query for complete results."
                )
            else:
                out["note"] = (
                    f"{total_found} properties matched but only the first {len(properties)} "
                    f"were returned (limit={limit}). Raise 'limit' to see more."
                )
        return out
    except (HesError, httpx.HTTPError, ValueError, KeyError) as e:
        return f"Error: {e}"


def main() -> None:
    """Entry point for running the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
