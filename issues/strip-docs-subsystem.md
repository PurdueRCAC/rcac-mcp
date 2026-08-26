---
status: unshaped
kind: refactor
appetite: big
lane: public
---

# Remove the documentation search subsystem

## Problem

Agentic documentation retrieval was extracted into a separate `rcac-docs-mcp` service, now hosted at
`docs.rcac.purdue.edu/mcp`, with an AskRCAC tool arriving on `docs.rcac.purdue.edu`. Knowledge work
evolves there; it is no longer this repository's responsibility. What is still here:

- `src/rcac_mcp/docs/` — `database.py` (310 lines), `indexer.py` (619), `__init__.py` (16), and
  `schema.sql`. An FTS5 index, a markdown walker, pymdownx snippet resolution, and a Jinja2 macro
  renderer that loads `main.py` out of the RCAC-Docs repo.
- `src/rcac_mcp/tools/docs.py` (179 lines) — the `doc_search` and `doc_load` tools, plus module-level
  resolution of `RCAC_DOCS_DB` → `~/.config/rcac-mcp/docs.db`.
- Three CLI flags on `MCPServerApp` — `--index-docs`, `--docs-path`, `--docs-output` — and the
  `_run_index_docs()` branch that short-circuits `run()` before the server ever starts.
- `SERVER_INSTRUCTIONS` (`server.py:68`) opens with an `IMPORTANT — Documentation Search` block
  telling every connecting model to call `doc_search` before advising on anything RCAC-specific, and
  lists both tools again under `Documentation Search`.
- `INSTRUCTIONS.md:13` carries the same instruction as a system-prompt paragraph.
- `README.md` § *Documentation Search Index* documents the build step and the tool pair.
- `tests/fixtures/RCAC-Docs` — a git submodule (`.gitmodules`), cloned only for these tests.
- `pyyaml` and `jinja2` in `[project.dependencies]`, added for the indexer.

Two of these are more than dead weight. The `SERVER_INSTRUCTIONS` block actively directs agents to a
capability this server should not own, and after removal would name tools that do not exist — the
server inducing its own hallucination. And `pyyaml` is *also* what the factory's FSM scripts import
(`.agents/factory/bin/_fsm.py`), so a naive dependency cleanup breaks the harness.

## Why it was deferred

Not deferred — this is the first cycle. Recorded as a seed so the removal is shaped against a
contract rather than performed as an undirected sweep, because it is larger than it looks: it spans
the CLI, the agent-facing instructions, the packaging metadata, a submodule, and the entire test
suite.

**Pre-existing:** yes. All of it is on `main` as of `2def4a4`.

## Outcome / vision

The server starts, registers its tools, and mentions documentation nowhere. A user who wants RCAC
documentation points their client at `docs.rcac.purdue.edu/mcp` — which the README says plainly,
because a silent removal reads as a regression. The factory's PyYAML dependency survives the
dependency cleanup because it was made explicit before the runtime one was dropped.

## Sketch of the acceptance criteria

- **R1** — The `rcac-mcp` package SHALL contain no `docs` subpackage and no `doc_search`/`doc_load`
  tools; `TOOL_REGISTRY` SHALL NOT contain a tool whose name begins `doc_`.
- **R2** — WHEN `rcac-mcp --help` is run, the output SHALL NOT mention `--index-docs`, `--docs-path`,
  or `--docs-output`, and passing any of them SHALL exit with `exit_status.bad_argument`.
- **R3** — `SERVER_INSTRUCTIONS` SHALL name no tool absent from `TOOL_REGISTRY`, verified by a test
  that cross-checks the two.
- **R4** — `README.md` SHALL point a reader wanting RCAC documentation at the `rcac-docs-mcp` service,
  rather than silently omitting the capability.
- **R5** — WHILE the docs runtime dependencies are removed, `uv run python
  .agents/factory/bin/next_phase.py` SHALL still succeed — PyYAML remains resolvable in the dev
  environment.
- **R6** — The `tests/fixtures/RCAC-Docs` submodule and its `.gitmodules` entry SHALL be removed, and
  a fresh `git clone` SHALL require no `git submodule update`.

## Notes

- Removing this takes the **entire** test suite with it (81 of 81 tests). That is not in scope to
  replace here — see [`restore-test-coverage.md`](restore-test-coverage.md), which this seed's
  promotion should name as an explicit non-goal so `/cm-roadmap` can verify the obligation landed.
- `INSTRUCTIONS.md` is a standalone system prompt, not generated from `SERVER_INSTRUCTIONS`; both
  need editing.
- Related: [`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md) is cheaper after this lands.
- Found by: the harness port, 2026-08-26 (out of cycle).
