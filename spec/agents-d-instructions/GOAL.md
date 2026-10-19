# GOAL — Serve /etc/agents.d as the server's instructions

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Edit requirements here; do **not** silently drift them during build.

- **slug:** agents-d-instructions
- **kind:** feature
- **appetite:** big

## Problem

`/etc/agents.d/*.md` is the convention this project exists partly to evangelize (`AGENTS.md`
§ Direction, § Invariants §8): administrators drop markdown there, and any conforming client picks
it up. Today the server delivers it in the one way most clients ignore, and delivers it badly.

- **Resource-only, and the resource cannot be read.** The files reach a client only as the
  `rcac://context` resource (`src/rcac_mcp/resources.py`), which most clients never read
  unprompted. Reading it fails anyway: both executor middlewares set the executor in
  `on_call_tool` only (`middleware.py:44`, `:74`, `:146`), so a resource read raises
  `No executor configured` in every mode (`issues/resource-reads-without-executor.md`). The site's
  rules (login-node limits, where to write, what not to run) reach the model only if it thinks to
  ask, and then not at all.
- **Every rule arrives twice.** The loader takes every `*.md` at `maxdepth 1`
  (`resources.py:48`), and a cluster's assembled `AGENTS.md` is a short preamble plus the same topic
  files. On Purdue's clusters the topic files run about 10.4K characters (Gautschi, Negishi) to
  12.4K (Anvil).
- **The cache is process-global and keyed by hostname** (`resources.py:23`, keyed at `:40`, hit at
  `:43`). `DelegatingExecutor.hostname` is `socket.gethostname()` (`executor/delegate.py:84`,
  returned at `:90`), identical for every user, so in `delegate` mode the first reader's
  `sudo -u <them> cat` result would be served to everyone after; nothing calls
  `clear_context_cache()` (`:88`). Pre-existing, on `main` since `2def4a4`; absorbed from
  `issues/context-cache-cross-user.md`.
- **The served instructions are a constant.** `SERVER_INSTRUCTIONS` (`server.py:65`, 2,085
  characters) is hand-written Purdue prose that cannot know what the connected site's
  administrators wrote.

Instructions are now the steering channel. With tool search on by default, Claude Code loads only
tool names and server instructions at session start, and it cuts server instructions at 4,096
characters by default; the model sees only the text before the cut (Clarifications, C3). Geoffrey,
2026-10-16: "automatically load all markdown files under `/etc/agents.d/` (except `AGENTS.md`)
directly into the instructions without requiring the agent to make a tool call."

## Outcome / vision

A client that connects gets the site's rules in the server's instructions, with no tool call, in
both protocol eras. The server reads the files once at startup, as itself; what fits the budget is
served in a priority order so a cut loses the least, and whatever does not fit is named, with a
pointer to a `cluster_context` tool that returns everything. `rcac://context` serves the same text
and becomes readable. Startup never hangs on the read, and the process-global per-host cache is
gone.

## Acceptance criteria (the contract)

"Site text" below means the files' content as read at startup, in the convention's form: each
file under a `<!-- Source: {filename} -->` header. "Base instructions" means the server-authored
part of the instructions.

- **R1** — WHEN the server starts, it SHALL read every `*.md` file directly in `/etc/agents.d/`
  except `AGENTS.md`, once, from the cluster in `ssh` mode and from the server's own host in
  `local` and `delegate` modes, keeping the convention's `maxdepth 1` glob and its
  `<!-- Source: {filename} -->` header per file; no later request SHALL cause the files to be
  read again.
- **R2** — WHEN a client connects through either protocol era (the handshake era's `initialize`,
  or revision 2026-07-28's `server/discover`), the served instructions SHALL consist of the base
  instructions followed by a site-context section carrying the site text, without the client making
  any tool call or resource read.
- **R3** — The site-context section SHALL present the files in a deterministic priority order that
  the docs state, so the same set of files always yields byte-identical instructions.
- **R4** — The composed instructions SHALL never exceed a character budget, settable by a CLI flag
  and an environment variable, whose default is at most 4,096 characters; the base instructions
  SHALL never be cut, and IF a configured budget cannot hold the base instructions and the R5 note,
  THEN the server SHALL refuse to start, exiting non-zero with a message naming the smallest budget
  it accepts.
- **R5** — WHEN site text does not fit the budget, the instructions SHALL name, by file name, every
  file left out or cut short, and SHALL name the `cluster_context` tool as the way to get it.
- **R6** — The `cluster_context` tool SHALL return the full site text read at startup, unaffected
  by the budget, in any execution mode.
- **R7** — Reading the `rcac://context` resource SHALL return exactly what `cluster_context`
  returns, in every execution mode and both protocol eras, without running anything through any
  executor.
- **R8** — IF `/etc/agents.d` is missing, holds no eligible file, cannot be read, or the read does
  not finish within a documented bound, THEN the server SHALL start within that bound, and its
  instructions SHALL be the base instructions plus one line naming why no site rules are served;
  `cluster_context` and `rcac://context` SHALL return `''` for a missing directory or no eligible
  file (§8's contract) and that same line for an unreadable or timed-out read.
- **R9** — WHILE running in `delegate` mode, every authenticated user SHALL receive identical site
  text in the instructions, from `cluster_context` and from `rcac://context`, read once by the
  server process and never as a user; a test SHALL show this for two identities whose executors
  report the same `hostname`, against a stub executor, without `sudo`.
- **R10** — The change SHALL land with its documentation in the same commit: `AGENTS.md`
  § Invariants §8 and §9 and § Configuration describe the new delivery, the budget and the
  exclusion of `AGENTS.md`, and drop the cache defect; `SERVER_INSTRUCTIONS` and `README.md`
  name `cluster_context` (§9); and the install docs tell Claude Code users how to raise
  `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`.

## Non-goals (no-gos)

- Per-user `agents.d` content, and any per-user fact (quotas, storage paths) in the instructions:
  those stay in tools.
- Live reload: an edit to `/etc/agents.d` takes effect at the next server start. The absorbed
  seed's "reflect the change without a restart" (its R2) is dropped, not deferred.
- Writing to `/etc/agents.d`.
- The `cluster-mcp` rename (`issues/rename-to-cluster-mcp.md`): the resource keeps `rcac://context`,
  and the budget's environment variable takes the project's current naming; the rename moves both.
- `rcac://storage` and its executor (`issues/resource-reads-without-executor.md`, absorbing
  `storage-paths-resource-typeerror`).
- Capability snippets between the base and the site context (`issues/capability-extras.md`) and
  packaged site context (`issues/site-extras.md`).
- Changing the convention's path, glob, `sort`-based discovery or header format beyond the
  `AGENTS.md` exclusion (C1).

## Clarifications

- **C1. Q:** What is read? — **A:** Every `*.md` file directly in `/etc/agents.d/` except
  `AGENTS.md`, read once at server startup and composed into the served instructions; a client
  gets the site's rules with no tool call. Skipping the assembled `AGENTS.md` is a deliberate,
  documented narrowing of §8's convention (it repeats the topic files), not a compatibility break
  for other centers' layouts (resolved 2026-10-19, foreman).
- **C2. Q:** Which protocol eras carry them? — **A:** Both: the handshake era's `initialize` and
  revision 2026-07-28's `server/discover` (resolved 2026-10-19, foreman).
- **C3. Q:** Budget and order? — **A:** A deterministic priority order, plus a configurable
  character budget whose default keeps the composed instructions within what Claude Code shows.
  Confirmed 2026-10-19 at code.claude.com/docs/en/mcp → *Tool search for MCP server authors*:
  "Server instructions, and the description of a tool that loads upfront: 4,096 characters" (2,048
  before v2.1.296), "Claude sees only the text before the cut", and
  `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` (v2.1.280+) "replaces both limits", so raising it also
  sets the 16,384 limit for deferred tool descriptions. The plan picks the default and the order
  rule; whatever does not fit is named in the instructions with a pointer to the tool that returns
  it, and whether a file is cut whole or by section is the plan's call within R5 (resolved
  2026-10-19, foreman).
- **C4. Q:** Tool and resource? — **A:** A `cluster_context` tool returns the full text, and
  `rcac://context` stays, serving the same startup-read text. Reading it then needs no per-request
  executor (R7), which settles `issues/resource-reads-without-executor.md` for `rcac://context`
  only; whether the tool takes an argument selecting one file is the plan's call (resolved
  2026-10-19, foreman).
- **C5. Q:** What if the read fails? — **A:** Startup never hangs on it: the read is bounded, and on
  failure the server starts with its base instructions plus a one-line note naming why the site
  rules are missing. With no live reload (C7), the tool and resource report that note rather than
  retrying; a missing directory or no eligible file keeps §8's `''` (resolved 2026-10-19, foreman).
- **C6. Q:** Delegate mode? — **A:** Read once as the server process: the files are world-readable
  administrative markdown, so there is no per-user read. The process-global per-host cache is
  retired, not patched; both seeds take `status: adopted:agents-d-instructions` (resolved
  2026-10-19, foreman).
- **C7. Q:** Non-goals? — **A:** Per-user agents.d, live reload, writing to agents.d, the
  cluster-mcp rename, and `rcac://storage` (resolved 2026-10-19, foreman).
- **C8. Q:** Size? — **A:** Ten R-IDs, one deliverable; no split (resolved 2026-10-19, foreman's
  bound of 10).
- **C9. Note:** This cycle builds only after `safe-execution` lands; it reworks the executor the
  startup read goes through. The GOAL states behaviour, not that executor's API (2026-10-19,
  foreman).
- **C10. The questions shaping left for the gate** (foreman, GOAL gate, 2026-10-19):
  - R4's refusal to start on a budget too small for the base instructions plus the omission note
    stands: a misconfiguration fails at once, naming the minimum, instead of serving cut base
    instructions.
  - R8's split stands: an absent directory or no eligible file is not an error (`''`, no note);
    an unreadable directory or a read that times out is (the one-line note).
  - A per-file `topic` argument on `cluster_context` is the plan's call, within R6.
  - The served surface changes (the new tool, the instructions text). Until
    `surface-fixture-after-a-surface-change` settles the method, the fixture changes by a scripted
    edit limited to this cycle's named deltas, each listed in the commit body, as `safe-execution`
    did.
  - `resource-reads-without-executor`'s ROADMAP entry narrows to `rcac://storage` at this cycle's
    retirement, not before.
- **C11. Who "every user" is in R7 and R9** (foreman, PLAN/TECH gate, 2026-10-19): R9's identical
  text is owed to every user the server serves, and the instructions and `rcac://context` reach
  any client that can connect. `cluster_context`, as a tool call, reaches only a mapped user,
  because `delegate` mode refuses tool calls from an authenticated identity absent from the user
  map today (§5), and this cycle does not change that. The site text is world-readable
  administrative markdown, so the difference discloses nothing; the plan reads "authenticated user"
  as "mapped user" for the tool, and §5 stays untouched.

## Related materials

- Seeds: [`issues/agents-d-instructions.md`](../../issues/agents-d-instructions.md),
  [`issues/context-cache-cross-user.md`](../../issues/context-cache-cross-user.md) (absorbed).
- Depends on: [`issues/safe-execution.md`](../../issues/safe-execution.md); the served surface
  changes, so [`issues/surface-fixture-after-a-surface-change.md`](../../issues/surface-fixture-after-a-surface-change.md)
  must be settled first (by `safe-execution` or here).
- Partly settles: [`issues/resource-reads-without-executor.md`](../../issues/resource-reads-without-executor.md)
  (`rcac://context` only).
- Source: `src/rcac_mcp/resources.py`, `server.py` (`SERVER_INSTRUCTIONS`), `middleware.py`,
  `executor/delegate.py`; `tests/test_instructions.py` cross-checks the instructions against
  `TOOL_REGISTRY` and needs rescoping to the base; `tests/test_surface.py`.
- Protocol: `DiscoverResult` is cacheable (`ttlMs`, `cacheScope`); host-wide text is the same for
  every user, so `cacheScope: "public"` is safe.
- Claude Code limits: <https://code.claude.com/docs/en/mcp#for-mcp-server-authors>.
- Design notes: KB `workspace/cluster-mcp/design.md` § 2.
