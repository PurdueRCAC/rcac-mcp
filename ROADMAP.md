# ROADMAP

The ordered index of work that is **not yet in flight**. One entry per `issues/{slug}.md` seed; the
seed holds the evidence and the draft criteria, this file holds the order and the reasoning about
order. Nothing here is a contract — the factory's feature step promotes a seed into a `spec/{slug}/GOAL.md`, and
that promotion is where appetite, non-goals and the R-IDs get negotiated with a human.

The first three Parts each end in a release. **Part 0** makes the server safe and useful as it stands and
ships as 0.1.3. **Part I** is the arc this project is actually on — becoming a general `cluster-mcp`
rather than a Purdue-specific `rcac-mcp` — and ships as 0.2.0. **Part II** is the hosted mode that
comes after. **Part III** holds infrastructure work that no release waits on.

The order was revised on 2026-10-16. Users install `main` directly, so fixes for today's users ship
first, under the name they already have, before the rename. The dependency stack moves before any
mechanism — registration, composed instructions, annotations — is built on it, so each is built
once. And every cycle starts from and lands on `develop`; a release fast-forwards `main` to a
released `develop` commit, so nothing reaches users between releases.

A seed that another cycle **absorbs** keeps a short entry under that cycle, so every file in
`issues/` stays indexed until the absorbing cycle retires it.

Read `AGENTS.md` § *Direction* before starting anything in Part I; it states the destination that
these entries are steps toward. `AGENTS.md` § Factory describes the lifecycle that consumes these
seeds; its method lives in the operator's KB.

---

# Part 0 — Safe and useful as it stands

Three cycles remain, ending in **release 0.1.3**: `ssh-reconnect-loop`, then
`sbatch-script-name-collision`, then `agents-d-instructions`; the resource reads follow it,
since it retires the cache a read would expose and may settle them outright. `stack-upgrade` landed
on `develop` on 2026-10-17 ([`spec/stack-upgrade/`](spec/stack-upgrade/)), and `ci-workflow` on
2026-10-18 ([`spec/ci-workflow/`](spec/ci-workflow/)), so the release gate now runs on every push.
`safe-execution` landed on 2026-10-23 ([`spec/safe-execution/`](spec/safe-execution/)): every
tool passes exactly what it was given, under one bash, and the three build on it to fix what
today's users still hit.

*Corrected 2026-10-23: release 0.1.2 was cut on 2026-10-22 from `develop` `90b0938`, before
`safe-execution` landed. It carried the CI workflow, the sdist hygiene fix and a lock past two
dependency advisories, so the release that closes Part 0, and the first to carry its executor
fixes, is now 0.1.3.*

*Corrected 2026-10-18: release 0.1.1 was cut early, on 2026-10-18 from `develop` `c68adaa`, before
any of Part 0's fixes landed. It carried the dependency-stack upgrade, the docs strip, the test
suite and the both-era gates, so the release that closes Part 0 is now 0.1.2.*

*Corrected 2026-10-17: `stack-upgrade` landed before `ci-workflow`, so the release recipe's `gate`
gained the dependency audit first; `ci-workflow`, landing next, mirrors that gate — audit
included — through its "CI mirrors the gate" check.*

*Corrected 2026-10-17: two small fixes that `safe-execution` found and deferred joined Part 0, right
after that cycle: a lost SSH connection hangs every later call, and concurrent `sbatch` submissions
can overwrite each other's script. Both are what today's users hit, and both are small, so they ship
in the release that closes Part 0 (now 0.1.3) rather than wait for Part I.*

*Corrected 2026-10-17: the `rcac://storage` fix moved under the resource-read entry below. Its
handler runs `findscratch` and `myquota` through the request's executor, which a read does not get
yet, so fixing the `TypeError` alone would only change the error a client sees.*

## A lost SSH connection makes every later call wait forever

`SSHExecutor._ensure_connected` reconnects with a backoff whose loop condition stays true once the
delay reaches its 10 s cap, so after the cluster drops out from under a running server, the next
tool call retries every 10 s for as long as the server runs instead of failing with the host's
name. A bounded retry that ends in an error fixes it. Pre-existing. Found by `safe-execution`'s
planning research, 2026-10-17.

It comes first now that `safe-execution` has landed, because it is a hang a user sees in `ssh`
mode, the default, and a small fix: `safe-execution` already bounds the first connect, and this
bounds the reconnect the same way, so it ships in 0.1.3.

*Horizon: near-term · Depends on: safe-execution (its connect timeouts, P3; cleared: landed on `develop` 2026-10-23) · Refs: [`spec/safe-execution/`](spec/safe-execution/) research 02 §4*
**Seed:** [`issues/ssh-reconnect-loop.md`](issues/ssh-reconnect-loop.md) · *status: unshaped*

## `sbatch` job scripts written in the same second share a name

`sbatch` writes `script_content` to `~/jobs/<job_name>_<timestamp>.sh` with a one-second
timestamp, so two submissions with the same name in the same second write one file: the first
job's script on disk is replaced, and two overlapping calls can submit the same content twice. An
exclusive create, or a unique part in the name, fixes it.
Pre-existing. Found by `safe-execution`'s planning research, 2026-10-17.

It sits in Part 0, not with the `job_*` family, because today's users submit concurrently (two
clients of one server, an agent submitting a sweep in a loop) and can clobber each other's script
now; the fix is small, and waiting for Part I would leave it in place until 0.2.0.

*Horizon: near-term · Depends on: safe-execution (the byte-exact script write; cleared: landed on `develop` 2026-10-23) · Refs: [`spec/safe-execution/`](spec/safe-execution/) research 04 §7, PLAN risk 7; [`issues/slurm-capability.md`](issues/slurm-capability.md), whose `job_submit` inherits the fixed naming*
**Seed:** [`issues/sbatch-script-name-collision.md`](issues/sbatch-script-name-collision.md) · *status: unshaped*

## The site's own rules should reach the model unasked

`/etc/agents.d/*.md` reaches agents only as the `rcac://context` resource, which most clients never
read unprompted, and it loads the assembled `AGENTS.md` beside the topic files, so every rule
arrives twice. The cycle composes the topic files into the served instructions at startup, read
once through the executor, in a priority order so a client that cuts server instructions at a few
thousand characters loses the least, within a configurable budget. A `cluster_context` tool returns
the full text, the resource stays, and startup never hangs on the read.

*Horizon: near-term · Depends on: stack-upgrade (both protocol eras carry instructions; cleared: landed on `develop` 2026-10-17), safe-execution (the executor it reads through; cleared: landed on `develop` 2026-10-23) · Refs: absorbs `context-cache-cross-user`*
**Seed:** [`issues/agents-d-instructions.md`](issues/agents-d-instructions.md) · *status: unshaped*

### Absorbed: one user's cluster context is served to every other user

`_cluster_context_cache` (`resources.py:23`) is a process-global dict keyed by `executor.hostname`.
For `DelegatingExecutor` that key is `socket.gethostname()` — identical for every authenticated
user — so in delegate mode the first reader's `/etc/agents.d` content is returned to everyone after,
and the cache is never invalidated. The content is world-readable administrative markdown, so the
impact is bounded; the *mechanism* is the bug. Reading the files once at startup, as the server,
retires the cache rather than patching it. Found by the harness port, 2026-08-26.

*Horizon: with agents-d-instructions · Depends on: — · Refs: absorbed by `agents-d-instructions`; `AGENTS.md` § Invariants §8 documents the defect as a known one*
**Seed:** [`issues/context-cache-cross-user.md`](issues/context-cache-cross-user.md) · *status: unshaped*

## Neither advertised resource can be read

Both executor middlewares set the request's executor in `on_call_tool` only, so a resource read
carries none, and `rcac://context` fails with `No executor configured` in every mode and both
protocol eras, on FastMCP 2 and 4 alike. It fails closed: nothing runs. The fix gives a read the
executor of the request that made it, the mapped user's in `delegate` mode, which is also what makes
the process-global context cache above reachable across users through the resource, so it lands
with or after that cache's retirement. Found by `stack-upgrade`'s research, 2026-10-17.

*Horizon: near-term · Depends on: agents-d-instructions (it retires the cache, and may settle this outright) · Refs: `AGENTS.md` § Invariants §8 documents the defect as a known one; absorbs `storage-paths-resource-typeerror`, the other resource's failure*
**Seed:** [`issues/resource-reads-without-executor.md`](issues/resource-reads-without-executor.md) · *status: unshaped*

### Absorbed: the rcac://storage resource fails on every read

`resources.py:151` calls `storage_paths()`, but `@mcp_tool` replaced that function with a
`fastmcp.tools.tool.FunctionTool`, which is not callable — the resource raises
`TypeError: 'FunctionTool' object is not callable` on every read. The generic lesson is already an
invariant (`AGENTS.md` § Invariants §7: a decorated tool cannot be called from Python; factor shared
logic into an undecorated helper). The tool it calls runs `findscratch` and `myquota` through the
request's executor, so the summary comes back only once a read has one: the two fixes land together.

*Horizon: with resource-reads-without-executor · Depends on: — · Refs: absorbed by `resource-reads-without-executor`; the same helper split that `generalize-storage-discovery` needs*
**Seed:** [`issues/storage-paths-resource-typeerror.md`](issues/storage-paths-resource-typeerror.md) · *status: unshaped*

**Release 0.1.3.** `main` fast-forwards to the released `develop` commit, and everyone who installs
`main` gets byte-exact writes, literal paths, `sbatch` arguments, one shell's semantics, clear
connection errors, a call that fails rather than hangs when the connection drops, job scripts that
never overwrite each other, and the site's rules in the instructions — under the name they already use.
Pages outside this repository that name its tools or install line — RCAC-Docs
`docs/agentic-ai/mcp_servers.md` and `docs/agentic-ai/running_agents/local.md` — are updated when
the release lands.

---

# Part I — Becoming cluster-mcp

Eight cycles that take this from *the Purdue RCAC MCP server* to *an MCP server for HPC clusters,
with Purdue as one supported site*, ending in **release 0.2.0**. Rename and restructure what stays,
make capabilities optional, then build each capability on that mechanism — Slurm first, then LMOD
and xdu as the consumers that prove it generalizes — and finally the site extra that bundles them.
Ahead of the tool families sits one executor fix, stopping a timed-out command, once its
measurement is approved.

## Nothing here should assume Purdue

The rename from `rcac-mcp` to `cluster-mcp` is mechanical in places (package, console script,
`pyproject.toml`, the `rcac://` resource URIs, the `RCAC_*` environment variables) and a judgement
call in others: `tools/rcac.py` genuinely *is* Purdue-specific and should keep a site-flavoured name,
while `SERVER_INSTRUCTIONS` is currently a Purdue document that needs splitting into a generic core
and a site overlay.

The same cycle restructures the tree into a uv workspace of namespace distributions — a core
`cluster-mcp` providing `cluster_mcp.core`, and one member per capability and site sharing the
`cluster_mcp` namespace — with no behaviour change, so every import moves once.

The environment-variable rename is the sharp edge — `RCAC_SSH_HOST` appears in every user's MCP
client config, and `bin/sandbox.sh` unsets it by name to fail closed. A deprecation
window, not a hard break; the old resource URIs are aliased through the same window.

*Horizon: after 0.1.3 · Depends on: the docs strip (cleared: landed on `develop` 2026-10-16), release 0.1.3 (fixes ship under the old name first) · Refs: starts the `SERVER_INSTRUCTIONS` split that `site-extras` finishes*
**Seed:** [`issues/rename-to-cluster-mcp.md`](issues/rename-to-cluster-mcp.md) · *status: unshaped*

## Capabilities you don't have shouldn't be tools you see

Today every tool registers unconditionally: a site without Slurm still advertises `sbatch`, `squeue`
and `sacct` to every connecting model, which is an invitation to hallucinate a workflow that cannot
run. The shape is packaging extras — `cluster-mcp[slurm]` — where each extra installs a real
distribution, registered through an entry-point group, with a runtime probe so an installed
capability with nothing behind it advertises nothing, and instructions composed from what loaded.

This is the load-bearing cycle of Part I: it establishes the mechanism that Slurm, LMOD, xdu and the
site extras plug into. It also collides directly with an existing invariant — registration is
currently an import-time side effect driven by a hardcoded list at the bottom of
`tools/__init__.py` (`AGENTS.md` § Invariants §7) — so the design has to replace that contract, not
work around it.

*Horizon: near-term after the rename · Depends on: the rename (the extras are named `cluster-mcp[…]`, and the workspace exists), stack-upgrade (FastMCP 4's providers are a candidate carrier; cleared: landed on `develop` 2026-10-17) · Refs: prerequisite for every capability entry below*
**Seed:** [`issues/capability-extras.md`](issues/capability-extras.md) · *status: unshaped*

## A timed-out command may keep running after its call returns

Every mode now returns a result at the deadline, but whether the command stops is open over SSH
(OpenSSH may ignore the `signal` request, and closing a non-PTY channel stops nothing) and under a
non-root `delegate` server (TERM reaches only the command, through `sudo`). Measuring it needs a
long-running process on a login node, outside the read-only dogfood class, so it waits for
approval. Found by `safe-execution`'s planning research, 2026-10-17.

It sits ahead of the tool families because they lean on bounded calls (a bounded `file_find`, a
`job_wait` considered), which make a timeout a routine outcome on a shared login node rather than
an accident. Its measurement needs Geoffrey's approval first; until it has it, nothing below waits
on it.

*Horizon: mid-term; the measurement needs Geoffrey's approval · Depends on: safe-execution (cleared: landed on `develop` 2026-10-23) · Refs: `AGENTS.md` § Invariants §3; [`spec/safe-execution/`](spec/safe-execution/) PLAN risk 4*
**Seed:** [`issues/timeout-process-cleanup.md`](issues/timeout-process-cleanup.md) · *status: unshaped*

## Bespoke tools first, the shell last

The file tools are too thin — `list_directory` costs a round trip per entry, `read_file` has no
offset, and there is no edit, stat, find or grep — so agents fall back to `run_command`, which no
client can reason about from its name. The cycle builds the `file_*` family (`file_list`,
`file_read`, `file_write`, `file_edit`, `file_stat`, a bounded `file_find`, `file_grep`, plus
transfer), gives every tool honest MCP annotations and a response budget, and keeps `run_command`
as the last resort with `shlex`-based guard rails that steer toward the right tool — documented
as a net for mistakes, not a security boundary. A `--profile read-only` registers only the
read-only tools. Aliases versus a clean break for the old names, and names a client can allow-list
by pattern, are open shaping questions.

*Horizon: mid-term · Depends on: capability-extras (the core distribution it lands in), safe-execution (the argv executor; cleared: landed on `develop` 2026-10-23) · Refs: sets the naming, annotation and budget rules every capability follows; absorbs `list-directory-parsing`*
**Seed:** [`issues/tool-surface.md`](issues/tool-surface.md) · *status: unshaped*

### Absorbed: `list_directory` misreads some entries

`list_directory` parses the column text of `ls -l`, so a name with a newline is split, a symbolic
link's name carries its `-> target`, a name loses its leading spaces, and a device file's
`major, minor` size fails the whole call. Parsing a NUL-terminated form fixes all four.
Pre-existing. Found by `safe-execution`'s planning research, 2026-10-17. The `file_*` family
rewrites `list_directory` as `file_list`, so the parse is fixed once, in the rewrite, rather than
patched in a tool about to be replaced.

*Horizon: with tool-surface · Depends on: — · Refs: absorbed by `tool-surface`; [`spec/safe-execution/`](spec/safe-execution/) research 04 §4, PLAN risk 7*
**Seed:** [`issues/list-directory-parsing.md`](issues/list-directory-parsing.md) · *status: unshaped*

## A job_* family that accepts what a model naturally sends

Found while submitting and watching test jobs through the server: `gpus` must be a string, a single
job id must be wrapped in a list, a purged id is an error rather than an empty result, and five
tools answer "tell me about this job". The `job_*` family — `job_submit` with `args`, `modules` and
typed resources accepting an int or a string, `job_list` and `job_history` accepting a scalar or a
list, a consolidated `job_info`, `job_output`, `job_cancel`, and a bounded `job_wait` considered —
is the first capability distribution, with validation errors that say what to send.

*Horizon: mid-term · Depends on: capability-extras, tool-surface, safe-execution (cleared: landed on `develop` 2026-10-23) · Refs: the first consumer of the extras mechanism*
**Seed:** [`issues/slurm-capability.md`](issues/slurm-capability.md) · *status: unshaped*

## Module systems are the other half of "can I run this"

Every tool here is about jobs; none is about software. On an LMOD cluster an agent cannot discover
what is installed, what versions exist, or what a module load would do to the environment — so it
guesses, and `INSTRUCTIONS.md` has to compensate with prose. `module avail`, `module spider`,
`module list` and `module show` are the missing capability group, and they are the second consumer
of the extras mechanism, which is what proves the mechanism generalizes past one instance. Module
state does not persist between calls, so a `modules=[…]` parameter on `run_command` and job
submission is part of the idea.

*Horizon: mid-term · Depends on: capability-extras (strictly), tool-surface (`run_command`'s parameters) · Refs: `slurm-capability` takes the same `modules=[…]`*
**Seed:** [`issues/lmod-tool-group.md`](issues/lmod-tool-group.md) · *status: unshaped*

## Answer storage questions from an index, not a walk

"What's filling my scratch" and "find every `*.h5` here" end today in a recursive `find` or `du` on
a parallel filesystem — the long metadata walk login-node rules forbid. The xdu capability detects
[xdu](https://github.com/xdu-project/xdu), installs a pinned, checksum-verified release into a
private prefix under `~/.local` only with explicit consent, keeps indexes in scratch rather than
home, indexes large trees through the scheduler and small ones at low priority, and serves
`storage_spaces`, `storage_index`, `file_find` routed to the index, and `storage_usage` — typed
operations only, never caller-written SQL. Byte-sum rollups are an upstream request to xdu.

*Horizon: mid-term · Depends on: capability-extras, tool-surface (`file_find`), slurm-capability (indexing jobs) · Refs: the third consumer of the extras mechanism*
**Seed:** [`issues/xdu-storage.md`](issues/xdu-storage.md) · *status: unshaped*

## A site extra is shorthand plus a point of view

`cluster-mcp[rcac]` should mean "the right capability extras for Purdue, plus Purdue's own tools and
context" — one install target a user can name without knowing that Gautschi runs Slurm and LMOD. That
makes it both a dependency alias and the home for `tools/rcac.py` and the Purdue half of
`SERVER_INSTRUCTIONS`. Getting the boundary right here is what makes `cluster-mcp[alcf]`,
`[ncsa]` and `[tacc]` a matter of contribution rather than redesign, so this cycle should produce a
documented pattern, not just a Purdue special case. It absorbs storage discovery, whose site
implementation needs exactly the hook this cycle establishes.

*Horizon: mid-term · Depends on: capability-extras (strictly); lmod-tool-group and xdu-storage (the extra pulls them in) · Refs: absorbs `generalize-storage-discovery`; sets the pattern other centers copy*
**Seed:** [`issues/site-extras.md`](issues/site-extras.md) · *status: unshaped*

### Absorbed: not every cluster has a Lustre scratch and a myquota

`storage_paths()` parses the output of `myquota`, and scratch discovery shells out to `findscratch` —
both Purdue commands, and neither degrades on a cluster that lacks them. The `rcac://storage` resource
is built on the same assumption. Generalizing means a discovery protocol with a site-provided
implementation and an honest "unknown" rather than a parse failure — and the site hook is what
`site-extras` builds. Found by the harness port, 2026-08-26.

*Horizon: with site-extras · Depends on: — · Refs: absorbed by `site-extras`; `storage-paths-resource-typeerror` leaves the helper seam it needs*
**Seed:** [`issues/generalize-storage-discovery.md`](issues/generalize-storage-discovery.md) · *status: unshaped*

**Release 0.2.0.** The new names, the capability extras and the new tool families reach `main`
together. Legacy `RCAC_*` variables and `rcac://` URIs keep working through the deprecation window;
the distribution names and the repository's home are settled before it ships.

---

# Part II — Hosted

## Run your own server on the cluster, behind a token

A single-user Streamable HTTP server, Jupyter-style: the user starts it on a login node or in a
batch job, and it runs as them. A JWT (`aud` bound to the server URI, `sub` the Unix user, `exp`
required) is printed at startup and written to a `0600` runtime file, or minted ahead with
`token new --lifetime` and passed by environment variable or file — never in argv or a URL. The
default listener is a `0600` unix socket for `ssh -L` forwarding; TCP is opt-in and always
token-gated; `Origin` and `Host` are validated; an idle timeout ends it. Commands then run next to
the data, with no SSH round trip per call.

A shared multi-user endpoint — one server that many users authenticate to — is out of scope for
now.

*Horizon: after 0.2.0 · Depends on: release 0.2.0, stack-upgrade (the HTTP transport; cleared: landed on `develop` 2026-10-17), safe-execution (cleared: landed on `develop` 2026-10-23) · Refs: adds a mode to the execution-mode ladder (`AGENTS.md` § Invariants §4)*
**Seed:** [`issues/https-token-mode.md`](issues/https-token-mode.md) · *status: unshaped*

---

# Part III — Infrastructure

## The absence check blames a stale checkout on a re-added module

`absence_violations` (`tests/test_registry.py`) explains an importable leftover of the removed
documentation modules by file suffix, so an untracked leftover that is neither bytecode nor source
(`.DS_Store`, an interrupted `*.pyc.<n>` temp file) is reported as a re-added module, with no remedy.
The test's verdict is right; only its explanation is wrong. Classifying by git status (ignored or
untracked versus tracked) fixes the message. Found by the `strip-docs-subsystem` review, cycle 3,
2026-10-16.

*Horizon: later · Depends on: — · Refs: split out of `restore-test-coverage`*
**Seed:** [`issues/absence-check-leftover-classification.md`](issues/absence-check-leftover-classification.md) · *status: adopted:absence-check-leftover-classification*

## The surface pin cannot follow a deliberate surface change

`tests/fixtures/served_surface.json` is the surface `3d010ec` served, taken on FastMCP 2, and
`tests/test_surface.py` rejects a fixture re-taken on FastMCP 4 because it carries the new stack's
schema metadata. After a deliberate change to a tool, an argument or the instructions there is no
FastMCP 2 build of the new surface to snapshot, so the only way to update the pin is to hand-edit
it, unchecked. A maintained snapshot command on the current stack, with a one-time re-baseline that
retires the tolerated-difference list, settles it. Found by `stack-upgrade` P3, 2026-10-17.

*Horizon: before the next cycle that changes the served surface · Depends on: — · Refs: `AGENTS.md` § Invariants §9 says the question is open; `safe-execution` changed the surface first and hand-edited its deltas into the fixture (`AGENTS.md` § Testing); next needed by `tool-surface` or `agents-d-instructions`, whichever lands first*
**Seed:** [`issues/surface-fixture-after-a-surface-change.md`](issues/surface-fixture-after-a-surface-change.md) · *status: unshaped*

## Nothing would catch the next silently wrong annotation

`auth.py`'s `AUTH_MODES` carried a slice inside its annotation's subscript for months:
`typing.get_type_hints` and eager evaluation both accepted it, in a high-blast-radius module, and it
was fixed only because `stack-upgrade` happened to rewrite the file (R7). Nothing in the suite or
the gate checks any other annotation. A gate step or standing test that type-checks `src/`, or at
least rejects an annotation in `src/rcac_mcp` that resolves to a non-type, closes it; the tool is
the plan's call. Found by `stack-upgrade`'s GOAL non-goals, carried at retirement, 2026-10-17.

*Horizon: later · Depends on: — · Refs: runs where the gate runs, so CI picks it up through its mirror of the gate (`ci-workflow`; cleared: landed on `develop` 2026-10-18); [`spec/stack-upgrade/`](spec/stack-upgrade/) pins the one annotation*
**Seed:** [`issues/annotation-check.md`](issues/annotation-check.md) · *status: unshaped*

## A startup file's background job can delay a result, or reach it over SSH

Startup output never reaches a result, but a background process a startup file leaves running holds
the call open until it exits or the timeout, and over SSH a job the remote login shell's own rc file
starts can write into the channel after the sentinel. Neither is separable without a PTY or a
server-owned session. Found by `safe-execution`'s focused review, 2026-10-17.

- 2026-10-18: the first case is fixed in `safe-execution` (a bounded grace once the command has
  exited, in all three modes); the SSH rc-file case remains, stated as the documented exception.
- 2026-10-18 (review cycle 2): the first case now stops once nothing has arrived for the grace,
  not a fixed grace after the exit, and the result carries a note saying so.
- 2026-10-22: the first case has no timing rule any more: the call returns at the command's end
  records on both streams; the SSH rc-file case remains for what such a job writes before them.

It stays here because no release waits on it: neither case shows with the clusters' default
startup files (`safe-execution` research 01), and the fix is a different launch (a PTY, or a
session the server owns), larger than a small cycle.

*Horizon: later · Depends on: safe-execution (cleared: landed on `develop` 2026-10-23) · Refs: [`spec/safe-execution/`](spec/safe-execution/) PLAN §2.2a, risk 5*
**Seed:** [`issues/startup-background-jobs.md`](issues/startup-background-jobs.md) · *status: unshaped*

## The `integration` marker says it drives the CLI, and not every integration test does

`pyproject.toml` describes `integration` as "drives the installed CLI", but `test_docs_modules_absent`
and the git-backed absence-message controls in `tests/test_registry.py` are `integration` only
because they start git, which the unit guard forbids in a `unit` test. In practice the marker means
"not `unit`"; its description states a narrower rule, and `AGENTS.md` § Testing notes the mismatch
instead of fixing it. Rewording both markers to the line the unit guard draws settles it. Found by
the `absence-check-leftover-classification` plan, 2026-10-19.

*Horizon: later · Depends on: — · Refs: [`spec/absence-check-leftover-classification/`](spec/absence-check-leftover-classification/) (PLAN §5)*
**Seed:** [`issues/integration-marker-wording.md`](issues/integration-marker-wording.md) · *status: unshaped*

---

# Settled questions

Deferrals closed **without** shipping — `declined` (considered and not taken on as debt) and
`accepted-behaviour` (reported as a defect, judged intended). They keep their `issues/` files because
nothing else in the repository records that the question was asked. Shipped work leaves no entry
here: `spec/{slug}/` holds that account and the code itself refutes a re-filing.

- **The factory has no front door** — *declined 2026-10-16.* The factory's front door is the
  operator's KB, where its method and skills now live; this repository carries only its profile
  (`AGENTS.md` § Factory). Record: [`issues/factory-onboarding-page.md`](issues/factory-onboarding-page.md).

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
