---
status: unshaped
kind: refactor
appetite: big
lane: public
---

# Rename rcac-mcp to cluster-mcp

## Problem

The project is generalizing from *the Purdue RCAC MCP server* to *an MCP server for HPC clusters*
(`AGENTS.md` § *Direction*). The name is load-bearing in more places than a rename usually is, and
they do not all want the same treatment:

**Mechanical, but user-visible.** `[project] name = "rcac-mcp"`, the `rcac-mcp` console script,
`packages = ["src/rcac_mcp"]`, the `rcac_mcp` package directory, `importlib.metadata.version('rcac-mcp')`
in `__init__.py:29`, and `__website__`. Renaming the distribution changes the install command in every
README and every user's MCP client config.

**Protocol surface.** The resource URIs `rcac://context` and `rcac://storage` (`resources.py:130`,
`:173`) are identifiers a client may have persisted. The FastMCP server `name='RCAC'`.

**Environment variables** — the sharp edge. `RCAC_SSH_HOST`, `RCAC_USER_MAP`, `RCAC_DOCS_DB` (going
away with the docs strip). `RCAC_SSH_HOST` in particular appears in every user's client config, and
`.agents/factory/bin/sandbox.sh` unsets it **by name** to make the default stdio→`ssh` path fail
closed. Rename it without updating the sandbox and every factory verify command silently loses its
most important guarantee.

**Judgement calls, not renames.** `tools/rcac.py` genuinely *is* Purdue-specific — `myquota`,
`jobinfo`, `slist`, `sfeatures`, `findscratch` — and should keep a site-flavoured identity rather than
be laundered into a generic name. `SERVER_INSTRUCTIONS` (`server.py:64`) is today a Purdue document:
it hardcodes the 25GB home quota, `/depot/<group>`, `$CLUSTER_SCRATCH`, and `findscratch`. It needs
splitting into a generic core and a site overlay, which is really the beginning of
[`site-extras.md`](site-extras.md), not a rename.

## Why it was deferred

Not deferred — sequenced. It comes after [`strip-docs-subsystem.md`](strip-docs-subsystem.md) so
there is less to rename, and before [`capability-extras.md`](capability-extras.md) because the extras
are named `cluster-mcp[slurm]` and doing it the other way means renaming them twice.

**Pre-existing:** n/a — this is new work, not a defect.

## Outcome / vision

`uvx --from git+https://github.com/purduercac/cluster-mcp cluster-mcp --ssh-host …` works, an
existing user's `RCAC_SSH_HOST` config keeps working through a deprecation window with a warning, and
nothing in the generic core reads as Purdue-specific. The site-specific parts stay clearly labelled
as site-specific rather than being renamed into false generality.

## Sketch of the acceptance criteria

- **R1** — The distribution SHALL be named `cluster-mcp`, the package `cluster_mcp`, and the console
  script `cluster-mcp`; `__version__` SHALL still resolve from installed metadata with no hardcoded
  version.
- **R2** — WHERE a legacy `RCAC_*` environment variable is set and its `CLUSTER_*` counterpart is not,
  the server SHALL honour the legacy value and emit a deprecation warning naming the replacement.
- **R3** — `.agents/factory/bin/sandbox.sh` SHALL unset both the legacy and the new SSH-host variable,
  and a bare `cluster-mcp` inside the sandbox SHALL still fail closed with a nonzero exit.
- **R4** — Resource URIs SHALL move to the new scheme; the change SHALL be stated in `README.md` as a
  breaking change for any client that persisted a URI.
- **R5** — The generic `SERVER_INSTRUCTIONS` core SHALL contain no Purdue-specific path, quota figure,
  or command name.
- **R6** — `AGENTS.md`, `SECURITY.md`, `INSTRUCTIONS.md` and `README.md` SHALL be consistent with the
  new naming in the same commit as the code.

## Notes

- The GitHub repository rename (`purduercac/rcac-mcp` → …) is a separate, human decision with its own
  redirect behaviour; the promotion conversation should decide whether it is in scope. Several
  `.agents/` files hardcode the repo URL (`cm-publish` permalinks, `cm-release`'s install probe).
- `SPDX-FileCopyrightText: 2025 Purdue University` headers are on every source file. Whether a
  generalized project keeps that attribution is a licensing question for the maintainer, not a
  mechanical substitution.
- Related: [`site-extras.md`](site-extras.md) inherits the `SERVER_INSTRUCTIONS` split this cycle
  starts.
- Found by: the harness port, 2026-08-26 (out of cycle).
