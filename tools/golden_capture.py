#!/usr/bin/env python3
"""Capture tools/list from the CURRENT legacy server as the frozen golden (recipe 3).

Reads JSON-RPC frames on stdout: initialize (request) -> tools/list (request).
Writes the bare tools array to golden/trove-scot.tools.json.
Paths derived from __file__, so script works regardless of cwd.
"""
import json, os, subprocess, sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYO = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp/bin/python3"
def rpc(p, obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush(); return p.stdout.readline()
def main():
    p = subprocess.Popen([PYO, "-m", "trove_scot_mcp.server"], cwd=REPO,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        rpc(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "golden", "version": "0"}}})
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        p.stdin.flush()
        resp = json.loads(rpc(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
        tools = resp["result"]["tools"]
        out = os.path.join(REPO, "golden", "trove-scot.tools.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(tools, f, indent=2, ensure_ascii=False); f.write("\n")
        print(f"captured {len(tools)} tools -> {out}")
    finally:
        p.stdin.close()
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill(); p.wait()   # record which branch fired in stdout note
if __name__ == "__main__":
    main()
