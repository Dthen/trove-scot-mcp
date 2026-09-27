"""Tests for the Canmore heritage MCP tools (server.py).

The server's module-level ``_client`` is swapped for a HesClient whose
``_urlopen`` seam is replaced by ``install_fake_urlopen`` so no live network
is used. Each tool's count-then-fetch sequence is exercised by queuing two
responses; the recorded full URLs are decoded via ``parse_qsl`` for semantic
parameter comparisons.

Ported from async httpx (tag 692af2a) to sync stdlib urllib. 51 test defs:
- 12 pure-helper tests (sync, unchanged)
- 39 transport tests (async -> direct call, MockTransport -> fake-urlopen)
- 2 parametrized blocks (7 + 6 cases)

Legacy -> new test name mapping (1:1, zero deletions):
  test_like_term_uppercases_and_wraps            -> test_like_term_uppercases_and_wraps
  test_build_where_term_only                     -> test_build_where_term_only
  test_build_where_no_args_matches_all           -> test_build_where_no_args_matches_all
  test_build_where_all_filters_anded             -> test_build_where_all_filters_anded
  test_build_where_filters_without_term          -> test_build_where_filters_without_term
  test_search_builds_correct_where               -> test_search_builds_correct_where
  test_search_adds_filters_with_and              -> test_search_adds_filters_with_and
  test_search_return_shape_and_enrichment        -> test_search_return_shape_and_enrichment
  test_search_truncated_when_over_cap            -> test_search_truncated_when_over_cap
  test_search_empty_result_friendly_message      -> test_search_empty_result_friendly_message
  test_get_by_id_returns_enriched_record         -> test_get_by_id_returns_enriched_record
  test_get_by_id_not_found                       -> test_get_by_id_not_found
  test_count_returns_total_found                 -> test_count_returns_total_found
  test_count_no_filters_counts_all               -> test_count_no_filters_counts_all
  test_count_small_result_no_note                -> test_count_small_result_no_note
  test_heritage_near_builds_envelope_and_sorts   -> test_heritage_near_builds_envelope_and_sorts
  test_heritage_near_adds_distance_km            -> test_heritage_near_adds_distance_km
  test_heritage_near_term_filter                 -> test_heritage_near_term_filter
  test_heritage_near_truncated_when_over_limit   -> test_heritage_near_truncated_when_over_limit
  test_heritage_near_limit_respected             -> test_heritage_near_limit_respected
  test_heritage_near_count_over_cap_truncated_with_cap_note -> test_heritage_near_count_over_cap_truncated_with_cap_note
  test_heritage_near_count_between_limit_and_cap_raise_limit_note -> test_heritage_near_count_between_limit_and_cap_raise_limit_note
  test_heritage_near_rejects_zero_radius         -> test_heritage_near_rejects_zero_radius
  test_heritage_near_rejects_negative_radius     -> test_heritage_near_rejects_negative_radius
  test_like_term_escapes_apostrophes             -> test_like_term_escapes_apostrophes
  test_search_with_apostrophe_builds_valid_where -> test_search_with_apostrophe_builds_valid_where
  test_like_term_escapes_percent                 -> test_like_term_escapes_percent
  test_like_term_escapes_underscore              -> test_like_term_escapes_underscore
  test_like_term_escapes_backslash_first         -> test_like_term_escapes_backslash_first
  test_like_clause_appends_escape_suffix         -> test_like_clause_appends_escape_suffix
  test_search_with_percent_builds_escaped_where  -> test_search_with_percent_builds_escaped_where
  test_like_term_escaping_parametrized           -> test_like_term_escaping_parametrized
  test_like_escaping_is_self_consistent          -> test_like_escaping_is_self_consistent
  test_search_empty_term_returns_error           -> test_search_empty_term_returns_error
  test_search_whitespace_term_returns_error      -> test_search_whitespace_term_returns_error
  test_count_empty_term_returns_error            -> test_count_empty_term_returns_error
  test_search_http_error_returns_error_string    -> test_search_http_error_returns_error_string
  test_get_by_id_http_error_returns_error_string -> test_get_by_id_http_error_returns_error_string
  test_listed_buildings_builds_correct_where     -> test_listed_buildings_builds_correct_where
  test_listed_buildings_category_validation_rejects_d -> test_listed_buildings_category_validation_rejects_d
  test_listed_buildings_return_shape_and_enrichment -> test_listed_buildings_return_shape_and_enrichment
  test_listed_buildings_empty_result_friendly_message -> test_listed_buildings_empty_result_friendly_message
  test_listed_buildings_http_error_returns_error_string -> test_listed_buildings_http_error_returns_error_string
  test_scheduled_monuments_builds_correct_where  -> test_scheduled_monuments_builds_correct_where
  test_scheduled_monuments_return_shape_and_enrichment -> test_scheduled_monuments_return_shape_and_enrichment
  test_scheduled_monuments_empty_result_friendly_message -> test_scheduled_monuments_empty_result_friendly_message
  test_scheduled_monuments_http_error_returns_error_string -> test_scheduled_monuments_http_error_returns_error_string
  test_properties_in_care_builds_correct_where   -> test_properties_in_care_builds_correct_where
  test_properties_in_care_return_shape_and_enrichment -> test_properties_in_care_return_shape_and_enrichment
  test_properties_in_care_empty_result_friendly_message -> test_properties_in_care_empty_result_friendly_message
  test_properties_in_care_http_error_returns_error_string -> test_properties_in_care_http_error_returns_error_string
"""

import urllib.error
from urllib.parse import parse_qsl, urlsplit

import pytest

import trove_scot_mcp.client as client_mod
from tests.helpers_transport import install_fake_urlopen
from trove_scot_mcp.server import (
    _build_where,
    _like_clause,
    _like_term,
    count_heritage,
    get_heritage_by_id,
    heritage_near,
    list_properties_in_care,
    search_heritage,
    search_listed_buildings,
    search_scheduled_monuments,
)

# Edinburgh Castle's BNG coords -> ~55.95, -3.20.
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


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Replace ``_sleep`` with a recorder so retry delays are pinned, not slept."""
    sleeps = []
    monkeypatch.setattr(client_mod, "_sleep", lambda s: sleeps.append(s))
    return sleeps


def _params(url):
    """Decode a recorded full URL into a query-param dict (semantic compare)."""
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


# -- pure helpers ----------------------------------------------------------

# Every LIKE clause carries this ESCAPE suffix so user-supplied % / _ match
# literally (see _like_term / _like_clause in server.py).
ESC = " ESCAPE '\\'"


def test_like_term_uppercases_and_wraps():
    assert _like_term("castle") == "'%CASTLE%'"
    assert _like_term("  Edinburgh Castle ") == "'%EDINBURGH CASTLE%'"


def test_build_where_term_only():
    assert _build_where("castle") == f"UPPER(NMRSNAME) LIKE '%CASTLE%'{ESC}"


def test_build_where_no_args_matches_all():
    assert _build_where() == "1=1"


def test_build_where_all_filters_anded():
    where = _build_where(
        "castle", sitetype="fort", council="edinburgh", broadclass="defence"
    )
    assert where == (
        f"UPPER(NMRSNAME) LIKE '%CASTLE%'{ESC} AND "
        f"UPPER(SITETYPE) LIKE '%FORT%'{ESC} AND "
        f"UPPER(COUNCIL) LIKE '%EDINBURGH%'{ESC} AND "
        f"UPPER(BROADCLASS) LIKE '%DEFENCE%'{ESC}"
    )


def test_build_where_filters_without_term():
    where = _build_where(council="fife")
    assert where == f"UPPER(COUNCIL) LIKE '%FIFE%'{ESC}"


# -- search_heritage -------------------------------------------------------


def test_search_builds_correct_where(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {"count": 1}),
        (200, {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}),
    ])
    result = search_heritage("edinburgh castle")

    urls = state["urls"]
    params0 = _params(urls[0])
    params1 = _params(urls[1])
    # First call is the count, second is the fetch -- both carry the same where.
    assert params0["where"] == f"UPPER(NMRSNAME) LIKE '%EDINBURGH CASTLE%'{ESC}"
    assert params1["where"] == f"UPPER(NMRSNAME) LIKE '%EDINBURGH CASTLE%'{ESC}"
    # Count call uses returnCountOnly; fetch call uses named outFields (not *).
    assert params0["returnCountOnly"] == "true"
    assert params1["outFields"] != "*"
    assert "NMRSNAME" in params1["outFields"]
    assert isinstance(result, dict)


def test_search_adds_filters_with_and(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {"count": 1}),
        (200, {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}),
    ])
    search_heritage("castle", sitetype="fort", council="edinburgh", broadclass="defence")
    expected = (
        f"UPPER(NMRSNAME) LIKE '%CASTLE%'{ESC} AND UPPER(SITETYPE) LIKE '%FORT%'{ESC} AND "
        f"UPPER(COUNCIL) LIKE '%EDINBURGH%'{ESC} AND UPPER(BROADCLASS) LIKE '%DEFENCE%'{ESC}"
    )
    params0 = _params(state["urls"][0])
    assert params0["where"] == expected


def test_search_return_shape_and_enrichment(monkeypatch):
    install_fake_urlopen(monkeypatch, [
        (200, {"count": 1}),
        (200, {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}),
    ])
    result = search_heritage("edinburgh castle", limit=50)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    site = result["sites"][0]
    assert site["NMRSNAME"] == "EDINBURGH CASTLE"
    # enrich_with_latlon added lat/lon from the BNG coords.
    assert site["lat"] == pytest.approx(55.95, abs=0.01)
    assert site["lon"] == pytest.approx(-3.20, abs=0.01)


def test_search_truncated_when_over_cap(monkeypatch):
    # Count reports 5000 (> 1000 cap); fetch returns 1000 features.
    install_fake_urlopen(monkeypatch, [
        (200, {"count": 5000}),
        (200, {"features": [{"attributes": {"CANMOREID": i, "NMRSNAME": "X"}} for i in range(1000)]}),
    ])
    result = search_heritage("castle", limit=50)

    assert result["total_found"] == 5000
    assert result["truncated"] is True
    assert result["count"] == 50  # truncated to limit
    assert len(result["sites"]) == 50
    assert "1000" in result["note"]  # mentions the cap


def test_search_empty_result_friendly_message(monkeypatch):
    install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = search_heritage("zzznotarealsite")
    assert isinstance(result, str)
    assert "No heritage sites matched" in result


# -- get_heritage_by_id ----------------------------------------------------


def test_get_by_id_returns_enriched_record(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}),
    ])
    result = get_heritage_by_id(52068)

    params0 = _params(state["urls"][0])
    assert params0["where"] == "CANMOREID=52068"
    assert params0["outFields"] == "*"  # full record
    assert isinstance(result, dict)
    assert result["NMRSNAME"] == "EDINBURGH CASTLE"
    assert result["lat"] == pytest.approx(55.95, abs=0.01)
    assert result["lon"] == pytest.approx(-3.20, abs=0.01)


def test_get_by_id_not_found(monkeypatch):
    install_fake_urlopen(monkeypatch, [(200, {"features": []})])
    result = get_heritage_by_id(999999999)
    assert result == "Error: no heritage site found with Canmore ID 999999999"


# -- count_heritage --------------------------------------------------------


def test_count_returns_total_found(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"count": 7098})])
    result = count_heritage(term="castle")
    params0 = _params(state["urls"][0])
    assert params0["returnCountOnly"] == "true"
    assert params0["where"] == f"UPPER(NMRSNAME) LIKE '%CASTLE%'{ESC}"
    assert result["total_found"] == 7098
    assert "note" in result  # > 1000 cap -> warning present


def test_count_no_filters_counts_all(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"count": 313456})])
    result = count_heritage()
    params0 = _params(state["urls"][0])
    assert params0["where"] == "1=1"
    assert result["total_found"] == 313456


def test_count_small_result_no_note(monkeypatch):
    install_fake_urlopen(monkeypatch, [(200, {"count": 42})])
    result = count_heritage(term="zzz")
    assert result == {"total_found": 42}


# -- heritage_near ---------------------------------------------------------


def _near_handler(features, count=None):
    """Fake responses for heritage_near: count query -> count, fetch -> features.

    ``count`` defaults to the number of features so the count-first query and
    the fetch agree.
    """
    if count is None:
        count = len(features)
    return [
        (200, {"count": count}),
        (200, {"features": [{"attributes": dict(f)} for f in features]}),
    ]


def test_heritage_near_builds_envelope_and_sorts(monkeypatch):
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
    state = install_fake_urlopen(monkeypatch, _near_handler([far, EDINBURGH_CASTLE]))
    # Edinburgh Castle lat/lon ~ 55.95, -3.20.
    result = heritage_near(55.95, -3.20, radius_km=2.0)

    urls = state["urls"]
    params0 = _params(urls[0])
    params1 = _params(urls[1])
    # First call is the count (returnCountOnly), second is the fetch.
    assert params0["returnCountOnly"] == "true"
    assert params1["geometryType"] == "esriGeometryEnvelope"
    assert params1["inSR"] == "4326"
    min_lon, min_lat, max_lon, max_lat = (float(x) for x in params1["geometry"].split(","))
    assert min_lon < -3.20 < max_lon
    assert min_lat < 55.95 < max_lat
    # ~2km radius -> lat delta ~ 2/111 ~ 0.018.
    assert (max_lat - min_lat) == pytest.approx(2 * 2.0 / 111.0, rel=0.01)

    # Sorted nearest-first; Edinburgh Castle (the actual point) comes first.
    assert result["count"] == 2
    assert result["total_found"] == 2
    assert result["truncated"] is False
    assert "note" not in result
    assert result["sites"][0]["CANMOREID"] == 52068
    assert result["sites"][1]["CANMOREID"] == 123


def test_heritage_near_adds_distance_km(monkeypatch):
    install_fake_urlopen(monkeypatch, _near_handler([EDINBURGH_CASTLE]))
    result = heritage_near(55.95, -3.20, radius_km=1.0)
    site = result["sites"][0]
    assert "distance_km" in site
    # Castle is essentially at the query point -> very small distance.
    assert site["distance_km"] < 1.0


def test_heritage_near_term_filter(monkeypatch):
    state = install_fake_urlopen(monkeypatch, _near_handler([]))
    heritage_near(55.95, -3.20, term="castle")
    # Count query only (count=0 -> early return).
    params0 = _params(state["urls"][0])
    assert params0["where"] == f"UPPER(NMRSNAME) LIKE '%CASTLE%'{ESC}"


def test_heritage_near_truncated_when_over_limit(monkeypatch):
    # 5 geocoded sites but limit=2 -> truncated, total_found reflects all 5.
    def _site(i):
        return {
            "CANMOREID": i,
            "NMRSNAME": f"SITE {i}",
            "XCOORD": 325112 + i,
            "YCOORD": 673497 + i,
        }

    install_fake_urlopen(monkeypatch, _near_handler([_site(i) for i in range(5)], count=5))
    result = heritage_near(55.95, -3.20, radius_km=1.0, limit=2)

    assert result["count"] == 2
    assert result["total_found"] == 5
    assert result["truncated"] is True
    assert len(result["sites"]) == 2
    assert "note" in result
    # Count is between limit and the 1000 cap -> "raise limit" note, not cap note.
    assert "Raise 'limit'" in result["note"]


def test_heritage_near_limit_respected(monkeypatch):
    install_fake_urlopen(monkeypatch, _near_handler([EDINBURGH_CASTLE]))
    result = heritage_near(55.95, -3.20, radius_km=1.0, limit=50)
    assert result["total_found"] == 1
    assert result["truncated"] is False


def test_heritage_near_count_over_cap_truncated_with_cap_note(monkeypatch):
    # Count reports 5000 (> 1000 cap); fetch returns 1000 features. truncated
    # must be True and the note must mention the 1000 cap + approximate ordering.
    def _site(i):
        return {
            "CANMOREID": i,
            "NMRSNAME": f"SITE {i}",
            "XCOORD": 325112 + (i % 40),
            "YCOORD": 673497 + (i % 40),
        }

    install_fake_urlopen(monkeypatch, _near_handler([_site(i) for i in range(1000)], count=5000))
    result = heritage_near(55.95, -3.20, radius_km=1.0, limit=50)

    assert result["total_found"] == 5000
    assert result["truncated"] is True
    assert result["count"] == 50
    assert "1000" in result["note"]  # mentions the cap
    assert "approximate" in result["note"]  # honest about nearest-first ordering


def test_heritage_near_count_between_limit_and_cap_raise_limit_note(monkeypatch):
    # Count = 200 (<= 1000 cap but > limit=50) -> raise-limit note, not cap note.
    def _site(i):
        return {
            "CANMOREID": i,
            "NMRSNAME": f"SITE {i}",
            "XCOORD": 325112 + (i % 40),
            "YCOORD": 673497 + (i % 40),
        }

    install_fake_urlopen(monkeypatch, _near_handler([_site(i) for i in range(200)], count=200))
    result = heritage_near(55.95, -3.20, radius_km=1.0, limit=50)

    assert result["total_found"] == 200
    assert result["truncated"] is True
    assert result["count"] == 50
    assert "Raise 'limit'" in result["note"]
    assert "1000" not in result["note"]  # not the cap note


def test_heritage_near_rejects_zero_radius(monkeypatch):
    state = install_fake_urlopen(monkeypatch, _near_handler([]))
    result = heritage_near(55.9, -3.2, radius_km=0)
    assert result == "Error: radius_km must be a positive number"
    assert state["urls"] == []  # short-circuited before any query


def test_heritage_near_rejects_negative_radius(monkeypatch):
    state = install_fake_urlopen(monkeypatch, _near_handler([]))
    result = heritage_near(55.9, -3.2, radius_km=-5)
    assert result == "Error: radius_km must be a positive number"
    assert state["urls"] == []  # short-circuited before any query


# -- apostrophe escaping (FIX 2) -------------------------------------------


def test_like_term_escapes_apostrophes():
    assert _like_term("St Mary's") == "'%ST MARY''S%'"
    assert _like_term("Queen Mary's Thorn") == "'%QUEEN MARY''S THORN%'"


def test_search_with_apostrophe_builds_valid_where(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {"count": 1}),
        (200, {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}),
    ])
    result = search_heritage("St Mary's")
    # Balanced quotes: the apostrophe is doubled, not left raw.
    params0 = _params(state["urls"][0])
    assert params0["where"] == f"UPPER(NMRSNAME) LIKE '%ST MARY''S%'{ESC}"
    assert isinstance(result, dict)


# -- LIKE wildcard escaping (FIX 4) ----------------------------------------


def test_like_term_escapes_percent():
    # A literal % must be backslash-escaped so it matches literally, not as a
    # SQL wildcard. (Paired with the ESCAPE '\' suffix on the clause.)
    assert _like_term("100%") == "'%100\\%%'"


def test_like_term_escapes_underscore():
    assert _like_term("a_b") == "'%A\\_B%'"


def test_like_term_escapes_backslash_first():
    # A literal backslash is doubled before % / _ escaping so it isn't confused
    # with an escape sequence.
    assert _like_term("a\\b") == "'%A\\\\B%'"


def test_like_clause_appends_escape_suffix():
    assert _like_clause("NMRSNAME", "100%") == (
        "UPPER(NMRSNAME) LIKE '%100\\%%' ESCAPE '\\'"
    )


def test_search_with_percent_builds_escaped_where(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [
        (200, {"count": 1}),
        (200, {"features": [{"attributes": dict(EDINBURGH_CASTLE)}]}),
    ])
    search_heritage("100%")
    params0 = _params(state["urls"][0])
    assert params0["where"] == "UPPER(NMRSNAME) LIKE '%100\\%%' ESCAPE '\\'"


# -- LIKE escaping: parametrized + self-consistency (backslash correctness) --

# One literal backslash, built via chr() to avoid any source-level ambiguity
# about Python string escapes (e.g. "a\\b" vs a raw tab from "a\tb").
_BS = chr(92)


@pytest.mark.parametrize(
    "term,expected_pattern",
    [
        # Plain text is just uppercased and wrapped in %...%.
        ("castle", "'%CASTLE%'"),
        # Literal % and _ are backslash-escaped so they match literally.
        ("100%", "'%100\\%%'"),
        ("a_b", "'%A\\_B%'"),
        # Apostrophe is doubled (standard SQL literal escaping).
        ("St Mary's", "'%ST MARY''S%'"),
        # ONE literal backslash becomes the escaped pair \\ -- which, under
        # ESCAPE '\', denotes exactly one literal backslash in the data.
        ("a" + _BS + "b", "'%A" + _BS + _BS + "B%'"),
        # Two literal backslashes become four (each one doubled).
        ("a" + _BS + _BS + "b", "'%A" + _BS + _BS + _BS + _BS + "B%'"),
        # Backslash-then-percent: backslash doubled first, then % escaped,
        # so neither the added backslashes nor the % is mis-handled.
        ("a" + _BS + "%b", "'%A" + _BS + _BS + "\\%B%'"),
    ],
)
def test_like_term_escaping_parametrized(term, expected_pattern):
    assert _like_term(term) == expected_pattern


def _sqlite_matches(clause: str, rows: list[str]) -> list[str]:
    """Return which of ``rows`` satisfy ``clause`` (a full WHERE predicate).

    Uses a real SQL engine (sqlite) executing the clause INLINE -- exactly as the
    ArcGIS server receives it -- so SQL-literal escaping ('' -> ') and the LIKE
    ESCAPE mechanism are both honoured the way the live server applies them.
    """
    import sqlite3

    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    cur.execute("CREATE TABLE t (NMRSNAME TEXT)")
    for r in rows:
        cur.execute("INSERT INTO t VALUES (?)", (r,))
    cur.execute("SELECT NMRSNAME FROM t WHERE " + clause)
    return [row[0] for row in cur.fetchall()]


@pytest.mark.parametrize(
    "term",
    [
        "100%",
        "a_b",
        "St Mary's",
        "a" + _BS + "b",          # one literal backslash
        "a" + _BS + _BS + "b",    # two literal backslashes
        "a" + _BS + "%b",         # backslash immediately followed by percent
    ],
)
def test_like_escaping_is_self_consistent(term):
    """The pattern _like_term builds, interpreted with ESCAPE '\', must match the
    literal term (uppercased) and must NOT match a near-miss neighbour.

    This is the regression guard for the backslash bug: a single-backslash term
    must match a single-backslash name and must not over-match a two-backslash
    name (or vice-versa).
    """
    target = term.strip().upper()
    # A neighbour that differs only in backslash count / a wildcard char, to
    # prove the escaping is exact rather than accidentally permissive.
    neighbours = {
        "100%": "100",
        "a_b": "AXB",  # an unescaped _ would match any single char (AXB)
        "St Mary's": "ST MARYXS",
        "a" + _BS + "b": "A" + _BS + _BS + "B",          # 2 backslashes
        "a" + _BS + _BS + "b": "A" + _BS + "B",          # 1 backslash
        "a" + _BS + "%b": "A" + _BS + "XB",              # % as wildcard would match
    }
    neighbour = neighbours[term]
    clause = _like_clause("NMRSNAME", term)
    matched = _sqlite_matches(clause, [target, neighbour])
    assert target in matched, f"{clause!r} should match literal {target!r}"
    assert neighbour not in matched, (
        f"{clause!r} must not match near-miss {neighbour!r} "
        "(escaping is too permissive)"
    )


# -- empty/whitespace term guard (FIX 3) -----------------------------------


def test_search_empty_term_returns_error(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = search_heritage("")
    assert result == "Error: please provide a search term"
    assert state["urls"] == []  # no query issued


def test_search_whitespace_term_returns_error(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = search_heritage("   ")
    assert result == "Error: please provide a search term"
    assert state["urls"] == []


def test_count_empty_term_returns_error(monkeypatch):
    state = install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = count_heritage(term="")
    assert isinstance(result, str)
    assert result.startswith("Error:")
    assert state["urls"] == []


# -- error handling --------------------------------------------------------


def test_search_http_error_returns_error_string(monkeypatch):
    install_fake_urlopen(monkeypatch, [urllib.error.URLError("boom")] * 3)
    result = search_heritage("castle")
    assert isinstance(result, str)
    assert result.startswith("Error: ")


def test_get_by_id_http_error_returns_error_string(monkeypatch):
    install_fake_urlopen(monkeypatch, [urllib.error.URLError("boom")] * 3)
    result = get_heritage_by_id(52068)
    assert isinstance(result, str)
    assert result.startswith("Error: ")


# -- designation layers ----------------------------------------------------

# Sample designation records (BNG X/Y coords -> enrich_with_latlon adds lat/lon).
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
    """Responses: count->{count:1}, fetch->one feature with ``record`` attrs."""
    return [
        (200, {"count": 1}),
        (200, {"features": [{"attributes": dict(record)}]}),
    ]


# -- search_listed_buildings ------------------------------------------------


def test_listed_buildings_builds_correct_where(monkeypatch):
    state = install_fake_urlopen(monkeypatch, _count_or_features(LISTED_BUILDING))
    search_listed_buildings("castle", category="a", local_authority="edinburgh")

    expected = (
        f"UPPER(DES_TITLE) LIKE '%CASTLE%'{ESC} AND CATEGORY = 'A' AND "
        f"UPPER(LOCAL_AUTH) LIKE '%EDINBURGH%'{ESC}"
    )
    params0 = _params(state["urls"][0])
    params1 = _params(state["urls"][1])
    # Count call and fetch call both carry the same where.
    assert params0["where"] == expected
    assert params1["where"] == expected
    assert params0["returnCountOnly"] == "true"
    assert "DES_TITLE" in params1["outFields"]
    assert "CATEGORY" in params1["outFields"]


def test_listed_buildings_category_validation_rejects_d(monkeypatch):
    # No network call should be made for an invalid category.
    state = install_fake_urlopen(monkeypatch, _count_or_features(LISTED_BUILDING))
    result = search_listed_buildings("castle", category="D")
    assert result == "Error: category must be A, B, or C"
    assert state["urls"] == []  # short-circuited before any query


def test_listed_buildings_return_shape_and_enrichment(monkeypatch):
    install_fake_urlopen(monkeypatch, _count_or_features(LISTED_BUILDING))
    result = search_listed_buildings("castle", limit=50)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    building = result["buildings"][0]
    assert building["CATEGORY"] == "B"
    # enrich_with_latlon added lat/lon from the BNG X/Y coords.
    assert building["lat"] == pytest.approx(55.95, abs=0.01)
    assert building["lon"] == pytest.approx(-3.20, abs=0.01)


def test_listed_buildings_empty_result_friendly_message(monkeypatch):
    install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = search_listed_buildings("zzznotarealbuilding")
    assert isinstance(result, str)
    assert "No listed buildings matched" in result


def test_listed_buildings_http_error_returns_error_string(monkeypatch):
    install_fake_urlopen(monkeypatch, [urllib.error.URLError("boom")] * 3)
    result = search_listed_buildings("castle")
    assert isinstance(result, str)
    assert result.startswith("Error: ")


# -- search_scheduled_monuments --------------------------------------------


def test_scheduled_monuments_builds_correct_where(monkeypatch):
    state = install_fake_urlopen(monkeypatch, _count_or_features(SCHEDULED_MONUMENT))
    search_scheduled_monuments("castle", local_authority="edinburgh")

    expected = (
        f"UPPER(DES_TITLE) LIKE '%CASTLE%'{ESC} AND UPPER(LOCAL_AUTH) LIKE '%EDINBURGH%'{ESC}"
    )
    params0 = _params(state["urls"][0])
    params1 = _params(state["urls"][1])
    assert params0["where"] == expected
    assert params1["where"] == expected
    assert params0["returnCountOnly"] == "true"
    assert "DES_TITLE" in params1["outFields"]
    assert "AREA" in params1["outFields"]


def test_scheduled_monuments_return_shape_and_enrichment(monkeypatch):
    install_fake_urlopen(monkeypatch, _count_or_features(SCHEDULED_MONUMENT))
    result = search_scheduled_monuments("castle", limit=50)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    monument = result["monuments"][0]
    assert monument["DES_TITLE"] == "EDINBURGH CASTLE"
    assert monument["lat"] == pytest.approx(55.95, abs=0.01)
    assert monument["lon"] == pytest.approx(-3.20, abs=0.01)


def test_scheduled_monuments_empty_result_friendly_message(monkeypatch):
    install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = search_scheduled_monuments("zzznotarealmonument")
    assert isinstance(result, str)
    assert "No scheduled monuments matched" in result


def test_scheduled_monuments_http_error_returns_error_string(monkeypatch):
    install_fake_urlopen(monkeypatch, [urllib.error.URLError("boom")] * 3)
    result = search_scheduled_monuments("castle")
    assert isinstance(result, str)
    assert result.startswith("Error: ")


# -- list_properties_in_care ------------------------------------------------


def test_properties_in_care_builds_correct_where(monkeypatch):
    state = install_fake_urlopen(monkeypatch, _count_or_features(PROPERTY_IN_CARE))
    list_properties_in_care(local_authority="edinburgh", term="castle")

    expected = (
        f"UPPER(PIC_NAME) LIKE '%CASTLE%'{ESC} AND UPPER(LOCAL_AUTH) LIKE '%EDINBURGH%'{ESC}"
    )
    params0 = _params(state["urls"][0])
    params1 = _params(state["urls"][1])
    assert params0["where"] == expected
    assert params1["where"] == expected
    assert params0["returnCountOnly"] == "true"
    assert "PIC_NAME" in params1["outFields"]


def test_properties_in_care_return_shape_and_enrichment(monkeypatch):
    install_fake_urlopen(monkeypatch, _count_or_features(PROPERTY_IN_CARE))
    result = list_properties_in_care(limit=100)

    assert result["count"] == 1
    assert result["total_found"] == 1
    assert result["truncated"] is False
    assert "note" not in result
    prop = result["properties"][0]
    assert prop["PIC_NAME"] == "Edinburgh Castle"
    assert prop["lat"] == pytest.approx(55.95, abs=0.01)
    assert prop["lon"] == pytest.approx(-3.20, abs=0.01)


def test_properties_in_care_empty_result_friendly_message(monkeypatch):
    install_fake_urlopen(monkeypatch, [(200, {"count": 0})])
    result = list_properties_in_care(term="zzznotarealproperty")
    assert isinstance(result, str)
    assert "No properties in care matched" in result


def test_properties_in_care_http_error_returns_error_string(monkeypatch):
    install_fake_urlopen(monkeypatch, [urllib.error.URLError("boom")] * 3)
    result = list_properties_in_care()
    assert isinstance(result, str)
    assert result.startswith("Error: ")
