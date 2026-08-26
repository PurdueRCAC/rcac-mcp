# ROADMAP

The ordered index of work that is **not yet in flight**. One entry per `issues/{slug}.md` seed; the
seed holds the evidence and the draft criteria, this file holds the order and the reasoning about
order. Nothing here is a contract — `/cm-feature` promotes a seed into a `spec/{slug}/GOAL.md`, and
that promotion is where appetite, non-goals and the R-IDs get negotiated with a human.

Three Parts, and they are sequenced for a reason. **Part I** is the arc this project is actually on:
becoming a general `cluster-mcp` rather than a Purdue-specific `rcac-mcp`. **Part II** is correctness
debt — four defects found while porting the software factory, each verified by execution, none of
them shipped by a cycle that knew about them. **Part III** is the project infrastructure the factory
assumes exists and which mostly does not yet.

Read `AGENTS.md` § *Direction* before starting anything in Part I; it states the destination that
these entries are steps toward. `.agents/factory/methodology.md` explains the lifecycle that
consumes these seeds.

---

# Part I — Becoming cluster-mcp

Six cycles that take this from *the Purdue RCAC MCP server* to *an MCP server for HPC clusters, with
Purdue as one supported site*. They are ordered so each one shrinks or clarifies the tree the next
one has to touch: remove what left, rename what stays, then make what stays optional.

## The documentation subsystem belongs to rcac-docs-mcp now

Agentic documentation retrieval was extracted into a separate `rcac-docs-mcp` service, hosted at
`docs.rcac.purdue.edu/mcp`, with an AskRCAC tool arriving on `docs.rcac.purdue.edu` shortly. Knowledge
work evolves there now and is no longer this repository's responsibility. What remains here is a
whole FTS5 indexing subsystem — `src/rcac_mcp/docs/` (945 lines), `tools/docs.py`, three CLI flags, a
git submodule, and prominent guidance in `SERVER_INSTRUCTIONS` telling every connecting agent to
consult a tool this server should no longer own.

This goes first. Everything else in Part I touches a smaller tree once it is gone, and the rename in
particular is meaningfully cheaper.

*Horizon: now · Depends on: — · Refs: forces `restore-test-coverage`*
**Seed:** [`issues/strip-docs-subsystem.md`](issues/strip-docs-subsystem.md) · *status: unshaped*

## Nothing here should assume Purdue

The rename from `rcac-mcp` to `cluster-mcp` is mechanical in places (package, console script,
`pyproject.toml`, the `rcac://` resource URIs, the `RCAC_*` environment variables) and a judgement
call in others: `tools/rcac.py` genuinely *is* Purdue-specific and should keep a site-flavoured name,
while `SERVER_INSTRUCTIONS` is currently a Purdue document that needs splitting into a generic core
and a site overlay.

The environment-variable rename is the sharp edge — `RCAC_SSH_HOST` appears in every user's MCP
client config, and `.agents/factory/bin/sandbox.sh` unsets it by name to fail closed. A deprecation
window, not a hard break.

*Horizon: near-term · Depends on: the docs strip (smaller surface to rename) · Refs: —*
**Seed:** [`issues/rename-to-cluster-mcp.md`](issues/rename-to-cluster-mcp.md) · *status: unshaped*

## Capabilities you don't have shouldn't be tools you see

Today every tool registers unconditionally: a site without Slurm still advertises `sbatch`, `squeue`
and `sacct` to every connecting model, which is an invitation to hallucinate a workflow that cannot
run. The intended shape is packaging extras — `cluster-mcp[slurm]` — backed by a registration
mechanism that can omit a tool group cleanly rather than importing it and hoping.

This is the load-bearing cycle of Part I: it establishes the mechanism that `lmod`, the site extras,
and every future capability plug into. It also collides directly with an existing invariant —
registration is currently an import-time side effect driven by a hardcoded list at the bottom of
`tools/__init__.py` (`AGENTS.md` § *Tools*) — so the design has to replace that contract, not work
around it.

*Horizon: near-term · Depends on: the rename (the extras are named `cluster-mcp[…]`) · Refs: prerequisite for both extras entries below*
**Seed:** [`issues/capability-extras.md`](issues/capability-extras.md) · *status: unshaped*

## Module systems are the other half of "can I run this"

Every tool here is about jobs; none is about software. On an LMOD cluster an agent cannot discover
what is installed, what versions exist, or what a module load would do to the environment — so it
guesses, and `INSTRUCTIONS.md` has to compensate with prose. `module avail`, `module spider`,
`module list` and `module show` are the missing capability group, and they are the second consumer
of the extras mechanism, which is what proves the mechanism generalizes past one instance.

*Horizon: near-term · Depends on: capability-extras (strictly) · Refs: —*
**Seed:** [`issues/lmod-tool-group.md`](issues/lmod-tool-group.md) · *status: unshaped*

## A site extra is shorthand plus a point of view

`cluster-mcp[rcac]` should mean "the right capability extras for Purdue, plus Purdue's own tools and
context" — one install target a user can name without knowing that Gautschi runs Slurm and LMOD. That
makes it both a dependency alias and the home for `tools/rcac.py` and the Purdue half of
`SERVER_INSTRUCTIONS`. Getting the boundary right here is what makes `cluster-mcp[alcf]`,
`[ncsa]` and `[tacc]` a matter of contribution rather than redesign, so this cycle should produce a
documented pattern, not just a Purdue special case.

*Horizon: mid-term · Depends on: capability-extras (strictly) · Refs: sets the pattern other centers copy*
**Seed:** [`issues/site-extras.md`](issues/site-extras.md) · *status: unshaped*

## Not every cluster has a Lustre scratch and a myquota

`storage_paths()` parses the output of `myquota`, and scratch discovery shells out to `findscratch` —
both Purdue commands, and neither degrades on a cluster that lacks them. The `rcac://storage` resource
is built on the same assumption. Generalizing means a discovery protocol with a site-provided
implementation and an honest "unknown" rather than a parse failure.

*Horizon: mid-term · Depends on: site-extras (the site hook is where a discovery impl lands) · Refs: also fixes the `rcac://storage` resource's second problem*
**Seed:** [`issues/generalize-storage-discovery.md`](issues/generalize-storage-discovery.md) · *status: unshaped*

---

# Part II — Correctness debt found while porting the factory

Four defects, all found and **verified by execution** while writing `AGENTS.md` and
`.agents/factory/invariants.md` on 2026-08-26 — the invariant-writing pass was itself the review that
nobody had run. None was introduced by that pass; all four are pre-existing on `main`. They are small
and independent, which makes them good first cycles for a factory that has never been driven here.

## The rcac://storage resource cannot execute at all

`resources.py:151` calls `storage_paths()`, but `@mcp_tool` replaced that function with a
`fastmcp.tools.tool.FunctionTool`, which is not callable — the resource raises
`TypeError: 'FunctionTool' object is not callable` on every read. Reproduced directly. The generic
lesson is already recorded as an invariant (`AGENTS.md` § *Tools*: a decorated tool cannot be called
from Python; factor shared logic into an undecorated helper), but the resource itself is still broken.

*Horizon: now · Depends on: — · Refs: the same helper split that `generalize-storage-discovery` needs*
**Seed:** [`issues/storage-paths-resource-typeerror.md`](issues/storage-paths-resource-typeerror.md) · *status: unshaped*

## A type annotation that is accidentally a slice

`auth.py:65` reads `Final[Dict[str, Callable[[], AuthProvider]]:]` — the trailing `:` inside the
subscript makes it a slice expression. The interesting part is what does *not* happen: evaluating it
raises nothing. `typing.get_type_hints` succeeds and silently returns
`Final[slice(Dict[...], None, None)]`, and deferred annotations are not what saves it — the same
expression evaluated eagerly on Python 3.14 yields the same nonsense without complaint. A silent
wrong type in the module that decides whether a request is authenticated is worse than a loud one,
and it means any check that merely asserts evaluation succeeds passes on the broken code.

*Horizon: now · Depends on: — · Refs: —*
**Seed:** [`issues/auth-modes-annotation-typo.md`](issues/auth-modes-annotation-typo.md) · *status: unshaped*

## One user's cluster context is served to every other user

`_cluster_context_cache` (`resources.py:23`) is a process-global dict keyed by `executor.hostname`.
For `DelegatingExecutor` that key is `socket.gethostname()` — identical for every authenticated
user — so in delegate mode the first reader's `/etc/agents.d` content is returned to everyone after,
and the cache is never invalidated. Today the content is world-readable administrative markdown, so
the impact is bounded; the *mechanism* is a cross-user cache in the one mode that exists to keep
users apart, and it is the shape of the bug rather than today's payload that matters.

*Horizon: now · Depends on: — · Refs: `AGENTS.md` § *Resources* documents the defect as a known one*
**Seed:** [`issues/context-cache-cross-user.md`](issues/context-cache-cross-user.md) · *status: unshaped*

## run() has two different contracts depending on the backend

`LocalShellExecutor` and `DelegatingExecutor` catch `subprocess.TimeoutExpired` and return a
`CommandResult` with `exit_code=-1`; `SSHExecutor` passes `timeout` to Fabric and catches nothing, so
Invoke's `CommandTimedOut` propagates to the caller. A tool that handles a timed-out command works in
`local` mode and raises in `ssh` mode — the default mode. The `-1` sentinel is separately overloaded
with a real SIGHUP death.

*Horizon: near-term · Depends on: — · Refs: touches the `Executor` protocol contract, so all three backends move together*
**Seed:** [`issues/executor-timeout-asymmetry.md`](issues/executor-timeout-asymmetry.md) · *status: unshaped*

---

# Part III — The infrastructure the factory assumes

Three gaps between what `.agents/` expects and what this repository has. None is a product feature;
all three change how much a green gate is worth.

## Every test in the suite is a docs test

All 81 tests cover the documentation index — `test_database.py` (22), `test_indexer.py` (35),
`test_tools.py` (19), `test_cli.py` (5). `strip-docs-subsystem` therefore takes the entire suite with
it, and the coupled core it leaves behind — the `ContextVar` isolation contract, the identity-claim
precedence, the user-map enforcement, the `sudo` argv, the exec-mode ladder — has **never had a
test**. Those are exactly the paths `invariants.md` marks highest-blast-radius, and a `verify:` gate
over an empty suite is theatre.

*Horizon: now, immediately after the docs strip · Depends on: strip-docs-subsystem · Refs: gates the credibility of every later cycle's verify command*
**Seed:** [`issues/restore-test-coverage.md`](issues/restore-test-coverage.md) · *status: unshaped*

## Nothing runs on push

There is no `.github/` at all: no test workflow, no publish workflow, no container build. `/cm-release`
says so out loud and its local gate is currently the only thing between a bad build and a permanent
tag. A minimal CI — pytest on the supported Python, plus the sdist-hygiene assertion so `.agents/`
and `.security/` can never reach a published artifact — is the cheap half.

*Horizon: near-term · Depends on: restore-test-coverage (CI over a docs-only suite tests the wrong thing) · Refs: `/cm-release` Step 4 duplicates the gate locally until this exists*
**Seed:** [`issues/ci-workflow.md`](issues/ci-workflow.md) · *status: unshaped*

## The factory has no front door

The upstream HyperShell harness ships `factory/getting-started.html`, a 60KB self-contained
introduction to agents, harnesses, Shape Up and this factory, written for practitioners *and*
institutional leadership. It was deliberately not copied during the port — duplicating near-identical
prose across two repositories invites exactly the silent drift the upstream ledger already filed a
finding about. But this repository now has eight skills and no on-ramp, and `/cm-harness` Step 6
carries a staleness check that is conditional on a file that does not exist.

*Horizon: later · Depends on: — · Refs: `.agents/factory/harness-log.md` bootstrap entry, divergence 4*
**Seed:** [`issues/factory-onboarding-page.md`](issues/factory-onboarding-page.md) · *status: unshaped*

---

# Settled questions

Deferrals closed **without** shipping — `declined` (considered and not taken on as debt) and
`accepted-behaviour` (reported as a defect, judged intended). They keep their `issues/` files because
nothing else in the repository records that the question was asked. Shipped work leaves no entry
here: `spec/{slug}/` holds that account and the code itself refutes a re-filing.

*(None yet.)*

---

# A note on security work

This server executes arbitrary shell commands, and in `delegate` mode it does so **as other people**,
from a privileged process, on shared research infrastructure. Deferrals that describe an
*unremediated* weakness — a live path to running a command as the wrong user, on the wrong host, or
without the intended authentication — do **not** appear in this file. They go to `.security/issues/`
and `.security/ROADMAP.md`, which are gitignored: a public, ordered index of live vulnerabilities is
an attacker's work plan. The *fixes* land as ordinary public commits and PRs when they ship.

`context-cache-cross-user` above is in the public lane deliberately, and the judgement is worth
recording: the cached content is world-readable administrative markdown that every user could read
anyway, so there is no exploitable disclosure to conceal — only a caching bug whose *shape* is
cross-user. Had it cached anything user-private, it would have gone to the hidden lane instead. When
in doubt, use `.security/` and ask.
