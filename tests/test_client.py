"""Tests for the HES ArcGIS client: URL/params, retries, counts, BNG→WGS84."""

import httpx
import pytest

import trove_scot_mcp.client as client_mod
from trove_scot_mcp.client import (
    HesClient,
    HesError,
    bng_to_wgs84,
    enrich_with_latlon,
)

LAYER = "CANMORE/Canmore_Points/MapServer/0"


def make_client(handler) -> HesClient:
    """Build a client backed by an httpx.MockTransport (no live network)."""
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://inspire.hes.scot/arcgis/rest/services", transport=transport
    )
    return HesClient(client=http)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make retry backoff instantaneous so tests run fast."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", _instant)


# -- URL / params ----------------------------------------------------------


async def test_query_builds_url_and_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"features": []})

    client = make_client(handler)
    await client.query(LAYER, "CANMOREID=52068")

    # httpx joins the client base_url path with the per-request path.
    assert seen["path"].endswith("/CANMORE/Canmore_Points/MapServer/0/query")
    assert seen["params"]["where"] == "CANMOREID=52068"
    assert seen["params"]["outFields"] == "*"
    assert seen["params"]["f"] == "json"
    assert seen["params"]["returnGeometry"] == "false"
    assert "returnCountOnly" not in seen["params"]


async def test_query_url_encodes_where():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw"] = request.url.raw_path.decode()
        return httpx.Response(200, json={"features": []})

    client = make_client(handler)
    await client.query(LAYER, "UPPER(NMRSNAME) LIKE '%CASTLE%'")
    # httpx encodes spaces, parens, and the % wildcard in the query string.
    assert "where=" in seen["raw"]
    assert "%25CASTLE%25" in seen["raw"]  # % encoded as %25


# -- count_only ------------------------------------------------------------


async def test_count_only_sets_param_and_returns_count():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"count": 313456})

    client = make_client(handler)
    n = await client.count(LAYER, "1=1")

    assert n == 313456
    assert seen["params"]["returnCountOnly"] == "true"
    assert "outFields" not in seen["params"]  # skipped when counting


# -- retry behaviour -------------------------------------------------------


async def test_retry_on_502_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, json={"count": 5})

    client = make_client(handler)
    assert await client.count(LAYER, "1=1") == 5
    assert calls["n"] == 2


async def test_all_retries_failed_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="Service Unavailable")

    client = make_client(handler)
    with pytest.raises(HesError, match="unavailable after"):
        await client.count(LAYER, "1=1")
    assert calls["n"] == client_mod.MAX_RETRIES + 1  # 3 total attempts


async def test_retry_on_timeout_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, json={"count": 1})

    client = make_client(handler)
    assert await client.count(LAYER, "1=1") == 1
    assert calls["n"] == 2


async def test_non_retryable_status_raises_immediately():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="Pagination is not supported.")

    client = make_client(handler)
    with pytest.raises(HesError, match="HTTP 400"):
        await client.query(LAYER, "1=1")
    assert calls["n"] == 1  # no retries for a 400


# -- fetch_features --------------------------------------------------------


async def test_fetch_features_extracts_attributes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "features": [
                    {"attributes": {"CANMOREID": 52068, "NMRSNAME": "EDINBURGH CASTLE"}},
                    {"attributes": {"CANMOREID": 52069, "NMRSNAME": "MONS MEG"}},
                ]
            },
        )

    client = make_client(handler)
    rows = await client.fetch_features(LAYER, "UPPER(NMRSNAME) LIKE '%CASTLE%'")
    assert rows == [
        {"CANMOREID": 52068, "NMRSNAME": "EDINBURGH CASTLE"},
        {"CANMOREID": 52069, "NMRSNAME": "MONS MEG"},
    ]


async def test_fetch_features_empty_when_no_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"features": []})

    client = make_client(handler)
    assert await client.fetch_features(LAYER, "CANMOREID=1") == []


# -- geometry params -------------------------------------------------------


async def test_geometry_params_added():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"features": []})

    client = make_client(handler)
    await client.query(LAYER, "1=1", geometry="-3.3,55.9,-3.1,56.0")

    assert seen["params"]["geometry"] == "-3.3,55.9,-3.1,56.0"
    assert seen["params"]["geometryType"] == "esriGeometryEnvelope"
    assert seen["params"]["inSR"] == "4326"
    assert seen["params"]["spatialRel"] == "esriSpatialRelIntersects"
    assert seen["params"]["returnGeometry"] == "false"


# -- ArcGIS error body -----------------------------------------------------


async def test_arcgis_error_body_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"code": 400, "message": "Pagination is not supported."}},
        )

    client = make_client(handler)
    with pytest.raises(HesError, match="Pagination is not supported"):
        await client.query(LAYER, "1=1")


# -- BNG → WGS84 conversion ------------------------------------------------


def test_bng_to_wgs84_edinburgh_castle():
    # Edinburgh Castle: XCOORD=325112, YCOORD=673497.
    # Verified real-world WGS84 position ≈ 55.9486, -3.2008 (latlong.net,
    # distancesfrom.com). NOTE: the original task brief quoted lon=-1.9486,
    # which is incorrect (that longitude is near Newcastle); the castle sits at
    # ≈ -3.20. Our Helmert result matches the true value to < 0.001°.
    lat, lon = bng_to_wgs84(325112, 673497)
    assert lat == pytest.approx(55.9486, abs=0.001)
    assert lon == pytest.approx(-3.2008, abs=0.001)


def test_bng_to_wgs84_second_point():
    # Ben Nevis summit: NGR NN 16671 71712 → easting 216671, northing 771712.
    # Real-world WGS84 ≈ 56.7969, -5.0037.
    lat, lon = bng_to_wgs84(216671, 771712)
    assert lat == pytest.approx(56.7969, abs=0.01)
    assert lon == pytest.approx(-5.0037, abs=0.01)


# -- coordinate enrichment -------------------------------------------------


def test_enrich_adds_latlon_from_xycoord():
    rec = {"CANMOREID": 52068, "XCOORD": 325112, "YCOORD": 673497}
    out = enrich_with_latlon(rec)
    assert out["lat"] == pytest.approx(55.9486, abs=0.001)
    assert out["lon"] == pytest.approx(-3.2008, abs=0.001)


def test_enrich_uses_x_y_for_designation_layers():
    rec = {"DES_TITLE": "Some Listed Building", "X": 325112, "Y": 673497}
    out = enrich_with_latlon(rec)
    assert "lat" in out and "lon" in out


def test_enrich_handles_missing_coords():
    rec = {"CANMOREID": 1}  # no coordinates at all
    out = enrich_with_latlon(rec)
    assert "lat" not in out
    assert "lon" not in out


def test_enrich_handles_null_coords():
    rec = {"CANMOREID": 2, "XCOORD": None, "YCOORD": None}
    out = enrich_with_latlon(rec)
    assert "lat" not in out and "lon" not in out


def test_enrich_treats_zero_coords_as_missing():
    # X=0/Y=0 is the HES "no data" sentinel — converting it would place the
    # record at lat≈49.77, lon≈-7.56 (the English Channel). It must be skipped.
    rec = {"CANMOREID": 3, "XCOORD": 0, "YCOORD": 0}
    out = enrich_with_latlon(rec)
    assert "lat" not in out and "lon" not in out


def test_enrich_treats_zero_xy_designation_coords_as_missing():
    rec = {"DES_TITLE": "Unlocated", "X": 0, "Y": 0}
    out = enrich_with_latlon(rec)
    assert "lat" not in out and "lon" not in out


def test_enrich_valid_coords_still_convert():
    rec = {"CANMOREID": 4, "XCOORD": 325112, "YCOORD": 673497}
    out = enrich_with_latlon(rec)
    assert out["lat"] == pytest.approx(55.9486, abs=0.001)
    assert out["lon"] == pytest.approx(-3.2008, abs=0.001)
