"""Smoke tests for the trove-scot FastMCP server skeleton."""

from trove_scot_mcp.server import mcp


def test_server_imports():
    assert mcp is not None


def test_server_name():
    assert mcp.name == "trove-scot"
