"""FastMCP server exposing Scotland's historic environment as MCP tools.

Data comes from the Historic Environment Scotland (HES) ArcGIS REST API, the
backend for trove.scot — over 313,000 heritage records (castles, monuments,
listed buildings, archaeological sites) plus designation layers (listed
buildings, scheduled monuments, properties in care).

This module currently holds the FastMCP skeleton; query tools are added on top
of :class:`trove_scot_mcp.client.HesClient`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "trove-scot",
    instructions=(
        "Search Scotland's historic environment — 313K+ heritage records "
        "(castles, monuments, listed buildings, archaeological sites) via "
        "Historic Environment Scotland"
    ),
)


def main() -> None:
    """Entry point for running the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
