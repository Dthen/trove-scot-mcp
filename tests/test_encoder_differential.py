"""Byte-differential test for encode_query() against golden wire fixtures.

Verifies that :func:`trove_scot_mcp.query.encode_query` reproduces the exact
query-segment output of ``httpx 0.28``'s ``Request(params=...)`` for every case
in ``golden/legacy-requests.json`` — byte-equality, zero skips, order
preserved.

Plus embedded literal edge cases per B.1 precedent.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict

import pytest

from trove_scot_mcp.query import encode_query

GOLDEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "golden", "legacy-requests.json"
)


def _load_cases() -> list[dict]:
    with open(GOLDEN_PATH) as f:
        return json.load(f)["cases"]


def _build_query_params(case: dict) -> OrderedDict:
    """Reconstruct the full httpx param dict for a fixture case.

    The fixture's ``params`` field holds only the user-supplied tool args
    (``where``, ``layer``, ``out_fields``, etc.); the ``raw_query`` is the
    FULL query string httpx produced, which also includes ``f=json``,
    ``outFields``, ``returnCountOnly``, ``returnGeometry``, etc. This helper
    rebuilds that full dict in the exact insertion order httpx would have
    seen it, so ``encode_query`` can be diffed against ``raw_query``.
    """
    p = case["params"]
    call = case["call"]
    qp = OrderedDict()
    qp["where"] = p["where"]
    qp["f"] = "json"
    if call == "count":
        qp["returnCountOnly"] = "true"
    else:
        if "out_fields" in p:
            qp["outFields"] = p["out_fields"]
        else:
            qp["outFields"] = "*"
        if p.get("distinct"):
            qp["returnDistinctValues"] = "true"
    if "geometry" in p:
        qp["geometry"] = p["geometry"]
        qp["geometryType"] = p.get("geometry_type", "esriGeometryEnvelope")
        qp["inSR"] = str(p.get("in_sr", 4326))
        qp["spatialRel"] = "esriSpatialRelIntersects"
    qp["returnGeometry"] = "false"
    return qp


# ---------------------------------------------------------------------------
# Differential: every golden case byte-equal, zero skips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    _load_cases(),
    ids=lambda c: c["id"],
)
def test_encode_query_matches_legacy(case: dict):
    """encode_query() reproduces httpx's raw_query byte-for-byte."""
    qp = _build_query_params(case)
    actual = encode_query(qp)
    expected = case["raw_query"]
    assert actual == expected, (
        f"\n  expected: {expected!r}\n  actual:   {actual!r}"
    )


# ---------------------------------------------------------------------------
# Embedded literal edges (B.1 precedent) — direct encode_query calls
# ---------------------------------------------------------------------------


def test_space_encoded_as_plus():
    assert encode_query({"a b": "x y"}) == "a+b=x+y"


def test_non_ascii_utf8_bytes():
    assert encode_query({"u": "é"}) == "u=%C3%A9"


def test_tilde_literal():
    assert encode_query({"t": "~"}) == "t=~"


def test_list_value_repeated_bare_key():
    assert encode_query({"k": ["v1", "v2"]}) == "k=v1&k=v2"


def test_special_chars_all_encoded():
    # ( ) ' % \  →  %28 %29 %27 %25 %5C
    result = encode_query(
        {"w": "UPPER(N) LIKE '%100%' ESCAPE '\\'"}
    )
    assert result == (
        "w=UPPER%28N%29+LIKE+%27%25100%25%27+ESCAPE+%27%5C%27"
    )


def test_order_preserved_three_keys():
    """Dict insertion order is preserved (no sorting)."""
    result = encode_query({"z": "1", "a": "2", "m": "3"})
    assert result == "z=1&a=2&m=3"


def test_order_preserved_five_keys():
    """Order preservation holds for 5+ keys — httpx sends them verbatim."""
    result = encode_query(
        {"last": "L", "first": "F", "middle": "M", "alpha": "A", "beta": "B"}
    )
    assert result == "last=L&first=F&middle=M&alpha=A&beta=B"


def test_tuple_value_repeated_bare_key():
    """Tuple values behave like lists — repeated bare key."""
    assert encode_query({"k": ("v1", "v2")}) == "k=v1&k=v2"


def test_int_value_coerced_to_str():
    """Int values (e.g. in_sr) are coerced to str upstream."""
    assert encode_query({"n": 42}) == "n=42"


def test_empty_string_value():
    assert encode_query({"k": ""}) == "k="


def test_empty_params():
    assert encode_query({}) == ""
