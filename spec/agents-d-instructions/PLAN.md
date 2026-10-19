# PLAN — Serve /etc/agents.d as the server's instructions

> **Status:** Draft for review · **Last updated:** 2026-10-19
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

At startup, before the server is built, `MCPServerApp.run()` reads `/etc/agents.d` once through a
new leaf module, `src/rcac_mcp/site.py`: over the shared `SSHExecutor` with one fixed script in
`ssh` mode, in this process on the server's host in `local` and `delegate` mode, inside a daemon
thread abandoned at a 10 s bound. The result, a frozen `SiteContext`, goes to
`create_mcp_server(…, site=…, budget=…)`, which composes the served instructions once (a compact
base, then `## Site context (/etc/agents.d)` with whole files in byte order of their names, packed
first-fit under a UTF-16 budget defaulting to 4,096, and a note naming what was left out) and
hands the same object to a new no-argument `cluster_context` tool and to `rcac://context`. Nothing
reads the files again; the hostname-keyed cache is deleted. Four phases: the reader and composer,
the startup path and instructions, the tool and resource, then documentation and mutants. It
builds on `safe-execution`'s argv `run()` and fixed-script registry, and on nothing else from it.

## 2. Design

### 2.1 `src/rcac_mcp/site.py` (new, a leaf)

- **Constants.** `CONVENTION = '/etc/agents.d'` (the path every text names), `AGENTS_D = CONVENTION`
  (where the read looks), `EXCLUDED = 'AGENTS.md'`, `READ_TIMEOUT = 10.0` (s),
  `DEFAULT_BUDGET = 4096`, `DETAIL_LIMIT = 80`, `HEADING = '## Site context (/etc/agents.d)'`.
  `AGENTS_D` and `READ_TIMEOUT` are read at call time (`directory`/`timeout` default to `None`),
  which is the test seam: tests and gates point the read at a planted directory, and no served
  text changes with it. There is no environment variable or flag for the path (C1).
- **`SiteFile(name, text)`**, frozen; `block` is `<!-- Source: {name} -->\n{text}`. `text` is the
  file decoded as UTF-8 (invalid bytes become U+FFFD) with **trailing** whitespace removed;
  leading whitespace (an indented first line) is kept, as the content was read. `name` is the
  entry's bytes decoded the same way (`os.fsencode(name).decode('utf-8', 'replace')`), so a
  non-UTF-8 name never puts a lone surrogate into the instructions (it would break serialisation)
  and both paths agree.
- **`SiteContext(files, reason, failed)`**, frozen. Constructors; each `reason` is one of exactly
  four lines, naming `where` (`the cluster` or `this host`, never a host name):
  - `found(files, where)`: drops whitespace-only files (not eligible: they carry no rule), sorts by
    the UTF-8 bytes of the names (`LC_ALL=C sort`); with nothing left,
    `No site rules are served: /etc/agents.d on {where} holds no rules (no non-empty *.md file but AGENTS.md).`
  - `missing(where)`: `No site rules are served: /etc/agents.d does not exist on {where}.`
  - `unreadable(where, detail)`:
    `No site rules are served: /etc/agents.d on {where} could not be read ({detail}).`, the detail
    cut to `DETAIL_LIMIT` characters (`…` last), so a long file name cannot grow it;
  - `timed_out(where, timeout)`:
    `No site rules are served: reading /etc/agents.d on {where} did not finish within {timeout:g} s.`
  - The last two set `failed`.
  - `text`, what the tool and the resource return: the blocks joined by a blank line; else
    `reason` when `failed`; else `''` (§8's contract for a missing or empty directory, C5).
- **Eligibility**, identical on both paths: the name matches `*.md` case-sensitively and is not
  exactly `AGENTS.md` (`agents.md` is served), and the entry is a regular file, not followed
  through a symlink (today's `find -maxdepth 1 -name "*.md" -type f`; dotfiles and a file named
  just `.md` included, as `find -name` includes them).
- **`read_local(where='this host', directory=None)`**: `os.scandir`; `FileNotFoundError` →
  `missing`; any other `OSError` on the directory or an eligible file → `unreadable` naming the
  file and `strerror` in lower case. Runs as the server process: in `delegate` mode, as the
  server, never as a user (C6).
- **`READ_SITE`**, a registered fixed script (`scripts.fixed`), run as
  `script_argv(READ_SITE, 'cluster_context', directory)`: the directory is `"$1"`, never script
  text. It unsets `base64 tr test` itself (a profile could export functions by those names, and
  `launch.FIXED_RESET` covers only its own list; widening that human-gated constant is not this
  cycle's), prints `missing`, `notdir` or `unreadable` for those states, else, looping over
  `./*.md ./.*.md ./.md`, one line per regular entry, `file <base64 name> <base64 content>` or `unreadable-file <base64 name>`, then
  `end <count>`. Every variable byte travels as base64, so no name or content can forge a line.
- **`parse_remote(stdout, where)`**: anything but a well-formed answer whose `end` count matches is
  `unreadable` ("incomplete" or "malformed"), so an answer cut short never reads as fewer files.
  Eligibility and order are applied again in Python. **`read_remote(executor, where, directory,
  timeout)`** runs it with `timeout`: `TIMEOUT_EXIT_CODE` → `timed_out`, another nonzero exit →
  `unreadable`.
- **`read_bounded(read, where, timeout=None)`**: runs `read` on a daemon thread and waits at most
  `READ_TIMEOUT`; past it, `timed_out`, and the thread is abandoned (it cannot hold the process at
  exit). Any exception `read` raises becomes `unreadable(where, <exception type>)`. `run(timeout=)`
  alone is no bound over ssh: `open_session` waits up to 30 s and a reconnect backs off outside it
  ([`04`](research/04-executor-and-suite.md) §1).
- **`units(text)`**: UTF-16 code units, `len(text.encode('utf-16-le', 'surrogatepass')) // 2`, the
  unit Claude Code measures in (`String.length`); never fewer than code points
  ([`02`](research/02-client-truncation.md)).
- **`register(server, site)` / `site_of(server)`**: a `WeakKeyDictionary` keyed by the server
  object, written once in `create_mcp_server`, read by the tool and the resource through FastMCP's
  `get_server()`. Not per host, not per user, not a cache (deviation table).

### 2.2 Composition (`compose(base, site, budget)`, `minimum_budget(base, site)`)

`head = base + '\n\n' + HEADING + '\n\n'`.

1. No files: `head + site.reason`.
2. All files fit (`units(head + blocks joined by '\n\n') <= budget`): that, with no note.
3. Otherwise walk the files in order; include a file when `head`, the included blocks, this one,
   and the note naming **every** file not yet included still fit. The note is
   `Left out here for length: a.md, b.md. Call the \`cluster_context\` tool for the full site
   context.` A later file that fits still goes in. The final note names fewer files than any
   note checked on the way, so the result fits.
4. `minimum_budget` is never less than the **floor**: `head` plus the longest of the four reasons
   (with a `DETAIL_LIMIT`-long detail), whatever the read found. With files it is the larger of
   the floor and the smaller of the whole text and `head` + the note naming every file. So a
   budget a good read accepts also holds any failed read's line, and the server starts whatever
   the read finds (R8 within R4). Below it `compose` raises `BudgetError(ValueError)`:
   `instructions budget N is too small: … need M characters; set --instructions-budget or
   RCAC_INSTRUCTIONS_BUDGET to at least M`.

Files go in whole (C3 leaves whole-file versus by-section to the plan): no file is ever cut
short, so R5's "cut short" set is always empty and the note names the left-out ones. The order is
the convention's own `sort` order, so an administrator who wants a file first names it so
(`00-policies.md`), and every other conforming client sees the same order.

### 2.3 Startup (`src/rcac_mcp/__init__.py`, high blast radius)

- New flag `--instructions-budget CHARS` (`type=int`, default `None`) in `APP_USAGE`/`APP_HELP`,
  and `RCAC_INSTRUCTIONS_BUDGET` (project's current naming, C7) under Environment Variables.
- `_resolve_budget()`: flag, else the variable (a non-integer is a `ValueError` naming it), else
  `DEFAULT_BUDGET`; ≤ 0 is a `ValueError`. Both exit `exit_status.bad_argument` (2).
- `_read_site(exec_mode, executor)`: `ssh` → `read_bounded(lambda: read_remote(executor),
  'the cluster')`; any other mode → `read_bounded(read_local, 'this host')`. The branch is on
  `'ssh'` only: no second `if exec_mode == 'delegate':` line (a mutant anchors the first), no new
  rung, no change to `_resolve_exec_mode`.
- `run()`: after the executor and middleware exist (so ssh has connected; a connect failure is
  `safe-execution`'s startup failure, before any read), inside the existing `try`:
  `budget = self._resolve_budget()`, `site = self._read_site(exec_mode, executor)`,
  `mcp = create_mcp_server(self.auth, middlewares=middlewares, site=site, budget=budget)`. A
  `BudgetError` is a `ValueError`: exit 2 with its message on stderr, the executor closed by the
  existing `finally`.

### 2.4 Server (`src/rcac_mcp/server.py`, high blast radius)

- `create_mcp_server(auth_mode=None, middlewares=None, *, site, budget=DEFAULT_BUDGET)`: `site` is
  required and keyword-only, so no caller can build a server that silently skips the site.
  `instructions=compose(SERVER_INSTRUCTIONS, site, budget)`; `register(server, site)` before any
  tool or resource is added. FastMCP serves the string verbatim in both eras and copies it per
  transport ([`01`](research/01-fastmcp-instructions.md) §1); nothing sets it later.
- `DiscoverResult` caching is left at FastMCP's default (`ttlMs=0`, `cacheScope='private'`): the
  constructor's hint would apply to every cacheable method, including per-user reads
  ([`01`](research/01-fastmcp-instructions.md) §2).
- **`SERVER_INSTRUCTIONS` is rewritten compact** (prototype: 1,333 units, from 2,085): one intro
  sentence ("prefer a dedicated tool"; "the site context section after this one carries the
  cluster's rules"), then one `- name: short description` line per tool, grouped (shell and files;
  site and storage, with `cluster_context`; jobs; cluster status), and one line naming both
  resources. The Purdue storage prose and `findscratch` go: the site's own files carry them, and
  the base must leave room in 4,096 (deviation table). It stays within 1,500 units (a P3 test).

### 2.5 Tool and resource

- `src/rcac_mcp/tools/context.py` (new module, appended to the bottom-of-file import list, §7):
  `@mcp_tool def cluster_context() -> str` returns `site_of(get_server()).text`. No argument: a
  `topic` would add the literal-args and empty-optional matrices for little gain, since the whole
  site is about 12K (C4, C10; [`04`](research/04-executor-and-suite.md) §4). Docstring in the
  `Returns:`/`Examples:` shape; says it runs nothing and what `''` and a "No site rules" line mean.
  In `delegate` mode the middleware still requires a mapped identity before the body runs, as for
  every tool (§5: an identity absent from the map is refused, never given a default user); the
  instructions and the resource reach an authenticated but unmapped user anyway
  ([`01`](research/01-fastmcp-instructions.md) §4). This design reads R9's "every authenticated
  user" and R7's "exactly what `cluster_context` returns" as applying to users the map admits:
  **a GOAL clarification for the foreman** (§ 5). Tests and the serve gate pin the unmapped
  user's behaviour as it is: instructions and resource identical, the tool refused.
- `resources.py`: `_cluster_context_resource()` returns `site_of(get_server()).text`; the
  description becomes `The site context read from /etc/agents.d at startup: every *.md file but
  AGENTS.md`. `_cluster_context_cache`, `load_cluster_context`, `get_cached_context`,
  `clear_context_cache` and `LIST_CONTEXT` are deleted; `rcac://storage` is untouched (non-goal).

### 2.6 Tests (each new test carries `unit` or `integration`)

- `tests/test_site.py`: `read_violations`, `compose_violations`. The eligible set and byte order on
  a layout that includes upper case and digits (`Z.md`, `10-first.md`), a dotfile, `.md`, a space,
  `-n.md`, `end 0.md`, a symlink, a directory, a FIFO, a whitespace-only file, `AGENTS.md`,
  `Upper.MD`; an indented first line kept; a non-UTF-8 name (on Linux; elsewhere the row asserts
  the file system's refusal, since the suite forbids skips: `test_only_need_shell_skips`);
  missing/empty/only-AGENTS/not-a-directory/unreadable dir/file (the suite runs as a non-root user,
  as CI does);
  remote answers (short count, no `end`, malformed, unknown line, timeout, nonzero exit); the
  script run for real under `bin/sandbox.sh` against planted dirs equal to `read_local`
  (integration); the bound within 0.2 s of it; the composition at every budget 0–1,200 for several
  layouts; a budget that serves a good read serves each failed one; UTF-16.
- `tests/test_context.py`: tool and resource on bare and shared servers, every site state, both
  eras, executor untouched; two delegate users over HTTP with a stub `DelegatingExecutor` whose
  instances report one hostname, identical text and instructions, no `sudo` (R9); an unmapped
  user's resource and instructions identical, its tool call refused; served instructions equal
  `compose`; nothing reads after construction, by the named readers or by anything else: the
  directory is rewritten and grown mid-session, and every answer still equals the first.
- `tests/test_site_startup.py`: `MCPServerApp.main` with the server stubbed: one read, remote with
  the `SSHExecutor` in ssh mode, local in `local`/`delegate`; a hung read, local **and ssh**, and
  one at the smallest budget a good read accepts, returns within 0.5 s of the bound and starts;
  the budget's default, flag, variable, precedence and refusals; the sandboxed CLI's exit 2 naming
  the minimum (integration).
- Rewrites ([`04`](research/04-executor-and-suite.md) §3): the harness gains `SITE`
  (`SiteContext.missing('this host')`), a `site=` keyword on each builder, a `read_resource` verb and
  `Listed.instructions`; `test_instructions` checks the base and that the served text is
  `compose(SERVER_INSTRUCTIONS, SITE)`; `test_protocol` compares with the composed text;
  `test_transports` requires each transport and era to serve `compose(SERVER_INSTRUCTIONS,
  read_local())` for the host it runs on; `test_exec_mode`'s stub takes `**kwargs`; `test_tools` and
  `test_no_executor` exempt an `EXECUTORLESS = {'cluster_context'}` set (with a coverage assertion),
  and the cache test goes; `EXPECTED_TOOLS` gains `cluster_context`; every direct
  `create_mcp_server('none')` passes `site=SITE`.
- **Fixture:** the named deltas `instructions`, `tools.cluster_context` (in 2.x shape: `myquota`'s
  schemas, the docstring) and `resources.rcac://context.description`, applied and checked by
  `verify/fixture_deltas.py` (C10), listed in the commit body, `test_surface.py`'s docstring and
  § Testing.
- **Mutants** (`tests/mutants.py`, P4): re-anchor `instructions-dropped` (P2), and add
  `site-agents-md-included`, `site-byte-order`, `site-budget-overflow`, `site-read-unbounded`
  (factor 2), `startup-ssh-unbounded` (`__init__.py`'s ssh branch unwrapped), `context-rereads`
  (`site_of` re-reads), `context-through-executor`.

### 2.7 Documentation (R10)

`AGENTS.md` §8 (read once at startup as the server, the exclusion, the order, the states and what
the tool and resource return, the per-server holder, the retired cache and the read defect), §9
(the instructions are the base plus the site context; §9's rules govern the base), § Configuration
(the variable, the flag, the unit, the default), § Testing (the three modules, the deltas), the
Footguns list (`resources.py` entry replaced by `site.py`); `README.md` (tool inventory; the site
context, its order (byte order of names; name a file `00-…` to put it first) and the `AGENTS.md`
exclusion, and how to raise both limits: `--instructions-budget 16384` and
`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH=16384`, in the shell or `settings.json` `env`, Claude Code
2.1.280+). `SERVER_INSTRUCTIONS` names `cluster_context` in the commit that adds the tool (§9).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `read_local` / `READ_SITE` + `parse_remote` (eligibility, `AGENTS.md` skipped, headers); `_read_site` by mode; read before `create_mcp_server`, which never reads; cache deleted (P1, P2, P3) |
| R2   | `compose` → `FastMCP(instructions=…)`, served verbatim in `initialize` and `server/discover` (P2) |
| R3   | byte order of names, in `SiteContext.found` and again in `parse_remote`; stated in §8 and README (P1, P3) |
| R4   | `units`, `compose`, `minimum_budget`, `BudgetError`; `--instructions-budget` / `RCAC_INSTRUCTIONS_BUDGET`, default 4,096; base never cut; exit 2 naming the minimum (P1, P2) |
| R5   | the note naming every left-out file and `cluster_context` (P1, served P2) |
| R6   | `cluster_context()` returns `SiteContext.text`, never budgeted, in every mode (P3) |
| R7   | `rcac://context` returns the same `SiteContext.text` through `site_of(get_server())`, no executor (P3) |
| R8   | `missing` / `found([])` / `unreadable` / `timed_out`; `read_bounded`'s 10 s; `text` is `''` or the line (P1, P2) |
| R9   | one read as the server; one `SiteContext` per server; stub-delegate test with one hostname (P3); mapped users, pending the clarification in § 5 |
| R10  | §8, §9, § Configuration, § Testing, README, `SERVER_INSTRUCTIONS` (P3, P4) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (intent) and again on this design. Sections touched: §1, §3, §4, §5, §7,
§8, §9, § Conventions; high-blast-radius paths `server.py` and `__init__.py` (so trigger 2).
`context.py`, `middleware.py`, `executor/*`, `auth.py`, `token.py`, `bin/sandbox.sh` are not
edited.

- §1 — The tool and the resource never call `get_executor()` or `set_executor()`; no middleware is
  added; the per-server holder is written once at construction, before any request, and holds
  host-wide text identical for every user, so no request can see another's state.
- §2 — No Protocol member changes; the read uses `run()` as `safe-execution` defines it.
- §3 — `TIMEOUT_EXIT_CODE` maps to the timeout line; no new sentinel; `hostname` keys nothing.
- §4 — `_resolve_exec_mode` and the ladder are untouched; `_read_site` branches on `ssh` only.
- §5 — In `delegate` mode nothing runs under `sudo`: the read is the server's own; the tool runs
  nothing; claim precedence and the user-map refusal are unchanged.
- §7 — `tools/context.py` joins the import list; the docstring is served whole.
- §8 — Path, `maxdepth 1` glob, regular files only, `sort` order and header kept; skipping
  `AGENTS.md` is C1's documented narrowing; "sort" is pinned to byte order (deviation). The cache
  §8 forbids building on is deleted, and the resource read no longer needs an executor.
- §9 — Tool, `SERVER_INSTRUCTIONS`, README and fixture change together (P3); the fixture follows
  by named deltas (C10).
- § Conventions — flag and `APP_HELP` in one commit; `compose.yml`'s argv unchanged; comments carry
  no spec ids; every test is marked; gates name test ids; CLI drives go through `bin/sandbox.sh`.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `SERVER_INSTRUCTIONS` rewritten to ~1.3K, dropping the Purdue storage prose and `findscratch` | At 2,085 units the base plus the heading and the note leaves about 1.8K of 4,096; no Purdue topic file (1.6–3.3K) would fit, so the default would serve no site rule ([`03`](research/03-agents-d-inventory.md)) | Keeping the old base: the default serves the note alone; the storage prose is Purdue-specific and the site's `filesystems.md` carries it |
| `sort` pinned to byte order (`LC_ALL=C`) | R3 needs byte-identical output; `sort` under a login locale collates differently by host | Locale `sort`: not deterministic across hosts; identical on every cluster probed (lowercase ASCII names) |
| A module-level `WeakKeyDictionary` keyed by server object (`site.register`) | Tools register at import time (§7), but the text belongs to one server; tests build many servers per process | A single module value: wrong with two servers in one process. Building the tool per server outside `TOOL_REGISTRY`: breaks the registry contract `test_registry` pins. FastMCP lifespan state: more surface for one string |
| `READ_SITE` unsets `base64 tr test` itself | `FIXED_RESET` (in `launch.py`, `safe-execution`'s, human-gated) does not cover them | Widening `FIXED_RESET`: an edit to another cycle's human-gated contract |

## 4. Rabbit holes (resolved)

- **What clients do with instructions** → Claude Code cuts at 4,096 UTF-16 units and appends
  `… [truncated]`; its header lines don't count; Zed drops instructions; Codex, Goose, Gemini CLI
  pass them uncut; spec sets no limit ([`02`](research/02-client-truncation.md)). Default 4,096,
  measured in UTF-16 units.
- **FastMCP 4.1** serves the constructor string verbatim, per transport copy, in both eras; empty
  resource and tool results work; no middleware touches a resource read
  ([`01`](research/01-fastmcp-instructions.md)).
- **What the clusters hold** → five topic files of 1.6–3.3K plus an `AGENTS.md` the size of all
  five, 10.4–11.8K bytes per site, all regular files; Gilbreth unprobed (host key)
  ([`03`](research/03-agents-d-inventory.md)).
- **Bound and executor** → the ssh timeout is not a hard bound, so a daemon thread; the SSH
  executor is safe to call from a helper thread; tests and suites to exempt or rewrite; no
  surviving fixture script, so this cycle commits its own
  ([`04`](research/04-executor-and-suite.md)).
- **Found by dry-running the drafted gates on a prototype** (detached worktree
  `.worktrees/agents-d-instructions-prototype`, built on `safe-execution` `4f4e43e`, one commit per
  phase):
  - `minimum_budget` first returned the base-plus-note size, which can exceed the whole text: a
    budget that holds everything was refused. Fixed by the whole-fit step (§2.2, rule 2) and a
    minimum that is the smaller of the two.
  - The `site-byte-order` mutant survived a lowercase-only layout; the layouts now carry `Z.md` and
    `10-first.md`.
  - `bin/sandbox.sh` blanks the auth environment, so the serve gate passes `JWT_SECRET` and the
    user map through `env` inside it.
  - Readers bound their defaults at definition time, so the gate's planted directory never
    reached them; `AGENTS_D` and `READ_TIMEOUT` are now read at call time.
- **Adversarial review** → § 4.1.

### 4.1 Adversarial design review

A fresh reviewer took the GOAL, this design and the drafted gates, copied the prototype into its own
detached worktree (removed after), planted breakages and ran the gates on each phase's tree. Ten
findings; each is folded, and the gates were re-run on rebuilt phase trees (§ 6).

| # | Finding | Fold |
|---|---|---|
| 1 HIGH | A budget a good read accepts made a timed-out or unreadable read refuse to start: the failure line can be longer than the whole text | `minimum_budget`'s floor holds the longest reason; reasons capped at `DETAIL_LIMIT`; reason texts name the convention path, not the read location; a startup test and a serve-gate row start a hung read at the good minimum |
| 2 HIGH | The ssh read's bound was ungated: unwrapping `read_bounded` in `_read_site`'s ssh branch stayed green | ssh row in the hung-read startup test; `startup-ssh-unbounded` mutant |
| 3 HIGH | R1's "never read again" was checked only through the patched readers: a re-read through `os.scandir` in `site_of` stayed green | `test_a_changed_directory_changes_nothing_served`; the serve gate rewrites and grows the directory mid-session and between delegate users; the shim logs any listing of it; `context-rereads` mutant |
| 4 MED | Bound checks allowed about 3× slack | site_gate < bound + 0.3 s; tests < bound + 0.2/0.5 s; serve gate < baseline + 13 s; mutant factor 2 |
| 5 MED | A non-UTF-8 name became a lone surrogate and broke every handshake on Linux; local and remote disagreed | names decoded from bytes with U+FFFD; a row in the test (asserting the refusal off Linux) and a Linux-only row in site_gate |
| 6 MED | An authenticated identity absent from the delegate map is refused `cluster_context` while reading the resource and instructions: R9/R7 as worded | GOAL clarification (§ 5); behaviour pinned in a test and the serve gate |
| 7 MED | docs_gate passed "some order" and reworded, line-wrapped defect bullets | §8 and README must state byte order; whitespace-flattened forbidden patterns; Footguns checked |
| 8 LOW | A file named `.md` was read locally, not remotely | `./.md` in the script's loop; in both layouts |
| 9 LOW | Stripping, blank files and lower-case `agents.md` were implicit | stated in § 2.1 and §8: trailing whitespace only; whitespace-only not eligible; only `AGENTS.md` exactly skipped |
| 10 LOW | No size cap; remote detail always "permission denied"; chmod rows fail as root; FastMCP's banner contacts PyPI | risk below; `notdir` state; the gates' chmod rows skip as root; the gate sets `FASTMCP_CHECK_FOR_UPDATES=off` |

## 5. Risks & open questions

- **`safe-execution` must land first (C9).** Assumed from it, and nothing else: `Executor.run`
  takes an argv and `timeout`, returns `TIMEOUT_EXIT_CODE` on a timeout and raises nothing on one;
  `scripts.fixed`/`script_argv`/`FIXED_SCRIPTS`/`script_of`; `launch.FIXED_RESET` exists; the
  harness's `RecordingExecutor`, `executes`/`launches` fences and `tests/test_tools.py`'s
  `ToolCase`/`PASSES_A_SCRIPT` shape; `startup.py`'s connect-failure path runs before the read; its
  fixture deltas are already in `served_surface.json`. If its result-completeness mechanism changes
  (an end-of-output marker), `READ_SITE`'s own `end <count>` still detects a short answer. If its
  ssh content moves to SFTP, `READ_SITE` uses only `run()` and is unaffected. Its `resources.py`
  edits (`LIST_CONTEXT`, `cat --`) are deleted here, so a rebase conflicts there by design.
- **GOAL clarification for the foreman (R7, R9).** In `delegate` mode an authenticated identity
  absent from the user map is refused every tool call by `AuthExecutorMiddleware` before the body
  runs (§5), `cluster_context` included, while it still receives the instructions and can read
  `rcac://context`. Proposed reading: R9's "every authenticated user" and R7's "what
  `cluster_context` returns" mean users the map admits. The alternative, exempting
  `cluster_context` from the map check, edits `middleware.py` (human-gated, §5) so an unmapped
  identity reaches a tool body; this plan does not, unless the foreman rules otherwise.
- **No size cap on a file.** A very large file (tens of MB) can push the ssh read past its bound,
  and then no site rule is served; administrative markdown is a few KB. Recorded, not capped.
- **FastMCP's banner contacts PyPI at startup** unless `FASTMCP_CHECK_FOR_UPDATES=off`
  (pre-existing, every mode). Outside this GOAL; noted for the foreman.
- **Order is name order.** On RCAC's clusters that puts `filesystems.md` first and `policies.md`
  third, so at 4,096 the default serves `filesystems.md` and names the rest. Putting the
  prohibitions first is an administrator's rename (`00-policies.md`), which also reorders every
  other client's view. **Open for the maintainer:** whether RCAC's administrators should be asked.
- **Claude Code before 2.1.296 cuts at 2,048**: the 1.3K base survives, the site section mostly
  does not; README says to upgrade or set the variable.
- **An abandoned ssh read** may hold one channel open up to 30 s after startup; Paramiko serialises
  channel opens, so later tool calls only wait for their own.
- **The minimum budget depends on the site's files** (their names enter the note), so one budget
  can start on one cluster and be refused on another; the message names the number. It never
  depends on whether the read succeeded (§ 4.1, finding 1).
- **File names enter the instructions verbatim** (header, note); administrators control them.
- **`test_transports` reads the host's real `/etc/agents.d`**: its expectation is computed on the
  same host, so it is self-consistent; the ubuntu runners are assumed to lack the directory.
- **The fixture method is still unsettled** (`surface-fixture-after-a-surface-change`); this cycle
  uses C10's scripted deltas.
- `resource-reads-without-executor`'s ROADMAP entry narrows to `rcac://storage` at retirement, not
  in this cycle (C10).

## 6. Verification strategy

Each phase's gate pairs the build's named tests (both protocol eras where a client is involved)
with a script written now, under `verify/`, whose oracle is independent of the build:

- `verify/site_gate.py` (P1): plants layouts, runs `read_local` and `READ_SITE` for real under
  `bin/sandbox.sh`, compares both with an oracle reading the directory itself, and compares
  `compose` with a re-implementation of §2.2 at every budget 0–1,299 on four layouts.
- `verify/serve_gate.py instructions` (P2) and `context` (P3): the real CLI through
  `verify/serve_shim.py` (which only points `site.AGENTS_D` at a planted directory and logs each
  read), over stdio in both eras and over HTTP in `delegate` mode with two minted users: served
  instructions against the oracle at the default, a flag and an environment budget; exit 2 naming
  the minimum; the missing, empty and unreadable lines; a read that never finishes still answered
  with the timeout line; tool and resource text; exactly one read per server.
- `verify/fixture_deltas.py check` (P2, P3, P4): the fixture differs from the merge base only in
  the named deltas, each exactly as `apply` writes it.
- `verify/docs_gate.py docs|mutants|all` (P4): R10's sections (the order stated; the retired cache
  and read failure gone, flattened so a line wrap can't hide them; the Footguns entry), the retired
  names absent from `src/`, `--help`, the seeds, the deferral ledger, and this cycle's mutants
  killed.

Each phase's gate is `verify/pN.sh`, which states its footprint. All four were dry-run on a
prototype tree cut back to that phase (`c9765a9`, `7ee7c35`, `ddcea7f`, `4c7a940`, on
`safe-execution` `4f4e43e`), and each later phase's gate on the tree before it, where it must
fail. In the gates (not the suite) the chmod rows skip as root, and the non-UTF-8 row runs on
Linux only. The first dry-run of the folded gates went red at P1 on `test_only_need_shell_skips`:
the suite forbids `skipif`, so the Linux-only test now asserts the refusal elsewhere.

The last phase also runs the profile's full `verify`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
