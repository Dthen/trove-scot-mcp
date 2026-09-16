#!/usr/bin/env python3
"""Characterization harness for legacy wire URLs and return values.

Runs under the OLD venv python ($PYO), in-process — no network.
Imports trove_scot_mcp.server + client, calls query/count/fetch_features
against an httpx.MockTransport handler that records the raw query segment,
and writes golden/legacy-requests.json + golden/legacy-behavior.json.
"""

import asyncio
import json
import os
import sys
from datetime import date

import httpx

from trove_scot_mcp import server, client
from trove_scot_mcp.client import HesClient
from trove_scot_mcp.server import (
    CANMORE_LAYER,
    LISTED_BUILDINGS_LAYER,
    SCHEDULED_MONUMENTS_LAYER,
    PROPERTIES_IN_CARE_LAYER,
    _build_where,
    _like_clause,
    _SEARCH_FIELDS,
    _NEAR_FIELDS,
    _LISTED_BUILDING_FIELDS,
    _SCHEDULED_MONUMENT_FIELDS,
    _PROPERTIES_IN_CARE_FIELDS,
    search_heritage,
    get_heritage_by_id,
    count_heritage,
    heritage_near,
    search_listed_buildings,
    search_scheduled_monuments,
    list_properties_in_care,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Sample records for canned responses
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


def make_mock_client(handler):
    """Create a HesClient backed by httpx.MockTransport with the given handler."""
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://inspire.hes.scot/arcgis/rest/services",
        transport=transport,
    )
    return HesClient(client=http)


def make_recording_handler(response_data):
    """Create a handler that records requests and returns canned responses."""
    log = []
    def handler(request):
        raw_query = request.url.raw_path.decode().split("?", 1)[1]
        path = str(request.url.copy_with(query=None))
        log.append({"raw_query": raw_query, "path": path})
        return httpx.Response(200, json=response_data)
    return handler, log


def make_count_or_features_handler(count, features):
    """Create a handler that returns count for count queries, features otherwise."""
    def handler(request):
        if request.url.params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": count})
        return httpx.Response(200, json={"features": [{"attributes": f} for f in features]})
    return handler


def patch_sleep():
    """Make asyncio.sleep instant for fast error-path testing."""
    original_sleep = asyncio.sleep
    async def instant_sleep(_seconds):
        return None
    asyncio.sleep = instant_sleep
    return original_sleep


def restore_sleep(original_sleep):
    asyncio.sleep = original_sleep


async def run_request_cases():
    """Run all request cases and return the case list."""
    cases = []

    # Case 1: plain where
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.query(CANMORE_LAYER, "CANMOREID=52068")
    cases.append({
        "id": "plain_where",
        "call": "query",
        "params": {"layer": CANMORE_LAYER, "where": "CANMOREID=52068"},
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    # Case 2: where with special chars (wildcard+apostrophe+backslash+percent)
    special_term = "St Mary's 100% a\\b_c"
    where = _build_where(special_term)
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.query(CANMORE_LAYER, where)
    cases.append({
        "id": "where_with_special_chars",
        "call": "query",
        "params": {"layer": CANMORE_LAYER, "where": where},
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    # Case 3: count_only
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.count(CANMORE_LAYER, _build_where("castle"))
    cases.append({
        "id": "count_only",
        "call": "count",
        "params": {"layer": CANMORE_LAYER, "where": _build_where("castle")},
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    # Case 4: distinct
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.query(CANMORE_LAYER, _build_where("castle"), distinct=True)
    cases.append({
        "id": "distinct",
        "call": "query",
        "params": {"layer": CANMORE_LAYER, "where": _build_where("castle"), "distinct": True},
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    # Case 5: geometry envelope
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.query(
        CANMORE_LAYER, _build_where("castle"),
        geometry="-3.21,55.94,-3.19,55.96",
        geometry_type="esriGeometryEnvelope",
        in_sr=4326,
    )
    cases.append({
        "id": "geometry_envelope",
        "call": "query",
        "params": {
            "layer": CANMORE_LAYER,
            "where": _build_where("castle"),
            "geometry": "-3.21,55.94,-3.19,55.96",
            "geometry_type": "esriGeometryEnvelope",
            "in_sr": 4326,
        },
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    # Case 6: out_fields str
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.query(
        CANMORE_LAYER, _build_where("castle"),
        out_fields="CANMOREID,NMRSNAME,SITETYPE",
    )
    cases.append({
        "id": "out_fields_str",
        "call": "query",
        "params": {
            "layer": CANMORE_LAYER,
            "where": _build_where("castle"),
            "out_fields": "CANMOREID,NMRSNAME,SITETYPE",
        },
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    # Case 7: out_fields list (synthetic)
    handler, log = make_recording_handler({"count": 1})
    server._client = make_mock_client(handler)
    await server._client.query(
        CANMORE_LAYER, _build_where("castle"),
        out_fields=["CANMOREID", "NMRSNAME"],  # type: ignore[arg-type]  # synthetic: list-valued out_fields to pin httpx k=v1&k=v2 rule
    )
    cases.append({
        "id": "out_fields_list_synthetic",
        "call": "query",
        "params": {
            "layer": CANMORE_LAYER,
            "where": _build_where("castle"),
            "out_fields": ["CANMOREID", "NMRSNAME"],
        },
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
        "synthetic": True,
    })

    # Case 8: fetch_features
    handler, log = make_recording_handler({"features": [{"attributes": EDINBURGH_CASTLE}]})
    server._client = make_mock_client(handler)
    await server._client.fetch_features(
        CANMORE_LAYER, _build_where("castle"),
        out_fields=_SEARCH_FIELDS,
    )
    cases.append({
        "id": "fetch_features",
        "call": "fetch_features",
        "params": {
            "layer": CANMORE_LAYER,
            "where": _build_where("castle"),
            "out_fields": _SEARCH_FIELDS,
        },
        "raw_query": log[0]["raw_query"],
        "path": log[0]["path"],
    })

    return cases


async def run_behavior_cases():
    """Run all behavior cases and return the behavior list."""
    cases = []

    # --- search_heritage ---
    # dict-success
    server._client = make_mock_client(make_count_or_features_handler(1, [EDINBURGH_CASTLE]))
    result = await search_heritage("castle")
    cases.append({
        "tool": "search_heritage",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # empty-match-str
    server._client = make_mock_client(make_count_or_features_handler(0, []))
    result = await search_heritage("zzznotarealsite")
    cases.append({
        "tool": "search_heritage",
        "scenario": "empty_match_str",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (empty term)
    result = await search_heritage("")
    cases.append({
        "tool": "search_heritage",
        "scenario": "error_str_empty_term",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    def raise_connect_error(request):
        raise httpx.ConnectError("boom")
    server._client = make_mock_client(raise_connect_error)
    result = await search_heritage("castle")
    restore_sleep(original_sleep)
    cases.append({
        "tool": "search_heritage",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    # --- get_heritage_by_id ---
    # dict-success
    server._client = make_mock_client(lambda req: httpx.Response(200, json={"features": [{"attributes": EDINBURGH_CASTLE}]}))
    result = await get_heritage_by_id(52068)
    cases.append({
        "tool": "get_heritage_by_id",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (not found)
    server._client = make_mock_client(lambda req: httpx.Response(200, json={"features": []}))
    result = await get_heritage_by_id(999999999)
    cases.append({
        "tool": "get_heritage_by_id",
        "scenario": "error_str_not_found",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    server._client = make_mock_client(raise_connect_error)
    result = await get_heritage_by_id(52068)
    restore_sleep(original_sleep)
    cases.append({
        "tool": "get_heritage_by_id",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    # --- count_heritage ---
    # dict-success
    server._client = make_mock_client(make_count_or_features_handler(7098, []))
    result = await count_heritage(term="castle")
    cases.append({
        "tool": "count_heritage",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (empty term)
    result = await count_heritage(term="")
    cases.append({
        "tool": "count_heritage",
        "scenario": "error_str_empty_term",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    server._client = make_mock_client(raise_connect_error)
    result = await count_heritage(term="castle")
    restore_sleep(original_sleep)
    cases.append({
        "tool": "count_heritage",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    # --- heritage_near ---
    # dict-success
    server._client = make_mock_client(make_count_or_features_handler(1, [EDINBURGH_CASTLE]))
    result = await heritage_near(55.95, -3.20, radius_km=1.0)
    cases.append({
        "tool": "heritage_near",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (zero radius)
    result = await heritage_near(55.95, -3.20, radius_km=0)
    cases.append({
        "tool": "heritage_near",
        "scenario": "error_str_zero_radius",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    server._client = make_mock_client(raise_connect_error)
    result = await heritage_near(55.95, -3.20, radius_km=1.0)
    restore_sleep(original_sleep)
    cases.append({
        "tool": "heritage_near",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    # --- search_listed_buildings ---
    # dict-success
    server._client = make_mock_client(make_count_or_features_handler(1, [LISTED_BUILDING]))
    result = await search_listed_buildings("castle")
    cases.append({
        "tool": "search_listed_buildings",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # empty-match-str
    server._client = make_mock_client(make_count_or_features_handler(0, []))
    result = await search_listed_buildings("zzznotarealbuilding")
    cases.append({
        "tool": "search_listed_buildings",
        "scenario": "empty_match_str",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (invalid category)
    result = await search_listed_buildings("castle", category="D")
    cases.append({
        "tool": "search_listed_buildings",
        "scenario": "error_str_invalid_category",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    server._client = make_mock_client(raise_connect_error)
    result = await search_listed_buildings("castle")
    restore_sleep(original_sleep)
    cases.append({
        "tool": "search_listed_buildings",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    # --- search_scheduled_monuments ---
    # dict-success
    server._client = make_mock_client(make_count_or_features_handler(1, [SCHEDULED_MONUMENT]))
    result = await search_scheduled_monuments("castle")
    cases.append({
        "tool": "search_scheduled_monuments",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # empty-match-str
    server._client = make_mock_client(make_count_or_features_handler(0, []))
    result = await search_scheduled_monuments("zzznotarealmonument")
    cases.append({
        "tool": "search_scheduled_monuments",
        "scenario": "empty_match_str",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    server._client = make_mock_client(raise_connect_error)
    result = await search_scheduled_monuments("castle")
    restore_sleep(original_sleep)
    cases.append({
        "tool": "search_scheduled_monuments",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    # --- list_properties_in_care ---
    # dict-success
    server._client = make_mock_client(make_count_or_features_handler(1, [PROPERTY_IN_CARE]))
    result = await list_properties_in_care(term="castle")
    cases.append({
        "tool": "list_properties_in_care",
        "scenario": "dict_success",
        "result_type": type(result).__name__,
        "result": result,
    })

    # empty-match-str
    server._client = make_mock_client(make_count_or_features_handler(0, []))
    result = await list_properties_in_care(term="zzznotarealproperty")
    cases.append({
        "tool": "list_properties_in_care",
        "scenario": "empty_match_str",
        "result_type": type(result).__name__,
        "result": result,
    })

    # error-str (ConnectError)
    original_sleep = patch_sleep()
    server._client = make_mock_client(raise_connect_error)
    result = await list_properties_in_care(term="castle")
    restore_sleep(original_sleep)
    cases.append({
        "tool": "list_properties_in_care",
        "scenario": "error_str_connect_error",
        "result_type": type(result).__name__,
        "result": result,
    })

    return cases


def main():
    request_cases = asyncio.run(run_request_cases())
    behavior_cases = asyncio.run(run_behavior_cases())

    # Write JSON files
    requests_path = os.path.join(REPO, "golden", "legacy-requests.json")
    behavior_path = os.path.join(REPO, "golden", "legacy-behavior.json")

    os.makedirs(os.path.dirname(requests_path), exist_ok=True)

    with open(requests_path, "w") as f:
        json.dump({
            "note": f"Captured from legacy httpx {httpx.__version__} on {date.today().isoformat()}",
            "cases": request_cases,
        }, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with open(behavior_path, "w") as f:
        json.dump({
            "note": f"Captured from legacy httpx {httpx.__version__} on {date.today().isoformat()}",
            "legacy_notes": "Legacy fastmcp additionally wrapped tool results in structuredContent on the wire. The migrated server emits no structuredContent (D3/§4 trap).",
            "cases": behavior_cases,
        }, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"wrote {requests_path} ({len(request_cases)} cases) + {behavior_path} ({len(behavior_cases)} cases)")


if __name__ == "__main__":
    main()
