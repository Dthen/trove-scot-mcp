#!/usr/bin/env python3
"""Era wire-contract suite for trove-scot-mcp — committed RED against legacy (TDD).

17 tests pinning the stateless 2026-07-28 era wire contract (REFERENCE §1–§7).
Designed to FAIL against the current legacy fastmcp server; made GREEN by T06/T07.
"""

import json
import os
import select
import subprocess
import sys

import pytest

# --- Module constants (REFERENCE §7 pinned-v2-production-spawn pattern) ---
PROD_PY = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp-v2/bin/python3"
SERVER_ARGS = ["-m", "trove_scot_mcp.server"]
REPO = "/home/kimbo/projects/trove-scot-mcp"

# Golden file path resolved from __file__ (loaded unconditionally — no skipif)
GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "..", "golden", "trove-scot.tools.json")


# --- Helpers (F9: mandatory read-timeout) ---


def read_line_with_timeout(f, sec=5.0):
    """Read a line from file object f with a timeout (F9).

    Uses select.select to avoid wedging on a silent server.
    Returns the line string, or None on timeout.
    """
    rlist, _, _ = select.select([f], [], [], sec)
    if not rlist:
        return None
    return f.readline()


def spawn():
    """Spawn the server as a subprocess with text-mode pipes."""
    return subprocess.Popen(
        [PROD_PY] + SERVER_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=REPO,
        text=True,
    )


def _close_pipe(pipe):
    """Close one subprocess pipe without masking the test's outcome."""
    if pipe is not None:
        try:
            pipe.close()
        except OSError:
            pass


def cleanup_process(p):
    """Close stdin, reap the child, and close stdout/stderr on every path."""
    try:
        _close_pipe(p.stdin)
        if p.poll() is None:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
    finally:
        try:
            _close_pipe(p.stdout)
        finally:
            _close_pipe(p.stderr)


def send(p, obj):
    """Send a JSON-RPC request to the server."""
    line = json.dumps(obj) + "\n"
    p.stdin.write(line)
    p.stdin.flush()


def rpc(p, obj, timeout=5.0):
    """Send a JSON-RPC request and read the response (routes through read_line_with_timeout).

    Returns the parsed JSON response dict, or None on timeout.
    """
    send(p, obj)
    resp_line = read_line_with_timeout(p.stdout, timeout)
    if resp_line is None:
        return None
    return json.loads(resp_line)


def load_golden():
    """Load the golden tools/list fixture (unconditional — no skipif)."""
    with open(GOLDEN_PATH) as f:
        return json.load(f)


# --- 12 era tests ---


def test_discover_era_shape():
    """§2: server/discover → supportedVersions == ["2026-07-28"], capabilities.tools present, triple."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        assert resp is not None, "§2: server/discover timed out (legacy has no discover)"
        assert resp["result"]["supportedVersions"] == ["2026-07-28"], "§2: supportedVersions"
        assert "tools" in resp["result"]["capabilities"], "§2: capabilities.tools"
        assert resp["result"]["resultType"] == "complete", "§2: resultType"
        assert resp["result"]["ttlMs"] == 0, "§2: ttlMs"
        assert resp["result"]["cacheScope"] == "private", "§2: cacheScope"
    finally:
        cleanup_process(p)


def test_discover_paramless():
    """§2: server/discover with no params key → identical shape."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 2, "method": "server/discover"})
        assert resp is not None, "§2: paramless discover timed out"
        assert resp["result"]["supportedVersions"] == ["2026-07-28"], "§2: supportedVersions"
        assert "tools" in resp["result"]["capabilities"], "§2: capabilities.tools"
        assert resp["result"]["resultType"] == "complete", "§2: resultType"
        assert resp["result"]["ttlMs"] == 0, "§2: ttlMs"
        assert resp["result"]["cacheScope"] == "private", "§2: cacheScope"
    finally:
        cleanup_process(p)


def test_initialize_returns_32601_and_never_hangs_or_closes():
    """§3: legacy initialize → error −32601 echoed id; then server/discover on same pipes answers era shape."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 3, "method": "initialize"})
        assert resp is not None, "§3: initialize timed out (must not hang)"
        assert resp["error"]["code"] == -32601, "§3: initialize must return -32601"
        assert resp["id"] == 3, "§3: id echoed"
        # Then discover on the SAME pipes (§3: never close stdout)
        resp2 = rpc(p, {"jsonrpc": "2.0", "id": 4, "method": "server/discover"})
        assert resp2 is not None, "§3: discover after initialize timed out"
        assert resp2["result"]["supportedVersions"] == ["2026-07-28"], "§3: supportedVersions"
    finally:
        cleanup_process(p)


def test_notifications_initialized_swallowed():
    """§6: notifications/initialized (no id) → no response; proven by following tools/list whose reply carries THAT id."""
    p = spawn()
    try:
        # Send notification (no id) — must be swallowed
        send(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        # Send tools/list with id=42 — reply must carry id=42 (not a phantom notification response)
        resp = rpc(p, {"jsonrpc": "2.0", "id": 42, "method": "tools/list"})
        assert resp is not None, "§6: tools/list after notification timed out"
        assert resp["id"] == 42, "§6: reply must carry tools/list id, not a notification response"
        assert "result" in resp, "§6: tools/list must return a result"
    finally:
        cleanup_process(p)


def test_tools_list_triple_and_no_output_schema():
    """§4: tools/list → triple fields + every tool has "outputSchema" not in tool."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        assert resp is not None, "§4: tools/list timed out"
        assert resp["result"]["resultType"] == "complete", "§4: resultType"
        assert resp["result"]["ttlMs"] == 0, "§4: ttlMs"
        assert resp["result"]["cacheScope"] == "private", "§4: cacheScope"
        for tool in resp["result"]["tools"]:
            assert "outputSchema" not in tool, f"§4: tool {tool['name']} must not have outputSchema"
    finally:
        cleanup_process(p)


def test_tools_list_golden_byte_identity():
    """§4/D4: per tool, name+description+inputSchema byte-identical to golden after stripping outputSchema."""
    golden = load_golden()
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
        assert resp is not None, "§4: tools/list timed out"
        server_tools = resp["result"]["tools"]
        assert len(server_tools) == len(golden), "§4: tool count mismatch"
        for g, s in zip(golden, server_tools):
            # Strip outputSchema from golden projection (§4 trap)
            g_proj = {k: g[k] for k in ("name", "description", "inputSchema")}
            s_proj = {k: s[k] for k in ("name", "description", "inputSchema")}
            assert json.dumps(g_proj, sort_keys=True) == json.dumps(s_proj, sort_keys=True), (
                f"§4: tool {g['name']} not byte-identical to golden"
            )
    finally:
        cleanup_process(p)


def test_tools_call_shortcut_error_is_text():
    """§5: search_heritage {"term":""} → text == "Error: please provide a search term" + triple."""
    p = spawn()
    try:
        resp = rpc(p, {
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "search_heritage", "arguments": {"term": ""}}
        })
        assert resp is not None, "§5: tools/call timed out"
        text = resp["result"]["content"][0]["text"]
        assert text == "Error: please provide a search term", "§5: error text"
        assert resp["result"]["resultType"] == "complete", "§5: resultType"
        assert resp["result"]["ttlMs"] == 0, "§5: ttlMs"
        assert resp["result"]["cacheScope"] == "private", "§5: cacheScope"
        # R3: isError NOT asserted here (legacy fastmcp did not set isError for in-band error strings)
        assert resp["result"].get("isError", False) is False, "§5: isError must be absent or false"
    finally:
        cleanup_process(p)


def test_tools_call_radius_error_is_text():
    """§5: heritage_near {"lat":55.95,"lon":-3.2,"radius_km":0} → text == "Error: radius_km must be a positive number" + triple."""
    p = spawn()
    try:
        resp = rpc(p, {
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "heritage_near", "arguments": {"lat": 55.95, "lon": -3.2, "radius_km": 0}}
        })
        assert resp is not None, "§5: tools/call timed out"
        text = resp["result"]["content"][0]["text"]
        assert text == "Error: radius_km must be a positive number", "§5: error text"
        assert resp["result"]["resultType"] == "complete", "§5: resultType"
        assert resp["result"]["ttlMs"] == 0, "§5: ttlMs"
        assert resp["result"]["cacheScope"] == "private", "§5: cacheScope"
    finally:
        cleanup_process(p)


def test_tools_call_unknown_tool():
    """§5: {"name":"bogus"} → text == '{"error":"Unknown tool: bogus"}' + isError: true."""
    p = spawn()
    try:
        resp = rpc(p, {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "bogus"}
        })
        assert resp is not None, "§5: tools/call timed out"
        text = resp["result"]["content"][0]["text"]
        assert text == '{"error":"Unknown tool: bogus"}', "§5: unknown tool dict shape"
        assert resp["result"]["isError"] is True, "§5: isError must be true for unknown tool"
        assert resp["result"]["resultType"] == "complete", "§5: resultType"
        assert resp["result"]["ttlMs"] == 0, "§5: ttlMs"
        assert resp["result"]["cacheScope"] == "private", "§5: cacheScope"
    finally:
        cleanup_process(p)


def test_tools_call_missing_params_is_32602():
    """§5: tools/call with no params → −32602, and with params:{} (no string name) → −32602."""
    p = spawn()
    try:
        # No params key at all
        resp1 = rpc(p, {"jsonrpc": "2.0", "id": 10, "method": "tools/call"})
        assert resp1 is not None, "§5: tools/call (no params) timed out"
        assert resp1["error"]["code"] == -32602, "§5: no params → -32602"
        # params:{} (no string name)
        resp2 = rpc(p, {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {}})
        assert resp2 is not None, "§5: tools/call (empty params) timed out"
        assert resp2["error"]["code"] == -32602, "§5: empty params → -32602"
    finally:
        cleanup_process(p)


@pytest.mark.parametrize(
    "tool_name,arguments,message_fragment",
    [
        ("search_heritage", {}, "term"),
        ("search_heritage", {"term": 123}, "term"),
        ("search_heritage", {"term": "castle", "sitetype": 123}, "sitetype"),
        ("search_heritage", {"term": "castle", "council": []}, "council"),
        ("search_heritage", {"term": "castle", "broadclass": 1}, "broadclass"),
        ("count_heritage", {"term": 123}, "term"),
        ("count_heritage", {"sitetype": 123}, "sitetype"),
        ("count_heritage", {"council": []}, "council"),
        ("heritage_near", {"lat": "not-a-number", "lon": 0}, "lat"),
        ("heritage_near", {"lat": 55.95, "lon": 0, "limit": "bad"}, "limit"),
        ("heritage_near", {"lat": 55.95, "lon": 0, "term": 123}, "term"),
        ("get_heritage_by_id", {"canmore_id": "bad"}, "canmore_id"),
        ("heritage_near", {"lon": 0}, "lat"),
        ("heritage_near", {"lat": 55.95}, "lon"),
        ("get_heritage_by_id", {}, "canmore_id"),
        ("search_listed_buildings", {"term": 123}, "term"),
        ("search_listed_buildings", {"category": 1}, "category"),
        ("search_listed_buildings", {"local_authority": []}, "local_authority"),
        ("search_scheduled_monuments", {"term": 123}, "term"),
        ("search_scheduled_monuments", {"local_authority": []}, "local_authority"),
        ("list_properties_in_care", {"local_authority": 123}, "local_authority"),
        ("list_properties_in_care", {"term": []}, "term"),
        ("heritage_near", ["not", "an", "object"], "object"),
    ],
)
def test_malformed_tool_arguments_return_validation_error_and_keep_server_alive(
    tool_name, arguments, message_fragment
):
    """Schema/coercion failures are tool errors, never process-killing exceptions."""
    p = spawn()
    try:
        resp = rpc(
            p,
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        assert resp is not None, "malformed tools/call timed out"
        assert resp["id"] == 101
        assert "error" not in resp
        assert resp["result"]["isError"] is True
        text = resp["result"]["content"][0]["text"]
        assert message_fragment in text
        assert resp["result"]["resultType"] == "complete"
        assert resp["result"]["ttlMs"] == 0
        assert resp["result"]["cacheScope"] == "private"

        alive = rpc(p, {"jsonrpc": "2.0", "id": 102, "method": "ping"})
        assert alive is not None
        assert alive["id"] == 102
        assert alive["result"] == {}
        assert p.poll() is None
    finally:
        cleanup_process(p)


def test_ping_answers_empty():
    """§6: ping id → result == {}."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 12, "method": "ping"})
        assert resp is not None, "§6: ping timed out"
        assert resp["result"] == {}, "§6: ping result must be empty dict"
    finally:
        cleanup_process(p)


def test_unknown_method_32601():
    """§3: resources/list id → −32601 (catch-all)."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 13, "method": "resources/list"})
        assert resp is not None, "§3: resources/list timed out"
        assert resp["error"]["code"] == -32601, "§3: unknown method → -32601"
        assert resp["id"] == 13, "§3: id echoed"
    finally:
        cleanup_process(p)


# --- 5 regression tests ---


def test_id_less_unknown_is_silent():
    """§1: {"jsonrpc":"2.0","method":"foo/bar"} (no id) then discover → first reply's id == discover's id."""
    p = spawn()
    try:
        # Send id-less unknown method — must NOT produce a response
        send(p, {"jsonrpc": "2.0", "method": "foo/bar"})
        # Send discover with id=14 — reply must carry id=14
        resp = rpc(p, {"jsonrpc": "2.0", "id": 14, "method": "server/discover"})
        assert resp is not None, "§1: discover after id-less timed out"
        assert resp["id"] == 14, "§1: reply must carry discover id, not a phantom response"
    finally:
        cleanup_process(p)


@pytest.mark.parametrize(
    "method", ["server/discover", "tools/list", "tools/call", "ping"]
)
def test_id_less_known_methods_are_silent(method):
    """§1: every no-id known method is a notification, including tools/call."""
    params = (
        {"name": "search_heritage", "arguments": {"term": ""}}
        if method == "tools/call"
        else None
    )
    request = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        request["params"] = params

    p = spawn()
    try:
        send(p, request)
        resp = rpc(p, {"jsonrpc": "2.0", "id": 141, "method": "ping"})
        assert resp is not None
        assert resp["id"] == 141
        assert p.poll() is None
    finally:
        assert p.stdin is not None
        cleanup_process(p)


def test_explicit_null_id_known_method_preserves_response():
    """§1: an explicit JSON-RPC id member, including null, is echoed verbatim."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": None, "method": "ping"})
        assert resp is not None
        assert resp["id"] is None
        assert resp["result"] == {}
    finally:
        assert p.stdin is not None
        cleanup_process(p)


def test_non_dict_json_lines_do_not_kill_the_server():
    """§1: 5\\n, null\\n, [1,2]\n, "str"\\n then ping → replies correlate; server alive."""
    p = spawn()
    try:
        # Send non-dict JSON lines — must be skipped, never kill the server
        for line in ["5\n", "null\n", "[1,2]\n", '"str"\n']:
            p.stdin.write(line)
        p.stdin.flush()
        # Ping must still work
        resp = rpc(p, {"jsonrpc": "2.0", "id": 15, "method": "ping"})
        assert resp is not None, "§1: ping after non-dict lines timed out"
        assert resp["id"] == 15, "§1: ping reply must carry id=15"
        assert resp["result"] == {}, "§1: ping result must be empty dict"
        assert p.poll() is None, "§1: server must still be alive"
    finally:
        cleanup_process(p)


def test_non_string_method_routes_as_unknown():
    """§1: {"id":9,"method":42} → −32601 not crash."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 9, "method": 42})
        assert resp is not None, "§1: non-string method timed out"
        assert resp["error"]["code"] == -32601, "§1: non-string method → -32601"
        assert resp["id"] == 9, "§1: id echoed"
        assert p.poll() is None, "§1: server must still be alive"
    finally:
        cleanup_process(p)


def test_garbage_lines_do_not_kill_the_server():
    """§7: not json\\n, {truncated\\n then tools/list OK."""
    p = spawn()
    try:
        # Send garbage lines — must be skipped
        p.stdin.write("not json\n")
        p.stdin.write("{truncated\n")
        p.stdin.flush()
        # tools/list must still work
        resp = rpc(p, {"jsonrpc": "2.0", "id": 16, "method": "tools/list"})
        assert resp is not None, "§7: tools/list after garbage timed out"
        assert resp["id"] == 16, "§7: tools/list reply must carry id=16"
        assert "result" in resp, "§7: tools/list must return a result"
        assert p.poll() is None, "§7: server must still be alive"
    finally:
        cleanup_process(p)


def test_binary_garbage_line_does_not_kill_the_server():
    """§7: bytes-mode Popen, stderr=DEVNULL, G→D→G→L stream, id correlation, liveness, rc==0 on EOF.

    REFERENCE §7 skeleton (verbatim):
    p = subprocess.Popen([PROD_PY, SERVER], stdin=PIPE, stdout=PIPE, stderr=subprocess.DEVNULL)
    try:
        p.stdin.write(b"\\xff\\xfe\\x00garbage\\n")                      # G — pre-stream invalid UTF-8
        p.stdin.write(discover_line_json.encode() + b"\\n"); p.stdin.flush()   # D
        resp = json.loads(p.stdout.readline())
        assert resp["id"] == 1 and resp["result"]["supportedVersions"] == ["2026-07-28"]
        p.stdin.write(b"\\x00\\xff\\n")                                 # G — mid-stream garbage
        p.stdin.write(tools_list_line_json.encode() + b"\\n"); p.stdin.flush()  # L
        resp2 = json.loads(p.stdout.readline())                      # id==2 proves no phantom response to G
        assert resp2["id"] == 2 and "result" in resp2
        assert p.poll() is None
        p.stdin.close(); assert p.wait(timeout=5) == 0               # clean EOF exit
    finally:
        if p.poll() is None: p.kill(); p.wait()
    """
    discover_line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}) + "\n"
    tools_list_line = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
    p = subprocess.Popen(
        [PROD_PY] + SERVER_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=REPO,
    )
    try:
        p.stdin.write(b"\xff\xfe\x00garbage\n")  # G — pre-stream invalid UTF-8
        p.stdin.write(discover_line.encode() + b"\n")
        p.stdin.flush()  # D
        resp = json.loads(p.stdout.readline())
        assert resp["id"] == 1 and resp["result"]["supportedVersions"] == ["2026-07-28"]
        p.stdin.write(b"\x00\xff\n")  # G — mid-stream garbage
        p.stdin.write(tools_list_line.encode() + b"\n")
        p.stdin.flush()  # L
        resp2 = json.loads(p.stdout.readline())  # id==2 proves no phantom response to G
        assert resp2["id"] == 2 and "result" in resp2
        assert p.poll() is None
        p.stdin.close()
        assert p.wait(timeout=5) == 0  # clean EOF exit
    finally:
        cleanup_process(p)
