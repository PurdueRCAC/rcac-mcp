---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Serve /etc/agents.d as the server's instructions, not only as a resource

## Problem

`/etc/agents.d/*.md` is the convention this project exists partly to evangelize (`AGENTS.md`
§ *Direction*, § Invariants §8): administrators drop markdown there, and any conforming client picks
it up. This server delivers it in the one way most clients ignore.

- **It is resource-only.** `rcac://context` (`resources.py`) loads the files, but most MCP clients
  never read a resource unprompted, so the site's rules — login-node limits, where to write, what
  not to run — reach the model only if the model thinks to ask.
- **Every rule arrives twice.** The loader takes every `*.md` at `maxdepth 1`, and a cluster's
  assembled `AGENTS.md` is a short preamble plus the same topic files. On Purdue's clusters the topic
  files run about 10.4K characters (Gautschi, Negishi) to 12.4K (Anvil); the assembled file repeats
  all of it.
- **The cache is process-global, keyed by hostname** — the defect in
  [`context-cache-cross-user.md`](context-cache-cross-user.md), absorbed here.
- **The served instructions are a constant.** `SERVER_INSTRUCTIONS` (`server.py:64`) is about 3K
  characters of hand-written Purdue prose that cannot know what the connected cluster's
  administrators wrote.

Instructions matter more than they used to. Clients with tool search on by default (Claude Code
among them) load only tool names and server instructions at session start, so the instructions are
the steering channel. But clients also cut them: Claude Code keeps the first 4,096 characters of a
server's instructions by default, configurable with `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`, and
the model sees only the text before the cut. Some clients cut harder or drop instructions entirely.

## Why it was deferred

Not deferred — a roadmap cycle. It lands in Part 0, after
[`spec/stack-upgrade/`](../spec/stack-upgrade/) (instructions travel in `InitializeResult` on older protocol
revisions and in `DiscoverResult.instructions` on 2026-07-28, so the mechanism is built once on the
current API) and [`safe-execution.md`](safe-execution.md) (it reads through the argv executor, and
startup has to fail clearly).

**Pre-existing:** the resource-only delivery and the double load are on `main` today; the cache
defect is filed separately and absorbed.

## Outcome / vision

At startup the server reads `/etc/agents.d/*.md` **once**, through its own executor — the cluster's
files in `ssh` mode, the host's in `local` and `delegate` — skipping the assembled `AGENTS.md`, and
keeping the convention's `maxdepth 1`, `sort` order and `<!-- Source: x.md -->` headers. The served
instructions become, in order:

1. a short generic core: what the server is, the tool families, "prefer the bespoke tools";
2. `## Site context (/etc/agents.d)` with the files.

**Ordered so a cut loses the least.** The core is about 1K characters, and the site context follows
in a priority order — prohibitions and login-node rules before the rest — so a client that keeps
only the first few thousand characters keeps the most important ones. A **configurable budget** (a
flag and an environment variable) bounds the total; when the text is cut, the instructions end by
naming `cluster_context` for the remainder. Whether the budget is filled with whole files in
priority order or with each file's first section is a shaping question.

**Reachable by tool too.** A `cluster_context(topic=None)` tool returns the full text, or one file.
The `rcac://context` resource is kept and serves the same text, without the duplicate.

**Read once, as the server.** `/etc/agents.d` is host-wide and world-readable, so serving it from
server-level instructions is correct for every user, in every mode. Reading it once at startup
retires the hostname-keyed cache rather than patching it. Per-user context — quotas, storage paths —
stays in tools, never in instructions.

**Startup never hangs on it.** The read is bounded by a timeout. If the files can't be read, the
server starts anyway, and the instructions say the site context is unavailable and name
`cluster_context` to retry.

The install docs tell Claude Code users to raise `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` (16384
covers every Purdue cluster today).

## Sketch of the acceptance criteria

- **R1** — WHEN the server starts, it SHALL read `/etc/agents.d/*.md` once through its executor,
  excluding `AGENTS.md`, in `sort` order with a `<!-- Source: … -->` header per file.
- **R2** — The served instructions SHALL consist of the generic core followed by the site context,
  in the documented priority order, on both protocol eras.
- **R3** — WHEN the composed text exceeds the configured budget, the server SHALL cut it at a file or
  section boundary and SHALL end the instructions with a line naming `cluster_context`.
- **R4** — `cluster_context` SHALL return the full site context, or one file WHEN `topic` names it,
  and SHALL match what the `rcac://context` resource serves.
- **R5** — IF `/etc/agents.d` is missing, empty, unreadable or slow past the timeout, THEN the server
  SHALL start within that bound, and its instructions SHALL say the site context is unavailable.
- **R6** — No cache keyed on `hostname` alone SHALL remain; in `delegate` mode two users SHALL
  receive identical host-wide text and no per-user content (carried from `context-cache-cross-user`,
  tested against a stub executor without `sudo`).
- **R7** — `AGENTS.md` § Invariants §8 and §9 SHALL describe the new delivery in the same commit; the
  path, glob, ordering and header format of the convention SHALL NOT change.

## Notes

- The protocol's `DiscoverResult` is cacheable (`ttlMs`, `cacheScope`). Host-wide `agents.d` text
  is the same for every user, so `cacheScope: "public"` is safe; anything per-user would have to be
  `"private"`, which is one more reason to keep per-user facts in tools.
- [`capability-extras.md`](capability-extras.md) later inserts capability snippets between the core
  and the site context; [`site-extras.md`](site-extras.md) documents how packaged site context
  composes with this runtime context.
- Related: [`context-cache-cross-user.md`](context-cache-cross-user.md) (absorbed),
  [`spec/stack-upgrade/`](../spec/stack-upgrade/), [`safe-execution.md`](safe-execution.md),
  [`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md) (moves the resource URI and the budget's
  environment variable to the new prefix).
- Found by: roadmap revision, 2026-10-16 (out of cycle).
- 2026-10-16: `tests/test_instructions.py` cross-checks the served instructions against
  `TOOL_REGISTRY`; once site context is composed in, rescope it to the server-authored core. Known
  limitation (recorded, not fixed): it cannot see a plain-word tool name, or search guidance free of
  doc-words, written in bare prose.
- 2026-10-16: removing the documentation guidance shrank `SERVER_INSTRUCTIONS` from about 3.0K to
  about 2.1K characters (2,988 to 2,085).
- 2026-10-17: since `safe-execution`, the context listing runs in a login bash, so its `sort` order
  follows whatever locale the login profile sets (it followed the zsh environment's before).
  Whether to pin it with `LC_ALL=C` is an § Invariants §8 compatibility decision this cycle owns
  (`spec/safe-execution/` PLAN risk 9).
