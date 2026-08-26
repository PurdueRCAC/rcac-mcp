# AGENTS.md

Guidance for coding agents (Claude Code and others) working in this repository.
`CLAUDE.md` is a symlink to this file — edit `AGENTS.md`, never a separate copy. (`.claude`
is likewise a symlink to `.agents`, so Claude Code discovers the factory skills and settings
through it.)

This document is the operating manual: the architecture, the load-bearing invariants, and
the process rules an autonomous agent needs to make correct, safe changes here without
rediscovering them each session. When something below disagrees with the code, **the code is
ground truth — fix this file.**

---

## Project

An **MCP server for HPC clusters**. It exposes shell execution, filesystem access, Slurm job
management, and site-specific tooling to AI agents over the Model Context Protocol, so a coding
agent can actually work on a cluster instead of guessing about one. The package is `rcac_mcp`; the
console entry point (`pyproject.toml [project.scripts]`) is **`rcac-mcp`** → `rcac_mcp:main`, and
`python -m rcac_mcp` works via `__main__.py`.

The server is built on **FastMCP 2.x** with a **cmdkit** CLI, and speaks three transports (`stdio`,
`sse`, `http`) against three execution backends (`ssh`, `local`, `delegate`).

**The primary supported deployment is local + ephemeral over SSH keys:** the user runs the server on
their own machine in `stdio` mode, and it executes on the cluster over their existing
`~/.ssh/config`. `SECURITY.md` documents all three modes; treat that one as the recommended path and
the others as real but secondary.

### Direction — this is becoming `cluster-mcp`

**Nothing in this section is true yet.** It is the agreed trajectory, recorded so a change can be
made *with the grain* rather than against it. Do not code against it, cite it as an invariant, or
"prepare" for it speculatively — the cycles in `ROADMAP.md` are how it lands.

- **Rename and generalize `rcac-mcp` → `cluster-mcp`.** Nothing here should assume Purdue RCAC.
- **Capabilities become packaging extras.** `cluster-mcp[slurm,lmod]` installs the schedulers and
  module systems you actually have; a site extra like `cluster-mcp[rcac]` is shorthand for the right
  capability extras *plus* that center's specific tools and context. Others follow
  (`cluster-mcp[alcf]`, `[ncsa]`, `[tacc]`).
- **The long game is any HPC cluster** — not necessarily Slurm, not necessarily LMOD, not necessarily
  a Lustre scratch. Today's `tools/slurm.py` and `tools/rcac.py` are the *first* implementations of
  what become pluggable capability groups, not the shape of the final thing.
- **`/etc/agents.d/*.md` stays and gets evangelized.** It is a deliberate cross-vendor convention —
  administrators drop markdown there, any conforming client picks it up — and the intent is to get
  other centers to adopt it. See "Resources" below; it is an invariant *today*, not just direction.

## Environment & working rules

- **`del`, not `rm`** — use `del <path>` (reversible trash; directories work with no flags). A
  deleted `issues/` seed is otherwise unrecoverable outside git history.
- **Commit only when explicitly asked.** When you do: branch off **`main`**. This is a
  single-long-lived-branch repo — there is no `develop`, and `main` is both the working branch and
  the default branch, so a merged PR is immediately what `uvx git+https://…` installs.
- **Squash-only merges.** Commit subjects follow `[category] Imperative summary`. Categories in use:
  `feature`, `fix`, `docs`, `refactor`, `test`, `harness` (the `.agents/` factory), `release`
  (version bumps), `ci`. The set is **not closed** — coin a new lowercase category when one genuinely
  fits. **Do not add a `Co-Authored-By:` trailer** — a deliberate repo convention (we don't want the
  authoring model recorded on every line of the commit log). End PR **bodies** with the Claude Code
  generation line.
- **Version is single-sourced from `pyproject.toml`** (`rcac_mcp.__version__` reads it via
  `importlib.metadata`). Never hardcode a version elsewhere.
- **A tool change updates the agent-facing surface in the same commit** — `SERVER_INSTRUCTIONS` in
  `server.py` and the tool inventory in `README.md`. Both are contracts: the first is what every
  connecting model reads before choosing a tool, the second is what a human reads before installing.
- **Never point a verify/test drive at a real cluster.** Use `.agents/factory/bin/sandbox.sh`.

## Commands

Dependency management is `uv` (`uv.lock`, `[dependency-groups] dev` syncs by default).

```bash
uv sync                                  # install all deps (incl. dev)
uv run rcac-mcp --help                   # the CLI
uv run rcac-mcp -e local                 # run locally (development only)

uv run pytest -v                         # full suite
uv run pytest -v -k "pattern"            # by name
uv run pytest -v -m unit                 # marker: unit | integration (--strict-markers)
                                         #   NOTE: selects NOTHING today — the markers are
                                         #   registered but no test carries one yet. See the
                                         #   restore-test-coverage seed in ROADMAP.md.

# Drive the CLI safely: throwaway HOME, blank auth env, unreachable cluster.
.agents/factory/bin/sandbox.sh uv run rcac-mcp --help
.agents/factory/bin/sandbox.sh sh -c "uv run rcac-mcp -e local --generate-token"

# A test drive must step back into the repo: the sandbox cd's out of it, and pytest would
# otherwise set rootdir to the throwaway dir and never load pyproject.toml (losing
# --strict-markers and every registered marker). HOME/auth/SSH isolation is unaffected.
.agents/factory/bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest -v -m integration'
```

**Supported Python is 3.14+** (`requires-python = ">=3.14"`). Do not reintroduce compatibility shims
for older interpreters.

**`.agents/factory/bin/sandbox.sh` is the only safe way to drive this CLI in a check.** It gives the
run a throwaway `HOME` (so `~` expansion inside a tool call cannot touch yours), blanks
`JWT_SECRET` / `OIDC_*` / `RCAC_USER_MAP`, and unsets `RCAC_SSH_HOST` — which makes a bare
`rcac-mcp` **fail closed** with "SSH host required" instead of opening a session on production. In
`local` mode this server runs arbitrary shell as you; the sandbox is what keeps a verify command from
being a mistake you cannot undo.

## Repository map

Top-level package `src/rcac_mcp/`:

| Path | Responsibility |
|------|----------------|
| `__init__.py` | `MCPServerApp` (cmdkit `Application`) — all CLI wiring, `APP_HELP`, defaults, `_resolve_exec_mode()`, `_create_executor()`, `main()`. **There is no `cli/` package.** |
| `__main__.py` | `python -m rcac_mcp` shim. |
| `server.py` | `create_mcp_server()` — builds the `FastMCP` instance, attaches auth, registers tools/resources/middleware/custom routes. Home of **`SERVER_INSTRUCTIONS`**, the agent-facing contract. |
| `context.py` | `executor_var` (`ContextVar`) + `get_executor()`/`set_executor()`. **The isolation primitive.** Tiny and load-bearing. |
| `middleware.py` | `SharedExecutorMiddleware` (ssh/local) and `AuthExecutorMiddleware` (delegate) — both set the per-request executor in `on_call_tool`. |
| `auth.py` | `AUTH_MODES` registry: `none` → `None`, `jwt` → `JWTVerifier`, `oidc` → `OIDCProxy`. Enforces the `JWT_SECRET` minimum. |
| `token.py` | `ISSUER`/`AUDIENCE`/`ALGORITHM` constants + `generate_token()` (the `--generate-token` path). Signer side of the JWT contract. |
| `resources.py` | `RESOURCE_REGISTRY` — `rcac://context` (loads `/etc/agents.d/*.md` from the cluster) and `rcac://storage`. |
| `executor/base.py` | `Executor` `Protocol` + the `CommandResult` dataclass. The contract all backends satisfy. |
| `executor/ssh.py` | `SSHExecutor` — Fabric `Connection`, `~/.ssh/config`-aware, reconnect with exponential backoff. |
| `executor/shell.py` | `LocalShellExecutor` — `subprocess` via `$SHELL`. Development only. |
| `executor/delegate.py` | `DelegatingExecutor` + `load_user_map()` — `sudo -n -H -u <user>` on behalf of an authenticated identity. |
| `tools/__init__.py` | `TOOL_REGISTRY`, the `@mcp_tool` decorator, and the **import list that makes registration happen**. |
| `tools/{shell,filesystem,transfer}.py` | Generic backend-agnostic tools: `run_command`, directory/file read+write, `upload_file`/`download_file`. |
| `tools/slurm.py` | Slurm tools: `sbatch`, `squeue`, `scancel`, `sacct`, `sinfo`, `scontrol_show_*`, plus RCAC's `slist`/`sfeatures`. |
| `tools/rcac.py` | Purdue-specific: `myquota`, `storage_paths`, `jobinfo`/`jobcmd`/`jobenv`/`jobscript`, `showpartitions`, `average_wait`. |
| `docs/` + `tools/docs.py` | **Being removed.** The documentation search index moved to the separate `rcac-docs-mcp` service (live at `docs.rcac.purdue.edu/mcp`). See the `strip-docs-subsystem` seed in `ROADMAP.md`; do not build on it. |

Two repo-level trees sit outside the package: **`.agents/`** — the spec-driven "software factory"
(the `cm-feature|plan|build|review|publish` lifecycle skills plus three operational siblings —
`cm-harness` (meta/maintenance), `cm-roadmap` (retire landed seeds, keep `ROADMAP.md` true) and
`cm-release` (version bumps / releases); `factory/` methodology, invariants, EARS/templates, a
non-Claude harness `portability.md` contract, and the `bin/` FSM + `meta_status.py` + `sandbox.sh`
scripts; `.claude` symlinks here); and **`spec/{slug}/`** — the committed, dated per-feature design
records (`GOAL.md`/`PLAN.md`/`TECH.md`/`REVIEW.md`, plus a `META.md` harness-feedback log) the
factory produces and **retains on merge**. `AGENTS.md` remains ground truth; `spec/{slug}/` is a
point-in-time record of intent. See `.agents/factory/methodology.md`.

Two more repo-level paths carry work that is **not yet in flight**:

```
issues/{slug}.md   # deferred code work, pre-shaped; /cm-feature promotes one into a GOAL
ROADMAP.md         # the ordered index of future cycles — one entry per issue
```

**Where a deferral goes — five homes, one rule each.** A pass that decides *not* to fix something
must still record it, and the destination is not a matter of taste:

| File | Holds | Written by |
|------|-------|------------|
| `spec/{slug}/META.md` | **Harness/skill feedback only** — "was this the *factory's* fault". Never code follow-ups. | the lifecycle skills |
| `issues/{slug}.md` | **Deferred code work**, pre-shaped from [`templates/ISSUE.md`](.agents/factory/templates/ISSUE.md) | whoever defers it |
| `ROADMAP.md` | the **ordered index** — one entry per issue, `**Seed:**` pointing at the `issues/` file | whoever defers it |
| `.security/issues/{slug}.md` + `.security/ROADMAP.md` | the same two things for **unremediated security findings** — gitignored, never published | whoever defers it |
| `spec/{slug}/` | work **actually in flight** | the lifecycle skills |

An `issues/{slug}.md` is a *candidate, not a contract*: `/cm-feature` promotes it into
`spec/{slug}/GOAL.md`, and that promotion is where appetite, non-goals and the R-IDs get negotiated
with a human. **Never copy one into a `GOAL.md` verbatim** — `cm-review` grades a GOAL, and a
proposal nobody accepted is not a contract. The `status:` field is the guard: `unshaped` (raw
deferral — evidence captured, nothing agreed), `shaped` (already negotiated with a human, but **not
yet accepted into a cycle**), `adopted:{slug}` (promoted; `spec/{slug}/` owns it now, and the record
stays *while the cycle is in flight* so the ROADMAP index does not dangle). Two further values close
a deferral **without** shipping — `declined` and `accepted-behaviour` — and those are terminal
records, indexed under `ROADMAP.md` § *Settled questions* rather than as cycles.

**A deferral is retired, not kept forever.** When the cycle that adopted a seed lands on `main`,
`/cm-roadmap` deletes the seed and removes its `ROADMAP.md` entry: `spec/{slug}/` is the retained
account and git history holds the file. Retire only on evidence the cycle *landed* — an
`adopted:{slug}` marker proves a cycle started, never that it finished. The security lane inverts
this: nothing under `.security/` is ever deleted, because it is gitignored and a deletion there
leaves no history to recover from.

**The security lane is not optional here.** This is a multi-user command-execution service that runs
as root in one of its modes, on shared research infrastructure. A deferral describing an
*unremediated* weakness — a live path to running a command as the wrong user, on the wrong host, or
without the intended authentication — goes in `.security/`, which is gitignored along with `.local/`.
Publishing a standing roadmap of live vulnerabilities hands an attacker a work plan. The *fixes* land
as ordinary public commits and PRs when they ship; only the inventory of what is still open stays
private. When in doubt which lane an item belongs to, use `.security/` and ask.

This coexists with the **GitHub tracker**: a GH issue is the public-facing ticket, `issues/{slug}.md`
is the pre-shaped spec behind it, and the two may link to each other. Neither replaces the other.

## Architecture & data flow

```
MCP client ──stdio│sse│http──▶ FastMCP ──▶ middleware.on_call_tool ──▶ set_executor(ContextVar)
                                                                            │
                                              tool fn ──get_executor()──────┘
                                                  │
                                                  ▼
                              Executor.run(cmd) ──▶ ssh │ local $SHELL │ sudo -u <user>
                                                  │
                                                  ▼
                                       CommandResult(stdout, stderr, exit_code, hostname)
```

Every tool follows the same three-line shape: `get_executor()`, run something, return a
`CommandResult` or a formatted string. Tools are backend-agnostic by construction — that is the whole
point of the `Executor` protocol, and it is why `run_command` works identically over SSH, locally,
and under `sudo`.

## Execution modes (read before touching `__init__.py` or `middleware.py`)

`MCPServerApp._resolve_exec_mode()` is a **security posture ladder**. Changing its order changes who
runs what, where:

1. explicit `-e/--exec-mode` wins;
2. `transport == 'stdio'` → `ssh`;
3. `--ssh-host` or `RCAC_SSH_HOST` set → `ssh` (regardless of transport);
4. `auth == 'none'` → `local`;
5. otherwise → `delegate`.

| Mode | Runs as | Auth | Notes |
|---|---|---|---|
| `ssh` | the SSH user, on the cluster | none at the MCP layer | The recommended path. The security boundary *is* the SSH connection. |
| `local` | the server process owner, on this box | none | **Development only.** Anyone who reaches the endpoint runs commands as you. |
| `delegate` | the mapped local user, via `sudo` | jwt or oidc, **required** | Server runs privileged; OS controls (sudoers/PAM) are the real enforcement. |

`delegate` installs `AuthExecutorMiddleware`; the other two install `SharedExecutorMiddleware`. Never
wire delegate behavior into the shared path.

## Request isolation — the single highest-blast-radius contract

**The current executor lives in a `ContextVar` (`context.py`), never a module-level global.**
`ContextVar`s are task-local, and FastMCP/Starlette gives each HTTP request its own async task, so
each request gets its own executor. Replace it with a plain global and user A's request runs as user
B. `SECURITY.md` § *Request Isolation in Delegate Mode* states the contract and shows the unsafe form
explicitly — read it before touching this.

Three consequences that are easy to get wrong:

- **`set_executor()` is called per tool call**, from middleware `on_call_tool` — not once at server
  construction, not once per connection. `SharedExecutorMiddleware` re-sets the *same* instance on
  every call precisely because a value set in one task is invisible to the next.
- **Tools never construct an executor.** They call `get_executor()`, which raises `RuntimeError` when
  unset. A tool that instantiates its own bypasses delegation entirely.
- **Any new middleware must `return await call_next(context)`.** Swallowing the chain means the
  executor is never set for that request.

## Executors

`Executor` (`executor/base.py`) is a `@runtime_checkable` `Protocol` with eight members: `hostname`
(property), `run`, `put`, `get`, `open`, `close`, `__enter__`, `__exit__`.

- **Three implementations move together** — `SSHExecutor`, `LocalShellExecutor`, `DelegatingExecutor`.
  There is no base class to inherit a default from, so adding a Protocol member means editing all
  three in the same commit. `runtime_checkable` `isinstance` checks **method presence only, never
  signatures** — a mismatched signature fails at call time, not at registration.
- **`open()`/`close()` are no-ops** for local and delegate, real for SSH.
- **`exit_code = -1` is overloaded.** Local and delegate return `-1` on `subprocess.TimeoutExpired`,
  which collides with a real return code of `-1` (killed by SIGHUP). New "never ran" sentinels go
  below `-1000`; never use the signal range `-1..-64`.
- **Timeout behavior is asymmetric.** Local and delegate *catch* the timeout and return a
  `CommandResult`; `SSHExecutor.run` passes `timeout` to Fabric and does **not** catch, so Invoke's
  `CommandTimedOut` propagates. Code assuming one shape breaks the other mode — check both.
- **`hostname` is not a user-distinguishing key.** It is the SSH target for `SSHExecutor` and
  `socket.gethostname()` for the other two, so in delegate mode every user reports the same value.

## Auth & delegation

- **`JWT_SECRET` must be ≥ 32 characters**, enforced in *two* places — `create_jwt_auth()` and the
  `--generate-token` path in `MCPServerApp.run()`. Never weaken either; never fix one and leave the
  other.
- **`ISSUER` / `AUDIENCE` / `ALGORITHM` are single-sourced in `token.py`** and consumed by
  `create_jwt_auth()`. Signer and verifier must agree, or every token is silently rejected.
- A new auth mode is an `AUTH_MODES` entry **plus** the `-a/--auth` `choices` list — they are not
  derived from each other.
- **Identity claim precedence is `sub` → `email` → `preferred_username`**, first non-empty wins.
  Reordering silently re-maps users. No claim → hard `RuntimeError`, never a fallback identity.
- **An identity absent from the user map is a `PermissionError`, never a default user.** The map is
  loaded once at middleware construction from `RCAC_USER_MAP`, and `DelegatingExecutor.__init__`
  re-checks — keep both, since the executor is constructible outside the middleware.
- Commands run as `sudo -n -H -u <local_user> <shell> -c <command>`. **`-n` is required**: there is no
  TTY, so dropping it turns a missing sudoers rule into a hang instead of an error. `cwd` is
  `shlex.quote`d; `command` deliberately is not — it *is* a shell command.
- `DelegatingExecutor._shell` is the **server's** `$SHELL`, chosen at construction, not the target
  user's and not read per-command.

## Tools

- Registration is an **import-time side effect**. `tools/__init__.py` defines `TOOL_REGISTRY` and
  `mcp_tool` *first*, then imports each tool module at the bottom; `@mcp_tool` appends at decoration
  time. **A tool module missing from that import list registers nothing and fails silently** — no
  error, just an absent tool.
- **`@mcp_tool` replaces the function with a `FunctionTool`, which is not callable.** Other Python
  code cannot invoke a decorated tool directly. When logic must be shared between a tool and
  something else, factor it into an undecorated helper and have both call that. (`resources.py`
  currently gets this wrong — see the `storage-paths-resource-typeerror` seed.)
- Tool **docstrings are API surface** — they become the MCP schema description every model reads.
  Keep the `Args:` / `Returns:` / `Examples:` shape the existing tools use.
- Returning a `@dataclass` (`CommandResult`, `StoragePaths`) is supported and used; an arbitrary
  object is not.

## Resources & the `/etc/agents.d` convention

`RESOURCE_REGISTRY` (`resources.py`) exposes `rcac://context` and `rcac://storage`.

**`/etc/agents.d/*.md` is a deliberate, external, cross-site convention** — cluster administrators
drop markdown there, and any conforming client picks it up. Files are found with a `maxdepth 1` glob,
concatenated in `sort` order, each prefixed `<!-- Source: {filename} -->`. Do not change the path,
the glob, the ordering, or the header format without treating it as a compatibility break; the
intent is to get *other* centers to adopt it. A missing directory or empty result is **not an
error** — it caches and returns `''`.

**`_cluster_context_cache` is a process-global dict keyed by `executor.hostname`.** Because every
delegate-mode executor reports the same `socket.gethostname()`, the first user's context is served to
everyone after. That is a known defect with a filed seed, not a design choice — do not build new
per-user state on this cache, and do not key any new cache on `hostname` alone.

## Configuration

There is no config file and no config singleton — everything is CLI flags plus environment:

| Variable | Used for |
|---|---|
| `RCAC_SSH_HOST` | default SSH target (`--ssh-host` overrides) |
| `RCAC_USER_MAP` | path to the delegate-mode identity → local-user map |
| `JWT_SECRET` | HS256 shared secret (≥ 32 chars) |
| `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `MCP_BASE_URL` | OIDC proxy (all four required together) |
| `MCP_BASE_URL` | also used to build the absolute icon URL in `server.py` |

`MCP_BASE_URL` is read **at import time** in `server.py` to compute `ICON_URL`; setting it after
import has no effect.

## Testing

- `uv run pytest`. Only **`@mark.unit`** and **`@mark.integration`** are real markers under
  `--strict-markers` — tag every new test. **No existing test carries one**, so `-m unit` and
  `-m integration` currently deselect all 81 and report "no tests ran"; the rule is forward-looking
  until the `restore-test-coverage` cycle lands.
- Integration tests drive the installed CLI and must run under `.agents/factory/bin/sandbox.sh`,
  stepping back into the repo first (`cd "$CLUSTER_MCP_REPO"`) so pytest finds `pyproject.toml`.
- `tests/conftest.py` currently provides only docs-index fixtures, and **every test in the suite
  today is a docs test** — removing the docs subsystem removes the whole suite. Rebuilding coverage
  for the executor/middleware/tool core is a tracked seed, not an optional nicety.
- The `tests/fixtures/RCAC-Docs` git submodule exists only for the docs index and goes with it.
- **PyYAML must stay resolvable in the dev environment** — the factory FSM scripts import it. It is
  pinned in the `dev` group for exactly that reason; dropping a runtime feature that happens to use
  YAML must not drop the dev pin.

## Packaging

- `hatchling`; the wheel ships only `src/rcac_mcp`.
- **`uv.lock` is tracked.** This server is installed straight from `git+https://…`, so the lock is the
  only thing pinning the transitive tree a user actually receives. A change to `[project.dependencies]`
  or `[dependency-groups]` runs `uv lock` and commits the result **in the same commit**; `/cm-release`
  stages it alongside the version bump. `uv lock --check` exits nonzero when the two have drifted.
- **The sdist must not ship `.agents/`, `spec/`, `issues/`, `.security/`, or `tests/fixtures/`.**
  `[tool.hatch.build.targets.sdist]` excludes them and `/cm-release`'s gate re-checks the tarball — a
  `.security/` path in a published artifact would publish an inventory of unremediated weaknesses.
- There is **no CI, no publish workflow, and no published container image**, and the project is not
  on PyPI — users install from `git+https://github.com/purduercac/rcac-mcp`. `/cm-release` cuts a
  tag and a GitHub release and says so plainly. Wiring CI is a tracked seed.
- A **container build does exist locally**: `Dockerfile` + `compose.yml` (+ `nginx-dev.conf` for the
  TLS dev proxy), documented in `README.md` § *Docker Compose with TLS*. `compose.yml` pins the
  server's argv (`-t http -H 0.0.0.0 -p 8000 -a jwt`), so a change to a CLI flag name or default is
  a change to those files too — same same-commit rule as `SERVER_INSTRUCTIONS` and the README.

## High-risk files & footguns (quick reference)

- **`context.py`** — 47 lines, the highest blast radius in the repo. A module global here is
  cross-user command execution.
- **`middleware.py`** — where identity becomes an executor. Claim precedence, the user-map check, and
  `return await call_next(context)` are all load-bearing.
- **`executor/delegate.py`** — the `sudo` argv, `-n`, `shlex.quote` on `cwd` only, the server's
  `$SHELL`.
- **`auth.py` / `token.py`** — the 32-char minimum in two places; the issuer/audience/algorithm
  triple that must match across signer and verifier. Note `auth.py`'s `AUTH_MODES` annotation carries
  a stray `:` that only parses because of PEP 563 — see the `auth-modes-annotation-typo` seed.
- **`executor/ssh.py`** — reconnect backoff; the *uncaught* Fabric timeout that makes `run()`'s
  contract differ from the other two backends.
- **`__init__.py`** — `_resolve_exec_mode()`, the posture ladder.
- **`server.py`** — `SERVER_INSTRUCTIONS` (what every model reads) and import-time `MCP_BASE_URL`.
- **`tools/__init__.py`** — the bottom-of-file import list; a missing entry silently drops a tool.
- **`resources.py`** — the process-global context cache keyed by a non-unique hostname.

## Working on this codebase as an agent

- **Use the factory for non-trivial work.** A feature/fix/refactor flows through the `.agents/`
  spec-driven lifecycle — `/cm-feature` (shape `GOAL.md`, or promote an `issues/{slug}.md`) →
  `/cm-plan` (research + `PLAN.md`/`TECH.md`) → `/cm-build` (execute phases) → `/cm-review` (blind,
  externally-verified QA) → `/cm-publish` (squash PR to `main`), each on a `feature/`|`fix/` branch
  with artifacts committed under `spec/{slug}/`. `.agents/factory/methodology.md` is the *why*;
  `.agents/factory/invariants.md` is the curated footgun checklist derived from this file (kept in
  lockstep — if it drifts, this file wins). Ceremony scales to appetite: a one-sentence change may
  skip the lifecycle entirely. Each lifecycle skill also ends with a **silence-by-default meta-note**
  that logs *harness* friction (not code issues) to `spec/{slug}/META.md`; `/cm-publish` surfaces
  substantial notes in the PR; and the human-gated **`/cm-harness`** (meta/maintenance, **not** a
  lifecycle step) applies those fixes back to `.agents/`, logging each in `factory/harness-log.md` —
  and never weakens a non-negotiable gate on a finding's say-so.
- **Retire what shipped with `/cm-roadmap`** (operational, **not** a lifecycle step). It finds every
  `issues/{slug}.md` carrying `status: adopted:{slug}`, confirms the cycle actually reached `main`
  (`git ls-tree main -- spec/{slug}`, because the marker only proves a cycle *started*), and after a
  human-gated preview deletes the seed and its `ROADMAP.md` entry — then repairs the drift the
  removal leaves. It is deliberately **not** folded into `/cm-publish`: retirement writes outside
  `spec/`, which is exactly what publish's staleness gate exists to notice.
- **Cut releases with `/cm-release`** (also operational): it bumps the single version source, runs
  the gate (pytest, `uv build`, `twine check --strict`, sdist hygiene), signs a tag, and publishes a
  GitHub release — rehearsed in a `git worktree` dry-run and gated on an explicit human OK before any
  irreversible push.
- **Verify by driving the CLI, not just tests — in the sandbox.** After a change, exercise the real
  flow: `.agents/factory/bin/sandbox.sh sh -c "uv run rcac-mcp -e local --help"`. **Exit 0 is not
  enough** — assert the post-condition you actually care about (the tool is in the registry, the
  error message and exit status are the intended ones, the file landed where you expected).
- **Put logic where it belongs:** command execution behind the `Executor` protocol; identity →
  executor in middleware; per-request state in a `ContextVar`. Reuse `cmdkit.app.exit_status`
  constants for return codes — don't invent integer literals. A new CLI flag is an
  `interface.add_argument` on `MCPServerApp` plus its entry in `APP_USAGE`/`APP_HELP`.
- **Comments are declarative statements, not spec pointers.** Write each comment/docstring as a
  capitalized statement of the invariant or the *why* (`# Non-interactive: there is no TTY to prompt
  on.`) — not a lowercase fragment. **Never embed feature-scoped spec ids** (`R#`, `P#`) in source:
  they restart per feature, live in `spec/{slug}/`, and collide across branches. Requirement
  provenance lives in the commit, the PR, and the retained `spec/{slug}/`. Referencing *stable* things
  is fine — real symbols, documented invariants, the `/etc/agents.d` path.
- **Parallelizing agent work:** the subsystems safe to touch independently are the leaf tool modules
  (`tools/slurm.py`, `tools/rcac.py`, `tools/filesystem.py`), docs, and tests. The coupled core —
  `context.py`, `middleware.py`, `auth.py`, `token.py`, `executor/*`, `server.py` — shares the
  contracts above and should be changed with a single coherent view; parallel edits there conflict on
  *contracts*, not just lines. When in doubt, fan out to read/understand and serialize the edit.
- **This file is the map, but it drifts.** For a deep change, re-verify the specific invariant
  against the source before relying on it, and update this file when the code moves.
