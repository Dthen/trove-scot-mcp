"""Smoke tests for the trove-scot-mcp era server (T09 rewrite).

The original ``mcp`` FastMCP skeleton died with the framework (T06 deleted
lifespan, T06-T07 replaced FastMCP with a stdlib loop). These tests pin the
public constants the loop serves: ``SERVER_INFO`` and the ``TOOLS`` literal.

Version coherence with ``pyproject.toml``/``__init__.py`` is asserted in T10
(0.3.0 bump) -- here we assert SHAPE (name, dotted-quad version) not value.
"""

from trove_scot_mcp.server import SERVER_INFO, TOOLS

EXPECTED_TOOLS = [
    "search_heritage",
    "get_heritage_by_id",
    "count_heritage",
    "heritage_near",
    "search_listed_buildings",
    "search_scheduled_monuments",
    "list_properties_in_care",
]


def test_server_imports():
    assert SERVER_INFO is not None
    assert TOOLS is not None


def test_server_info_shape():
    assert SERVER_INFO["name"] == "trove-scot"
    assert SERVER_INFO["version"].count(".") == 2


def test_tools_count_and_names():
    assert len(TOOLS) == 7
    names = [t["name"] for t in TOOLS]
    assert names == EXPECTED_TOOLS
