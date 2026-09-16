# trove-scot-mcp MIGRATION-NOTES

## T11: scratch probe gate (auto + stateless)

**Timestamp (UTC):** 2026-09-16T15:25:33Z
**Timestamp (local):** 2026-09-16T16:25:33+0100
**HerMES_HOME:** /tmp/probe_scratch/{auto,stateless} (deleted after probes)
**Spec gate (PASS):** stdout of BOTH runs contains `✓ Connected` AND `✓ Tools discovered: 7`

---

### AUTO probe (initialize→-32601→discover)

```
Testing 'trove-scot'...
  Transport: stdio → /mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp-v2/bin/python3
  Auth: none
  ✓ Connected (1764ms)
  ✓ Tools discovered: 7

    search_heritage                      Search Scotland's National Record of the Historic Envir...
    get_heritage_by_id                   Get full details of a specific heritage site by its Can...
    count_heritage                       Count how many heritage sites match a search, without f...
    heritage_near                        Find heritage sites near a geographic point (lat/lon in...
    search_listed_buildings              Search Scotland's listed buildings — 67K+ buildings of ...
    search_scheduled_monuments           Search Scotland's scheduled monuments — nationally impo...
    list_properties_in_care              List Historic Environment Scotland properties in care —...
```

**Verdict:** PASS (✓ Connected + ✓ Tools discovered: 7)

---

### STATELESS probe (discover-first)

```
Testing 'trove-scot'...
  Transport: stdio → /mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp-v2/bin/python3
  Auth: none
  ✓ Connected (1632ms)
  ✓ Tools discovered: 7

    search_heritage                      Search Scotland's National Record of the Historic Envir...
    get_heritage_by_id                   Get full details of a specific heritage site by its Can...
    count_heritage                       Count how many heritage sites match a search, without f...
    heritage_near                        Find heritage sites near a geographic point (lat/lon in...
    search_listed_buildings              Search Scotland's listed buildings — 67K+ buildings of ...
    search_scheduled_monuments           Search Scotland's scheduled monuments — nationally impo...
    list_properties_in_care              List Historic Environment Scotland properties in care —...
```

**Verdict:** PASS (✓ Connected + ✓ Tools discovered: 7)

---

### Config anchor (F11)

| | digest |
|---|---|
| T00 baseline anchor (f11fb99) | `e167c107305d9a77cd3244845b7418785e2922832423dffd0ce3dd0a17f2a24b` |
| Current `~/.hermes/config.yaml` | `1875a864b75eefd197ab99d39d330c3215493314ddbc7f958cee283e3c5f6cd9` |

**Mismatch:** YES — digests differ.

**Disposition (per spec card-amendment 2026-09-15):**
- Config mtime: `2026-09-16 14:50:47 +0100`
- Nearest trove-scot task window (T05 client port): commit `ea8c99e` at `14:52:58` — mtime is ~2 min BEFORE the earliest chain task; T11's own window starts ~16:19.
- mtime falls OUTSIDE any trove-scot task window → **OPERATOR-DRIFT** — record and proceed.
- No worker tamper suspected.

---

### Ledger state

- Repo: clean (`nothing to commit, working tree clean`)
- Branch: main, ahead of origin/main by 12 commits (T00–T10)
- Full suite green: 130 passed, 17 era tests spawn v2 successfully (per T10 handoff)
- v2 venv: verified at `/mnt/HC_Volume_105667182/kimbo/mcp-venvs/trove-scot-mcp-v2/`

---

### Deviations / notes

- Both probes used `cwd:` key in scratch config — accepted by the CLI (no error), both connected and discovered 7 tools.
- Exit code 0 on both (meaningless per recipe 1).
- No `$PYO` path leaked into probe configs.
- Real config untouched (no edits to `~/.hermes/config.yaml`).
- Scratch dirs deleted after probes.
