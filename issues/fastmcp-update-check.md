---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Every start asks the package index for FastMCP's latest version

## Problem

`MCPServerApp.run` (`src/rcac_mcp/__init__.py`) starts the server with `mcp.run(transport=…)` and
leaves FastMCP's banner on (`fastmcp.settings.show_server_banner`, default true). FastMCP's
`log_server_banner` calls `fastmcp.utilities.version_check.check_for_newer_version()`, which, unless
`FASTMCP_CHECK_FOR_UPDATES=off`, fetches FastMCP's latest version from PyPI (a 2 s request timeout,
the answer cached for 12 hours under FastMCP's home directory). So a start, in every mode and on
every transport, can make an outbound HTTPS request nobody asked for, from wherever the server runs,
and when PyPI has a newer release the banner tells the user to run `pip install --upgrade fastmcp`,
for a dependency that `uv.lock` pins and the user does not manage.

## Why it was deferred

Outside the `agents-d-instructions` GOAL; that cycle's plan noted it and left it (its `PLAN.md`
§ 5). **Pre-existing:** present on `develop` before that cycle, in every mode.

## Outcome / vision

The server makes no network request at startup beyond what its mode needs (the SSH connection in
`ssh` mode), and prints no upgrade advice for its own pinned dependencies.

## Sketch of the acceptance criteria

- **R1** — WHEN the server starts, in any mode and on any transport, it SHALL make no request to a
  package index.
- **R2** — The server's startup output SHALL NOT advise upgrading FastMCP.

## Notes

- Related: `spec/agents-d-instructions/` (PLAN § 5); FastMCP's `check_for_updates` setting
  (`FASTMCP_CHECK_FOR_UPDATES`: `stable`, `prerelease` or `off`) and `show_server_banner`.
- Found by: `agents-d-instructions` planning, filed at P4 (2026-10-23).
