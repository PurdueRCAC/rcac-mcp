# 00 — Digest: what the research decided

Four briefs, written 2026-10-19 for the plan. This file states the decisions they support and
settles where they disagree; [`../PLAN.md`](../PLAN.md) is the design built on them.

| Brief | Topic | Source |
|---|---|---|
| [`01`](01-fastmcp-instructions.md) | How FastMCP 4.1 / MCP SDK 2.3 serve `instructions`, resources and an executor-less tool | installed packages, a probe against them |
| [`02`](02-client-truncation.md) | What clients do with server instructions | Claude Code docs and changelog, client sources, MCP spec |
| [`03`](03-agents-d-inventory.md) | What `/etc/agents.d` holds on the clusters | read-only probe, names and sizes only |
| [`04`](04-executor-and-suite.md) | The `safe-execution` executor the read goes through, and the suite contracts the change meets | `fix/safe-execution` at `4f4e43e` |

## Decisions

1. **Compose once, before the server exists, and pass the string to `FastMCP(instructions=…)`.**
   FastMCP serves it verbatim, uncapped, in `initialize` and `server/discover`, copied per transport
   (01 §1, §6). Nothing sets it afterwards.
2. **Budget default 4,096, counted in UTF-16 code units.** Claude Code compares JavaScript
   `String.length` with 4,096 and appends `… [truncated]`; its own header lines are added after the
   cut (02 §1). Code units are never fewer than code points, so the count is safe for any client
   that counts either.
3. **Read with a daemon thread and a hard wait**, not `run(timeout=)` alone: over ssh,
   `open_session` (30 s) and reconnect backoff sit outside the timeout (04 §1). Catch every
   exception (04 §2).
4. **ssh: one registered fixed script, the directory as `$1`, base64 framing and a final count**;
   local and delegate: Python on the server's host, as the server (04 §1, §5; C6).
5. **Hermetic seam without a production knob:** module constants read at call time plus a
   `directory` parameter; `bin/sandbox.sh` cannot redirect `/etc`, and C1 rules out a path override
   (04 §4, §5).
6. **`cluster_context` takes no argument and lives in its own tools module** (04 §4); the suite
   exempts it from the reach-the-executor and no-executor contracts by a named set (04 §3).
7. **Leave `DiscoverResult` caching at FastMCP's default** (`ttlMs=0`, `private`) (01 §2).
8. **At 4,096 the base must shrink to leave room for any topic file** (03): the topic files run
   1.6K–3.3K, the current base 2,085.

## Contradictions settled

- **A default for `create_mcp_server`'s site** (04 §Implications 1 proposes one) versus a required
  keyword: required. A default lets a caller build a server that silently serves no site context;
  the harness passes its own `SITE`, and the cost is one keyword at a handful of call sites.
- **`cacheScope`:** 02 suggests `private` with a modest `ttlMs` if instructions vary; 01 notes the
  constructor's cache hint applies to every cacheable method, per-user reads included. The site
  text is host-wide and the default is already `private` with no TTL: leave it.
- **The budget variable's name:** the KB design notes say `CLUSTER_MCP_INSTRUCTIONS_BUDGET`; the
  GOAL's non-goals say the project's current naming: `RCAC_INSTRUCTIONS_BUDGET`, moved by the rename.
- **Priority order:** the seed wanted "prohibitions and login-node rules first"; nothing
  site-agnostic can find those, so the order is the convention's own name order, which an
  administrator controls by naming a file `00-…` (an open question for the maintainer in PLAN § 5).
- **Widening `FIXED_RESET`** for a one-script read (04 §7): not done. The script unsets the three
  words it uses that the reset does not cover; `launch.py` is `safe-execution`'s and human-gated.
