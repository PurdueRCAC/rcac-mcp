# Invariant gate & footgun checklist

A curated, explicitly-enumerated subset of the load-bearing invariants in
[`AGENTS.md`](../../AGENTS.md), maintained **in lockstep with it** (`AGENTS.md` is ground truth — if
this drifts, fix it). Two consumers:

- **`cm-plan` (gate):** before research *and* after PLAN/TECH is drafted, walk the sections a change
  touches and confirm the design honors each. Record any bend in PLAN's deviation-justification
  table.
- **`cm-review` (footgun list):** a violation of any invariant here is **auto-CRITICAL** and, when it
  touches the high-blast-radius core, forces a human sign-off gate.

Only invoke the sections relevant to the change — do not manufacture findings against untouched
subsystems.

**This file describes what is true today, not what is planned.** The project is mid-generalization
from `rcac-mcp` to `cluster-mcp` (see `ROADMAP.md`); when a cycle lands a new contract — packaging
extras, a plugin registry, a capability-probe protocol — it adds the section here in the same commit
that adds it to `AGENTS.md`. Do not pre-write invariants for work that has not shipped.

## High-blast-radius files (any CONFIRMED finding here → mandatory human gate)

`src/rcac_mcp/context.py` · `src/rcac_mcp/middleware.py` · `src/rcac_mcp/auth.py` ·
`src/rcac_mcp/token.py` · `src/rcac_mcp/executor/base.py` · `src/rcac_mcp/executor/delegate.py` ·
`src/rcac_mcp/executor/ssh.py` · `src/rcac_mcp/server.py`

These are the files where a mistake executes someone else's command, executes as the wrong user, or
executes on the wrong machine. Everything else in the tree is a leaf.

---

## 1. Per-request executor isolation (`context.py` + `middleware.py`) — highest blast radius

- **The current executor lives in a `ContextVar`, never a module-level global.** `executor_var` is
  task-local; async tasks (one per HTTP request under FastMCP/Starlette) each get their own copy.
  Replacing it with a plain module global is a **cross-user command-execution vulnerability** — user
  A's request would run as user B. `SECURITY.md` § *Request Isolation in Delegate Mode* states the
  contract and shows the unsafe form explicitly.
- **`set_executor()` is called per tool call, from middleware `on_call_tool`** — not at server
  construction, not once per connection. `SharedExecutorMiddleware` re-sets the same instance every
  call precisely because a ContextVar set in one task is invisible to the next.
- **Tools never construct an executor.** They call `get_executor()`, which raises `RuntimeError` when
  unset. A tool that instantiates its own executor bypasses delegation entirely.
- Any new middleware that runs *before* the executor middleware must not swallow the call chain
  (`return await call_next(context)`), or the executor is never set for that request.

## 2. Executor Protocol contract (`executor/base.py`)

- `Executor` is a `@runtime_checkable` `Protocol` with **eight** members: `hostname` (property),
  `run`, `put`, `get`, `open`, `close`, `__enter__`, `__exit__`.
- **Three implementations move together:** `SSHExecutor`, `LocalShellExecutor`, `DelegatingExecutor`.
  Adding or changing a Protocol member means editing all three in the same commit — there is no base
  class to inherit a default from, and `runtime_checkable` `isinstance` checks **method presence
  only, never signatures**, so a mismatched signature fails at call time, not at registration.
- `open()`/`close()` are **no-ops** for the local and delegating executors and real for SSH. Code
  that depends on `close()` releasing something must not assume it did.

## 3. `CommandResult` and exit-code semantics

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
  and delegating executors. It is **not** a user-distinguishing key (see §8).

## 4. Execution-mode resolution (`__init__.py`)

`MCPServerApp._resolve_exec_mode()` is a security posture ladder; changing its order changes who runs
what, where. The current order is:

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

## 5. Delegate mode — sudo wrapping, user map, identity claims (`executor/delegate.py` + `middleware.py`)

- **Identity claim precedence is `sub` → `email` → `preferred_username`**, first non-empty wins
  (`AuthExecutorMiddleware._extract_identity`). Reordering silently re-maps users. No claim → hard
  `RuntimeError`, never a fallback identity.
- **An identity absent from the user map is a `PermissionError`, never a default user.** The map is
  loaded once at middleware construction from `RCAC_USER_MAP`; `DelegatingExecutor.__init__` re-checks
  and raises `KeyError`. Keep both checks — the executor is constructible outside the middleware.
- Commands run as `sudo -n -H -u <local_user> <shell> -c <command>`. `-n` (non-interactive) is
  required — there is no TTY, so dropping it turns a missing sudoers rule into a hang instead of an
  error. `cwd` is `shlex.quote`d; `command` deliberately is not (it *is* a shell command).
- `DelegatingExecutor._shell` is the **server process's** `$SHELL`, not the target user's. It is
  chosen at construction; do not read it per-command from an attacker-influenced environment.
- **The user map file format is `<identity> <local-user>`, one per line**, `#` comments, first
  whitespace splits. A malformed line raises `ValueError` naming the line number — a partial map is
  worse than no map.

## 6. Auth providers and token claims (`auth.py` + `token.py`)

- **`JWT_SECRET` must be at least 32 characters.** Enforced in *two* places — `create_jwt_auth()` and
  the `--generate-token` path in `MCPServerApp.run()`. Never weaken either; never weaken one and
  leave the other.
- **`ISSUER`, `AUDIENCE`, `ALGORITHM` are single-sourced in `token.py`** and consumed by
  `create_jwt_auth()`. Signer and verifier must agree; changing one alone silently rejects every
  token.
- `AUTH_MODES` maps `'none' | 'jwt' | 'oidc'` to provider factories, where `'none'` returns `None`
  (FastMCP's "no auth"). A new auth mode is a new entry **plus** the `-a/--auth` `choices` list in
  `MCPServerApp` — they are not derived from each other.
- OIDC requires all four of `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `MCP_BASE_URL`;
  the all-or-nothing check exists so a half-configured proxy fails at startup, not at first request.

## 7. Tool registration is an import-time side effect (`tools/__init__.py`)

- `TOOL_REGISTRY` and `mcp_tool` are defined **first**, then each tool module is imported at the
  bottom of the file. `@mcp_tool` appends at decoration time, so **a tool module missing from that
  import list registers nothing and fails silently** — no error, just an absent tool.
- **`@mcp_tool` replaces the function with a `fastmcp.tools.tool.FunctionTool`, which is not
  callable.** Other Python code cannot invoke a decorated tool directly; `storage_paths()` from
  `resources.py` raises `TypeError: 'FunctionTool' object is not callable`. When logic must be shared
  between a tool and something else, factor it into an undecorated helper and have both call that.
- Tool return values are serialized by FastMCP. Returning a `@dataclass` (`CommandResult`,
  `StoragePaths`) is supported and used; returning an arbitrary object is not.

## 8. Resources and the `/etc/agents.d/*.md` convention (`resources.py`)

- **`/etc/agents.d/*.md` is a deliberate, external, cross-site convention** — cluster administrators
  drop markdown there and any conforming client picks it up. Files are concatenated in `sort` order
  with a `<!-- Source: {filename} -->` header each. Do not change the path, the glob, the ordering, or
  the header format without treating it as a compatibility break.
- A missing directory or empty result is **not an error** — it caches and returns `''`.
- **`_cluster_context_cache` is a process-global dict keyed by `executor.hostname`.** In delegate mode
  every user's executor reports the same `socket.gethostname()`, so the first user's context is served
  to all subsequent users. That is a known defect with a filed seed, not a design choice — do not
  build new per-user state on this cache, and do not key any new cache on `hostname` alone.

## 9. The agent-facing surface is part of the API (`server.py`)

- `SERVER_INSTRUCTIONS` is what every connecting model reads before choosing a tool. **Adding,
  renaming, or removing a tool updates `SERVER_INSTRUCTIONS` in the same commit** — a tool the
  instructions do not mention is a tool agents will not reach for, and an instruction naming a tool
  that no longer exists is a hallucination the server itself induced.
- The same commit also updates the tool inventory in `README.md`. Both are user-visible contracts.
- Tool **docstrings** are the per-tool schema description sent over MCP; they are API surface, not
  internal notes. Keep the `Args:` / `Returns:` / `Examples:` shape the existing tools use.

## 10. Project conventions (same-commit rules) — violations are HIGH, not auto-CRITICAL

- **Version is single-sourced from `pyproject.toml`** — `__version__` reads it via
  `importlib.metadata.version('rcac-mcp')`. Never hardcode a version anywhere else.
- **Supported Python is 3.14+** (`requires-python = ">=3.14"`). Do not add compatibility shims for
  older interpreters.
- **`PyYAML` must stay resolvable in the dev environment** — the factory FSM scripts import it. It is
  pinned in the `dev` dependency group for exactly that reason; removing a runtime feature that
  happens to use YAML must not drop the dev pin.
- **Reuse `cmdkit.app.exit_status` constants for return codes** — do not invent integer literals. A
  new CLI flag is an `interface.add_argument` on `MCPServerApp` plus its entry in `APP_USAGE` /
  `APP_HELP`, in the same commit.
- **Tests: only `@mark.unit` and `@mark.integration` are real markers** under `--strict-markers`. Tag
  every new test. **No existing test carries a marker**, so `-m unit`/`-m integration` deselect
  everything today — do not write a `verify:` gate whose only assertion is a marker selection, or it
  passes green over zero tests. (Tracked: the `restore-test-coverage` seed.)
- **A CLI drive never touches the developer's real home, config, or a real cluster.** Wrap it in
  `.agents/factory/bin/sandbox.sh`, which unsets `RCAC_SSH_HOST` so the default stdio→ssh path fails
  closed instead of connecting to production. The sandbox `cd`s out of the repo, so a **pytest** drive
  must step back in — `sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest …'` — or pytest roots
  itself in the throwaway dir, never reads `pyproject.toml`, and silently loses `--strict-markers`.
- **Comments are declarative statements of the invariant or the *why*** — capitalized sentences, not
  lowercase fragments, and **never** a feature-scoped spec id (`R#`, `P#`): those restart per feature
  and collide across branches. Referencing stable things (a real symbol, a documented invariant, an
  `/etc/agents.d` path) is fine.
- **No `Co-Authored-By` trailer** on any commit (repo convention). PR *bodies* end with the Claude
  Code generation line.
