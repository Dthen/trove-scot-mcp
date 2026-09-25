#!/usr/bin/env python3
"""Release verification probe for trove-scot-mcp.

Runs entirely against the current checkout and a local loopback HTTP fixture.
It does not mutate environments, configuration, remotes, or the live service.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import select
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

REPO = Path(__file__).resolve().parents[1]
PROD_PY = Path(
    "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/"
    "trove-scot-mcp-v2/bin/python3"
)
sys.path.insert(0, str(REPO / "src"))

from trove_scot_mcp.client import (  # noqa: E402
    BASE_URL,
    HesClient,
    HesError,
    bng_to_wgs84,
)
from trove_scot_mcp.server import TOOLS  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request_lines(process: subprocess.Popen[bytes]) -> None:
    assert process.stdin is not None
    for request in (
        {"jsonrpc": "2.0", "method": "server/discover"},
        {"jsonrpc": "2.0", "method": "tools/list"},
        {"jsonrpc": "2.0", "method": "ping"},
        {"jsonrpc": "2.0", "id": 40, "method": "ping"},
    ):
        process.stdin.write(json.dumps(request).encode() + b"\n")
    process.stdin.flush()


def _close_pipe(pipe) -> None:
    """Close one subprocess pipe without masking the probe's outcome."""
    if pipe is not None:
        try:
            pipe.close()
        except OSError:
            pass


def cleanup_process(process: subprocess.Popen[bytes]) -> None:
    """Close stdin, reap the child, and close stdout/stderr on every path."""
    try:
        _close_pipe(process.stdin)
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        try:
            _close_pipe(process.stdout)
        finally:
            _close_pipe(process.stderr)


def read_line(process: subprocess.Popen[bytes], timeout: float = 5.0) -> dict[str, Any]:
    assert process.stdout is not None
    readable, _, _ = select.select([process.stdout], [], [], timeout)
    require(bool(readable), "server did not answer before timeout")
    line = process.stdout.readline()
    require(bool(line), "server closed stdout before answering")
    return json.loads(line)


def protocol_probe() -> dict[str, Any]:
    process = subprocess.Popen(
        [str(PROD_PY), "-m", "trove_scot_mcp.server"],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        request_lines(process)
        reply = read_line(process)
        require(reply.get("id") == 40, "known-method notification was not silent")
        require(reply.get("result") == {}, "ping result changed")
        require(process.poll() is None, "server died during protocol probe")
    finally:
        assert process.stdin is not None
        process.stdin.close()
        try:
            require(process.wait(timeout=5) == 0, "server did not exit cleanly on EOF")
        finally:
            cleanup_process(process)
    return {"notification_guard": "pass", "ping": "pass", "eof_rc": 0}


class TruncatedHandler(BaseHTTPRequestHandler):
    attempts = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).attempts += 1
        if type(self).attempts == 1:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"count":')
            self.wfile.flush()
            return
        body = b'{"count":7}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ShapeHandler(BaseHTTPRequestHandler):
    payload = b"[]"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_once(handler_type: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def transport_probe() -> dict[str, Any]:
    original_sleeps: list[float] = []
    import trove_scot_mcp.client as client_module

    original_sleep = client_module._sleep
    client_module._sleep = original_sleeps.append
    try:
        server, base_url = serve_once(TruncatedHandler)
        try:
            require(HesClient(base_url=base_url, timeout=1).count("layer", "1=1") == 7,
                    "truncated response did not retry successfully")
            require(TruncatedHandler.attempts == 2, "truncated response attempt count changed")
        finally:
            server.shutdown()
            server.server_close()

        ShapeHandler.payload = b"[]"
        server, base_url = serve_once(ShapeHandler)
        try:
            client = HesClient(base_url=base_url, timeout=1)
            for operation in (client.count, client.fetch_features):
                try:
                    operation("layer", "1=1")
                except HesError as exc:
                    require("JSON object" in str(exc), "shape error was not normalized")
                else:
                    raise AssertionError("non-object JSON was accepted")
        finally:
            server.shutdown()
            server.server_close()
    finally:
        client_module._sleep = original_sleep
    return {
        "truncated_read": "retried_then_success",
        "truncated_attempts": TruncatedHandler.attempts,
        "non_object_json": "HesError",
        "sleeps": original_sleeps,
    }


def coordinate_probe() -> dict[str, Any]:
    for coordinate in (math.nan, math.inf, -math.inf):
        try:
            bng_to_wgs84(coordinate, 673497)
        except ValueError as exc:
            require("finite" in str(exc), "coordinate error lacked finite-coordinate detail")
        else:
            raise AssertionError("non-finite coordinate was accepted")
    return {"non_finite_coordinates": "ValueError"}


def error_probe() -> int:
    error = HTTPError(BASE_URL, 400, "Bad Request", {}, None)  # type: ignore[arg-type]
    require(error.code == 400, "real HTTPError construction failed")
    return error.code


def main() -> int:
    require(PROD_PY.is_file(), f"missing pinned v2 interpreter: {PROD_PY}")
    require(len(TOOLS) == 7, "public tool count is not exactly seven")
    result = {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "tools": len(TOOLS),
        "golden_sha256": hashlib.sha256(
            (REPO / "golden" / "trove-scot.tools.json").read_bytes()
        ).hexdigest(),
        "protocol": protocol_probe(),
        "transport": transport_probe(),
        "coordinates": coordinate_probe(),
        "http_error_instance": {"code": error_probe(), "class": "HTTPError"},
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
