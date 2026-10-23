# AGENTS.md

Guidance for coding agents (Claude Code and others) working in this repository.
`CLAUDE.md` is a symlink to this file — edit `AGENTS.md`, never a separate copy. The factory's
method and skills live in the operator's KB, not in this repository; this file carries the repo's
side of it (§ Factory, § Invariants).

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

The server is built on **FastMCP 4.x** and the **MCP SDK 2.x** with a **cmdkit** CLI, and speaks
three transports (`stdio`, `sse`, `http`) against three execution backends (`ssh`, `local`,
`delegate`). `stdio` and `http` serve both protocol eras (below); `sse` serves only the handshake
era: revision 2026-07-28 deprecates HTTP+SSE, and a 2026-07-28 client that reaches an `sse` server
falls back to the handshake. It is still served, so `-t sse` stays.

It serves **both MCP protocol eras** with no configuration: a client on a handshake revision
(2024-11-05 through 2025-11-25) completes `initialize` and gets `SERVER_INSTRUCTIONS` in its
`InitializeResult`; a client on revision **2026-07-28** is answered on `server/discover`, gets the
same instructions in its `DiscoverResult`, and never sends `initialize`. Both list the same tools.

**Accepted behaviour changes from the FastMCP 4 / MCP SDK 2 move:**

- A stateful streamable-HTTP session (`-t http`) now closes after 30 minutes idle, the SDK's
  default; on the previous stack it never closed. A client idle that long reconnects. The default is
  kept deliberately.
- An `-a oidc` deployment's client registrations do not survive the upgrade. FastMCP 2's OIDC proxy
  kept them in an encrypted `DiskStore` at `<home>/oauth-proxy/cache.db`; FastMCP 4's keeps them in
  an encrypted `FileTreeStore` under `<home>/oauth-proxy/<key-fingerprint>/` and does not read the
  old store (`<home>` is FastMCP's home directory). Every client registered before the upgrade must
  register again (§6, `SECURITY.md`).

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
  other centers to adopt it. See § Invariants §8 below; it is an invariant *today*, not just direction.

## Environment & working rules

- **`del`, not `rm`** — use `del <path>` (reversible trash; directories work with no flags). A
  deleted `issues/` seed is otherwise unrecoverable outside git history.
- **Commit only when explicitly asked.** When you do, branch per § Factory → Profile (`base:
  develop`). `main` is the default branch and the release channel: it moves only at a release, by
  fast-forward to a released `develop` commit, so `uvx git+https://…` installs the last release.
- **Squash-only merges.** Commit subjects follow `[category] Imperative summary`. Categories in use:
  `feature`, `fix`, `docs`, `refactor`, `test`, `harness` (the factory's footprint here: § Factory
  and § Invariants), `release` (version bumps), `ci`. The set is **not closed** — coin a new
  lowercase category when one genuinely fits. **Do not add a `Co-Authored-By:` trailer** — a
  deliberate repo convention (we don't want the authoring model recorded on every line of the
  commit log). End PR **bodies** with the Claude Code generation line.
- The version, agent-facing-surface and sandbox rules live in § Invariants (§9 and § Conventions).

## Commands

Dependency management is `uv` (`uv.lock`, `[dependency-groups] dev` syncs by default).

```bash
uv sync                                  # install all deps (incl. dev)
uv run rcac-mcp --help                   # the CLI
uv run rcac-mcp -e local                 # run locally (development only)

uv run pytest -v                         # full suite
uv run pytest -v -k "pattern"            # by name
uv run pytest -v -m unit                 # marker: unit | integration (--strict-markers)
                                         #   every test carries one; the conftest fails the
                                         #   run on an unmarked test (§ Testing)

# Drive the CLI safely: throwaway HOME, blank auth env, unreachable cluster.
bin/sandbox.sh uv run rcac-mcp --help
bin/sandbox.sh sh -c "uv run rcac-mcp -e local --generate-token"

# A test drive must step back into the repo: the sandbox cd's out of it, and pytest would
# otherwise set rootdir to the throwaway dir and never load pyproject.toml (losing
# --strict-markers and every registered marker). HOME/auth/SSH isolation is unaffected.
bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest -v -m integration'
```

**Supported Python is 3.14+** (`requires-python = ">=3.14"`). Do not reintroduce compatibility shims
for older interpreters.

**Supported uv is 0.12.17+**; the floor is `[tool.uv] required-version` in `pyproject.toml`. uv
0.5.14 and later read that field: a release from 0.5.14 up to 0.12.16 refuses to sync, lock or run
the project, naming 0.12.17. uv 0.5.13 and earlier predate the field: they warn about an unknown
field and run, and no uv-native mechanism reaches them.

**`bin/sandbox.sh` is the only safe way to drive this CLI in a check.** It gives the
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
| `middleware.py` | `SharedExecutorMiddleware` (ssh/local) and `AuthExecutorMiddleware` (delegate) — both set the per-request executor in `on_call_tool` — and `ToolErrorMiddleware`, the error boundary `create_mcp_server` adds before them, which turns an exception raised around a tool call into a tool error result carrying `str(exc)`. |
| `auth.py` | `AUTH_MODES` registry: `none` → `None`, `jwt` → `JWTVerifier`, `oidc` → `OIDCProxy`. Enforces the `JWT_SECRET` minimum and OIDC's all-four check. |
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

Five repo-level paths sit outside the package: **`bin/sandbox.sh`** — the throwaway-environment
helper every CLI drive runs under (§ Factory → Throwaway environment); **`bin/audit_lock.py`** — the
release gate's dependency audit, which CI also runs (§ Factory → Release recipe);
**`bin/sdist_hygiene.py`** — the one definition of what a built sdist must not ship, run by the
release recipe's `gate` and by CI (§ Packaging); **`.github/workflows/`** — `ci.yml`, the gate on
every push and pull request, and `mutants.yml`, the mutation runner on demand (§ Conventions binds
`ci.yml` to the gate's step lists); and **`spec/{slug}/`** — the
committed, dated per-feature design records (`GOAL.md`/`PLAN.md`/`TECH.md`/`REVIEW.md`) the
factory produces and **retains on merge**. `AGENTS.md` remains ground truth; `spec/{slug}/` is a
point-in-time record of intent. The factory's method and skills live in the operator's KB; see
§ Factory.

Two more repo-level paths carry work that is **not yet in flight**:

```
issues/{slug}.md   # deferred code work, pre-shaped; the feature step promotes one into a GOAL
ROADMAP.md         # the ordered index of future cycles — one entry per issue
```

Where each kind of deferral goes is fixed: § Factory → Where deferred work goes.

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

The contracts each piece keeps — request isolation, the executor protocol, the execution-mode
ladder, delegation and auth, tool registration, resources, and the agent-facing surface — are
§ Invariants §1–§9.

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
  `--strict-markers`, and **every test carries one**: `tests/conftest.py` holds a single collection
  hook that fails the run with a `pytest.UsageError` listing any collected test that carries
  neither. So `-m unit` and `-m integration` each select a real subset.
- The suite pins the coupled core's contracts (§ Invariants §1–§7) and the agent-facing surface
  (§9). Every module is built around **pure check functions** (`*_violations`) that its tests feed
  observed behaviour, and that gates may import directly (`sys.path.insert(0, 'tests')`). Their
  names and the parametrised test ids are a contract: renaming one breaks a gate loudly. Each check
  has standing negative controls, planted shapes it must reject.
  - `tests/test_registry.py` (`unit`, `integration`): `EXPECTED_TOOLS`, `registry_violations`,
    `module_absent`, `absence_violations`. `TOOL_REGISTRY` and the tools the server actually serves
    (listed by a harness client, `served_tools`) match `EXPECTED_TOOLS` exactly, and the removed
    documentation modules stay unimportable; when one does import, `absence_violations` says where
    from and asks the developer's own git, through `bin/sandbox.sh`, what each file there is. A
    tracked file means the module was re-added, and the message says so with no `del`; an untracked
    file and an ignored one (the stale checkout below) each get their own line and a `del` that
    reaches only that class. The classes are the developer's own `git status --ignored`: every file
    is keyed by its path as git prints it (a symlink, the location itself included, is the link git
    records, never its target; on macOS names are compared composed, whatever
    `core.precomposeUnicode` says), and a repository nested there is the one entry git lists it as,
    in git's class for it, with a line saying git does not look into it. Git runs with the
    developer's HOME and XDG directories handed back inside the sandbox and the rest of the
    environment as it is, so it reads exactly the configuration their `git status` reads (global and
    system config, `[include]` and `includeIf`, `~` in any value, `GIT_CONFIG_*`, every excludes
    file); only what points it at another repository is dropped (`GIT_DIR`, `GIT_WORK_TREE`,
    `GIT_INDEX_FILE` and the rest of what a hook exports, as `git rev-parse --local-env-vars` lists
    them, and the pathspec-mode variables), and `GIT_CEILING_DIRECTORIES` is kept. Running the
    developer's git is safe because it only reads: the calls are `rev-parse`, `ls-files` and
    `check-ignore`, which write nothing; `GIT_OPTIONAL_LOCKS=0` keeps git from refreshing the index;
    and `core.fsmonitor=false` and `core.hooksPath=/dev/null` on the command line, which outrank
    every config file, keep anything a config names from running. Where git cannot answer, the
    message is the old suffix rule's: bytecode alone is a stale checkout, anything else a re-added
    module. That is the case without git on PATH, without `bin/sandbox.sh` (an sdist), outside a
    work tree (one above `GIT_CEILING_DIRECTORIES` included), in a `unit` test, which may start no
    process, when a call fails or lists a file in no class or in two, and when a directory there
    cannot be read. So `test_docs_modules_absent` is `integration` although it drives no CLI, and
    `-m unit` no longer runs it; the git-backed controls build their own repository under
    `tmp_path`, with a scratch HOME and system config so the developer's configuration is never
    read, and go through `conftest.need_git`, which skips them without git off CI and fails them on
    CI, where it never skips (its own test catches every outcome, so it cannot skip itself).
    `EXPECTED_TOOLS` is hard-coded, so a tool change edits it in the same
    commit as `SERVER_INSTRUCTIONS` and the README (§ Invariants §9).
  - `tests/test_instructions.py` (`unit`): `instruction_violations`, `guidance_violations`,
    `NON_TOOL_TERMS`. `SERVER_INSTRUCTIONS`, both as the constant and as served (what a harness
    client receives on connecting, `served_instructions`), lists exactly the
    registered tools, and neither it nor `INSTRUCTIONS.md` steers agents toward documentation
    search — that is the separate `rcac-docs-mcp` service, which only the README points to. This
    is §9 made executable; it reads list entries and backticked or snake_case names, so a tool
    whose name is a plain word, mentioned in bare prose, escapes it.
  - `tests/test_cli.py`: `flag_violations`. The `integration` cases drive the installed CLI with
    flags it no longer accepts and assert a clean `exit_status.bad_argument` rejection before the
    server starts; the `unit` cases feed `flag_violations` synthetic results it must reject.
  - `tests/test_isolation.py` (`unit`): `isolation_violations`. Two concurrent calls, each under
    its own delegate identity over HTTP (and, in shared mode, on two servers in one process), each
    run as their own user and get back only their own result. A `Rendezvous` holds both inside the
    executor middleware until both are in flight, so a module global crosses them every time.
    Sequential calls on one server, shared and delegate, show that each executor middleware sets
    the executor on every call, not only the first (§1).
  - `tests/test_no_executor.py` (`unit`): `refusal_violations`. A call with no executor for its
    request (a bare server, every tool; a tokenless delegate call), including right after and while
    another request did set one, fails where the client sees it with `No executor configured`, and
    nothing runs. Any error is not enough: the stated reason is required (§1).
  - `tests/test_tools.py` (`unit`): `TOOL_CASES`, `tool_violations`. Every `EXPECTED_TOOLS` tool,
    on a `SharedExecutorMiddleware` server, reaches only the installed executor, carries its own
    argument there and returns the executor's answer (§1, §7). A tool change adds its `TOOL_CASES`
    row in the same commit; `test_tool_cases_cover_expected_tools` fails otherwise.
  - `tests/test_identity.py` (`unit`): `delegation_violations`. Claim precedence (`sub`, `email`,
    `preferred_username`) over real tokens; no claim or an unmapped identity is refused with no
    fall-through; `DelegatingExecutor` raises `KeyError`; the user-map loader, which names a
    malformed line by its line in the file, comments and blanks counted (§5).
  - `tests/test_executors.py` (`unit`): `sudo_argv_violations`. The exact `sudo -n -H -u <user>
    <$SHELL> -c <command>` argv recorded at `Popen`, `$SHELL` captured at construction, `cwd`
    quoted and the command not; all three backends satisfy `Executor` (§2, §5).
  - `tests/test_exec_mode.py` (`unit`): `LADDER_CASES`, `ladder_violations`. The posture ladder
    through `MCPServerApp.main` with server construction stubbed, and the middleware class each
    mode installs (§4). An explicit `-e` is decided at rung 1 whatever the later rungs read: a row
    for each `-e` value (`local`, `ssh`, `delegate`) against each later-rung input (`stdio`,
    `--ssh-host`, `RCAC_SSH_HOST`, auth `none`, `jwt`, `oidc`), and
    `test_explicit_mode_decides_alone` over every combination of those inputs' values. A new input
    to `_resolve_exec_mode` joins both in the same commit.
  - `tests/test_tokens.py` (`unit`, `integration`): `token_cli_violations`. Both `JWT_SECRET`
    floors, `create_jwt_auth()` and the sandboxed `--generate-token`, each pinned on its own; tokens
    from either signer accepted by the verifier, and a wrong issuer, audience, secret or algorithm,
    or an expired token, refused with a 401 and nothing launched (§6).
  - `tests/test_auth_modes.py` (`unit`): `oidc_violations`, `proxy_violations`,
    `posture_violations`, `annotation_violations`. OIDC with any one of its four variables unset or
    empty is refused with a `ValueError` naming all four, with nothing built and nothing reached
    (`rcac_mcp.auth.OIDCProxy` is replaced by a recorder; the unit guard records any network reach).
    With all four set the recorder is reached, its arguments bind to the real `OIDCProxy`
    signature, and `enable_cimd` and `forward_resource` arrive `False` (§6). `none` installs no
    provider and answers a tokenless HTTP call. `typing.get_type_hints(rcac_mcp.auth)['AUTH_MODES']`
    resolves to `Final` of a mapping from `str` to `Callable[[], AuthProvider | None]`; a `slice`,
    `Callable[..., X]` and a factory taking an argument are rejected. A live OIDC flow is untested.
  - `tests/test_hygiene.py` (`unit`, `integration`): `sandbox_violations`,
    `stack_import_violations`, the suite's own rails. Every launch in every `tests/*.py` goes
    through `bin/sandbox.sh` and no argv names `sudo` or `ssh` (`test_cli_drives_are_sandboxed`,
    checked statically); no module but `tests/harness.py` imports the stack, by any of its import
    names `fastmcp`, `mcp`, `mcp_types`, `httpx2`, `httpcore2` (and the old `httpx`)
    (`test_only_the_harness_imports_the_stack`, also static: `import`, `from … import`, and
    `import_module` / `__import__` / `pytest.importorskip` called with a literal name, including
    through a `from pytest import importorskip as …` alias; a computed name is not read, and the
    check's docstring lists what else it cannot see); self-tests prove the guard, the PATH shim,
    the harness fence and the build-inside-the-loop rule are active.
  - `tests/test_protocol.py` (`unit`): `boundary_violations`, `cancellation_violations`,
    `era_violations`. A `PermissionError` (in memory and over HTTP), a `RuntimeError` and an
    `MCPError` raised by middleware around a tool call each arrive as a tool error result whose
    text is exactly `str(exc)`, and nothing runs; cancellation passes through `ToolErrorMiddleware`
    (§1). Both protocol eras, each pinned on its own rows whatever era the run selected
    (`test_client_era_is_served[<era>-<memory|http>]`): a handshake-era client gets an
    `InitializeResult` and the server sees `initialize` and no `server/discover`; a 2026-07-28 client
    gets no `InitializeResult` and the server sees `server/discover` and no `initialize`; each
    receives `SERVER_INSTRUCTIONS` and lists exactly `EXPECTED_TOOLS` (§9). The delegate contracts
    (isolation, the tokenless refusal, a foreign token's 401) also run in the modern era on every
    run (§1, §5, §6). The identity the delegate middleware stores in the context state is visible
    to its own tool call and to no later request on the same session, in either era
    (`state_scope_violations`, §1; only the handshake era has a session for it to leak into). It
    also pins the harness's era selection (`select_era`, the post-connect
    check) and `call_http`'s last-answer 401 rule (`rejection_status`).
  - `tests/test_surface.py` (`unit`): `surface_violations`, `TOLERATED`, `tolerated_differences`.
    What a harness client in the selected era is served (`served_surface`) matches
    `tests/fixtures/served_surface.json`: the tool set, each description, each argument's name,
    type and default, each `required` list, each output schema, every resource's URI, name,
    description and MIME type, and the instructions (§9). The fixture was written by
    `spec/stack-upgrade/verify/snapshot.py` run in an archive of `3d010ec` in that commit's own
    locked FastMCP 2 environment, never on the current stack, and
    `test_surface_fixture_is_the_baseline_shape` rejects one that carries FastMCP 4's metadata.
    `TOLERATED` names the four differences FastMCP 4 introduces, each with its cause and accepted
    only in its own shape: an argument `description` where there was none, `additionalProperties`
    added as exactly `false`, output `$defs` inlined, and a `description` on an inlined output node
    only where the snapshot had a `$ref`. `tolerated_differences` lists them per tool.
  - `tests/test_ssh_smoke.py` (`integration`): `smoke_violations`. `SSHExecutor` runs a command
    over the locked Fabric and Paramiko against an in-process Paramiko server on `127.0.0.1` with
    generated keys: stdout, exit status and hostname come back, and the server received the exact
    command. No process is launched and no real host is reached: `HOME` is under `tmp_path`, the
    agent socket is unset, no key files or agent are consulted, and Fabric gets an empty SSH config.
  - `tests/test_transports.py` (`integration`, with `unit` controls): `transport_violations`. The
    installed `rcac-mcp -e local` starts under `bin/sandbox.sh` on each transport and a client lists
    exactly `EXPECTED_TOOLS` in the era it asked for: `stdio` and `http` in both eras, `sse` in the
    handshake era only (`test_transport_lists_expected_tools[<transport>-<era>]`). HTTP and SSE
    servers bind `127.0.0.1` on a free port, started with `start_new_session=True` and stopped with
    `os.killpg`: `bin/sandbox.sh` does not forward SIGTERM to its child, so stopping its shell alone
    leaves the server listening. After every row the process group is gone, the port refuses
    connections and the sandbox directory (made under the test's own `TMPDIR`) has been removed. A
    server that never listens fails as `<transport>: the server did not start listening`.
  - `tests/test_audit.py` (`unit`, `integration`): `audit_violations`, `offline_requested`,
    `command` and `child_env` (all in `bin/audit_lock.py`, loaded from its file),
    `offline_violations`, `unreachable_violations`. Planted `uv audit` outputs (one finding or two,
    under exit 1 or exit 0; an unparsed report under exit 1; each unreachable marker; an audit that
    cannot run or was killed; a short or long count; an empty or different lock; no summary, or one
    not on a line of its own) are each red with exactly their own reasons and documented exit
    status, and only a no-findings summary covering every registry package in `uv.lock` is green.
    The wrapper decides offline itself: `main()` reads `UV_OFFLINE` once, by uv's boolean rules
    (unset or false-ish is online; anything else, empty and unparseable included, is offline), and
    that one decision both puts `--offline` on uv's argv and marks the verdict offline; `command`
    takes the decision, never the environment, so nothing reads the variable twice. uv's
    vocabulary, as uv 0.12.17 and 0.13.1 read it, is `1 t true y yes on` and `0 f false n no off`
    in any case form, and uv refuses every other value; a unit row pins the
    wrapper's reading of every case form and of refused values, and an integration row hands each
    value to the running uv on the wrapper's argv and requires uv to make a request through the
    canary exactly when the wrapper reads it as online. An offline audit exits 2 as "advisory source
    unreachable" whatever uv printed or exited, findings included. The tests pin the wrapper's exit
    status and its own `audit_lock:` messages, never uv's wording, which differs between supported
    uv releases. The command runs the launching uv (`UV`, else `uv` on `PATH`), names this checkout
    with `--project` and `--frozen`/`--no-config`, and the child environment drops `UV_PROJECT`,
    `UV_OFFLINE` and every `UV_*` but `UV_CACHE_DIR`, `UV_HTTP_RETRIES`, `UV_NATIVE_TLS`,
    `UV_SYSTEM_CERTS`; both are pinned exactly. `main()` is pinned on its own exit status and
    output: with `subprocess.run` a fake uv that answers only a launch of exactly `command` with
    exactly `child_env` from the checkout root, its output captured as text, each planted run
    (clean, short count, a finding, findings on both streams, unreachable, cannot run, three
    offline, and a clean run under every case form of the vocabulary and every refused value, where
    the argv's `--offline` must agree with the verdict and both with the vocabulary) must give
    exactly the expected status, uv's output passed through on its own stream and the wrapper's
    `audit_lock:` lines on stderr, so a wiring fault anywhere in `main()` fails under any uv. Two
    integration rows run the real script under `bin/sandbox.sh` with every proxy at a
    canary, a loopback listener that counts connections and answers none (`UV_HTTP_RETRIES=0`):
    offline (`UV_OFFLINE=1`) requires exit 2, the wrapper's "advisory source unreachable: the audit
    ran offline" and **zero** connections, so no lookup was attempted; online requires exit 2, the
    wrapper's online "advisory source unreachable (uv audit exited N)" and **at least one**
    connection, the positive control that shows the canary sees this uv's requests. The networked
    proofs belong to the release gate, not the suite.
  - `tests/test_mutations.py` (`unit`): the fast half of the mutation runner (below).
  - `tests/test_sdist_hygiene.py` (`unit`): `member_violations`, `sdist_violations` and `main` of
    `bin/sdist_hygiene.py`, loaded by path and fed tarballs built in `tmp_path`. Each furniture
    entry is named, lookalikes (`binary.txt`, `specs/`, `tests/fixturesX/`) pass, a member outside
    the root named after the tarball or with `..` is named, and a missing or unreadable sdist
    fails. Unreadable means not read in full: a truncated stream (every cut through a gzip
    stream's tail), a damaged gzip checksum or length, a damaged header mid-archive, no
    end-of-archive block, data after it (however far), a truncated or damaged later xz or bz2
    stream, and any error raised while reading, each named in the one `cannot be read` line with no
    traceback, while the other sdists are still checked. Readable variants (GNU format,
    uncompressed, multi-stream gzip, xz and bz2) and every kind of member (directories, links,
    duplicates) are judged as before. The furniture cases run over a tuple written in the test,
    never the script's own `FURNITURE`, and `test_furniture_matches_the_recorded_set` pins the two
    together, so dropping an entry is red.
- **The rails, in `tests/conftest.py`**, beside the collection hook (which they never feed: no
  fixture adds a marker):
  - **The unit guard** makes `unit` mean something: for a `unit` test, `HOME` and `XDG_*` point
    under `tmp_path`, the auth and SSH environment is blanked as `bin/sandbox.sh` blanks it, and
    every process launch (`subprocess.Popen`, `os.system`/`popen`/`exec*`/`spawn*`/`fork`,
    `posix_spawn`, `pty`, `multiprocessing`) and network connect or lookup is refused and recorded.
    A refusal fails the test at teardown; contract tests also assert `guard.refused == []` in the
    body, because a refusal inside a tool comes back as a tool error. **File I/O is not guarded:** a
    tool that falls back to local file operations is caught by `refusal_violations`' stated reason
    and the `transfer-local-fallback` planted case, not by the guard.
  - **The PATH shim**, for every test: `sudo` and `ssh` stand-ins come first on `PATH`; each
    prints `refused by the test PATH shim` and exits 126. `bin/sandbox.sh` keeps `PATH`, so
    sandboxed children inherit it.
  - **`launches`** (`unit` and `integration`) replaces `subprocess.Popen` with a recorder: nothing
    runs, and the recorded argv is what the test asserts.
  - **The main-context check:** after every test the main context must hold no executor.
- **`tests/harness.py` is the only test module that imports FastMCP, mcp or the HTTP client
  (`httpx2`)** (`test_only_the_harness_imports_the_stack` fails on any other). Its docstring lists
  the FastMCP 4 / MCP SDK 2 touchpoints it relies on. The contract-test modules speak only its
  verbs (`drive`, `serving`, `http_client`, `in_memory`, `call`, `call_http`, `negotiate`, `mint`,
  `user_map`, `served_tools`, `served_instructions`, `served_surface`, `installed_auth`, `protocol_error`,
  `RecordingExecutor`, `Rendezvous`, `Raises`, `MethodLog`, `StateProbe`, `tool_names`, `delegate_server`,
  `shared_server`, `bare_server`, `stdio_tools`, `url_tools`; the builders take extra middleware, such as a `Rendezvous`, added inside the
  executor middleware, and add a `MethodLog` last) and the `JWTVerifier` type it re-exports. `negotiate` connects in a given
  era without the post-connect check and returns what both ends saw (`Negotiated`: version,
  whether an `InitializeResult` exists, instructions, tool names, and the methods the server's
  `MethodLog` saw), so a test decides the era itself. Delegate mode runs over an in-process ASGI route: a real JWT through the real
  `JWTVerifier` and `AuthExecutorMiddleware`, with no socket and no port; in FastMCP 4 the
  verifier's 401 arrives as an error from connect-time negotiation, so `call_http` reads it from a
  response recorder and reports a rejection only when the last answer was a 401. `stdio_tools`
  and `url_tools` list the tools of a server in another process and report the era it negotiated
  (`Listed`): `stdio_tools` launches the server itself, inside FastMCP where the static launch
  check cannot see it, so it refuses an argv whose first element is not this repo's
  `bin/sandbox.sh`, and closes the server when its client exits; `url_tools` reaches only a
  loopback host.
- **The protocol era.** `CLUSTER_MCP_TEST_ERA` selects the era every harness client speaks:
  `legacy` (the default: the `initialize` handshake) or
  `modern` (revision 2026-07-28 through `server/discover`, built with `mode='auto'`, never a
  version pin, which skips `server/discover`). Any other value stops the run at import, naming the
  variable. After every connect the harness checks the negotiated era and raises
  `harness era: …` on a mismatch. `in_memory`, `http_client` and `call_http` take `era=` for a
  contract that must hold in one era on every run; those clients are checked the same way. With
  `CLUSTER_MCP_ERA_LOG` set to a file, every in-process connect in the selected era appends
  `era=… version=… first=…` (the first request the server's `MethodLog` saw on that connection),
  which a gate reads to prove a pass ran in its era; a connect in an era chosen with `era=` is not
  logged, since it does not exercise the selector. The profile's `verify`, the release recipe's
  `gate` and CI, which mirrors both, run the suite in both eras (`uv run pytest -q`, then
  `CLUSTER_MCP_TEST_ERA=modern uv run pytest -q`), so neither era's contracts go unchecked between
  cycles; the mutation runner's children inherit both variables.
- The harness keeps two rules:
  - **Build inside the loop.** Every server and middleware is constructed inside the coroutine
    handed to `drive`; the builders raise otherwise. Anything a construction sets in the context
    then reaches the request tasks as it would in production, so a set-at-construction bug shows.
  - **The fence.** An in-process server call raises unless `Popen` is the guard's refuser or the
    `launches` recorder, so an `integration` test that calls a server in-process requests
    `launches`. `stdio_tools` and `url_tools` are not fenced: they make no in-process call, and
    the server they reach is its own process, contained by the sandbox.
- **The mutation runner, `tests/mutants.py`, is not collected.** Each of its cases copies the
  imported package (never `src/`), applies exact source edits that break one contract, and runs the
  named target tests against the copy in a child pytest under `bin/sandbox.sh`. A case is
  **killed** only when every target fails (a JUnit `<failure>`) with the case's `why`, the fragment
  of the violation message that names the contract. A child takes about 2 s, all cases about 2½ minutes,
  which is why `uv run pytest` does not run it. Run it after changing a contract or its test, from
  the repo root:

  ```bash
  uv run python tests/mutants.py --self-check CASE_ID…     # named cases, plus the runner's controls
  uv run python tests/mutants.py --self-check $(uv run python -c \
      "import sys; sys.path.insert(0, 'tests'); import mutants; print(*(c.id for c in mutants.CASES))")
  ```

  A `FAILED` line means a case survived (the test does not pin its contract) or was killed for the
  wrong reason (the test failed on something else, an import error say): fix the test, never the
  targets. A reworded violation message also reads as a wrong reason until the case's `why` follows
  it. A red `test_mutation_anchor_applies[<case>]` in the suite means a change moved the code that
  case edits: re-plant the case against the new code in the same commit and re-run it. A retarget
  or removal records its reason in the commit body. `.github/workflows/mutants.yml` runs every case
  in `mutants.CASES` on demand (`workflow_dispatch`) and fails when a case survives or none are
  found. It gates nothing: it never runs on push or pull request, and publish does not read it.
  GitHub offers a manual run only for a workflow on the default branch, so it becomes dispatchable
  at the release that first carries it to `main`.
- **Recorded limitations.** The **issuer/audience swap** is undetectable while `ISSUER ==
  AUDIENCE` (both `rcac-mcp` in `token.py`): no token can tell a verifier that swapped the two from
  a correct one, so no test pins it. If the constants ever differ, add the case. File I/O is not
  guarded (above).
- **Internals-bound tests**, the rewrite points for a stack move and for `safe-execution`; items
  1–5 say so in their docstrings:
  1. `tests/harness.py`, all of it: the FastMCP/mcp client, the `httpx2` ASGI route, the
     middleware API, the era selector and result decoding (rewritten for FastMCP 4 / MCP SDK 2).
  2. `tests/test_exec_mode.py`: the `rcac_mcp.create_mcp_server` stub and the
     `rcac_mcp.executor.ssh.Connection` fake.
  3. `tests/test_executors.py`: the `Connection` fake (the `ssh` row) and the argv rows, which are
     `safe-execution`'s deliberate rewrite.
  4. `tests/test_tokens.py`: `create_jwt_auth()` returning a `JWTVerifier` (imported through the
     harness), and acceptance and rejection observed through the harness's HTTP route (the
     FastMCP 4 move left the module unchanged: the route's changes stayed in the harness).
  5. `tests/mutants.py` and its anchors in `tests/test_mutations.py`: tied to today's source text,
     so any cycle that edits an anchored line re-plants.

  `tests/test_registry.py` and `tests/test_instructions.py`, which once read the FastMCP server
  API directly, now read the served tools and instructions through harness verbs and left the list.

  Everything else speaks harness verbs or fakes at the stdlib boundary, and is expected to pass
  through both cycles unchanged.
- **A stale checkout fails the absence check, and the check is right.** Git leaves ignored files
  behind, so a checkout that ran the code from before the documentation package was removed can
  keep `src/rcac_mcp/docs/__pycache__/`. That directory imports as an empty namespace package, and
  `test_docs_modules_absent[rcac_mcp.docs]` fails with a message naming the path. Delete the
  leftover with `del src/rcac_mcp/docs` (§ Environment & working rules) and re-run; a fresh clone,
  the wheel and the sdist never carry it.
- Integration tests drive the installed CLI, and **each one wraps its own drives in
  `bin/sandbox.sh`** (checked statically, above). A whole-suite run under the sandbox still steps
  back into the repo first (`cd "$CLUSTER_MCP_REPO"`) so pytest finds `pyproject.toml`
  (§ Commands).
- **PyYAML is not a dependency of this repository**, runtime or dev; it reaches `uv.lock` only
  transitively (through `fastmcp`), and nothing in `src/` imports it. The factory FSM scripts live
  in the operator's KB, which runs them with `uv run --with pyyaml`.

## Packaging

- `hatchling`; the wheel ships only `src/rcac_mcp`.
- **`uv.lock` is tracked.** This server is installed straight from `git+https://…`, so the lock is the
  only thing pinning the transitive tree a user actually receives. A change to `[project.dependencies]`
  or `[dependency-groups]` runs `uv lock` and commits the result **in the same commit**; the release
  recipe's `bump.files` stages it alongside the version bump. `uv lock --check` exits nonzero when
  the two have drifted.
- **The release gate audits the lock.** `bin/audit_lock.py` (stdlib; `uv audit` over the tree
  `uv.lock` resolves) fails the release recipe's `gate` when any locked package carries a known
  advisory, and when the advisory source cannot be reached (§ Factory → Release recipe, its `audit`
  caveat). It needs the network, so the profile's `verify` does not run it; CI, which mirrors the
  gate, does (§ Conventions).
- **The sdist must not ship `bin/`, `spec/`, `issues/`, `.security/`, or `tests/fixtures/`.**
  `[tool.hatch.build.targets.sdist]` excludes them, and `bin/sdist_hygiene.py`, which the release
  recipe's `gate` and CI both run, re-checks the built tarball — a `.security/` path in a published artifact would publish an inventory of unremediated
  weaknesses.
- **CI runs the gate on every push and pull request.** `.github/workflows/ci.yml` runs the profile's
  `setup` and `verify` (the suite in both protocol eras) and the release recipe's dependency audit
  and build checks for each push to any branch and each pull request into `develop` or `main`
  (§ Conventions binds the step lists). There is **no publish workflow and no published container
  image**, and the project is not on PyPI — users install from
  `git+https://github.com/purduercac/rcac-mcp`. The release recipe cuts a tag and a GitHub release
  and says so plainly.
- A **container build does exist locally**: `Dockerfile` + `compose.yml` (+ `nginx-dev.conf` for the
  TLS dev proxy), documented in `README.md` § *Docker Compose with TLS*. `compose.yml` pins the
  server's argv (`-t http -H 0.0.0.0 -p 8000 -a jwt`), so a change to a CLI flag name or default is
  a change to those files too — same same-commit rule as `SERVER_INSTRUCTIONS` and the README.

## Working on this codebase as an agent

- **Use the factory for non-trivial work.** A feature/fix/refactor flows through the spec-driven
  lifecycle hosted in the operator's KB, run as `code rcac-mcp …` from that KB — feature (shape
  `GOAL.md`, or promote an `issues/{slug}.md`) → plan (research + `PLAN.md`/`TECH.md`) → build
  (execute phases) → review (blind, externally-verified QA) → publish (the profile's `landing`),
  each on a branch named by the profile's `branches`, with artifacts committed under
  `spec/{slug}/`. The KB's `method/agentic-engineering.md` is the *why*; § Invariants is the
  curated footgun checklist that plan and review read. Ceremony scales to appetite: a one-sentence
  change may skip the lifecycle entirely. Harness friction (not code issues) is a `friction` line
  in the operator's KB inbox, never a file here; `code harness` applies those fixes to the KB's
  skills — and never weakens a non-negotiable gate on a finding's say-so.
- **Retire what shipped with `code rcac-mcp roadmap`** (operational, **not** a lifecycle step). It
  finds every `issues/{slug}.md` carrying `status: adopted:{slug}`, confirms the cycle actually
  reached the profile's `base` on `origin`, because the marker only proves a cycle *started*, and
  after a human-gated preview deletes the seed and its `ROADMAP.md` entry — then repairs the drift
  the removal leaves. It is deliberately **not** folded into publish: retirement writes outside
  `spec/`, which is exactly what publish's staleness gate exists to notice.
- **Cut releases with `code rcac-mcp release`** (also operational): it follows § Factory → Release
  recipe — bumps the single version source, runs the gate (pytest, `uv build`, `twine check --strict`,
  sdist hygiene), signs a tag, and publishes a GitHub release — rehearsed in a `git worktree` dry-run
  and gated on an explicit human OK before any irreversible push.
- **Parallelizing agent work:** the subsystems safe to touch independently are the leaf tool modules
  (`tools/slurm.py`, `tools/rcac.py`, `tools/filesystem.py`), docs, and tests. The coupled core —
  `context.py`, `middleware.py`, `auth.py`, `token.py`, `executor/*`, `server.py` — shares the
  contracts above and should be changed with a single coherent view; parallel edits there conflict on
  *contracts*, not just lines. When in doubt, fan out to read/understand and serialize the edit.
- **This file is the map, but it drifts.** For a deep change, re-verify the specific invariant
  against the source before relying on it, and update this file when the code moves.

## Factory

This repo is built by the spec-driven factory: GOAL → PLAN/TECH → build → blind review → publish. The method and the skills live in the operator's KB (`method/agentic-engineering.md`, `skill/code-*.md`). The cycle's records live here: `spec/{slug}/` for work in flight, `issues/` and `ROADMAP.md` for deferred work. Inside this repo, its own rules win.

### Profile

| Key | Value |
|---|---|
| base | `develop`, the branch every cycle starts from and lands on; `main` moves only at a release, by fast-forward to a released `develop` commit |
| branches | `feature/{slug}`, `fix/{slug}`, `refactor/{slug}`, `docs/{slug}`, `test/{slug}`, `ci/{slug}`: the prefix is the GOAL's `kind`; the set is open |
| commit categories | `[feature]` `[fix]` `[docs]` `[refactor]` `[test]` `[ci]` `[release]` `[harness]`: lowercase, an open set (coin one when it fits). Branch commits take the GOAL's `kind` verbatim; landing subjects use these categories |
| branch commits | `[{kind}] Build {slug} P<n>`; no `Co-Authored-By:` trailer (existing repo convention) |
| landing | `squash-local`: squash onto `origin/develop` in the cycle worktree as one `[category] What changed` commit, then push `develop` |
| ci | `.github/workflows/ci.yml`, job `gate`: the one check publish reads. Before a `squash-local` landing, list the push runs of the cycle branch's head commit with `gh run list --workflow ci.yml --commit "$(git rev-parse HEAD)" -e push --json databaseId,attempt,status,conclusion`: at least one run must be listed, and every listed run, in its latest attempt, must be `completed` with conclusion `success`. A red, cancelled, missing or unfinished run is not green. Pull-request runs and `.github/workflows/mutants.yml` are never read |
| setup | `uv sync --locked`: dependency refresh, run before any verify |
| verify | `uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && bin/sandbox.sh uv run rcac-mcp --help`: the full gate. Phases narrow it; publish and release run all of it. It runs the suite in both protocol eras: the handshake era first, then revision 2026-07-28 (§ Testing) |
| throwaway environment | `bin/sandbox.sh <cmd…>` (contract below) |
| coupled core | `src/rcac_mcp/context.py`, `middleware.py`, `auth.py`, `token.py`, `executor/*`, `server.py` |
| same-commit rules | see § Conventions |
| high-blast-radius paths | see § Invariants |
| security lane | on: unremediated security findings go to the gitignored `.security/`, never `issues/` |
| release | see Release recipe (`release.trigger`) |

### Release recipe

```yaml
release:
  trigger: on-demand
  version:
    source: pyproject.toml project.version
    scheme: pep440
    tag_format: "{v}"
    prerelease_syntax: '^\d+\.\d+\.\d+(a|b|rc)\d+$'
    propose_next: by-kind
  bump:
    lock: uv lock
    lockstep: []
    files: [pyproject.toml, uv.lock]
    diff_rule: only project.version in pyproject.toml and the rcac-mcp version entry in uv.lock change
    subject: "[release] Version {v}"
  gate:
    - uv lock --check
    - uv run python bin/audit_lock.py
    - uv run pytest -q
    - CLUSTER_MCP_TEST_ERA=modern uv run pytest -q
    - uv build -o dist/
    - uvx twine check --strict dist/*
    # Sdist hygiene: fails on any repository furniture in a built sdist, or when there is no sdist.
    # bin/sdist_hygiene.py is the one definition of the furniture set; nothing restates it.
    - uv run python bin/sdist_hygiene.py dist/
  tag:
    message: "rcac-mcp {v}"
    signed: true
  notes: git-log-by-category
  modes:
    release:
      base: develop
      prerelease: forbidden
      final: true
      inputs: []
      work_branch: in-place
      branch_ops: []
      merge_into: main          # FAST-FORWARD only (git merge --ff-only), never --no-ff: main never takes a commit that is not on develop
      tag_on: bump
      back_merge_into: none
      push: [HEAD:develop, HEAD:main, "{v}"]   # each its own approval
      publish: gh release create {v} --verify-tag
  post_verify:
    - bin/sandbox.sh uvx --from git+https://github.com/purduercac/rcac-mcp@{v} rcac-mcp --help
  caveats:
    main-fast-forward: >-
      merge_into main is a fast-forward to the released develop commit, never a merge commit; main
      never takes a commit that is not on develop. If main cannot fast-forward, STOP.
    ci-mirror-no-index: >-
      CI (`.github/workflows/ci.yml`) mirrors this gate on every push and on pull requests into
      develop and main. The recipe keeps its own copy because a release can be cut before CI has
      run on that commit: run the gate here, and never wait on CI in its place. Pushing develop and
      main starts CI runs; the tag push starts none. There is no publish workflow, no published
      container image, and no PyPI project. A release is a signed tag plus a GitHub release, and
      users install from `git+https://github.com/purduercac/rcac-mcp@{v}`.
    pypi: >-
      A PyPI upload (uvx twine upload dist/*) is out of scope unless Geoffrey opts in for that
      release; a version string on a package index can never be reused.
    twine: >-
      twine check --strict runs although nothing is uploaded: it validates the metadata a future
      publish depends on and catches a broken long description before it is baked into a tag.
    no-sources: >-
      The old gate built with uv build --no-sources. pyproject.toml has no [tool.uv.sources] today,
      so uv build -o dist/ is equivalent; add --no-sources if a source override is ever added.
    security-in-sdist: >-
      A .security/ path in the sdist is a hard stop. Tell Geoffrey plainly: it would publish an
      inventory of unremediated weaknesses. The hygiene check names each offending path.
    audit: >-
      bin/audit_lock.py runs uv audit over the tree uv.lock resolves (frozen, --no-config, --project
      naming this checkout, every UV_* outside a short allowlist dropped) and exits non-zero naming
      package, version and advisory when any locked package carries a known advisory. It needs the
      network and fails closed without it: an unreachable advisory source, an audit that cannot run,
      or a summary covering fewer packages than the lock pins is red. There is no waiver: an
      advisory with no fixed release holds the release until a seed takes it up. The profile's
      verify never runs it: phase gates stay offline-capable and deterministic. CI runs it, as part
      of its mirror of this gate (ci-mirror-no-index), on a runner with network.
    lock: >-
      uv.lock is tracked and pins the transitive tree a git+https install receives; a bump that
      leaves it stale ships a version the lock disagrees with. If anything besides pyproject.toml
      and uv.lock wants staging in the bump, STOP and look.
    versions: >-
      PEP 440, no v prefix, and no hyphen in a prerelease (0.2.0a1, not 0.2.0-a1). The version must
      be strictly greater than the latest tag and not already a tag.
    first-release: >-
      The first tag, 0.1.1 (2026-10-18), drew its notes from the beginning of history; every later
      release's notes start at the tag before it.
    install-probe: >-
      post_verify runs under bin/sandbox.sh so the probe cannot pick up the working tree or reach a
      real cluster.
  clean_paths: [dist/]
```

### Throwaway environment

`bin/sandbox.sh <cmd…>` runs `<cmd…>` against a disposable instance of the app. It:

1. runs from the repo or worktree root;
2. creates a fresh state directory and removes it on every exit path;
3. redirects all app state (database, config, index, logs, keys) into that directory, so real data is never touched;
4. runs the command inside it, so relative writes can't reach the worktree;
5. exercises the current tree: rebuild or run from source, never a stale binary;
6. pins tool discovery back to the repo;
7. writes its own output to stderr and passes the command's exit status through.

Here: the root is the checkout the script lives in; the state directory is a `mktemp -d` under
`$TMPDIR`, removed by a trap; `HOME` and XDG point into it, the auth environment is blanked, and
`RCAC_SSH_HOST` is unset so the default stdio→`ssh` path fails closed; `uv run` runs this tree from
source, with `UV_PROJECT` and `CLUSTER_MCP_REPO` pinning discovery back to the repo.

### Where deferred work goes

**Where a deferral goes — five homes, one rule each.** A pass that decides *not* to fix something
must still record it, and the destination is not a matter of taste:

| File | Holds | Written by |
|------|-------|------------|
| `issues/{slug}.md` | **Deferred code work**, pre-shaped from the KB's `ISSUE.md` template, `status: unshaped` | whoever defers it |
| `ROADMAP.md` | the **ordered index** — one entry per issue, `**Seed:**` pointing at the `issues/` file | whoever defers it |
| `.security/issues/{slug}.md` + `.security/ROADMAP.md` | the same two things for **unremediated security findings** — gitignored, never published | whoever defers it |
| `spec/{slug}/` | work **actually in flight** | the lifecycle skills |
| the KB inbox | **Harness/skill feedback only** — "was this the *factory's* fault", as `friction` lines. Never code follow-ups. | the lifecycle skills |

An `issues/{slug}.md` is a *candidate, not a contract*: the feature step promotes it into
`spec/{slug}/GOAL.md`, and that promotion is where appetite, non-goals and the R-IDs get negotiated
with a human. **Never copy one into a `GOAL.md` verbatim** — the review step grades a GOAL, and a
proposal nobody accepted is not a contract. The `status:` field is the guard: `unshaped` (raw
deferral — evidence captured, nothing agreed), `shaped` (already negotiated with a human, but **not
yet accepted into a cycle**), `adopted:{slug}` (promoted; `spec/{slug}/` owns it now, and the record
stays *while the cycle is in flight* so the ROADMAP index does not dangle). Two further values close
a deferral **without** shipping — `declined` and `accepted-behaviour` — and those are terminal
records, indexed under `ROADMAP.md` § *Settled questions* rather than as cycles.

**A deferral is retired, not kept forever.** When the cycle that adopted a seed lands on the
profile's `base`, `code rcac-mcp roadmap` deletes the seed and removes its `ROADMAP.md` entry:
`spec/{slug}/` is the retained account and git history holds the file. Retire only on evidence the
cycle *landed* — an `adopted:{slug}` marker proves a cycle started, never that it finished. The
security lane inverts this: nothing under `.security/` is ever deleted, because it is gitignored and
a deletion there leaves no history to recover from.

**The security lane is not optional here.** This is a multi-user command-execution service that runs
as root in one of its modes, on shared research infrastructure. A deferral describing an
*unremediated* weakness — a live path to running a command as the wrong user, on the wrong host, or
without the intended authentication — goes in `.security/`, which is gitignored along with `.local/`.
Publishing a standing roadmap of live vulnerabilities hands an attacker a work plan. The *fixes* land
as ordinary public commits and PRs when they ship; only the inventory of what is still open stays
private. When in doubt which lane an item belongs to, use `.security/` and ask.

This coexists with the **GitHub tracker**: a GH issue is the public-facing ticket, `issues/{slug}.md`
is the pre-shaped spec behind it, and the two may link to each other. Neither replaces the other.

## Invariants

The gate for every plan and the footgun list for every review. A plan walks only the sections its change touches. A review grades every violation of a numbered section, lettered subsections included, as CRITICAL; a violation of § Conventions is HIGH.

Only invoke the sections relevant to the change — do not manufacture findings against untouched
subsystems.

**This section describes what is true today, not what is planned.** The project is mid-generalization
from `rcac-mcp` to `cluster-mcp` (see `ROADMAP.md`); when a cycle lands a new contract — packaging
extras, a plugin registry, a capability-probe protocol — it adds the section here in the same commit
that lands the contract. Do not pre-write invariants for work that has not shipped.

### High-blast-radius paths (human gate)

A CONFIRMED finding in any of these files needs a human decision before the cycle lands; a plan touching one always runs research. The list only widens: when code moves, add the new home and never remove the old entry. Skills and templates cite this list and never copy it.

- `src/rcac_mcp/context.py`
- `src/rcac_mcp/middleware.py`
- `src/rcac_mcp/auth.py`
- `src/rcac_mcp/token.py`
- `src/rcac_mcp/executor/base.py`
- `src/rcac_mcp/executor/delegate.py`
- `src/rcac_mcp/executor/ssh.py`
- `src/rcac_mcp/server.py`
- `src/rcac_mcp/__init__.py` — `_resolve_exec_mode()`, the execution-mode posture ladder (§4)
- `bin/sandbox.sh` — the throwaway environment; a mistake there turns a verify into a command
  against a real home or a production cluster

These are the files where a mistake executes someone else's command, executes as the wrong user, or
executes on the wrong machine. Everything else in the tree is a leaf.

#### Footguns (quick reference)

The two leaf files at the end of this list are footguns, not human-gated paths.

- **`context.py`** — 47 lines, the highest blast radius in the repo. A module global here is
  cross-user command execution.
- **`middleware.py`** — where identity becomes an executor. Claim precedence, the user-map check, and
  `return await call_next(context)` are all load-bearing.
- **`executor/delegate.py`** — the `sudo` argv, `-n`, `shlex.quote` on `cwd` only, the server's
  `$SHELL`.
- **`auth.py` / `token.py`** — the 32-char minimum in two places; the issuer/audience/algorithm
  triple that must match across signer and verifier; OIDC's all-four check and the proxy's held
  posture (§6). `AUTH_MODES`' annotation is checked in its resolved shape: a stray `:` once made its
  `Final` argument a `slice`, which evaluated without error, PEP 563 or not.
- **`executor/ssh.py`** — reconnect backoff; the *uncaught* Fabric timeout that makes `run()`'s
  contract differ from the other two backends.
- **`__init__.py`** — `_resolve_exec_mode()`, the posture ladder.
- **`server.py`** — `SERVER_INSTRUCTIONS` (what every model reads) and import-time `MCP_BASE_URL`.
- **`tools/__init__.py`** — the bottom-of-file import list; a missing entry silently drops a tool.
- **`resources.py`** — the process-global context cache keyed by a non-unique hostname.

### 1. Per-request executor isolation (`context.py` + `middleware.py`) — highest blast radius [human gate]

- **The current executor lives in a `ContextVar` (`context.py`), never a module-level global.**
  `executor_var` is task-local; async tasks (one per request under FastMCP/Starlette) each get their
  own copy, so each request gets its own executor. A sync tool runs on a worker thread that receives
  a copy of the request's context, so it reads the executor its call's middleware set; a
  `set_executor()` inside a tool would land only in that thread's copy. Replacing it with a plain module global is a
  **cross-user command-execution vulnerability** — user A's request would run as user B.
  `SECURITY.md` § *Request Isolation in Delegate Mode* states the contract and shows the unsafe form
  explicitly — read it before touching this. The three bullets below are its consequences that are
  easy to get wrong.
- **`set_executor()` is called per tool call, from middleware `on_call_tool`** — not at server
  construction, not once per connection. `SharedExecutorMiddleware` re-sets the *same* instance every
  call precisely because a ContextVar set in one task is invisible to the next.
- **Tools never construct an executor.** They call `get_executor()`, which raises `RuntimeError` when
  unset. A tool that instantiates its own executor bypasses delegation entirely.
- **Any new middleware must not swallow the call chain** (`return await call_next(context)`), above
  all one that runs *before* the executor middleware, or the executor is never set for that request.
- **`ToolErrorMiddleware` is the error boundary and wraps the executor middleware.**
  `create_mcp_server` adds it before the middleware it is given, and FastMCP runs middleware added
  later inside middleware added earlier. FastMCP 4 turns only exceptions from a tool's *body* into
  an error result; one raised by middleware (§5's `PermissionError` and `RuntimeError`, an
  `MCPError`) would reach the client as a masked `Internal server error`. The boundary re-raises any
  `Exception` but a `FastMCPError` as `ToolError(str(exc))`, so the client sees the reason as a
  tool error result, exactly as on 2.x. It catches `Exception`, never `BaseException`
  (cancellation propagates), sets nothing, and returns `await call_next(context)`. It would also
  turn the error FastMCP 4 raises for a tool that needs a capability the client lacks into a tool
  error result rather than a protocol error; no tool here needs one, and the first that does
  revisits the boundary. Middleware state
  (`Context.set_state`) is async in 4.x and is set request-scoped (`serializable=False`), so a
  per-user value never reaches the session store, where a later request on the same session would
  read it; `test_protocol.py::test_delegate_state_is_request_scoped` pins it.

### 2. Executor Protocol contract (`executor/base.py`) [human gate]

- `Executor` is a `@runtime_checkable` `Protocol` with **eight** members: `hostname` (property),
  `run`, `put`, `get`, `open`, `close`, `__enter__`, `__exit__`.
- **Three implementations move together:** `SSHExecutor`, `LocalShellExecutor`, `DelegatingExecutor`.
  Adding or changing a Protocol member means editing all three in the same commit — there is no base
  class to inherit a default from, and `runtime_checkable` `isinstance` checks **method presence
  only, never signatures**, so a mismatched signature fails at call time, not at registration.
- `open()`/`close()` are **no-ops** for the local and delegating executors and real for SSH. Code
  that depends on `close()` releasing something must not assume it did.

### 3. `CommandResult` and exit-code semantics [human gate]

- Every executor returns `CommandResult(stdout, stderr, exit_code, hostname)`. Tools format it; they
  do not invent their own result shape.
- **`exit_code = -1` is overloaded.** `LocalShellExecutor.run` and `DelegatingExecutor.run` return
  `-1` on `subprocess.TimeoutExpired`, which collides with a real subprocess return code of `-1`
  (killed by SIGHUP). Do not add new sentinels in the signal range `-1..-64`; if a distinct "never
  ran" signal is needed, pick a value below `-1000` and document it here first.
- **Timeout behavior is asymmetric across executors.** Local and delegate *catch* the timeout and
  return a `CommandResult`; `SSHExecutor.run` passes `timeout` to Fabric and does **not** catch —
  Invoke raises `CommandTimedOut`, which propagates to the caller. A change that assumes one shape
  breaks the other mode. Reproduce against both before relying on either.
- `hostname` differs by mode: the SSH target for `SSHExecutor`, `socket.gethostname()` for the local
  and delegating executors. It is **not** a user-distinguishing key: in delegate mode every user
  reports the same value (see §8).

### 4. Execution-mode resolution (`__init__.py`) — read before touching `__init__.py` or `middleware.py` [human gate]

`MCPServerApp._resolve_exec_mode()` is a **security posture ladder**; changing its order changes who
runs what, where. The current order is:

1. explicit `-e/--exec-mode` wins;
2. `transport == 'stdio'` → `ssh`;
3. `--ssh-host` or `RCAC_SSH_HOST` set → `ssh` (regardless of transport);
4. `auth == 'none'` → `local`;
5. otherwise → `delegate`.

- **`local` mode executes as the server process owner with no authentication**, and — read rung 4
  carefully — it **is** what a bare `rcac-mcp -t http` or `-t sse` resolves to today, because
  `DEFAULT_AUTH` is `'none'` and rung 3 only preempts when an SSH host is configured. That is the
  documented out-of-the-box posture (`APP_HELP`: "default for http with auth=none"; `SECURITY.md`
  § *Mode 2*), not a defect to report. The invariant is: **never widen it.** Do not make `local`
  reachable when authentication *is* configured, and do not add a rung that reaches it earlier.
- **`delegate` mode requires authentication.** `AuthExecutorMiddleware` is only installed for
  `exec_mode == 'delegate'`; the other modes install `SharedExecutorMiddleware`. Never wire delegate
  behavior into the shared path.

What each mode means:

| Mode | Runs as | Auth | Notes |
|---|---|---|---|
| `ssh` | the SSH user, on the cluster | none at the MCP layer | The recommended path. The security boundary *is* the SSH connection. |
| `local` | the server process owner, on this box | none | **Development only.** Anyone who reaches the endpoint runs commands as you. |
| `delegate` | the mapped local user, via `sudo` | jwt or oidc, **required** | Server runs privileged; OS controls (sudoers/PAM) are the real enforcement. |

### 5. Delegate mode — sudo wrapping, user map, identity claims (`executor/delegate.py` + `middleware.py`) [human gate]

- **Identity claim precedence is `sub` → `email` → `preferred_username`**, first non-empty wins
  (`AuthExecutorMiddleware._extract_identity`). Reordering silently re-maps users. No claim → hard
  `RuntimeError`, never a fallback identity.
- **An identity absent from the user map is a `PermissionError`, never a default user.** The map is
  loaded once at middleware construction from `RCAC_USER_MAP`; `DelegatingExecutor.__init__` re-checks
  and raises `KeyError`. Keep both checks — the executor is constructible outside the middleware.
- Commands run as `sudo -n -H -u <local_user> <shell> -c <command>`. **`-n` (non-interactive) is
  required** — there is no TTY, so dropping it turns a missing sudoers rule into a hang instead of an
  error. `cwd` is `shlex.quote`d; `command` deliberately is not (it *is* a shell command).
- `DelegatingExecutor._shell` is the **server process's** `$SHELL`, not the target user's. It is
  chosen at construction; do not read it per-command from an attacker-influenced environment.
- **The user map file format is `<identity> <local-user>`, one per line**, `#` comments, first
  whitespace splits. A malformed line raises `ValueError` naming the line number — a partial map is
  worse than no map.

### 6. Auth providers and token claims (`auth.py` + `token.py`) [human gate]

- **`JWT_SECRET` must be at least 32 characters.** Enforced in *two* places — `create_jwt_auth()` and
  the `--generate-token` path in `MCPServerApp.run()`. Never weaken either; never weaken or fix
  one and leave the other.
- **`ISSUER`, `AUDIENCE`, `ALGORITHM` are single-sourced in `token.py`** and consumed by
  `create_jwt_auth()`. Signer and verifier must agree; changing one alone silently rejects every
  token.
- `AUTH_MODES` maps `'none' | 'jwt' | 'oidc'` to zero-argument provider factories, annotated
  `Final[Dict[str, Callable[[], AuthProvider | None]]]`: `'none'` returns `None` (FastMCP's "no
  auth": the server installs no provider), `'jwt'` a `JWTVerifier`
  (`fastmcp.server.auth.providers.jwt`), `'oidc'` an `OIDCProxy` (imported, with `AuthProvider`,
  from `fastmcp.server.auth`, which re-exports `fastmcp.server.auth.oidc_proxy`). A new auth mode
  is a new entry **plus** the `-a/--auth` `choices` list in `MCPServerApp` — they are not derived
  from each other.
- OIDC requires all four of `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `MCP_BASE_URL`;
  the all-or-nothing check exists so a half-configured proxy fails at startup, before any provider
  is built or any request is made, with an error naming all four. FastMCP 4's `OIDCProxy` makes
  `client_secret` optional; it is required here. `create_auth_oidc()` builds the module-level
  `OIDCProxy` (no local import), which is what lets a test replace it and see nothing built.
- **The OIDC proxy is held at the posture it had on FastMCP 2, pending a deliberate decision.**
  FastMCP 4's `OIDCProxy` fetches client-supplied metadata documents (CIMD, `enable_cimd`) and
  forwards the `resource` parameter upstream (`forward_resource`), both on by default; FastMCP 2
  did neither.
  `create_auth_oidc()` passes both `False`. Turning either on is a posture change, not a cleanup:
  decide it, then change this line, `SECURITY.md` and the posture test together.
- **Upgrading an `-a oidc` deployment from FastMCP 2 drops its client registrations** (an accepted
  change, § Project). The proxy's store moved from an encrypted `DiskStore` at
  `<home>/oauth-proxy/cache.db` to an encrypted `FileTreeStore` under
  `<home>/oauth-proxy/<key-fingerprint>/` (`<home>` is FastMCP's home directory), and the new store
  does not read the old one, so every client registered before the upgrade must register again.

### 7. Tool registration is an import-time side effect (`tools/__init__.py`)

- `TOOL_REGISTRY` and `mcp_tool` are defined **first**, then each tool module is imported at the
  bottom of the file. `@mcp_tool` appends at decoration time, so **a tool module missing from that
  import list registers nothing and fails silently** — no error, just an absent tool.
- **`@mcp_tool` replaces the function with a `fastmcp.tools.function_tool.FunctionTool`, which is
  not callable** (the function itself is its `.fn`). Other Python code cannot invoke a decorated
  tool directly; `storage_paths()` from
  `resources.py` raises `TypeError: 'FunctionTool' object is not callable`. When logic must be shared
  between a tool and something else, factor it into an undecorated helper and have both call that.
  (`resources.py` currently gets this wrong — see the `storage-paths-resource-typeerror` seed.)
- **`@mcp_tool` serves the full docstring as the description**
  (`Tool.from_function(func, description=inspect.getdoc(func))`). FastMCP 4 on its own serves only
  the summary paragraph; dropping the keyword silently cuts `Args:`, `Returns:` and `Examples:` from
  what every model reads (§9). FastMCP 4 also copies each `Args:` line into that argument's
  `description` in the input schema, whatever the keyword says.
- Tool return values are serialized by FastMCP. Returning a `@dataclass` (`CommandResult`,
  `StoragePaths`) is supported and used; returning an arbitrary object is not.

### 8. Resources and the `/etc/agents.d/*.md` convention (`resources.py`)

- `RESOURCE_REGISTRY` (`resources.py`) exposes `rcac://context` and `rcac://storage`.
- **`/etc/agents.d/*.md` is a deliberate, external, cross-site convention** — cluster administrators
  drop markdown there and any conforming client picks it up. Files are found with a `maxdepth 1`
  glob and concatenated in `sort` order with a `<!-- Source: {filename} -->` header each. Do not
  change the path, the glob, the ordering, or the header format without treating it as a
  compatibility break; the intent is to get *other* centers to adopt it.
- A missing directory or empty result is **not an error** — it caches and returns `''`.
- **`_cluster_context_cache` is a process-global dict keyed by `executor.hostname`.** In delegate mode
  every user's executor reports the same `socket.gethostname()`, so the first user's context is served
  to all subsequent users. That is a known defect with a filed seed, not a design choice — do not
  build new per-user state on this cache, and do not key any new cache on `hostname` alone.
- **A resource read gets no executor.** Both executor middlewares set it in `on_call_tool` only, so
  reading `rcac://context` fails with `No executor configured` in every mode and both protocol
  eras (it did on FastMCP 2 too). It fails closed. That is a known defect with a filed seed
  (`issues/resource-reads-without-executor.md`), not a design choice; a fix that sets an executor
  for reads makes the cache above reachable across users in delegate mode, so it lands with or
  after that cache's retirement. Never satisfy a read with an executor that is not the request's
  own (§1).

### 9. The agent-facing surface is part of the API (`server.py`) [human gate]

- `SERVER_INSTRUCTIONS` (`server.py`) is what every connecting model reads before choosing a tool.
  **Adding, renaming, or removing a tool — any tool change — updates `SERVER_INSTRUCTIONS` in the
  same commit** — a tool the instructions do not mention is a tool agents will not reach for, and an
  instruction naming a tool that no longer exists is a hallucination the server itself induced.
- The same commit also updates the tool inventory in `README.md`, which is what a human reads before
  installing. Both are user-visible contracts.
- Tool **docstrings** are the per-tool schema description sent over MCP, which every model reads;
  they are API surface, not internal notes. The whole docstring is served as the description (§7),
  and each `Args:` line is served a second time as its argument's `description` in the input
  schema. Keep the `Args:` / `Returns:` / `Examples:` shape the existing tools use.
- **The served surface is pinned** against `tests/fixtures/served_surface.json`, the snapshot of
  what the server served before the FastMCP 4 move (`tests/test_surface.py`, § Testing): tool names,
  descriptions, argument names, required lists, types and defaults, resources and instructions.
  Any change to them, a reworded docstring line included, fails `test_served_surface_matches_baseline`.
  The fixture is 2.x-shaped by design (re-taken on the current stack it carries FastMCP 4's
  metadata, which the fixture's shape test rejects), so how it follows a deliberate surface change
  is not settled yet: raise it before making one (`issues/surface-fixture-after-a-surface-change.md`).

### Conventions (same-commit rules)

- **Version is single-sourced from `pyproject.toml`**, the release recipe's `version.source` —
  `rcac_mcp.__version__` reads it via `importlib.metadata.version('rcac-mcp')`. Never hardcode a
  version anywhere else.
- **Supported Python is 3.14+** (`requires-python = ">=3.14"`). Do not add compatibility shims for
  older interpreters.
- A change to `[project.dependencies]` or `[dependency-groups]` and its `uv lock` land in the same
  commit, and a CLI flag name or default moves `compose.yml` with it: § Packaging.
- **CI mirrors the gate.** `.github/workflows/ci.yml`'s `gate` job runs the profile's `setup`, then
  each command of its `verify`, then each release-recipe `gate` step that `verify` lacks: one step
  per command, verbatim, in that order. A change to any of the three lists changes the workflow in
  the same commit, and the reverse. Before them, one provisioning step installs the login shells the suite
  drives (`zsh`, `tcsh`, `ksh`, `csh`) and nothing else: the runner lacks them, and without them
  those tests skip on Linux. It has no local counterpart, since macOS ships them. The sdist furniture set lives only in `bin/sdist_hygiene.py`,
  which the recipe and CI both run. The workflow's `python-version` is the floor of
  `requires-python` and moves with it. Every `uses:` in `.github/workflows/` names a full commit
  SHA, the version in a trailing comment; the token stays `permissions: contents: read`, and no
  workflow reads a secret or writes outside its runner.
- **Put logic where it belongs:** command execution behind the `Executor` protocol; identity →
  executor in middleware; per-request state in a `ContextVar`. **Reuse `cmdkit.app.exit_status`
  constants for return codes** — do not invent integer literals. A new CLI flag is an
  `interface.add_argument` on `MCPServerApp` plus its entry in `APP_USAGE` / `APP_HELP`, in the same
  commit.
- **Tests: only `@mark.unit` and `@mark.integration` are real markers** under `--strict-markers`. Tag
  every new test; `tests/conftest.py` fails the run on one that carries neither (§ Testing). Still,
  do not write a `verify:` gate whose only assertion is a marker selection: a selection can match
  nothing, or not the tests you meant, and pass green. Name the test ids the gate depends on.
- **A CLI drive — any verify or test drive — never touches the developer's real home, config, or a
  real cluster.** Wrap it in
  `bin/sandbox.sh`, which unsets `RCAC_SSH_HOST` so the default stdio→ssh path fails
  closed instead of connecting to production. The sandbox `cd`s out of the repo, so a **pytest** drive
  must step back in — `sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest …'` — or pytest roots
  itself in the throwaway dir, never reads `pyproject.toml`, and silently loses `--strict-markers`.
- **Verify by driving the CLI, not just tests — in the sandbox.** After a change, exercise the real
  flow: `bin/sandbox.sh sh -c "uv run rcac-mcp -e local --help"`. **Exit 0 is not
  enough** — assert the post-condition you actually care about (the tool is in the registry, the
  error message and exit status are the intended ones, the file landed where you expected).
- **Comments (and docstrings) are declarative statements of the invariant or the *why*** —
  capitalized sentences (`# Non-interactive: there is no TTY to prompt on.`), not lowercase
  fragments, and **never** a feature-scoped spec id (`R#`, `P#`): those restart per feature, live in
  `spec/{slug}/`, and collide across branches. Requirement provenance lives in the commit, the PR,
  and the retained `spec/{slug}/`. Referencing stable things (a real symbol, a documented invariant,
  an `/etc/agents.d` path) is fine.
- **No `Co-Authored-By` trailer** on any commit (repo convention). PR *bodies* end with the Claude
  Code generation line.
- Point to a rule; don't restate it. A rule restated in N places is a same-commit obligation in all N.
- Prose and commit voice follow § Environment & working rules (commit subjects) and the comment
  rules above; voice is hygiene, not a review finding.
