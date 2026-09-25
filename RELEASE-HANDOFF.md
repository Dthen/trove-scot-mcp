# trove-scot-mcp v0.3.0 release-candidate handoff

Date: 2026-09-24

## Authority and plan-identity reconciliation

The task-specific T00–T13 files and the repository's post-migration history were used as the executable authority. The plan-local `_chain.md` is not a provenance signal: at the start of the original recovery, its opening was an unchanged crtsh copy (title, repository, card and shared constants), which conflicts with its trove-scot-specific body and the task. This handoff records that metadata defect without rewriting the historical plan prose. The actual repository/history, `T13-final-freeze-and-local-tag.md`, and the task-specific cards control this RC.

## Release identity

- Repository: `/home/kimbo/projects/trove-scot-mcp`
- Branch: `main`
- Corrective handoff HEAD: `e467642` (`trove-scot-mcp: record string validation evidence`), whose parent `1e75c0e` contains the implementation; this final evidence commit is recorded in the kanban completion metadata and targeted by local `v0.3.0`
- `origin/main`: `2676d49dc76081c8a9b4a557f291b2cbf234d8b4`
- Origin delta at final verification: 0 behind, 22 ahead
- `v0.3.0`: lightweight local tag whose ref object is a commit; it points at the exact corrective handoff HEAD above
- `pre-migration/20260914`: preserved lightweight tag at `692af2a48109865926d924e00455027fe9752bd2`
- External mutation: none. No push, remote mutation, gateway/live-config edit, or environment rebuild.

## Runtime contract

- Python package entry point: `<venv>/bin/python3 -m trove_scot_mcp.server`
- Protocol: MCP `2026-07-28`, stateless mode; legacy `initialize` is rejected with JSON-RPC `-32601` so Hermes auto mode falls back to `server/discover`.
- Public tool count: exactly seven, with the frozen tool descriptions and input schemas.
- Transport: synchronous stdlib `urllib.request`; no `fastmcp`, `httpx`, asyncio runtime, or other third-party runtime dependency.
- Reliability: 20-second timeout; three total attempts; transient transport errors, read-stage HTTP protocol failures, and HTTP 502/503 retry after 1 and 2 seconds; non-retryable HTTP and malformed ArcGIS responses fail promptly; exhausted transient failures become friendly tool-result text.
- Input boundary: missing, malformed, or non-object tool arguments produce tool-level `isError: true` results instead of terminating stdio; the dispatch catch-all returns JSON-RPC `-32603` for unexpected handler failures.
- Framing boundary: every message with no `id` member is silent before method dispatch; an explicit `id: null` is still echoed verbatim.
- Response boundary: successful HTTP responses must decode to a JSON object; list, string, null, number, and non-JSON bodies are normalized to `HesError`.
- Coordinate boundary: NaN and infinite BNG coordinates are rejected before the iterative projection and ignored during record enrichment.
- Results: discover, tools/list, and tools/call results carry `resultType=complete`, `ttlMs=0`, and `cacheScope: private`; dictionary results are compact non-ASCII-preserving JSON; legacy bare-string error/empty-result paths pass through unchanged. The ping result remains the reference-specified empty object.

## Corrective repair lane

The independent review at failed card `t_a103e101` reproduced five correctness blockers. The corrective commits are:

- `74606f639d0fc556bf9ca5560c0769617096ed32` — `trove-scot-mcp: harden JSON-RPC tool dispatch`
  - validates and coerces arguments at the dispatch seam;
  - returns missing/malformed/non-object argument failures as tool-level errors;
  - restores the required dispatch-level `-32603` catch-all;
  - silences every id-less known method while preserving explicit `id: null`;
  - adds subprocess regressions proving malformed calls cannot kill the loop.
- `110b0d477b6ee04b5340b83ca79f04e96c1a3fe3` — `trove-scot-mcp: harden HTTP and coordinate boundaries`
  - moves response body reading into the urllib normalization seam;
  - retries read-stage `IncompleteRead` through the existing 1s/2s ladder and exhausts to `HesError`;
  - rejects non-object decoded JSON before `.get()` use;
  - rejects non-finite BNG coordinates before the unbounded convergence loop.
- `8fe1f8682e42406f4a7956e8b309bb4886291876` — `trove-scot-mcp: add release verification probe`
  - adds a network-free/local-fixture release probe for protocol silence, ping/EOF, truncated reads, response shape, non-finite coordinates, exact seven tools, and a real `HTTPError` instance.

## Verification evidence

1. Full suite in the live checkout:

   `/mnt/HC_Volume_105667182/kimbo/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/ -q -W error::DeprecationWarning`

   Result after the corrective commits: 182 passed, 0 skipped, 0 warnings (6.17 seconds).

2. Tight reviewer-blocker regressions:

   - Protocol suite: 45 passed, including wrong-typed required/optional string arguments across all seven tools, id-less discover/list/call/ping silence, and explicit null-id preservation.
   - Client suite: 52 passed, including read-stage `IncompleteRead` retry/exhaustion, list/string/null/number JSON shapes, and NaN/infinity coordinate validation/enrichment.
   - Tool/server/encoder suites: 84 passed.

3. Truly isolated fresh no-hardlink clone:

   - Cloned locally into a temporary `/tmp/trove-scot-isolated.*` directory with `git clone --no-hardlinks`.
   - Source and clone `server.py` had matching committed HEADs but different device/inode identities and link counts of one.
   - `PYTHONPATH=<clone>/src` full suite: 182 passed, 0 skipped, 0 warnings (6.20 seconds).
   - Isolated protocol suite: 45 passed.
   - The clone remote was only the source checkout; no network or push occurred. The fixture was removed after verification.

4. Local protocol/transport/error/coordinates probe:

   `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 tools/release_verification.py`

   Result:
   - exact tools: 7;
   - golden SHA-256: `c9075ff88dd67090ec3b8af1f778002d253ec162a2c989bed7d3a17d1157b140`;
   - id-less known-method guard: pass; ping: pass; EOF rc: 0;
   - real truncated loopback response: retried once then returned count 7, with recorded sleep `[1.0]`;
   - non-object loopback JSON: normalized to `HesError`;
   - non-finite coordinates: `ValueError`;
   - real `urllib.error.HTTPError` instance: status 400;
   - no `ResourceWarning` under `python -W error`; protocol child stdout/stderr pipes are closed after the child is reaped.

5. Both scratch Hermes probes through the v2 interpreter:

   - Auto config omitted `protocol`; stdout contained one `✓ Connected` and one `✓ Tools discovered: 7`; all seven named tools were listed.
   - Stateless config set `protocol: stateless`; stdout contained the same exact counts and seven names.
   - Scratch `HERMES_HOME` trees were `/tmp/trove-scot-probe-auto` and `/tmp/trove-scot-probe-stateless`; the real `~/.hermes/config.yaml` was not modified.

6. Packaging/runtime audit:

   - `pyproject.toml` is version 0.3.0 with `dependencies = []`; `__version__` and `SERVER_INFO` are also 0.3.0.
   - v2 `pip list --format=freeze` contains only `pip==24.0`, `setuptools==79.0.1`, and editable `trove-scot-mcp==0.3.0`; no venv mutation was performed by this lane.
   - The permanent AST gate in the full suite finds no third-party imports under `src/trove_scot_mcp`.
   - The old venv at `/mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp/` remains the rollback artifact and was not modified.

7. Repository/tag audit:

   - `origin/main` is an ancestor of local `main`; the final delta is 0 behind, 22 ahead, with no merge commits added in the corrective lane.
   - `pre-migration/20260914` still resolves to `692af2a48109865926d924e00455027fe9752bd2` and has object type `commit`.
   - The local `v0.3.0` tag is lightweight (`git cat-file -t v0.3.0` => `commit`) and resolves to the exact final clean HEAD.
   - Working tree is clean after the corrective handoff commit and tag reconciliation.

## Cutover and rollback

Owner-gated cutover remains:

```yaml
mcp_servers:
  trove-scot:
    command: /mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp-v2/bin/python3
    args: ["-m", "trove_scot_mcp.server"]
    protocol: stateless
```

Do not publish or push without owner approval. Until cutover, leave the live config and old venv unchanged. Rollback is the existing live config plus `/mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp/`; do not remove it during integration.

## Residual risks

- HES remains an external, keyless service; availability and record correctness cannot be guaranteed beyond the canned client/server and loopback fixture behavior tests.
- The 1000/5000/10000 record caps have no pagination, so broad results are intentionally marked truncated.
- Coordinate conversion is an approximate Helmert transform (documented about 5 m accuracy), not OSTN15-grade.
- The migration history cannot satisfy the original one-task/one-message-prefix hygiene rule without forbidden history rewriting; the historical `4dfc8a7` and `df471b5` prefix deviations remain immutable.
- Integration and release publication remain owner-gated downstream decisions; this RC is local-only and must not be treated as a published release.
