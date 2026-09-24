# trove-scot-mcp v0.3.0 release-candidate handoff

Date: 2026-09-24

## Authority and plan-identity reconciliation

The task-specific T00–T13 files and the repository's post-migration history were used as the executable authority. The plan-local `_chain.md` is not a provenance signal: at the start of this recovery, its opening was an unchanged crtsh copy (title, repository, card and shared constants), which conflicts with its trove-scot-specific body and the task. This handoff records that metadata defect without rewriting the historical plan prose. The actual repository/history, `T13-final-freeze-and-local-tag.md`, and the task-specific cards control this RC.

## Release identity

- Repository: `/home/kimbo/projects/trove-scot-mcp`
- Branch: `main`
- Handoff HEAD: the release-repair commit containing this handoff; its exact object ID is recorded in the kanban completion metadata
- `origin/main`: `2676d49dc76081c8a9b4a557f291b2cbf234d8b4`
- Origin delta at final verification: 0 behind, 16 ahead
- `v0.3.0`: lightweight local tag whose ref object is a commit; it points at the release-repair commit containing this handoff
- `pre-migration/20260914`: preserved lightweight tag at `692af2a48109865926d924e00455027fe9752bd2`
- External mutation: none. No push, remote mutation, gateway/live-config edit, or environment rebuild.

## Runtime contract

- Python package entry point: `<venv>/bin/python3 -m trove_scot_mcp.server`
- Protocol: MCP `2026-07-28`, stateless mode; legacy `initialize` is rejected with JSON-RPC `-32601` so Hermes auto mode falls back to `server/discover`.
- Public tool count: exactly seven, with the frozen tool descriptions and input schemas.
- Transport: synchronous stdlib `urllib.request`; no `fastmcp`, `httpx`, asyncio runtime, or other third-party runtime dependency.
- Reliability: 20-second timeout; three total attempts; transient transport errors and HTTP 502/503 retry after 1 and 2 seconds; non-retryable HTTP and malformed ArcGIS responses fail promptly; exhausted transient failures become friendly tool-result text.
- Results: discover, tools/list, and tools/call results carry `resultType=complete`, `ttlMs=0`, and `cacheScope: private`; dictionary results are compact non-ASCII-preserving JSON; legacy bare-string error/empty-result paths pass through unchanged. The ping result remains the reference-specified empty object.

## Verification evidence

1. Full suite in the live checkout:

   `/mnt/HC_Volume_105667182/kimbo/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/ -q -W error::DeprecationWarning`

   Result: 132 passed, 0 skipped, 0 warnings.

2. Truly isolated fresh clone:

   - Cloned the repository into a new `/tmp/tmp.*` directory.
   - Preflight with `PYTHONPATH=<clone>/src` proved both `trove_scot_mcp.__file__` and `trove_scot_mcp.server` resolved below that clone rather than through the production editable-install `.pth` shadow.
   - Ran the full suite from the clone with that `PYTHONPATH`: 130 passed, 0 skipped. The clone then received the same docstring and HTTPError repairs and the suite was re-run with `-W error::DeprecationWarning`: 132 passed, 0 skipped, 0 warnings.
   - Removed the clone fixture.

3. Both scratch Hermes probes through the v2 interpreter:

   - Auto config omitted `protocol`; stdout contained `✓ Connected` once, `✓ Tools discovered: 7` once, and seven named tool rows.
   - Stateless config set `protocol: stateless`; stdout contained the same exact counts and seven names.
   - Both scratch `HERMES_HOME` trees were removed. The real `~/.hermes/config.yaml` was not modified.

4. Packaging/runtime audit:

   - `pyproject.toml` is version 0.3.0 with `dependencies = []`; `__version__` and `SERVER_INFO` are also 0.3.0.
   - v2 `pip list` remains limited to `pip`, `setuptools`, and editable `trove-scot-mcp 0.3.0`; no venv mutation was performed by this recovery.
   - The permanent AST gate finds no third-party imports under `src/trove_scot_mcp`.
   - The old venv at `/mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp/` remains the rollback artifact and was not modified.

5. Repository/tag audit:

   - `origin/main` is an ancestor of local `main`; no merge commits were added in the migration chain.
   - `pre-migration/20260914` still resolves to `692af2a48109865926d924e00455027fe9752bd2`.
   - The local `v0.3.0` tag is lightweight (`git cat-file -t v0.3.0` => `commit`), resolves to the final reviewed-state handoff commit, and remains local.
   - Working tree is clean after the handoff commit and tag move.

## Repair made by this recovery

The release code and frozen contract were already implemented. This recovery made two narrowly scoped repairs. First, the initial full run exposed an invalid Python escape sequence in `server.py`'s `_like_term` docstring as a DeprecationWarning; `pytest tests/test_zero_deps.py -q -W error::DeprecationWarning` failed with `SyntaxError: invalid escape sequence '\`'`, and making the docstring raw made the gate pass. Second, the pre-commit review found that real `urllib.error.HTTPError` was falling into the generic retry tuple, so 400 responses were retried despite the task contract. New tests pin both branches: a real HTTPError 400 raises `HesError` after one attempt with no sleep, while HTTPError 503 joins the normal 1s/2s retry ladder. The production fix handles `HTTPError` before `URLError`; fake-response status handling remains intact. No tool list, protocol framing, retry constants, TTL fields, packaging, or version behavior changed.

The pre-existing historical hygiene findings remain immutable: commit `4dfc8a7` lacks the `trove-scot-mcp:` prefix, and `df471b5` uses `trove_scot_mcp:`. The task forbids rebase/reset, so they are recorded rather than rewritten.

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

- HES remains an external, keyless service; availability and record correctness cannot be guaranteed beyond the canned client/server behavior tests.
- The 1000/5000/10000 record caps have no pagination, so broad results are intentionally marked truncated.
- Coordinate conversion is an approximate Helmert transform (documented about 5 m accuracy), not OSTN15-grade.
- The migration history cannot satisfy the original one-task/one-message-prefix hygiene rule without forbidden history rewriting; the two immutable deviations are listed above.
- Integration and release publication remain owner-gated downstream decisions; this RC is local-only and must not be treated as a published release.
