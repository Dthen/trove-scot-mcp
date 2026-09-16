# Changelog
All notable changes to this project are documented here.
Format: https://keepachangelog.com · Versioning: semver

## [0.3.0] - 2026-09-16
### Changed
- Migrated to the MCP 2026-07-28 stateless era: the server is now pure Python stdlib —
  no fastmcp, no httpx, no runtime dependencies at all.
- `server/discover` is the only entry point; the legacy `initialize` handshake is answered
  with JSON-RPC -32601 so auto-negotiating clients (Hermes) fall back to the era path.
- Tool results no longer carry `outputSchema`/`structuredContent` (era guidance for
  text-content servers).
### Added
- README "Protocol" section documenting the era requirement for clients.
## [0.2.0] - 2026-08-19
### Fixed
- Migrate to the standalone `fastmcp` package (mcp SDK 2.x dropped `mcp.server.fastmcp`).
## [0.1.0] - 2026-07-29
### Added
- Initial release: 7 tools over the Historic Environment Scotland ArcGIS API (Canmore
  search/count/get-by-id/near-point + listed buildings, scheduled monuments, properties in
  care), BNG→WGS84 conversion, retry/backoff client.
