"""Shared transport test helpers for the trove-scot-mcp test suite.

Used by test_client.py and test_tools.py so both files exercise the same
``_urlopen`` / ``_sleep`` seams and never diverge.
"""

import json

import trove_scot_mcp.client as client_mod


class FakeResponse:
    """Minimal stand-in for the object returned by ``urllib.request.urlopen``."""

    def __init__(self, status, body):
        self._status = status
        if isinstance(body, bytes):
            self._body = body
        elif isinstance(body, str):
            self._body = body.encode()
        else:
            self._body = json.dumps(body).encode()

    def getcode(self):
        return self._status

    def read(self):
        return self._body


def install_fake_urlopen(monkeypatch, responses):
    """Install a fake ``client_mod._urlopen`` that yields *responses* in order.

    Each item in *responses* is either:
    - an ``Exception`` instance -> the fake raises it
    - a ``(status, json_body)`` tuple -> returns a ``FakeResponse``

    Returns a mutable ``state`` dict with:
    - ``state["calls"]`` -- total number of times the fake was invoked
    - ``state["urls"]`` -- list of every URL string passed to the fake
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
            return FakeResponse(status, json_body)
        raise TypeError(f"Bad canned response: {resp!r}")

    monkeypatch.setattr(client_mod, "_urlopen", fake_urlopen)
    return state
