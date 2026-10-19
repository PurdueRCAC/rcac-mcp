# 04 — Executor contract and suite collisions (safe-execution tree, `fix/safe-execution` @ 4f4e43e)

All citations are to `.worktrees/safe-execution`.

## 1. Executor `run()` contract

- **Timeout result.** No backend raises on a timeout (`executor/base.py:103-106`). The code is
  `TIMEOUT_EXIT_CODE = -1001` (`base.py:19`), and the output is whatever arrived before the deadline.
  A command that never started returns `NOT_STARTED_EXIT_CODE = -1002` (`base.py:23`). `separate`
  keeps an unstarted timeout as an empty -1001 (`launch.py:276-278`). The notes go on stderr:
  `HELD_NOTE` (exited, but a holder kept output open, so reading stopped after `GRACE` with nothing
  arriving) and `LATE_NOTE` (exited, output still arriving `GRACE` past the deadline, reported as
  -1001) (`launch.py:174-177`). Bad arguments raise `TypeError`/`ValueError` before anything starts
  (`base.py:113-117`, `check_timeout` at `launch.py:318-326`).
- **How long past `timeout` a call can run.** `GRACE = 1.0` (`launch.py:81`).
  - local/delegate `run_process`: at the deadline it sends SIGTERM, drains for `GRACE`, then SIGKILL
    and another `GRACE` drain (`launch.py:388-402`). Worst case is timeout + 2 s.
  - ssh `_collect`: at the deadline it sends TERM, then `_stopped` drains for `GRACE`
    (`ssh.py:159-162`, `:99-107`), or ends at deadline + `GRACE` on the late-output path
    (`ssh.py:151-152`).
  - Two waits in ssh are **outside** `timeout`:
    - `transport.open_session(timeout=OPEN_TIMEOUT=30.0)` (`ssh.py:40`, `:345`).
    - `_ensure_connected()` (`ssh.py:268-295`). If the link has dropped, its backoff sleeps
      0.5+1+2+4+8+10 s, plus up to `CONNECT_TIMEOUT` per connect step.
  - So `run(timeout=T)` is **not** a hard wall-clock bound over ssh. A strict startup bound needs a
    helper thread with `join(T)`, on a daemon thread whose late result is discarded.
- **Connected at construction.** Yes: `SSHExecutor.__init__` calls `self.open()` (`ssh.py:247-248`).
  `_create_executor` builds it inside a try that turns any failure into `StartupFailure`
  (`__init__.py:240-245`). The connect bound is `CONNECT_TIMEOUT = 15` s for each of the TCP
  connect, banner and auth steps, through `_bounded` (`ssh.py:44`, `:190-211`); an ssh_config
  `ConnectTimeout` still wins. The read would run right after a successful connect, so
  `_ensure_connected` returns immediately.
- **Calling it outside a request.** `run()` never touches the ContextVar. Only tools call
  `get_executor()` (`context.py:24-37`). `MCPServerApp.run` already holds `executor`
  (`__init__.py:193`), so calling `executor.run(...)` there needs no context.
- **Thread safety.** There is no lock in `SSHExecutor`. It is already shared across threads: sync
  tools run on worker threads (AGENTS.md:1022-1024), so concurrent `run()` calls on one executor are
  today's normal case. Each call opens its own channel; Paramiko's `Transport.open_channel` takes
  `self.lock`. A startup helper thread that outlives its `join` would only be one more concurrent
  channel.
  - The one unlocked race is `_ensure_connected`'s reconnect, if the link drops. It is
    pre-existing, and listed under `issues/ssh-reconnect-loop.md` in the footguns.

## 2. `resources.py` and what references it

- `LIST_CONTEXT = fixed('find /etc/agents.d -maxdepth 1 -name "*.md" -type f 2>/dev/null | sort')`
  (`resources.py:27`). The loader runs that script, then `executor.run(['cat', '--', filepath])`
  per file, a plain argv (`:51`, `:66`), and joins with `<!-- Source: {name} -->\n{strip()}` and
  `'\n\n'` (`:70-72`).
- The cache is at `:24`, `:47-48`, `:55`, `:73`; `get_cached_context` is at `:77`,
  `clear_context_cache` at `:90`. The resource handler calls `get_executor()` (`:126`) and is
  registered at `:131-136`.
- Over ssh, each `cat` is a separate login-bash launch, so N+1 round trips at startup. A single
  fixed script would be better, but:
  - `FIXED_RESET` unsets only `cat cd chmod cp find mkdir printf sort` (`launch.py:167`).
  - A script that calls a regular builtin such as `read`, `test`/`[` or `echo`, or another program
    by name, can be shadowed by a function a profile exports. Such a script needs `launch.py`
    widened, which is human-gated, or must avoid those names (`exec` bypasses functions).
  - Telling "missing" apart from "unreadable" (R8) cannot come from the pipeline's status: there is
    no pipefail, and `find`'s error goes to `/dev/null`.
- **Tests that touch it:**
  - `test_tools.py:471-491` `test_context_resource_lists_with_a_fixed_script` uses `LIST_CONTEXT`,
    `clear_context_cache` and `load_cluster_context`, and pins the argvs.
  - `test_surface.py:234` asserts the resources are exactly `['rcac://context', 'rcac://storage']`.
  - The fixture pins the `rcac://context` description and name (`served_surface.json:4-7`), and the
    instructions line naming it (`server.py:112`).
  - No mutant anchors on `resources.py`.

## 3. Suite contracts that collide

- **`test_tools.py`.**
  - `test_tool_reaches_only_the_installed_executor` runs over every `EXPECTED_TOOLS` entry
    (`:184-201`). `tool_violations` fails a tool that recorded no executor call ("never reached the
    executor", `:160-161`).
  - `test_tool_cases_cover_expected_tools` requires `set(TOOL_CASES) == EXPECTED_TOOLS`
    (`:204-206`).
  - So every tool currently must reach the executor. A `cluster_context` tool needs an exemption,
    for example a `ToolCase` flag meaning "no executor call allowed; the result is the stored text".
  - If the tool lives in `tools/rcac.py` or `tools/slurm.py`,
    `test_slurm_argvs_cover_the_slurm_and_site_tools` (`:454-459`) also demands a `SLURM_ARGVS`
    row. A new module avoids that, but it must join the import list at `tools/__init__.py`'s
    bottom (§7).
- **`test_no_executor.py:99-111`.** It parametrizes over **every** `EXPECTED_TOOLS` entry. Each call
  must fail with "No executor configured" (`refusal_violations`, `:43-60`), and it reads
  `TOOL_CASES[tool].arguments`.
  - There is no exemption mechanism today. Add one (for example
    `EXECUTORLESS = frozenset({'cluster_context'})`, parametrize over `EXPECTED_TOOLS - EXECUTORLESS`)
    plus a positive row: on `bare_server()` the tool succeeds, returns the text and launches
    nothing.
- **`test_registry.py:24-48`.** `EXPECTED_TOOLS` gains `cluster_context`, and every test that
  imports it moves with it (test_tools, test_no_executor, test_surface, test_transports).
  `create_mcp_server('none')` is called positionally (`:124`), so a new parameter needs a default.
- **Fixed-script rule.** Every shell argv a tool or the resource hands the executor must be a
  registered script behind `FIXED_RESET`. This is enforced in `test_tools.tool_violations`
  (`:164-174`), `test_literal_args.r1_violations` (`:263-274`) and `test_writes.py:172`;
  `script_of` is at `scripts.py:45-51`. Plain argvs (`['cat','--',p]`) are fine. A startup read
  through `script_argv(fixed(...))` satisfies the rule if anything records it.
  - **`test_literal_args`.** Any string parameter on a tool needs a (mode, tool, param) row in all 3
    modes (`coverage_violations` `:755-769`), and an optional one also needs an `EMPTY_OPTIONALS`
    row (`test_tools.py:443-451`), and those rows expect executor calls. A **parameterless**
    `cluster_context` avoids both; a `topic` argument costs a new oracle.
- **`test_instructions.py:83-91`.** It cross-checks both `SERVER_INSTRUCTIONS` and the **served**
  text (`create_mcp_server('none')`).
  - `SNAKE` (`:32`) and `DOCS_GUIDANCE` (`:27`) would flag site text, and the R5 note's file names
    (`login_nodes.md` reads as `login_nodes`).
  - Rescope: keep the cross-check on the base constant, and for served text require
    `served.startswith(SERVER_INSTRUCTIONS)` (or equality with the composed text).
  - The R5/R8 note lines must themselves avoid "doc(s)/documentation/user guide" if they are ever
    checked.
  - `INSTRUCTIONS.md` is also scanned for docs words (`:94-98`), which matters for R10's text.
- **`test_protocol.py`.** `era_violations` requires `negotiated.instructions == SERVER_INSTRUCTIONS`
  (`:218-219`), and the `LEGACY`/`MODERN` constants embed it (`:249-252`). It must compare against
  the composed text; this is the natural home for R2 in both eras.
- **`test_surface.py`.** `surface()` compares instructions exactly (`:108-109`).
  `test_surface_fixture_is_the_baseline_shape` requires tools == `EXPECTED_TOOLS`, each tool with
  exactly `description/inputSchema/outputSchema`, no `additionalProperties` and no argument
  descriptions (`:228-242`).
  - A string-returning tool's 2.x shape (from `myquota`): input `{"properties": {}, "type":
    "object"}`, output `{"properties": {"result": {"type": "string"}}, "required": ["result"],
    "type": "object", "x-fastmcp-wrap-result": true}`.
  - **Method:** no script survives. The commit body `a9a2901` is squashed and lists no deltas, and
    nothing is in `spec/safe-execution/verify/` (only `sweep.sh`). The method is prose only:
    `spec/safe-execution/PLAN.md:512-518` ("copying only the named deltas from the served surface
    with argument `description`s dropped"), `TECH.md:777-781`, AGENTS.md:423-428 and 1320-1331, and
    the `test_surface.py:10-15` docstring, which lists the deltas.
  - This cycle should commit its edit script (for example `spec/agents-d-instructions/verify/`) and
    add its deltas to all three lists.
- **`test_exec_mode.py`.**
  - The stub is `create_mcp_server(auth_mode=None, middlewares=None)` (`:161-165`). A new keyword
    raises `TypeError`, giving exit 6, so the stub must gain it, and should record it.
  - In ssh rows `Connection` is `FakeConnection` (`test_executors.py:535-561`), which has no
    `.client`. `SSHExecutor.run` hits `self._ensure_connected().client` (`ssh.py:330`) and raises
    `AttributeError`.
  - So the startup read must catch broadly (an R8 "cannot be read" note), or every ssh row breaks.
  - local and delegate rows would read the host's real `/etc/agents.d` in Python.
  - `test_startup.run_app` stubs with `*a, **k` (`test_startup.py:266`), so it is fine, and connect
    fails before any read.
- **`test_transports.py` and other subprocess drives.**
  - They start `rcac-mcp -e local` under `bin/sandbox.sh` (`:134`) and check only the tool names
    against `EXPECTED_TOOLS` (`:37-49`), never the instructions.
  - The server reads the host's real `/etc/agents.d`. It is absent on this Mac (checked); assumed
    absent on GitHub's ubuntu runners (unverified). The result is base plus the R8 line,
    unasserted.
  - The test is not hermetic, but nothing pinned depends on it. Any CLI-level assertion on the
    instructions would be host-dependent.
- **`tests/mutants.py`.** `apply` requires every anchor to occur **exactly once** (`:1126-1138`),
  and `test_mutations` checks that each anchor applies.
  - `instructions-dropped` (`:609-614`) anchors `        instructions=SERVER_INSTRUCTIONS,\n`
    (`server.py:133`). Its targets are `test_client_era_is_served[legacy|modern-memory]`. It breaks
    if that line changes; re-anchor it on the composed value.
  - `tool-error-boundary-dropped` (`:580`) anchors `server.add_middleware(ToolErrorMiddleware())`.
  - In `__init__.py`:
    - `delegate-gets-shared-middleware` (`:526-529`) anchors
      `        if exec_mode == 'delegate':\n`. A second identical line at that indent in `run()`
      breaks it.
    - The ladder cases (`:446-524`) anchor `_resolve_exec_mode` text.
    - `sse-transport-unserved` (`:629`) anchors the `mcp.run(transport='sse'…)` line.
    - `startup-exit-zero` (`:948`) anchors the `exceptions` map.
  - No case anchors `resources.py`.
- **`test_hygiene.py:557-572`.** It forbids `R#`/`P#` tokens anywhere in the package or tests.

## 4. `bin/sandbox.sh`

- It offers no override for `/etc/agents.d`. It only:
  - redirects `HOME`/XDG (`:59-65`);
  - unsets `RCAC_SSH_HOST RCAC_USER_MAP JWT_SECRET OIDC_* MCP_BASE_URL` (`:68-69`);
  - exports `CLUSTER_MCP_SANDBOX`/`CLUSTER_MCP_REPO` (`:73-75`).
- It is a high-blast-radius path (AGENTS.md:968-969).
- The § Throwaway environment contract (`:884-899`) says "redirects all app state … into that
  directory", and `/etc/agents.d` is read-only input rather than state.
- AGENTS.md has no rule naming test-only hooks either way (no hits for "test-only/seam/hook" in
  § Invariants).
- C1 says the path, glob and header are fixed. An env var that moves the path would be a new
  production configuration surface (§ Configuration table and `APP_HELP`). It would also contradict
  "Do not change the path" in spirit, and it is not needed for in-process tests.

## 5. CLI flag conventions

- **Flag.** Declare a class attribute and call `interface.add_argument(...)` in `MCPServerApp`
  (`__init__.py:127-155`), for example `port: int` with `type=int`.
- **Help text.** List it in `APP_USAGE` (`:60-66`) and in `APP_HELP` Options and Environment
  Variables (`:90-110`). Add a § Configuration row (AGENTS.md:197-203).
- **Environment variable.** Env defaults are read by hand (`os.environ.get('RCAC_SSH_HOST')`,
  `:220`, `:235`); there is no cmdkit env binding.
- **compose.yml.** It pins `-t http -H 0.0.0.0 -p 8000 -a jwt` (`compose.yml:21`). "A change to a
  CLI flag name or default is a change to those files too" (AGENTS.md:732-735), and the same applies
  to `Dockerfile` and the README § Docker Compose. A new flag whose default compose does not pin
  needs only the README, but say so in the commit.
- **Bad values.** Exit codes go through the `exceptions` map (`:159-164`) with cmdkit
  `exit_status` (bad_argument=2, bad_config=3, runtime_error=5, uncaught_exception=6, usage=1):
  - `ValueError` → `bad_argument` (2);
  - `RuntimeError` → 5;
  - `StartupFailure` → 5, with the message already reported;
  - an argparse `type=int` failure is a usage error before `run`.
  - For R4's "budget too small", raise `ValueError` naming the minimum. That gives exit 2, matching
    the missing-SSH-host precedent (`test_exec_mode` `FAILED_CLOSED`, status 2), and should come
    before any executor or connect.

## Implications for the plan

1. **Hermetic seam, no environment variable.**
   - Make the reader a function taking the directory, `read_site_context(executor_or_None,
     directory=AGENTS_D, timeout=…)`, with `AGENTS_D = '/etc/agents.d'` a module constant.
   - Over ssh, pass the directory to the fixed script as `"$1"`, never as script text.
   - `create_mcp_server(..., site=<result>)` takes the **already-read** result and has a default
     (tests call it positionally with `'none'`).
   - In-process tests pass text directly, or monkeypatch `AGENTS_D` to a `tmp_path`. Consider making
     the unit guard (or a conftest autouse fixture) point `AGENTS_D` at a nonexistent tmp path, so
     no unit test reads the host's `/etc`.
   - Subprocess drives stay host-dependent but assert nothing about the instructions. Say so in
     § Testing.
   - The ssh read can be proven against `tests/ssh_server.py`'s loopback with `$1` pointed at a
     fixture directory.
2. **Bound.**
   - Run the read in a daemon thread and `join(bound)`. `run(timeout=)` alone over ssh can exceed
     the bound by up to `OPEN_TIMEOUT` (30 s) + `GRACE`, or by the reconnect backoff.
   - Map -1001 to the "timed out" note and -1002 or any exception to "cannot be read". Catch
     `Exception` broadly; `test_exec_mode`'s `FakeConnection` raises `AttributeError`.
3. **Exemptions.**
   - Add an executor-less tool set in `test_tools` (a `ToolCase` variant asserting zero executor
     calls and the stored text) and in `test_no_executor` (exclude it, plus a positive bare-server
     row).
   - Put the tool in a new `tools/` module, not `rcac.py`/`slurm.py`, and keep it
     **parameterless** to avoid the literal-args matrix and the `EMPTY_OPTIONALS` rows.
   - Hold the text per server, through the FastMCP `Context` (`ctx.fastmcp`) or a closure, not a
     module global (§8: no new process-global cache). Tests build many servers per process.
4. **Rewrites.**
   - `test_instructions`: base-only cross-check plus a prefix check on the served text.
   - `test_protocol.era_violations` and the `LEGACY`/`MODERN` constants: compare against the
     composed text.
   - `test_exec_mode` stub: add the new keyword.
   - `test_tools.py:471-491`: the cache-based test is replaced.
   - `mutants.py`: re-anchor `instructions-dropped`, and add plants for the R1/R4/R5/R8/R9
     behaviours.
5. **Fixture deltas** (scripted, listed in the commit body, the `test_surface.py` docstring and
   AGENTS.md § Testing and §9):
   - `instructions`: the new base text, naming `cluster_context`, plus whatever default site
     section `create_mcp_server('none')` serves, such as the R8 line.
   - `tools.cluster_context`: new, in 2.x shape.
   - `resources.rcac://context.description`, if reworded (for example "except AGENTS.md").
   - `EXPECTED_TOOLS` gains `cluster_context`.
6. **New-flag mechanics.**
   - The budget flag and its env var go into `APP_USAGE`, `APP_HELP` and the § Configuration table.
     compose.yml stays untouched unless its pinned argv changes.
   - A too-small budget raises `ValueError`, exiting 2 before connecting.
   - Avoid adding a second `if exec_mode == 'delegate':` line, which would break a mutant anchor.
