#!/usr/bin/env python3
"""trove-scot-mcp: stateless 2026-07-28 era MCP server (stdio loop).

REFERENCE §1–§3, §4 (TOOLS literal), §6–§7 copied verbatim from
/home/kimbo/.hermes/plans/mcp-2x-migration/REFERENCE.md per PLAN D1
("copied from REFERENCE.md, never re-derived"). §5 (tools/call handler
bodies) lands in T07 — the dispatch here owns only the -32602 params
guard (F6) and routes every call to a placeholder.

Data comes from the Historic Environment Scotland (HES) ArcGIS REST API,
the backend for trove.scot — over 313,000 heritage records (castles,
monuments, listed buildings, archaeological sites) plus designation layers
(listed buildings, scheduled monuments, properties in care).

These tools query the Canmore layer (Scotland's National Record of the
Historic Environment, NRHE) via :class:`trove_scot_mcp.client.HesClient`.

Server quirks handled here (see RESEARCH-ARCGIS.md):
- Text data is UPPERCASE and ``LIKE`` is case-sensitive, so every text filter
  wraps the column in ``UPPER()`` and uppercases the search term.
- There is NO pagination; results silently cap at 1000. We always count first
  and flag truncation so callers know to narrow broad queries.
- ``XCOORD``/``YCOORD`` are British National Grid, converted to lat/lon via
  :func:`trove_scot_mcp.client.enrich_with_latlon`.
"""

from __future__ import annotations

import inspect
import json
import math
import sys
import urllib.error
from typing import Any, cast

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

# lifespan() deleted (was: close the async HTTP client on shutdown); sync urllib holds no state (B.4, tag 692af2a verified)

# Single shared client instance (module-level).
_client = HesClient()


# ---------------------------------------------------------------------------
# Query-building helpers
# ---------------------------------------------------------------------------


# Suffix appended to every ``LIKE`` clause so the backslash is treated as the
# escape character. Without it, the ``\``%``/``\``_`` sequences that ``_like_term``
# emits would be read as a literal backslash followed by a wildcard, which is
# wrong. Verified against the live ArcGIS API: with ``ESCAPE '\'`` a search for
# ``100%`` matches only records literally containing "100%", and ``a_b`` matches
# only a literal underscore (the bracket form ``[%]``/``[_]`` proved unreliable
# on this server, returning inconsistent counts).
_LIKE_ESCAPE_SUFFIX = " ESCAPE '\\'"


def _like_term(term: str) -> str:
    """Uppercase a search term and wrap it as a SQL ``LIKE`` wildcard pattern.

    Canmore text data is stored in UPPERCASE and ``LIKE`` is case-sensitive, so
    the pattern must be uppercased to match. ``"castle"`` → ``"'%CASTLE%'"``.

    Escaping (all literal, so user input can never inject a wildcard or break
    the SQL):
    - ``\`` → ``\\`` first, so the backslashes we add below aren't doubled.
    - ``%`` → ``\%`` and ``_`` → ``\_`` so a literal percent/underscore in the
      term matches literally instead of acting as a SQL wildcard (paired with
      ``_LIKE_ESCAPE_SUFFIX`` on the clause). Without this, ``"100%"`` matched
      every record containing "100".
    - ``'`` → ``''`` (standard SQL escaping) so terms like ``"St Mary's"``
      produce a balanced, valid ``LIKE`` pattern rather than broken SQL that the
      ArcGIS server rejects with a 400.

    The backslash replacement runs first so the escape pairs added afterwards for
    percent and underscore are not themselves doubled. With the ``ESCAPE`` clause
    active, a doubled backslash in the pattern denotes ONE literal backslash in
    the data — so a single-backslash search term correctly matches a
    single-backslash name (and does not over-match a two-backslash name). This is
    verified against a real SQL engine and the live HES API by the
    self-consistency tests in ``tests/test_tools.py``.
    """
    escaped = (
        term.strip()
        .upper()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("'", "''")
    )
    return f"'%{escaped}%'"


def _like_clause(column: str, term: str) -> str:
    """Build a full ``UPPER(column) LIKE '%TERM%' ESCAPE '\'`` clause.

    Centralises the column-wrapping, pattern building, and the ``ESCAPE`` suffix
    so every text filter escapes user-supplied wildcards consistently.
    """
    return f"UPPER({column}) LIKE {_like_term(term)}{_LIKE_ESCAPE_SUFFIX}"


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
        clauses.append(_like_clause("NMRSNAME", term))
    if sitetype:
        clauses.append(_like_clause("SITETYPE", sitetype))
    if council:
        clauses.append(_like_clause("COUNCIL", council))
    if broadclass:
        clauses.append(_like_clause("BROADCLASS", broadclass))
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
# Tool handlers (sync; dispatched by handle_call via the §1 loop)
# ---------------------------------------------------------------------------


def search_heritage(
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
        term = term.strip() if term else ""
        if not term:
            return "Error: please provide a search term"
        where = _build_where(term, sitetype=sitetype, council=council, broadclass=broadclass)
        total_found = _client.count(CANMORE_LAYER, where)
        if total_found == 0:
            return (
                f"No heritage sites matched term={term!r}"
                + (f" with sitetype={sitetype!r}" if sitetype else "")
                + (f" in council={council!r}" if council else "")
                + (f" with broadclass={broadclass!r}" if broadclass else "")
                + ". Try a broader term or fewer filters."
            )

        features = _client.fetch_features(
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
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


def get_heritage_by_id(canmore_id: int) -> dict[str, Any] | str:
    """Get full details of a specific heritage site by its Canmore ID. Returns the complete record including name, type, classification, location (lat/lon), grid reference, and trove.scot link.

    Args:
        canmore_id: The stable Canmore ID of the site (e.g. 52068 for Edinburgh
            Castle). This is the number at the end of a trove.scot/place/{id} URL.

    Returns the complete record dict (all fields) with lat/lon added, or an
    ``Error:`` string if no site has that ID or the request fails.
    """
    try:
        features = _client.fetch_features(
            CANMORE_LAYER, f"CANMOREID={int(canmore_id)}", out_fields="*"
        )
        if not features:
            return f"Error: no heritage site found with Canmore ID {canmore_id}"
        record = features[0]
        enrich_with_latlon(record)
        return record
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


def count_heritage(
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
        if term is not None and not term.strip():
            return "Error: please provide a search term (or omit term to count all records)"
        where = _build_where(term, sitetype=sitetype, council=council)
        total_found = _client.count(CANMORE_LAYER, where)
        out: dict[str, Any] = {"total_found": total_found}
        if total_found > CANMORE_ROW_CAP:
            out["note"] = (
                f"{total_found} matches exceed the {CANMORE_ROW_CAP}-per-query cap — "
                "a search would be truncated. Narrow the query for complete results."
            )
        return out
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


def heritage_near(
    lat: float,
    lon: float,
    radius_km: float = 1.0,
    term: str | None = None,
    limit: int = 50,
) -> dict[str, Any] | str:
    """Find heritage sites near a geographic point (lat/lon in WGS84). Builds a bounding box around the point and returns matching sites sorted by distance. Optional name filter.

    Args:
        lat: Latitude of the centre point in decimal degrees (WGS84).
        lon: Longitude of the centre point in decimal degrees (WGS84).
        radius_km: Search radius in kilometres (default 1.0). Used to build a
            square bounding box around the point.
        term: Optional name text to filter results by (substring match).
        limit: Maximum number of sites to return (default 50).

    Returns an object with ``count`` (sites returned), ``total_found`` (the true
    number of matches within the area, from a count query), ``truncated`` (True
    when more matched than could be returned), an optional ``note`` explaining
    truncation, and ``sites`` — up to ``limit`` sites sorted nearest-first, each
    with a ``distance_km`` field plus name, type, council, grid reference,
    lat/lon, and trove.scot URL. Returns an ``Error:`` string on bad input or
    failure.
    """
    try:
        lat = float(lat)
        lon = float(lon)
        radius_km = float(radius_km)
        if radius_km <= 0:
            return "Error: radius_km must be a positive number"

        lat_delta = radius_km / 111.0
        cos_lat = math.cos(math.radians(lat))
        lon_delta = radius_km / (111.0 * cos_lat) if cos_lat > 1e-6 else 360.0
        envelope = (
            f"{lon - lon_delta},{lat - lat_delta},"
            f"{lon + lon_delta},{lat + lat_delta}"
        )

        where = _like_clause("NMRSNAME", term) if term else "1=1"

        count_data = _client.query(
            CANMORE_LAYER,
            where,
            count_only=True,
            geometry=envelope,
            geometry_type="esriGeometryEnvelope",
            in_sr=4326,
        )
        total_found = int(count_data.get("count", 0))
        if total_found == 0:
            return {
                "count": 0,
                "total_found": 0,
                "truncated": False,
                "sites": [],
            }

        features = _client.fetch_features(
            CANMORE_LAYER,
            where,
            out_fields=_NEAR_FIELDS,
            geometry=envelope,
            geometry_type="esriGeometryEnvelope",
            in_sr=4326,
        )
        for f in features:
            enrich_with_latlon(f)

        geocoded = [f for f in features if "lat" in f and "lon" in f]
        for f in geocoded:
            f["distance_km"] = round(_haversine_km(lat, lon, f["lat"], f["lon"]), 3)
        geocoded.sort(key=lambda f: f["distance_km"])

        truncated = total_found > CANMORE_ROW_CAP or total_found > limit
        sites = geocoded[:limit]
        out: dict[str, Any] = {
            "count": len(sites),
            "total_found": total_found,
            "truncated": truncated,
            "sites": sites,
        }
        if truncated:
            if total_found > CANMORE_ROW_CAP:
                out["note"] = (
                    f"{total_found} sites matched within {radius_km} km but the API "
                    f"caps a single query at {CANMORE_ROW_CAP} with no pagination, so "
                    f"only the first {len(sites)} of a {CANMORE_ROW_CAP}-record subset "
                    "are shown. In dense areas the nearest-first ordering is "
                    "approximate (nearest within the fetched subset, which may not "
                    "include the true nearest sites). Narrow the area (smaller radius) "
                    "or add a term filter for complete results."
                )
            else:
                out["note"] = (
                    f"{total_found} sites matched within {radius_km} km but only the "
                    f"nearest {len(sites)} were returned (limit={limit}). Raise 'limit' "
                    "to see more."
                )
        return out
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


def search_listed_buildings(
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
            clauses.append(_like_clause("DES_TITLE", term))
        if category:
            cat = category.strip().upper()
            if cat not in {"A", "B", "C"}:
                return "Error: category must be A, B, or C"
            clauses.append(f"CATEGORY = '{cat}'")
        if local_authority:
            clauses.append(_like_clause("LOCAL_AUTH", local_authority))
        where = " AND ".join(clauses) if clauses else "1=1"

        total_found = _client.count(LISTED_BUILDINGS_LAYER, where)
        if total_found == 0:
            return (
                f"No listed buildings matched term={term!r}"
                + (f" with category={category!r}" if category else "")
                + (f" in local_authority={local_authority!r}" if local_authority else "")
                + ". Try a broader term or fewer filters."
            )

        features = _client.fetch_features(
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
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


def search_scheduled_monuments(
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
            clauses.append(_like_clause("DES_TITLE", term))
        if local_authority:
            clauses.append(_like_clause("LOCAL_AUTH", local_authority))
        where = " AND ".join(clauses) if clauses else "1=1"

        total_found = _client.count(SCHEDULED_MONUMENTS_LAYER, where)
        if total_found == 0:
            return (
                f"No scheduled monuments matched term={term!r}"
                + (f" in local_authority={local_authority!r}" if local_authority else "")
                + ". Try a broader term or fewer filters."
            )

        features = _client.fetch_features(
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
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


def list_properties_in_care(
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
            clauses.append(_like_clause("PIC_NAME", term))
        if local_authority:
            clauses.append(_like_clause("LOCAL_AUTH", local_authority))
        where = " AND ".join(clauses) if clauses else "1=1"

        total_found = _client.count(PROPERTIES_IN_CARE_LAYER, where)
        if total_found == 0:
            return (
                f"No properties in care matched term={term!r}"
                + (f" in local_authority={local_authority!r}" if local_authority else "")
                + ". Try a broader term or fewer filters."
            )

        features = _client.fetch_features(
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
    except (HesError, urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError) as e:
        return f"Error: {e}"


# ===========================================================================
# REFERENCE §1 — framing
# ===========================================================================

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(errors="replace")


def send(resp):
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


# ===========================================================================
# REFERENCE §1 — era constants
# ===========================================================================

ERA_VERSION = "2026-07-28"
SERVER_INFO = {"name": "trove-scot", "version": "0.3.0"}
ERA_RESULT_FIELDS = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
RESULT_META = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}


def era_result(payload):
    """A result carrying the era-strict fields D3 mandates on every response."""
    out = dict(payload)
    out.update(ERA_RESULT_FIELDS)
    out["_meta"] = RESULT_META
    return out


# ===========================================================================
# TOOLS literal from golden (REFERENCE §4, D4 byte-freeze)
# Generated from golden/trove-scot.tools.json
# sha256: c9075ff88dd67090ec3b8af1f778002d253ec162a2c989bed7d3a17d1157b140
# outputSchema and _meta stripped per F3.
# ===========================================================================

TOOLS = [
    {
        "name": "search_heritage",
        "description": "Search Scotland's National Record of the Historic Environment (Canmore) — 313K+ heritage sites including castles, monuments, churches, archaeological sites, and historic buildings. Search by name, with optional filters for site type, council area, and broad classification. Returns site names, types, locations (lat/lon), and trove.scot links.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "term": {
                    "type": "string",
                    "description": "Name text to search for (matched case-insensitively against the\nsite name, e.g. \"Edinburgh Castle\", \"castle\", \"standing stone\")."
                },
                "sitetype": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional site-type filter, substring-matched (e.g. \"castle\",\n\"church\", \"fort\")."
                },
                "council": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional council-area filter, substring-matched (e.g.\n\"edinburgh\", \"fife\", \"glasgow\"). Absorbs the \", CITY OF\" suffix used\nfor cities."
                },
                "broadclass": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional broad-classification filter, substring-matched\n(e.g. \"defence\", \"religious\", \"domestic\")."
                },
                "limit": {
                    "default": 50,
                    "type": "integer",
                    "description": "Maximum number of sites to return (default 50)."
                }
            },
            "required": ["term"],
            "type": "object"
        }
    },
    {
        "name": "get_heritage_by_id",
        "description": "Get full details of a specific heritage site by its Canmore ID. Returns the complete record including name, type, classification, location (lat/lon), grid reference, and trove.scot link.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "canmore_id": {
                    "type": "integer",
                    "description": "The stable Canmore ID of the site (e.g. 52068 for Edinburgh\nCastle). This is the number at the end of a trove.scot/place/{id} URL."
                }
            },
            "required": ["canmore_id"],
            "type": "object"
        }
    },
    {
        "name": "count_heritage",
        "description": "Count how many heritage sites match a search, without fetching the records. Useful to check if a query is too broad (results cap at 1000) before searching.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "term": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional name text to match against the site name."
                },
                "sitetype": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional site-type filter, substring-matched."
                },
                "council": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional council-area filter, substring-matched."
                }
            },
            "type": "object"
        }
    },
    {
        "name": "heritage_near",
        "description": "Find heritage sites near a geographic point (lat/lon in WGS84). Builds a bounding box around the point and returns matching sites sorted by distance. Optional name filter.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "lat": {
                    "type": "number",
                    "description": "Latitude of the centre point in decimal degrees (WGS84)."
                },
                "lon": {
                    "type": "number",
                    "description": "Longitude of the centre point in decimal degrees (WGS84)."
                },
                "radius_km": {
                    "default": 1.0,
                    "type": "number",
                    "description": "Search radius in kilometres (default 1.0). Used to build a\nsquare bounding box around the point."
                },
                "term": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional name text to filter results by (substring match)."
                },
                "limit": {
                    "default": 50,
                    "type": "integer",
                    "description": "Maximum number of sites to return (default 50)."
                }
            },
            "required": ["lat", "lon"],
            "type": "object"
        }
    },
    {
        "name": "search_listed_buildings",
        "description": "Search Scotland's listed buildings — 67K+ buildings of special architectural or historic interest. Filter by name/address, listing category (A, B, or C — A being highest significance), and local authority. Returns building name/address, category grade, date designated, location (lat/lon), and a link.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "term": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional name/address text to search for (matched\ncase-insensitively against the building's name and address, e.g.\n\"castle\", \"Edinburgh\", \"church\")."
                },
                "category": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional listing grade filter — exactly \"A\", \"B\", or \"C\"\n(case-insensitive). A is the highest significance."
                },
                "local_authority": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional council-area filter, substring-matched\ncase-insensitively (e.g. \"edinburgh\", \"fife\", \"glasgow\")."
                },
                "limit": {
                    "default": 50,
                    "type": "integer",
                    "description": "Maximum number of buildings to return (default 50)."
                }
            },
            "type": "object"
        }
    },
    {
        "name": "search_scheduled_monuments",
        "description": "Search Scotland's scheduled monuments — nationally important archaeological sites and historic monuments protected by law. Filter by name and local authority. Returns monument name, class, area, location (lat/lon), and a link.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "term": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional name text to search for (matched case-insensitively\nagainst the monument name, e.g. \"castle\", \"broch\", \"standing stone\")."
                },
                "local_authority": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional council-area filter, substring-matched\ncase-insensitively (e.g. \"highland\", \"orkney\", \"fife\")."
                },
                "limit": {
                    "default": 50,
                    "type": "integer",
                    "description": "Maximum number of monuments to return (default 50)."
                }
            },
            "type": "object"
        }
    },
    {
        "name": "list_properties_in_care",
        "description": "List Historic Environment Scotland properties in care — the castles, abbeys, standing stones, and other monuments that HES manages on behalf of the nation (e.g. Edinburgh Castle, Melrose Abbey, Callanish Stones). Filter by name or local authority.",
        "inputSchema": {
            "additionalProperties": False,
            "properties": {
                "local_authority": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional council-area filter, substring-matched\ncase-insensitively (e.g. \"edinburgh\", \"highland\", \"fife\")."
                },
                "term": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ],
                    "default": None,
                    "description": "Optional name text to search for (matched case-insensitively\nagainst the property name, e.g. \"castle\", \"abbey\", \"stones\")."
                },
                "limit": {
                    "default": 100,
                    "type": "integer",
                    "description": "Maximum number of properties to return (default 100 — this is a\nsmall layer of ~300 HES-managed sites)."
                }
            },
            "type": "object"
        }
    }
]


# ---------------------------------------------------------------------------
# handle_call — REFERENCE §5 dispatch + text encoding
# ---------------------------------------------------------------------------

# DEVIATION from REFERENCE §5 (justified: legacy tools return bare str on empty-match/error
# paths — golden/legacy-behavior.json pins them; passthrough verbatim per B.1 precedent; dict
# successes use fastmcp-era compact non-ascii-preserving JSON per §5's B.4 derivation rule)
def _encode_result(result):
    if isinstance(result, str):
        return result
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False)


# Frozen dispatch table (7 tools). kwargs built explicitly from the provided arguments
# dict — unknown keys are ignored (fastmcp leniency: extra keys must not TypeError).
def handle_call(name, arguments):
    args = arguments or {}
    if name == "search_heritage":
        return search_heritage(
            term=cast(str, args.get("term")),
            sitetype=cast(str, args.get("sitetype")) if args.get("sitetype") is not None else None,
            council=cast(str, args.get("council")) if args.get("council") is not None else None,
            broadclass=cast(str, args.get("broadclass")) if args.get("broadclass") is not None else None,
            limit=int(args.get("limit", 50)),
        )
    if name == "get_heritage_by_id":
        return get_heritage_by_id(canmore_id=int(args.get("canmore_id", 0)))
    if name == "count_heritage":
        return count_heritage(
            term=cast(str, args.get("term")) if args.get("term") is not None else None,
            sitetype=cast(str, args.get("sitetype")) if args.get("sitetype") is not None else None,
            council=cast(str, args.get("council")) if args.get("council") is not None else None,
        )
    if name == "heritage_near":
        return heritage_near(
            lat=float(args.get("lat", 0)),
            lon=float(args.get("lon", 0)),
            radius_km=float(args.get("radius_km", 1.0)),
            term=cast(str, args.get("term")) if args.get("term") is not None else None,
            limit=int(args.get("limit", 50)),
        )
    if name == "search_listed_buildings":
        return search_listed_buildings(
            term=cast(str, args.get("term")) if args.get("term") is not None else None,
            category=cast(str, args.get("category")) if args.get("category") is not None else None,
            local_authority=cast(str, args.get("local_authority")) if args.get("local_authority") is not None else None,
            limit=int(args.get("limit", 50)),
        )
    if name == "search_scheduled_monuments":
        return search_scheduled_monuments(
            term=cast(str, args.get("term")) if args.get("term") is not None else None,
            local_authority=cast(str, args.get("local_authority")) if args.get("local_authority") is not None else None,
            limit=int(args.get("limit", 50)),
        )
    if name == "list_properties_in_care":
        return list_properties_in_care(
            local_authority=cast(str, args.get("local_authority")) if args.get("local_authority") is not None else None,
            term=cast(str, args.get("term")) if args.get("term") is not None else None,
            limit=int(args.get("limit", 100)),
        )
    return {"error": f"Unknown tool: {name}"}


# ===========================================================================
# Main loop (REFERENCE §1–§3, §6–§7)
# ===========================================================================


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: req = json.loads(line)
        except Exception: continue
        if not isinstance(req, dict): continue
        rid = req.get("id")
        method = req.get("method")
        if not isinstance(method, str): method = ""

        if method == "server/discover":
            # §2
            send({"jsonrpc":"2.0","id":rid,"result":era_result({
                "supportedVersions":[ERA_VERSION],
                "capabilities":{"tools":{}}})})
        elif method == "tools/list":
            # §4
            send({"jsonrpc":"2.0","id":rid,"result":era_result({"tools":TOOLS})})
        elif method == "tools/call":
            # §5 — F6: -32602 params guard mandatory; handler dispatch via handle_call
            params = req.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32602,
                    "message":"missing required param: params (with string 'name')"}})
                continue
            result = handle_call(params["name"], params.get("arguments", {}))
            is_err = isinstance(result, dict) and "error" in result
            payload: dict = {"content": [{"type": "text", "text": _encode_result(result)}]}
            if is_err:
                payload["isError"] = True
            send({"jsonrpc":"2.0","id":rid,"result":era_result(payload)})
        elif method == "ping":
            # §6
            send({"jsonrpc":"2.0","id":rid,"result":{}})
        elif method.startswith("notifications/"):
            # §6 — swallow
            pass
        else:
            # §3 catch-all (includes legacy initialize)
            if rid is None and "id" not in req: continue
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
