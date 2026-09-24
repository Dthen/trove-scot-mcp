"""Tests for the HES ArcGIS client: URL/params, retries, counts, BNG->WGS84.

Ported from async HTTP library to sync stdlib urllib. 25 tests:
- 20 legacy behaviour tests (ported 1:1, see mapping below)
- 3 R1 TimeoutError-pin tests
- 2 F2 seam-class-pin tests

No network, no asyncio, no third-party HTTP library.

Legacy → new test name mapping (spec preservation):
  test_query_builds_url_and_params          → test_query_builds_url_and_params
  test_query_url_encodes_where              → test_query_url_encodes_where
  test_count_only_sets_param_and_returns_count → test_count_only_sets_param_and_returns_count
  test_retry_on_502_then_success            → test_retry_on_502_then_success
  test_all_retries_failed_raises            → test_all_retries_failed_raises
  test_retry_on_timeout_then_success        → test_retry_on_timeout_then_success
  test_non_retryable_status_raises_immediately → test_non_retryable_status_raises_immediately
  test_fetch_features_extracts_attributes  → test_fetch_features_extracts_attributes
  test_fetch_features_empty_when_no_match  → test_fetch_features_empty_when_no_match
  test_geometry_params_added               → test_geometry_params_added
  test_arcgis_error_body_raises            → test_arcgis_error_body_raises
  test_bng_to_wgs84_edinburgh_castle       → test_bng_to_wgs84_edinburgh_castle
  test_bng_to_wgs84_second_point           → test_bng_to_wgs84_second_point
  test_enrich_adds_latlon_from_xycoord     → test_enrich_adds_latlon_from_xycoord
  test_enrich_uses_x_y_for_designation_layers → test_enrich_uses_x_y_for_designation_layers
  test_enrich_handles_missing_coords       → test_enrich_handles_missing_coords
  test_enrich_handles_null_coords          → test_enrich_handles_null_coords
  test_enrich_treats_zero_coords_as_missing → test_enrich_treats_zero_coords_as_missing
  test_enrich_treats_zero_xy_designation_coords_as_missing → test_enrich_treats_zero_xy_designation_coords_as_missing
  test_enrich_valid_coords_still_convert   → test_enrich_valid_coords_still_convert
  (NEW R1)                                  → test_timeout_is_retried_then_friendly_exhaustion
  (NEW R1)                                  → test_timeout_always_raises_HesError_not_escape
  (NEW R1)                                  → test_urlerror_and_oserror_classes_also_retried
  (NEW F2)                                  → test_httpexception_folds_to_urlerror_at_seam
  (NEW F2)                                  → test_remotedisconnected_is_oserror_and_retried
"""

import http.client
import json
import math
from email.message import Message

import pytest
import urllib.error
import urllib.request

import trove_scot_mcp.client as client_mod
from trove_scot_mcp.client import (
    HesClient,
    HesError,
    bng_to_wgs84,
    enrich_with_latlon,
)

LAYER = "CANMORE/Canmore_Points/MapServer/0"


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for the object returned by ``urllib.request.urlopen``."""

    def __init__(self, status, body, read_error=None):
        self._status = status
        self._read_error = read_error
        if isinstance(body, bytes):
            self._body = body
        elif isinstance(body, str):
            self._body = body.encode()
        else:
            self._body = json.dumps(body).encode()

    def getcode(self):
        return self._status

    def read(self):
        if self._read_error is not None:
            raise self._read_error
        return self._body


def install_fake_urlopen(monkeypatch, responses):
    """Install a fake ``client_mod._urlopen`` that yields *responses* in order.

    Each item in *responses* is either:
    - an ``Exception`` instance → the fake raises it
    - a ``(status, json_body)`` tuple → returns the normalized ``_urlopen`` tuple

    Returns a mutable ``state`` dict with:
    - ``state["calls"]`` — total number of times the fake was invoked
    - ``state["urls"]`` — list of every URL string passed to the fake
    """
    state = {"calls": 0, "urls": []}

    def fake_urlopen(url, timeout=None):
        idx = state["calls"]
        state["calls"] += 1
        state["urls"].append(url)
        if idx >= len(responses):
            raise AssertionError(
                f"_urlopen called {idx + 1} times but only {len(responses)} "
                "responses queued"
            )
        resp = responses[idx]
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, tuple) and len(resp) == 2:
            status, json_body = resp
            return status, FakeResponse(status, json_body).read()
        raise TypeError(f"Bad canned response: {resp!r}")

    monkeypatch.setattr(client_mod, "_urlopen", fake_urlopen)
    return state


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Replace ``_sleep`` with a recorder so retry delays are pinned, not slept."""
    sleeps = []
    monkeypatch.setattr(client_mod, "_sleep", lambda s: sleeps.append(s))
    return sleeps


# ---------------------------------------------------------------------------
# URL / params
# ---------------------------------------------------------------------------


def test_query_builds_url_and_params(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"features": []})])
    client = HesClient()
    client.query(LAYER, "CANMOREID=52068")

    url = state["urls"][0]
    path = url.split("?")[0]
    assert path.endswith("/CANMORE/Canmore_Points/MapServer/0/query")
    # Golden wire form for CANMOREID=52068 (from golden/legacy-requests.json)
    assert "where=CANMOREID%3D52068" in url
    assert "f=json" in url
    assert "outFields=%2A" in url
    assert "returnGeometry=false" in url
    assert "returnCountOnly" not in url
    assert state["calls"] == 1
    assert no_sleep == []


def test_query_url_encodes_where(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"features": []})])
    client = HesClient()
    client.query(LAYER, "UPPER(NMRSNAME) LIKE '%CASTLE%'")

    url = state["urls"][0]
    assert "where=" in url
    assert "%25CASTLE%25" in url  # % encoded as %25


# ---------------------------------------------------------------------------
# count_only
# ---------------------------------------------------------------------------


def test_count_only_sets_param_and_returns_count(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"count": 313456})])
    client = HesClient()
    n = client.count(LAYER, "1=1")

    assert n == 313456
    url = state["urls"][0]
    assert "returnCountOnly=true" in url
    assert "outFields" not in url  # skipped when counting


# ---------------------------------------------------------------------------
# retry behaviour
# ---------------------------------------------------------------------------


def test_retry_on_502_then_success(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (502, {}),
        (200, {"count": 5}),
    ])
    client = HesClient()
    assert client.count(LAYER, "1=1") == 5
    assert state["calls"] == 2
    assert no_sleep == [1.0]


def test_all_retries_failed_raises(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (503, {}),
        (503, {}),
        (503, {}),
    ])
    client = HesClient()
    with pytest.raises(HesError, match="unavailable after"):
        client.count(LAYER, "1=1")
    assert state["calls"] == 3
    assert no_sleep == [1.0, 2.0]


def test_retry_on_timeout_then_success(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        TimeoutError("timed out"),
        (200, {"count": 1}),
    ])
    client = HesClient()
    assert client.count(LAYER, "1=1") == 1
    assert state["calls"] == 2
    assert no_sleep == [1.0]


def test_non_retryable_status_raises_immediately(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(400, "Pagination is not supported.")])
    client = HesClient()
    with pytest.raises(HesError, match="HTTP 400"):
        client.query(LAYER, "1=1")
    assert state["calls"] == 1  # no retries for a 400
    assert no_sleep == []


def test_non_retryable_urllib_httperror_raises_immediately(no_sleep, monkeypatch):
    headers = Message()
    state = install_fake_urlopen(monkeypatch, [
        urllib.error.HTTPError("https://hes.example", 400, "Bad Request", headers, None),
        urllib.error.HTTPError("https://hes.example", 400, "Bad Request", headers, None),
    ])
    client = HesClient()
    with pytest.raises(HesError, match="HTTP 400"):
        client.query(LAYER, "1=1")
    assert state["calls"] == 1
    assert no_sleep == []


def test_retryable_urllib_httperror_uses_ladder(no_sleep, monkeypatch):
    headers = Message()
    state = install_fake_urlopen(monkeypatch, [
        urllib.error.HTTPError("https://hes.example", 503, "Unavailable", headers, None),
        (200, {"count": 5}),
    ])
    client = HesClient()
    assert client.count(LAYER, "1=1") == 5
    assert state["calls"] == 2
    assert no_sleep == [1.0]


def test_read_stage_incomplete_read_is_normalized_and_retried(
    no_sleep, monkeypatch
):
    responses = [
        FakeResponse(
            200,
            b"",
            read_error=http.client.IncompleteRead(b'{"count": ', 10),
        ),
        (200, {"count": 7}),
    ]
    call_count = {"n": 0}

    def fake_inner_urlopen(*args, **kwargs):
        response = responses[call_count["n"]]
        call_count["n"] += 1
        if isinstance(response, Exception):
            raise response
        if isinstance(response, FakeResponse):
            return response
        status, json_body = response
        return FakeResponse(status, json_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_inner_urlopen)

    assert HesClient().count(LAYER, "1=1") == 7
    assert call_count["n"] == 2
    assert no_sleep == [1.0]


def test_read_stage_incomplete_read_exhausts_to_hes_error(
    no_sleep, monkeypatch
):
    response = FakeResponse(
        200,
        b"",
        read_error=http.client.IncompleteRead(b'{"count": ', 10),
    )
    call_count = {"n": 0}

    def fake_inner_urlopen(*args, **kwargs):
        call_count["n"] += 1
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_inner_urlopen)

    with pytest.raises(HesError, match="unavailable after"):
        HesClient().count(LAYER, "1=1")
    assert call_count["n"] == 3
    assert no_sleep == [1.0, 2.0]


# ---------------------------------------------------------------------------
# fetch_features
# ---------------------------------------------------------------------------


def test_fetch_features_extracts_attributes(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {
            "features": [
                {"attributes": {"CANMOREID": 52068, "NMRSNAME": "EDINBURGH CASTLE"}},
                {"attributes": {"CANMOREID": 52069, "NMRSNAME": "MONS MEG"}},
            ],
        }),
    ])
    client = HesClient()
    rows = client.fetch_features(LAYER, "UPPER(NMRSNAME) LIKE '%CASTLE%'")
    assert rows == [
        {"CANMOREID": 52068, "NMRSNAME": "EDINBURGH CASTLE"},
        {"CANMOREID": 52069, "NMRSNAME": "MONS MEG"},
    ]


def test_fetch_features_empty_when_no_match(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"features": []})])
    client = HesClient()
    assert client.fetch_features(LAYER, "CANMOREID=1") == []


# ---------------------------------------------------------------------------
# geometry params
# ---------------------------------------------------------------------------


def test_geometry_params_added(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"features": []})])
    client = HesClient()
    client.query(LAYER, "1=1", geometry="-3.3,55.9,-3.1,56.0")

    url = state["urls"][0]
    assert "geometry=-3.3%2C55.9%2C-3.1%2C56.0" in url
    assert "geometryType=esriGeometryEnvelope" in url
    assert "inSR=4326" in url
    assert "spatialRel=esriSpatialRelIntersects" in url
    assert "returnGeometry=false" in url


# ---------------------------------------------------------------------------
# ArcGIS error body
# ---------------------------------------------------------------------------


def test_arcgis_error_body_raises(no_sleep, monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {"error": {"code": 400, "message": "Pagination is not supported."}}),
    ])
    client = HesClient()
    with pytest.raises(HesError, match="Pagination is not supported"):
        client.query(LAYER, "1=1")


@pytest.mark.parametrize("payload", [b"[]", b'"unexpected"', b"null", b"7"])
def test_non_object_json_is_rejected(no_sleep, monkeypatch, payload):
    install_fake_urlopen(monkeypatch, [(200, payload)])
    with pytest.raises(HesError, match="JSON object"):
        HesClient().query(LAYER, "1=1")


@pytest.mark.parametrize("payload", [b"[]", b'"unexpected"', b"null", b"7"])
def test_count_rejects_non_object_json(no_sleep, monkeypatch, payload):
    install_fake_urlopen(monkeypatch, [(200, payload)])
    with pytest.raises(HesError, match="JSON object"):
        HesClient().count(LAYER, "1=1")


@pytest.mark.parametrize("payload", [b"[]", b'"unexpected"', b"null", b"7"])
def test_fetch_features_rejects_non_object_json(no_sleep, monkeypatch, payload):
    install_fake_urlopen(monkeypatch, [(200, payload)])
    with pytest.raises(HesError, match="JSON object"):
        HesClient().fetch_features(LAYER, "1=1")


# ---------------------------------------------------------------------------
# BNG -> WGS84 conversion (sync pure, copy verbatim)
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize(
    "easting,northing",
    [
        (math.nan, 673497),
        (325112, math.nan),
        (math.inf, 673497),
        (325112, -math.inf),
    ],
)
def test_bng_to_wgs84_rejects_non_finite_coordinates(easting, northing):
    with pytest.raises(ValueError, match="finite"):
        bng_to_wgs84(easting, northing)


@pytest.mark.parametrize(
    "easting,northing",
    [
        (math.nan, 673497),
        (325112, math.nan),
        (math.inf, 673497),
        (325112, -math.inf),
    ],
)
def test_enrich_ignores_non_finite_coordinates(easting, northing):
    attributes = {"XCOORD": easting, "YCOORD": northing}
    assert enrich_with_latlon(attributes) == attributes
    assert "lat" not in attributes
    assert "lon" not in attributes


# ---------------------------------------------------------------------------
# coordinate enrichment
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# R1: TimeoutError pin
# ---------------------------------------------------------------------------


def test_timeout_is_retried_then_friendly_exhaustion(no_sleep, monkeypatch):
    """TimeoutError is retried; final success returns the payload."""
    state = install_fake_urlopen(monkeypatch, [
        TimeoutError("timed out"),
        TimeoutError("timed out"),
        (200, {"count": 7}),
    ])
    client = HesClient()
    result = client.count(LAYER, "1=1")
    assert result == 7
    assert state["calls"] == 3
    assert no_sleep == [1.0, 2.0]


def test_timeout_always_raises_HesError_not_escape(no_sleep, monkeypatch):
    """Always-timeout → HesError, never a raw TimeoutError toward -32603."""
    state = install_fake_urlopen(monkeypatch, [
        TimeoutError("timed out"),
        TimeoutError("timed out"),
        TimeoutError("timed out"),
    ])
    client = HesClient()
    with pytest.raises(HesError, match="unavailable after"):
        client.count(LAYER, "1=1")
    assert state["calls"] == 3
    assert no_sleep == [1.0, 2.0]


@pytest.mark.parametrize("exc_factory", [
    lambda: urllib.error.URLError("test"),
    lambda: OSError("test"),
    lambda: TimeoutError("test"),
    lambda: ConnectionResetError("test"),
])
def test_urlerror_and_oserror_classes_also_retried(no_sleep, monkeypatch, exc_factory):
    """Every ladder-retried class exhausts retries with backoff shape."""
    state = install_fake_urlopen(monkeypatch, [
        exc_factory(),
        exc_factory(),
        exc_factory(),
    ])
    client = HesClient()
    with pytest.raises(HesError, match="unavailable after"):
        client.count(LAYER, "1=1")
    assert state["calls"] == 3
    assert no_sleep == [1.0, 2.0]


# ---------------------------------------------------------------------------
# F2: T05 seam class pin (http.client.HTTPException folds to URLError)
# ---------------------------------------------------------------------------


def test_httpexception_folds_to_urlerror_at_seam(no_sleep, monkeypatch):
    """T05 §5 seam: http.client.HTTPException is NOT an OSError subclass.

    The seam folds it to URLError, so the ladder retries it and exhausts to
    a friendly HesError. Also covers BadStatusLine (another non-OSError sibling).
    """
    assert not issubclass(http.client.HTTPException, OSError)
    assert not issubclass(http.client.BadStatusLine, OSError)

    # Exercise the REAL _urlopen seam by patching urllib.request.urlopen.
    # (install_fake_urlopen replaces _urlopen entirely, bypassing the seam.)
    call_count = {"n": 0}

    def bad_urlopen(*args, **kwargs):
        call_count["n"] += 1
        raise http.client.HTTPException("connection broken")

    monkeypatch.setattr(urllib.request, "urlopen", bad_urlopen)

    client = HesClient()
    with pytest.raises(HesError, match="unavailable after"):
        client.count(LAYER, "1=1")

    assert call_count["n"] == 3  # 3 attempts
    assert no_sleep == [1.0, 2.0]


def test_remotedisconnected_is_oserror_and_retried(no_sleep, monkeypatch):
    """RemoteDisconnected ⊂ ConnectionResetError ⊂ OSError.

    Documented deviation: legacy did NOT retry protocol errors. Here it is
    retried because it IS an OSError; user-visible outcome is friendly-text
    parity (exhaustion or fold → "Error: …").
    """
    assert issubclass(http.client.RemoteDisconnected, ConnectionResetError)
    assert issubclass(ConnectionResetError, OSError)

    call_count = {"n": 0}
    responses = [
        http.client.RemoteDisconnected("connection reset by peer"),
        http.client.RemoteDisconnected("connection reset by peer"),
        (200, {"count": 1}),
    ]

    def fake_inner_urlopen(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        resp = responses[idx]
        if isinstance(resp, Exception):
            raise resp
        return FakeResponse(resp[0], resp[1])

    monkeypatch.setattr(urllib.request, "urlopen", fake_inner_urlopen)

    client = HesClient()
    result = client.count(LAYER, "1=1")
    assert result == 1
    assert call_count["n"] == 3
    assert no_sleep == [1.0, 2.0]
