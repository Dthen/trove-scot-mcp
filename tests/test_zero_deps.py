#!/usr/bin/env python3
"""Zero-dependencies regression gate (R5).

Scans ``src/trove_scot_mcp`` for any non-stdlib top-level imports
(besides the package itself). Replaces the retired VmHWM gate — this
proves R5 (no third-party deps) deterministically from source text.

A future PR that slips ``import httpx`` or ``import fastmcp`` back into
the server will fail this test immediately — that is the point.
"""

import ast
import pathlib
import sys

SRC = pathlib.Path(__file__).parent.parent / "src" / "trove_scot_mcp"
SELF = "trove_scot_mcp"


def _scan() -> set[str]:
    mods: set[str] = set()
    for f in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods.add(n.module.split(".")[0])
    std = set(sys.stdlib_module_names)
    return {m for m in mods if m not in std and m != SELF}


def test_zero_deps():
    r"""No non-stdlib third-party imports anywhere under ``src/trove_scot_mcp``."""
    bad = _scan()
    assert not bad, f"non-stdlib third-party imports detected: {sorted(bad)}"
