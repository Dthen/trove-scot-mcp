#!/usr/bin/env python3
"""Capture tools/list from the CURRENT legacy server as the frozen golden (recipe 3).

Reads JSON-RPC frames on stdout: initialize (request) -> tools/list (request).
Writes the bare tools array to golden/trove-scot.tools.json.
Paths derived from __file__, so script works regardless of cwd.
"""
import json, os, subprocess, sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prod_py_old() -> str:
    """Resolve the PRE-FLIP (legacy, non-v2) interpreter this capture tool targets.

    Deliberately a separate seam from the v2 production interpreter: this tool
    captures the frozen golden from the legacy server, so it must never be
    silently repointed at the migrated v2 venv.
    """
    env = os.environ.get("PROD_PY_TROVE_SCOT_OLD")
    if env:
        return env
    local = os.path.join(REPO, ".prod_py.old")  # gitignored, untracked, machine-local
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as fh:
            return fh.read().strip()
    raise RuntimeError(
        "Legacy (pre-flip) interpreter not configured. Set $PROD_PY_TROVE_SCOT_OLD, or "
        f"write the path to {local} (gitignored). This tool captures the frozen golden from "
        "the legacy server, so it deliberately does not fall back to the v2 production "
        "interpreter or to sys.executable."
    )


PYO = _prod_py_old()

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
