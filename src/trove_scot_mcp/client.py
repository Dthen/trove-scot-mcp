"""Async HTTP client for the Historic Environment Scotland ArcGIS REST API.

HES exposes a keyless ArcGIS REST endpoint (the backend for trove.scot) with
over 313,000 heritage records plus designation layers (listed buildings,
scheduled monuments, properties in care). This client wraps the ``query``
operation with:

- a 20s timeout (the server is slow under load),
- retries with backoff for transient failures (502/503, timeouts, connection
  errors) — other errors fail immediately,
- helpers for ``returnCountOnly`` counts and feature extraction, and
- a pure-Python British National Grid (EPSG:27700) → WGS84 converter, since
  the API's ``XCOORD``/``YCOORD`` (and ``X``/``Y`` on designation layers) are
  OSGB36 eastings/northings, **not** lat/lon.

Server quirks (see RESEARCH-ARCGIS.md): pagination is unsupported
(``resultRecordCount``/``resultOffset`` → HTTP 400) and results silently cap at
``maxRecordCount`` — so callers should count before fetching. Text data is
UPPERCASE and ``LIKE`` is case-sensitive; wrap columns in ``UPPER()``.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

BASE_URL = "https://inspire.hes.scot/arcgis/rest/services"

# Default request timeout (seconds). The HES server can be slow under load.
DEFAULT_TIMEOUT = 20.0

# Retry configuration for transient failures: up to 2 retries (3 attempts
# total) with backoff delays of 1s, 2s.
MAX_RETRIES = 2
BACKOFF_BASE = 1.0  # seconds; delays are 1s, 2s

# HTTP status codes that warrant a retry.
RETRYABLE_STATUS = {502, 503}


class HesError(Exception):
    """Raised when an HES ArcGIS request ultimately fails or returns an error body."""


# ---------------------------------------------------------------------------
# British National Grid (OSGB36 / EPSG:27700) → WGS84 conversion
# ---------------------------------------------------------------------------
#
# Approximate Helmert 7-parameter transform following Ordnance Survey's
# "A guide to coordinate systems in Great Britain". This is NOT the high
# accuracy OSTN15 grid transformation — expect ~5 m horizontal accuracy, which
# is plenty for locating heritage sites on a map.

# Airy 1830 ellipsoid (OSGB36).
_AIRY_A = 6377563.396
_AIRY_B = 6356256.909

# WGS84 ellipsoid.
_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257223563
_WGS84_B = _WGS84_A * (1 - _WGS84_F)

# British National Grid projection constants.
_BNG_F0 = 0.9996012717  # scale factor on the central meridian
_BNG_LAT0 = math.radians(49.0)  # true origin latitude
_BNG_LON0 = math.radians(-2.0)  # true origin longitude (central meridian)
_BNG_N0 = -100000.0  # false northing
_BNG_E0 = 400000.0  # false easting

# Helmert 7 parameters for OSGB36 → WGS84 (OSTN-style "position vector" form).
_HELMERT_TX = 446.448
_HELMERT_TY = -125.157
_HELMERT_TZ = 542.060
_HELMERT_S = -20.4894  # parts per million
# Rotations in arc-seconds → radians.
_ARCSEC_TO_RAD = math.pi / (180.0 * 3600.0)
_HELMERT_RX = 0.1502 * _ARCSEC_TO_RAD
_HELMERT_RY = 0.2470 * _ARCSEC_TO_RAD
_HELMERT_RZ = 0.8421 * _ARCSEC_TO_RAD


def _bng_to_osgb36_latlon(easting: float, northing: float) -> tuple[float, float, float]:
    """Convert BNG easting/northing to OSGB36 lat/lon (radians) on the Airy ellipsoid.

    Returns ``(lat, lon, height)`` with height relative to the Airy ellipsoid
    (effectively 0 for surface points). Iterative, per the OS guide.
    """
    a, b = _AIRY_A, _AIRY_B
    f0 = _BNG_F0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)

    # Iteratively solve for latitude from the northing.
    lat = _BNG_LAT0
    m = 0.0
    while True:
        lat = (northing - _BNG_N0 - m) / (a * f0) + lat
        ma = (1 + n + (5 / 4) * n**2 + (5 / 4) * n**3) * (lat - _BNG_LAT0)
        mb = (3 * n + 3 * n**2 + (21 / 8) * n**3) * math.sin(lat - _BNG_LAT0) * math.cos(lat + _BNG_LAT0)
        mc = ((15 / 8) * n**2 + (15 / 8) * n**3) * math.sin(2 * (lat - _BNG_LAT0)) * math.cos(2 * (lat + _BNG_LAT0))
        md = (35 / 24) * n**3 * math.sin(3 * (lat - _BNG_LAT0)) * math.cos(3 * (lat + _BNG_LAT0))
        m = b * f0 * (ma - mb + mc - md)
        if abs(northing - _BNG_N0 - m) < 1e-5:
            break

    nu = a * f0 / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    rho = a * f0 * (1 - e2) * (1 - e2 * math.sin(lat) ** 2) ** -1.5
    eta2 = nu / rho - 1

    tan_lat = math.tan(lat)
    sec_lat = 1 / math.cos(lat)
    p = easting - _BNG_E0

    vii = tan_lat / (2 * rho * nu)
    viii = tan_lat / (24 * rho * nu**3) * (5 + 3 * tan_lat**2 + eta2 - 9 * tan_lat**2 * eta2)
    ix = tan_lat / (720 * rho * nu**5) * (61 + 90 * tan_lat**2 + 45 * tan_lat**4)
    x = sec_lat / nu
    xi = sec_lat / (6 * nu**3) * (nu / rho + 2 * tan_lat**2)
    xii = sec_lat / (120 * nu**5) * (5 + 28 * tan_lat**2 + 24 * tan_lat**4)
    xiia = sec_lat / (5040 * nu**7) * (61 + 662 * tan_lat**2 + 1320 * tan_lat**4 + 720 * tan_lat**6)

    lat = lat - vii * p**2 + viii * p**4 - ix * p**6
    lon = _BNG_LON0 + x * p - xi * p**3 + xii * p**5 - xiia * p**7
    return lat, lon, 0.0


def _helmert_osgb36_to_wgs84(lat: float, lon: float, height: float) -> tuple[float, float]:
    """Apply the Helmert transform to OSGB36 lat/lon and return WGS84 (lat, lon) in radians."""
    a, b = _AIRY_A, _AIRY_B
    e2 = 1 - (b * b) / (a * a)
    nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)

    # Geodetic → cartesian on the Airy ellipsoid.
    x = (nu + height) * math.cos(lat) * math.cos(lon)
    y = (nu + height) * math.cos(lat) * math.sin(lon)
    z = ((1 - e2) * nu + height) * math.sin(lat)

    # Helmert (position-vector convention).
    sf = 1 + _HELMERT_S / 1e6
    xw = _HELMERT_TX + sf * x - _HELMERT_RZ * y + _HELMERT_RY * z
    yw = _HELMERT_TY + _HELMERT_RZ * x + sf * y - _HELMERT_RX * z
    zw = _HELMERT_TZ - _HELMERT_RY * x + _HELMERT_RX * y + sf * z

    # Cartesian → geodetic on the WGS84 ellipsoid (iterative).
    wa, wb = _WGS84_A, _WGS84_B
    we2 = 1 - (wb * wb) / (wa * wa)
    lon_w = math.atan2(yw, xw)
    p = math.sqrt(xw**2 + yw**2)
    lat_w = math.atan2(zw, p * (1 - we2))
    for _ in range(10):
        nu_w = wa / math.sqrt(1 - we2 * math.sin(lat_w) ** 2)
        lat_new = math.atan2(zw + we2 * nu_w * math.sin(lat_w), p)
        if abs(lat_new - lat_w) < 1e-12:
            lat_w = lat_new
            break
        lat_w = lat_new
    return lat_w, lon_w


def bng_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Convert British National Grid (OSGB36 / EPSG:27700) easting/northing to WGS84.

    This is an approximate Helmert 7-parameter transform (the OSGB36→WGS84
    parameters published by Ordnance Survey), NOT the high-accuracy OSTN15
    grid transformation. Expect roughly **±5 m** horizontal accuracy, which is
    more than adequate for placing heritage sites on a map.

    Args:
        easting: BNG easting in metres (e.g. 325112).
        northing: BNG northing in metres (e.g. 673497).

    Returns:
        A ``(lat, lon)`` tuple in decimal degrees (WGS84).
    """
    lat, lon, height = _bng_to_osgb36_latlon(easting, northing)
    lat_w, lon_w = _helmert_osgb36_to_wgs84(lat, lon, height)
    return math.degrees(lat_w), math.degrees(lon_w)


def enrich_with_latlon(attributes: dict[str, Any]) -> dict[str, Any]:
    """Add ``lat`` and ``lon`` keys to a record's attribute dict, in place.

    Reads BNG coordinates from ``XCOORD``/``YCOORD`` (Canmore layers) or
    ``X``/``Y`` (designation layers). If coordinates are missing or null the
    record is returned unchanged (no ``lat``/``lon`` added) rather than raising.

    Args:
        attributes: A feature ``attributes`` dict from the ArcGIS API.

    Returns:
        The same dict, with ``lat``/``lon`` added when coordinates were present.
    """
    easting = attributes.get("XCOORD")
    northing = attributes.get("YCOORD")
    if easting is None or northing is None:
        easting = attributes.get("X")
        northing = attributes.get("Y")
    if easting is None or northing is None:
        return attributes
    try:
        lat, lon = bng_to_wgs84(float(easting), float(northing))
    except (TypeError, ValueError):
        return attributes
    attributes["lat"] = round(lat, 6)
    attributes["lon"] = round(lon, 6)
    return attributes


class HesClient:
    """Async wrapper around the HES ArcGIS REST ``query`` operation.

    Construct with no arguments for the live service, or pass a custom
    ``httpx.AsyncClient`` (e.g. backed by ``httpx.MockTransport``) for tests.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url
        if client is not None:
            self._client = client
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                headers={"User-Agent": "trove-scot-mcp/0.1.0 (Historic Environment Scotland MCP)"},
            )
        self._owns_client = client is None

    async def __aenter__(self) -> "HesClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def query(
        self,
        layer: str,
        where: str,
        out_fields: str = "*",
        geometry: str | None = None,
        geometry_type: str = "esriGeometryEnvelope",
        in_sr: int = 4326,
        count_only: bool = False,
        distinct: bool = False,
    ) -> dict[str, Any]:
        """Run an ArcGIS ``query`` against a layer and return the parsed JSON.

        Args:
            layer: Layer path such as ``"CANMORE/Canmore_Points/MapServer/0"``.
                The URL is built as ``{base}/{layer}/query``.
            where: The SQL ``where`` clause (e.g. ``"CANMOREID=52068"`` or
                ``"UPPER(NMRSNAME) LIKE '%CASTLE%'"``).
            out_fields: Comma-separated field list or ``"*"``. Ignored when
                ``count_only`` is True.
            geometry: Optional geometry string (e.g. a WGS84 envelope
                ``"minLon,minLat,maxLon,maxLat"``). When provided, spatial
                filter params are added.
            geometry_type: ArcGIS geometry type for ``geometry``.
            in_sr: Spatial reference of the input ``geometry`` (4326 = WGS84).
            count_only: When True, request only the match count
                (``returnCountOnly=true``).
            distinct: When True, request distinct values
                (``returnDistinctValues=true``). Note the server only dedupes
                within the first result page.

        Returns:
            The parsed JSON response dict.

        Raises:
            HesError: If all retries are exhausted, a non-retryable HTTP status
                is returned, or the response contains an ArcGIS error body.
        """
        path = f"/{layer}/query"
        params: dict[str, Any] = {
            "where": where,
            "f": "json",
        }
        if count_only:
            params["returnCountOnly"] = "true"
        else:
            params["outFields"] = out_fields
            if distinct:
                params["returnDistinctValues"] = "true"
        if geometry is not None:
            params["geometry"] = geometry
            params["geometryType"] = geometry_type
            params["inSR"] = str(in_sr)
            params["spatialRel"] = "esriSpatialRelIntersects"
            params["returnGeometry"] = "false"
        else:
            params["returnGeometry"] = "false"

        data = await self._fetch_with_retries(path, params)

        # ArcGIS returns HTTP 200 with an error body for some failures (e.g.
        # invalid where clauses / unsupported pagination).
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            code = err.get("code")
            message = err.get("message", "unknown error")
            raise HesError(f"ArcGIS error {code}: {message}")
        return data

    async def count(self, layer: str, where: str) -> int:
        """Return the number of features matching ``where`` (via ``returnCountOnly``)."""
        data = await self.query(layer, where, count_only=True)
        return int(data.get("count", 0))

    async def fetch_features(
        self,
        layer: str,
        where: str,
        out_fields: str = "*",
        geometry: str | None = None,
        geometry_type: str = "esriGeometryEnvelope",
        in_sr: int = 4326,
        distinct: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch features and return the list of ``features[].attributes`` dicts.

        Returns an empty list when nothing matches. Note the server silently
        caps results at ``maxRecordCount`` (1000 for Canmore, 5000 for listed
        buildings, 10000 for scheduled monuments) — count first with
        :meth:`count` if completeness matters.
        """
        data = await self.query(
            layer,
            where,
            out_fields=out_fields,
            geometry=geometry,
            geometry_type=geometry_type,
            in_sr=in_sr,
            distinct=distinct,
        )
        features = data.get("features") or []
        return [f.get("attributes", {}) for f in features if isinstance(f, dict)]

    async def _fetch_with_retries(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET ``path`` with ``params``, retrying transient failures with backoff."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            if attempt > 0:
                await asyncio.sleep(BACKOFF_BASE * attempt)  # 1s, 2s
            try:
                response = await self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                last_error = exc
                continue

            if response.status_code in RETRYABLE_STATUS:
                last_error = HesError(f"HES returned HTTP {response.status_code}")
                continue

            if response.status_code != 200:
                raise HesError(f"HES request failed with HTTP {response.status_code}")

            try:
                return response.json()
            except ValueError as exc:
                raise HesError(
                    "HES returned a non-JSON response — the service may be "
                    f"overloaded. Raw body start: {response.text[:120]!r}"
                ) from exc

        raise HesError(
            f"HES unavailable after {MAX_RETRIES + 1} attempts — the service may "
            f"be overloaded, retry shortly. Last error: {last_error}"
        )
