"""Tests for the Canmore heritage MCP tools (server.py).

The server's module-level ``_client`` is swapped for a HesClient backed by an
``httpx.MockTransport`` so no live network is used. A request log captures the
``where``/``geometry``/``outFields`` params so we can assert on query building.
"""

import httpx
import pytest

import trove_scot_mcp.client as client_mod
import trove_scot_mcp.server as server_mod
from trove_scot_mcp.client import HesClient
from trove_scot_mcp.server import (
    _build_where,
    _like_term,
    count_heritage,
    get_heritage_by_id,
    heritage_near,
    list_properties_in_care,
    search_heritage,
    search_listed_buildings,
    search_scheduled_monuments,
)

# Edinburgh Castle's BNG coords (from RESEARCH-ARCGIS.md) → ~55.95, -3.20.
EDINBURGH_CASTLE = {
    "CANMOREID": 52068,
    "NMRSNAME": "EDINBURGH CASTLE",
    "SITETYPE": "CASTLE (MEDIEVAL)",
    "BROADCLASS": "DEFENCE, MONUMENT (BY FORM)",
    "COUNCIL": "EDINBURGH, CITY OF",
    "COUNTY": "MIDLOTHIAN",
    "PARISH": "EDINBURGH (EDINBURGH, CITY OF)",
    "GRIDREF": "NT 25112 73497",
    "URL": "https://www.trove.scot/place/52068",
    "XCOORD": 325112,
    "YCOORD": 673497,
}


def install_mock_client(monkeypatch, handler):
    """Replace server._client with a MockTransport-backed HesClient; return the log."""
    log: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        log.append(dict(request.url.params))
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    http = httpx.AsyncClient(
        base_url="https://inspire.hes.scot/arcgis/rest/services", transport=transport
    )
    monkeypatch.setattr(server_mod, "_client", HesClient(client=http))
    return log


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make retry backoff instantaneous so error-path tests run fast."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", _instant)


# -- pure helpers ----------------------------------------------------------


def test_like_term_uppercases_and_wraps():
    assert _like_term("castle") == "'%CASTLE%'"
    assert _like_term("  Edinburgh Castle ") == "'%EDINBURGH CASTLE%'"


def test_build_where_term_only():
    assert _build_where("castle") == "UPPER(NMRSNAME) LIKE '%CASTLE%'"


def test_build_where_no_args_matches_all():
    assert _build_where() == "1=1"


def test_build_where_all_filters_anded():
    where = _build_where(
        "castle", sitetype="fort", council="edinburgh", broadclass="defence"
    )
    assert where == (
        "UPPER(NMRSNAME) LIKE '%CASTLE%' AND "
        "UPPER(SITETYPE) LIKE '%FORT%' AND "
        "UPPER(COUNCIL) LIKE '%EDINBURGH%' AND "
        "UPPER(BROADCLASS) LIKE '%DEFENCE%'"
    )


def test_build_where_filters_without_term():
    where = _build_where(council="fife")
    assert where == "UPPER(COUNCIL) LIKE '%FIFE%'"


# -- search_heritage -------------------------------------------------------


async def test_search_builds_correct_where(monkeypatch):
    log = install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"count": 1}
            if req.url.params.get("returnCountOnly") == "true"
            else {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]},
        ),
    )
    result = await search_heritage("edinburgh castle")

    # First call is the count, second is the fetch — both carry the same where.
    wheres = [entry["where"] for entry in log]
    assert wheres[0] == "UPPER(NMRSNAME) LIKE '%EDINBURGH CASTLE%'"
    assert wheres[1] == "UPPER(NMRSNAME) LIKE '%EDINBURGH CASTLE%'"
    # Count call uses returnCountOnly; fetch call uses named outFields (not *).
    assert log[0]["returnCountOnly"] == "true"
    assert log[1]["outFields"] != "*"
    assert "NMRSNAME" in log[1]["outFields"]
    assert isinstance(result, dict)


async def test_search_adds_filters_with_and(monkeypatch):
    log = install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"count": 1}
            if req.url.params.get("returnCountOnly") == "true"
            else {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]},
        ),
    )
    await search_heritage("castle", sitetype="fort", council="edinburgh", broadclass="defence")
    expected = (
        "UPPER(NMRSNAME) LIKE '%CASTLE%' AND UPPER(SITETYPE) LIKE '%FORT%' AND "
        "UPPER(COUNCIL) LIKE '%EDINBURGH%' AND UPPER(BROADCLASS) LIKE '%DEFENCE%'"
    )
    assert log[0]["where"] == expected


async def test_search_return_shape_and_enrichment(monkeypatch):
    install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"count": 1}
            if req.url.params.get("returnCountOnly") == "true"
            else {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]},
        ),
    )
    result = await search_heritage("edinburgh castle", limit=50)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    site = result["sites"][0]
    assert site["NMRSNAME"] == "EDINBURGH CASTLE"
    # enrich_with_latlon added lat/lon from the BNG coords.
    assert site["lat"] == pytest.approx(55.95, abs=0.01)
    assert site["lon"] == pytest.approx(-3.20, abs=0.01)


async def test_search_truncated_when_over_cap(monkeypatch):
    # Count reports 5000 (> 1000 cap); fetch returns 1000 features.
    install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"count": 5000}
            if req.url.params.get("returnCountOnly") == "true"
            else {"features": [{"attributes": {"CANMOREID": i, "NMRSNAME": "X"}} for i in range(1000)]},
        ),
    )
    result = await search_heritage("castle", limit=50)

    assert result["total_found"] == 5000
    assert result["truncated"] is True
    assert result["count"] == 50  # truncated to limit
    assert len(result["sites"]) == 50
    assert "1000" in result["note"]  # mentions the cap


async def test_search_empty_result_friendly_message(monkeypatch):
    install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(200, json={"count": 0}),
    )
    result = await search_heritage("zzznotarealsite")
    assert isinstance(result, str)
    assert "No heritage sites matched" in result


# -- get_heritage_by_id ----------------------------------------------------


async def test_get_by_id_returns_enriched_record(monkeypatch):
    log = install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200, json={"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}
        ),
    )
    result = await get_heritage_by_id(52068)

    assert log[0]["where"] == "CANMOREID=52068"
    assert log[0]["outFields"] == "*"  # full record
    assert isinstance(result, dict)
    assert result["NMRSNAME"] == "EDINBURGH CASTLE"
    assert result["lat"] == pytest.approx(55.95, abs=0.01)
    assert result["lon"] == pytest.approx(-3.20, abs=0.01)


async def test_get_by_id_not_found(monkeypatch):
    install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"features": []})
    )
    result = await get_heritage_by_id(999999999)
    assert result == "Error: no heritage site found with Canmore ID 999999999"


# -- count_heritage --------------------------------------------------------


async def test_count_returns_total_found(monkeypatch):
    log = install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"count": 7098})
    )
    result = await count_heritage(term="castle")
    assert log[0]["returnCountOnly"] == "true"
    assert log[0]["where"] == "UPPER(NMRSNAME) LIKE '%CASTLE%'"
    assert result["total_found"] == 7098
    assert "note" in result  # > 1000 cap → warning present


async def test_count_no_filters_counts_all(monkeypatch):
    log = install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"count": 313456})
    )
    result = await count_heritage()
    assert log[0]["where"] == "1=1"
    assert result["total_found"] == 313456


async def test_count_small_result_no_note(monkeypatch):
    install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"count": 42})
    )
    result = await count_heritage(term="zzz")
    assert result == {"total_found": 42}


# -- heritage_near ---------------------------------------------------------


async def test_heritage_near_builds_envelope_and_sorts(monkeypatch):
    # Two sites: one near Edinburgh (52068) and one far away (Aberdeen-ish BNG).
    far = {
        "CANMOREID": 123,
        "NMRSNAME": "FAR AWAY SITE",
        "SITETYPE": "FORT",
        "COUNCIL": "ABERDEEN",
        "GRIDREF": "NJ 9 0",
        "URL": "https://www.trove.scot/place/123",
        "XCOORD": 394000,
        "YCOORD": 806000,  # ~57.14, -2.10 (Aberdeen area)
    }
    log = install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"features": [
                {"attributes": dict(far)},
                {"attributes": dict(EDINBURGH_CASTLE)},
            ]},
        ),
    )
    # Edinburgh Castle lat/lon ≈ 55.95, -3.20.
    result = await heritage_near(55.95, -3.20, radius_km=2.0)

    # Envelope geometry sent as WGS84 with the right type/SR.
    assert log[0]["geometryType"] == "esriGeometryEnvelope"
    assert log[0]["inSR"] == "4326"
    min_lon, min_lat, max_lon, max_lat = (float(x) for x in log[0]["geometry"].split(","))
    assert min_lon < -3.20 < max_lon
    assert min_lat < 55.95 < max_lat
    # ~2km radius → lat delta ≈ 2/111 ≈ 0.018.
    assert (max_lat - min_lat) == pytest.approx(2 * 2.0 / 111.0, rel=0.01)

    # Sorted nearest-first; Edinburgh Castle (the actual point) comes first.
    assert result["count"] == 2
    assert result["sites"][0]["CANMOREID"] == 52068
    assert result["sites"][1]["CANMOREID"] == 123


async def test_heritage_near_adds_distance_km(monkeypatch):
    install_mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200, json={"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}
        ),
    )
    result = await heritage_near(55.95, -3.20, radius_km=1.0)
    site = result["sites"][0]
    assert "distance_km" in site
    # Castle is essentially at the query point → very small distance.
    assert site["distance_km"] < 1.0


async def test_heritage_near_term_filter(monkeypatch):
    log = install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"features": []})
    )
    await heritage_near(55.95, -3.20, term="castle")
    assert log[0]["where"] == "UPPER(NMRSNAME) LIKE '%CASTLE%'"


# -- error handling --------------------------------------------------------


async def test_search_http_error_returns_error_string(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_mock_client(monkeypatch, handler)
    result = await search_heritage("castle")
    assert isinstance(result, str)
    assert result.startswith("Error: ")


async def test_get_by_id_http_error_returns_error_string(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_mock_client(monkeypatch, handler)
    result = await get_heritage_by_id(52068)
    assert isinstance(result, str)
    assert result.startswith("Error: ")


# -- designation layers ----------------------------------------------------

# Sample designation records (BNG X/Y coords → enrich_with_latlon adds lat/lon).
LISTED_BUILDING = {
    "DES_TITLE": "Duke of York Statue, Edinburgh Castle Esplanade, Edinburgh",
    "ENT_TITLE": "Duke of York Statue",
    "CATEGORY": "B",
    "DESIGNATED": 1262304000000,
    "LOCAL_AUTH": "Edinburgh",
    "CLASS": "STATUE",
    "PARBUR": "EDINBURGH",
    "LINK": "https://www.trove.scot/designation/LB1",
    "X": 325112,
    "Y": 673497,
}
SCHEDULED_MONUMENT = {
    "DES_TITLE": "EDINBURGH CASTLE",
    "CLASS": "CASTLE",
    "CATEGORY": "SECULAR",
    "AREA": "Edinburgh",
    "LOCAL_AUTH": "Edinburgh",
    "PARISH": "EDINBURGH",
    "DESIGNATED": 1262304000000,
    "LINK": "https://www.trove.scot/designation/SM1",
    "X": 325112,
    "Y": 673497,
}
PROPERTY_IN_CARE = {
    "PIC_ID": 1,
    "PIC_NAME": "Edinburgh Castle",
    "LOCAL_AUTH": "Edinburgh",
    "LINK": "https://www.trove.scot/property/edinburgh-castle",
    "X": 325112,
    "Y": 673497,
}


def _count_or_features(record):
    """Handler factory: count→{count:1}, fetch→one feature with ``record`` attrs."""
    return lambda req: httpx.Response(
        200,
        json={"count": 1}
        if req.url.params.get("returnCountOnly") == "true"
        else {"features": [{"attributes": dict(record)}]},
    )


# -- search_listed_buildings ------------------------------------------------


async def test_listed_buildings_builds_correct_where(monkeypatch):
    log = install_mock_client(monkeypatch, _count_or_features(LISTED_BUILDING))
    await search_listed_buildings("castle", category="a", local_authority="edinburgh")

    expected = (
        "UPPER(DES_TITLE) LIKE '%CASTLE%' AND CATEGORY = 'A' AND "
        "UPPER(LOCAL_AUTH) LIKE '%EDINBURGH%'"
    )
    # Count call and fetch call both carry the same where.
    assert log[0]["where"] == expected
    assert log[1]["where"] == expected
    assert log[0]["returnCountOnly"] == "true"
    assert "DES_TITLE" in log[1]["outFields"]
    assert "CATEGORY" in log[1]["outFields"]


async def test_listed_buildings_category_validation_rejects_d(monkeypatch):
    # No network call should be made for an invalid category.
    log = install_mock_client(monkeypatch, _count_or_features(LISTED_BUILDING))
    result = await search_listed_buildings("castle", category="D")
    assert result == "Error: category must be A, B, or C"
    assert log == []  # short-circuited before any query


async def test_listed_buildings_return_shape_and_enrichment(monkeypatch):
    install_mock_client(monkeypatch, _count_or_features(LISTED_BUILDING))
    result = await search_listed_buildings("castle", limit=50)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    building = result["buildings"][0]
    assert building["CATEGORY"] == "B"
    # enrich_with_latlon added lat/lon from the BNG X/Y coords.
    assert building["lat"] == pytest.approx(55.95, abs=0.01)
    assert building["lon"] == pytest.approx(-3.20, abs=0.01)


async def test_listed_buildings_empty_result_friendly_message(monkeypatch):
    install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"count": 0})
    )
    result = await search_listed_buildings("zzznotarealbuilding")
    assert isinstance(result, str)
    assert "No listed buildings matched" in result


async def test_listed_buildings_http_error_returns_error_string(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_mock_client(monkeypatch, handler)
    result = await search_listed_buildings("castle")
    assert isinstance(result, str)
    assert result.startswith("Error: ")


# -- search_scheduled_monuments --------------------------------------------


async def test_scheduled_monuments_builds_correct_where(monkeypatch):
    log = install_mock_client(monkeypatch, _count_or_features(SCHEDULED_MONUMENT))
    await search_scheduled_monuments("castle", local_authority="edinburgh")

    expected = (
        "UPPER(DES_TITLE) LIKE '%CASTLE%' AND UPPER(LOCAL_AUTH) LIKE '%EDINBURGH%'"
    )
    assert log[0]["where"] == expected
    assert log[1]["where"] == expected
    assert log[0]["returnCountOnly"] == "true"
    assert "DES_TITLE" in log[1]["outFields"]
    assert "AREA" in log[1]["outFields"]


async def test_scheduled_monuments_return_shape_and_enrichment(monkeypatch):
    install_mock_client(monkeypatch, _count_or_features(SCHEDULED_MONUMENT))
    result = await search_scheduled_monuments("castle", limit=50)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    monument = result["monuments"][0]
    assert monument["DES_TITLE"] == "EDINBURGH CASTLE"
    assert monument["lat"] == pytest.approx(55.95, abs=0.01)
    assert monument["lon"] == pytest.approx(-3.20, abs=0.01)


async def test_scheduled_monuments_empty_result_friendly_message(monkeypatch):
    install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"count": 0})
    )
    result = await search_scheduled_monuments("zzznotarealmonument")
    assert isinstance(result, str)
    assert "No scheduled monuments matched" in result


async def test_scheduled_monuments_http_error_returns_error_string(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_mock_client(monkeypatch, handler)
    result = await search_scheduled_monuments("castle")
    assert isinstance(result, str)
    assert result.startswith("Error: ")


# -- list_properties_in_care ------------------------------------------------


async def test_properties_in_care_builds_correct_where(monkeypatch):
    log = install_mock_client(monkeypatch, _count_or_features(PROPERTY_IN_CARE))
    await list_properties_in_care(local_authority="edinburgh", term="castle")

    expected = (
        "UPPER(PIC_NAME) LIKE '%CASTLE%' AND UPPER(LOCAL_AUTH) LIKE '%EDINBURGH%'"
    )
    assert log[0]["where"] == expected
    assert log[1]["where"] == expected
    assert log[0]["returnCountOnly"] == "true"
    assert "PIC_NAME" in log[1]["outFields"]


async def test_properties_in_care_return_shape_and_enrichment(monkeypatch):
    install_mock_client(monkeypatch, _count_or_features(PROPERTY_IN_CARE))
    result = await list_properties_in_care(limit=100)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    prop = result["properties"][0]
    assert prop["PIC_NAME"] == "Edinburgh Castle"
    assert prop["lat"] == pytest.approx(55.95, abs=0.01)
    assert prop["lon"] == pytest.approx(-3.20, abs=0.01)


async def test_properties_in_care_empty_result_friendly_message(monkeypatch):
    install_mock_client(
        monkeypatch, lambda req: httpx.Response(200, json={"count": 0})
    )
    result = await list_properties_in_care(term="zzznotarealproperty")
    assert isinstance(result, str)
    assert "No properties in care matched" in result


async def test_properties_in_care_http_error_returns_error_string(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_mock_client(monkeypatch, handler)
    result = await list_properties_in_care()
    assert isinstance(result, str)
    assert result.startswith("Error: ")
