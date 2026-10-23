---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Bespoke tools first, the shell last, and every tool honest about what it does

## Problem

The server's file tools are too few and too thin, so agents fall back to `run_command` for
everyday work — and `run_command` is the one tool nobody can reason about from its name.

- **The file tools are minimal.** `list_directory` (`tools/filesystem.py:33`) costs N+1 round trips —
  it runs a command per entry inside its loop — parses `ls -l` with GNU-only `--time-style`, and
  returns symlinks as `name -> target`. `read_file` reads a whole file or its first N bytes, with no
  offset, no line numbers and no truncation flag. There is no edit, stat, find or grep tool, so an
  agent that needs one runs it through the shell, gets unstructured text back, and on a parallel
  filesystem may start a recursive walk from a root on a shared login node.
- **No tool carries annotations.** `readOnlyHint`, `destructiveHint` and `idempotentHint` are absent
  everywhere, so a client can't tell `read_file` from `write_file` from `run_command` without
  reading prose. Clients that match permissions by tool name (Claude Code allow-lists
  `mcp__<server>__<tool>`, with globs after the prefix) can pre-approve a read-only family only if
  its names let a pattern select it.
- **No response budgets.** Tools return whatever the command printed. Clients enforce their own
  limits — Claude Code warns at 10K tokens of output, stops at 25K by default and keeps only the
  ends of a long error — so an unbounded listing is truncated by the client, not by the tool that
  knows how to page it.
- **Context cost is measured, not guessed.** Today 25 tools carry about 25.6K characters of
  definitions (about 6.4K tokens) on every turn; `sbatch` alone is 2.9K.

## Why it was deferred

Not deferred — a roadmap cycle in Part I. It renames today's file tools, so it comes after
[`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md) and
[`capability-extras.md`](capability-extras.md) (every import and registration moves once), and it
is built on the argv executor from [`spec/safe-execution/`](../spec/safe-execution/).

**Pre-existing:** the thin tools and missing annotations are on `main`; the family is new work.

## Outcome / vision

**Bespoke first, shell last.** A bespoke tool earns its place by doing something the shell can't:
**exactness** (byte-exact writes, structured results, one interpreter's semantics), **politeness**
(a site's rules live in the tool rather than in prose the model may skip), or **permission
granularity** (a tool whose behaviour is fixed can carry an honest `readOnlyHint`; `run_command`
can't). The file family:

- `file_list` — one call, structured entries;
- `file_read` — offset and limit, line numbers, a byte cap, a `truncated` flag;
- `file_write` — byte-exact; create, overwrite or append; makes parent directories;
- `file_edit` — exact string replacement with a uniqueness check and an mtime guard, so an edit
  doesn't round-trip a whole file;
- `file_stat`;
- `file_find` — a bounded `find` (routed to an index later by [`xdu-storage.md`](xdu-storage.md));
- `file_grep` — bounded content search;
- transfer — upload and download, as now.

**Honest annotations** on every tool, and **response budgets** below common client limits, each
saying when it truncated and how to get the rest.

**`run_command` stays, as the last resort.** Hiding it would buy nothing — on a cluster, running
code is the product, and an agent denied a shell routes around it through job submission. Instead
it gets guard rails that **steer**: the command is parsed with `shlex` into argv per pipeline
segment and checked against a short, site-extensible policy. Most matches return an actionable note
naming the right tool (a recursive `find`, `du` or `ls -R` over a parallel-filesystem root →
`file_find`; a bare `module load` → the `modules=[…]` parameter, since state doesn't persist between
calls; an interactive program with no TTY → it would hang). Very little is refused outright: a
recursive delete or `chmod` of a home, scratch or project root, fork-bomb shapes, and `sudo`, which
can't prompt. A command that doesn't parse passes through unsteered. The docs say plainly what this is: a net for a
cooperative agent's mistakes, **not a security boundary** — the boundary is the Unix account.

**`--profile read-only`** registers only tools with `readOnlyHint`, for deployments that want one.
Dropping the shell alone would constrain nothing while `file_write` and job submission remain.

Open questions for shaping:

1. **Aliases or a clean break.** Tool names are part of the agent-facing API (`AGENTS.md`
   § Invariants §9). Keep `list_directory`, `read_file` and `write_file` as aliases through a
   deprecation window, or break cleanly with the 0.2.0 release that already renames everything?
2. **Names a client can select by pattern.** Resource-first names (`file_read`, `file_write`) read
   well, but a glob can't separate readers from writers. Is a naming scheme that lets a client
   allow-list the read-only family by pattern worth the cost in readability?
3. **Tool search.** FastMCP 4's server-side tool search, evaluated against clients' own.

## Sketch of the acceptance criteria

- **R1** — The core SHALL register `file_list`, `file_read`, `file_write`, `file_edit`, `file_stat`,
  `file_find` and `file_grep`, each returning structured results.
- **R2** — `file_list` SHALL make a bounded number of remote calls regardless of the number of
  entries, and SHALL not depend on GNU-only `ls` options.
- **R3** — WHEN `file_edit`'s target string occurs other than exactly once, or the file's mtime has
  changed since the caller read it, the tool SHALL refuse and say which.
- **R4** — Every tool SHALL carry MCP annotations that match its behaviour; `run_command` SHALL NOT
  carry `readOnlyHint`.
- **R5** — WHEN a result would exceed the tool's documented budget, the tool SHALL truncate it, set a
  truncation flag and say how to page.
- **R6** — WHEN `run_command` matches a steering rule, it SHALL return a note naming the preferred
  tool; WHEN the command does not parse, it SHALL run unsteered. The docs SHALL state that the
  guard rails are not a security boundary.
- **R7** — WHERE `--profile read-only` is set, only tools with `readOnlyHint` SHALL register.
- **R8** — The total size of tool definitions SHALL be measured in a test and reported against the
  6.4K-token baseline.

## Notes

- Related: [`spec/safe-execution/`](../spec/safe-execution/) (the executor this builds on),
  [`slurm-capability.md`](slurm-capability.md) (the `job_*` family follows the same rules),
  [`lmod-tool-group.md`](lmod-tool-group.md) (`modules=[…]`), [`xdu-storage.md`](xdu-storage.md)
  (routes `file_find`), [`spec/stack-upgrade/`](../spec/stack-upgrade/) (annotations and `_meta` on the
  current API).
- Found by: roadmap revision, 2026-10-16 (out of cycle).
- 2026-10-16: the docs strip removed `doc_search` and `doc_load`, so 23 tools remain. A JSON count of
  each served tool's MCP definition gives 26.5K characters for the 25 tools before and 23.5K for the
  23 after (the two were about 3.0K), so the 25.6K-character, 6.4K-token baseline above is about 11%
  high: re-measure it before R8 reports against it.
- 2026-10-23: `safe-execution` gave `write_file` a `base64` encoding for binary content and left
  the matching binary read to this family (its GOAL non-goals): `file_read` should return binary
  content the same way, so a byte-exact round trip needs no `run_command`.
