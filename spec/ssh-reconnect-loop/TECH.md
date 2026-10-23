---
slug: ssh-reconnect-loop
title: A lost SSH connection fails a call within a bound, never hangs it
kind: fix
appetite: small
status: in_progress
branch: fix/ssh-reconnect-loop
base: develop
current_phase: P1
last_updated: '2026-10-23'
phases:
- id: P1
  name: 'Executor: a reconnect that ends within the bound, reported as not run'
  status: pending
  satisfies:
  - R1
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/ssh-reconnect-loop/verify/gate.sh P1
- id: P2
  name: 'Recovery and callers: the host returns, every tool reports it, in both eras'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/ssh-reconnect-loop/verify/gate.sh P2
- id: P3
  name: 'Full gate and the deferral ledger'
  status: pending
  satisfies:
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/ssh-reconnect-loop/verify/gate.sh mutants && test -f issues/ssh-half-open-link.md && grep -q 'issues/ssh-half-open-link.md' ROADMAP.md && bin/sandbox.sh uv run rcac-mcp --help
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — A lost SSH connection fails a call within a bound, never hangs it

The **context engine and finite-state machine** for building this fix. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/ssh-reconnect-loop/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.
- **Gate:** [`verify/gate.sh`](verify/gate.sh). Each phase's mode runs named test ids in both eras,
  then that phase's mutation cases and the sandboxed CLI drive. `tests` and `mutants` are the
  parts on their own.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

---

## Phase P1 — Executor: a reconnect that ends within the bound, reported as not run
**Satisfies:** R1, R4, R5, R6 · **Depends on:** —
**Goal:** `SSHExecutor` gives up a reconnect within three times the connect bound of the call's
start.
- On giving up, `run` and `write` return −1002 with `cannot connect to <host>: <cause>`, and `put`
  and `get` raise that message.
- A connection is published only once it has opened, under a lock.
- Unit rows pin it under an injected clock and real-Fabric construction, and the texts state the
  bound.

- [ ] **Move the message helper.** `connect_failure_message` and its helpers (`_chain`, `_refused`,
      `_cause_class`, `_detail`, `CHAIN_DEPTH`) move from `startup.py` into `executor/ssh.py`.
      `startup.py` and `__init__.py` import them from there. The text is unchanged (PLAN §2.1).
- [ ] **Add the new pieces.** `RECONNECT_FACTOR`, `ATTEMPT_POLL`, `FINAL_ATTEMPT_LEAD`,
      `self._lock`, `self._window`, `_bound`, `_new`, `_attempt`, `_release` (the client and the
      gateway chain, outside the lock), `_publish` (returns a transport, refreshes `_window`),
      `_Pending` and `_await`.
  - `open()` runs one `_new()` + `open()` on the calling thread and publishes it.
  - Its startup behaviour and exception are unchanged.
- [ ] **Rewrite `_ensure_connected(deadline)`** exactly as PLAN §2.1 says:
  - The fast path returns the transport it captured.
  - `_new()` and `open()` run only on the worker, never on the calling thread.
  - The wait goes through `_await`, every `ATTEMPT_POLL`.
  - At the deadline the caller owns the outcome under the lock: it publishes or takes a finished
    outcome, or marks the attempt abandoned and closes `client.get_transport()` and the gateway
    chain outside the lock.
  - A late success releases itself.
  - It catches `except Exception` and re-raises a `BaseException` in the caller.
  - `AuthenticationException` gives up at once.
  - Backoff is capped so the last attempt starts at least `FINAL_ATTEMPT_LEAD` before the deadline.
  - The clock is read only as `time.monotonic` and `time.sleep`.
  - It returns the message on giving up.
- [ ] **Callers.** Every public method takes `deadline = time.monotonic() + self._window` at entry.
  - `run` wraps `_run(…, deadline)` and returns the −1002 `CommandResult` directly, never through
    `launch.separate`.
  - `write` returns −1002 on a failed first connect and passes its deadline to the inner `_run`
    calls.
  - `_reason` treats −1002 as "no reason".
  - `put` and `get` raise a one-argument `ConnectionError(f'{message}; nothing was transferred')`.
- [ ] **Texts** (PLAN §2.3):
  - The bound statement goes in word for word, in the `SSHExecutor` docstring and in AGENTS.md's
    `executor/ssh.py` footgun entry, replacing "reconnect backoff (… never gives up …)".
  - AGENTS.md §3's −1002 bullet gains the reconnect cause and its stderr. **Only once the foreman
    has ruled on PLAN's deviation table, row 1.**
  - AGENTS.md's repository-map rows for `startup.py` and `executor/ssh.py`.
  - `base.py`'s −1002 comment and `Executor.run` Returns, and `put`/`get` `Raises: ConnectionError`.
  - `ssh.py`'s module, constants and method docstrings.
  - If the foreman rules Q6 the other way, the statement's wording follows the ruling.
- [ ] **Tests** (`tests/test_ssh_exec.py`, a new "A reconnect" section next to "A lost
      connection", marker `unit`). PLAN §2.4 and §6 describe the harness; each row the gate names
      is built here.
  - **`FakeClock`**: real time plus an offset, with short sleeps passed through and an `_await`
    stand-in that advances the clock. `Runaway(BaseException)` fires past 2 × the bound, only on
    the test thread.
  - **A scripted fake `Connection`**: `'ok'`, `(spent, exc)` or `'hang'`. Rows start from a dead
    published connection.
  - **Real `_new()` rows**, needing no socket:
    - `_bound` from ssh_config `ConnectTimeout 30`, and from `connect_kwargs` `timeout` 30;
    - `CanonicalizeHostname` with `socket.gethostbyname` patched to block.
  - **What every row asserts**: `guard.refused == []`. Every R1 row also asserts −1002, never
    −1003, and fake elapsed time no more than the bound.
  - **`test_reconnect_texts_state_the_bound`** pins R5's two texts and the old text's absence.
- [ ] **Mutants** (`tests/mutants.py`):
  - New cases. Each targets an executor-level row, and its `why` is the row's failure text:
    - `ssh-reconnect-unbounded-poll`;
    - `ssh-reconnect-unbounded-retry`;
    - `ssh-reconnect-reads-as-lost`;
    - `ssh-reconnect-retries-refused-auth`;
    - `ssh-reconnect-late-success-leaks`.
  - Re-plants, each named in the commit body:
    - `startup-host-unnamed`, on the helper's new home;
    - `startup-no-connect-timeout`, on `_new`'s `Connection(...)` line;
    - `staged-checksum-unchecked`, on write's place step, now a `_run` call;
    - `transfer-cause-unnamed`, on the `_reason` call.
  - Update the § Testing rows for all of them.
- **Verify:** `sh spec/ssh-reconnect-loop/verify/gate.sh P1`.
- **Touches:** `src/rcac_mcp/executor/ssh.py`, `src/rcac_mcp/executor/base.py` (docstrings),
  `src/rcac_mcp/startup.py`, `src/rcac_mcp/__init__.py` (import), `tests/test_ssh_exec.py`,
  `tests/mutants.py`, `AGENTS.md`.

## Phase P2 — Recovery and callers: the host returns, every tool reports it, in both eras
**Satisfies:** R1, R2, R3, R4, R6 · **Depends on:** P1
**Goal:** Pin, against the in-process SSH server, that a host which comes back is used and that the
server keeps serving after a failure. Every tool's failed reconnect reads as "not run" and spends
one window per executor call, in both protocol eras.

- [ ] **`tests/ssh_server.py`.** Two new methods on `LoopbackSSH`:
  - `refuse()` closes the listener **and** `_drop()`s every server transport;
  - `serve_again()` rebinds the same port with `SO_REUSEADDR` and restarts the accept thread.

  Update its docstring and the § Testing row.
- [ ] **`tools/slurm.py`.**
  - `sbatch` maps −1002 to `The job was not submitted: {stderr}` on both paths: the `run(argv)`
    branch beside −1003, and the `script_content` path's `write`.
  - `sacct` checks `whoami`'s exit, and on failure raises `Failed to get the current user: {stderr}`.
- [ ] **`resources.py` `load_cluster_context`.** Any −1002, from the listing or from a `cat`, stops
      the load and returns `''` without caching. §8's path, glob, order and headers are unchanged.
- [ ] **Tests** (`tests/test_ssh_exec.py`). Integration rows wait for the client's transport to
      read inactive before calling.
  - **R2** (integration): `refuse()`, then a counting `Connection` subclass calls `serve_again()`.
    Parametrised on the second attempt and on an attempt 40 s into the window. Each gives the
    baseline's result, a second accept and a new `_conn`.
  - **R3** (integration): one `in_memory` session. Call 1 gives −1002 within the fake bound, then
    `serve_again()`, then call 2 succeeds on the same server object.
  - **Abandonment** (integration): a silent accept-and-hold listener, with `ssh.CONNECT_TIMEOUT`
    patched to 1 before the executor is built, and `banner_timeout=30`. The call returns within
    about 3.5 s real, and the worker exits within 1 s more.
  - **Tool rows** (unit, through `_tool` in the selected era, under a real-time watchdog):
    - `run_command`;
    - `sbatch` on `script_path` and on `script_content`: not submitted, never "may have been
      submitted";
    - `write_file`, `upload_file` and `download_file`, each carrying `cannot connect to <host>`;
    - `sacct`, whose failed `whoami` spends one window.
  - **The context cache** isn't filled on −1002, from the listing or from a `cat`.
- [ ] **Mutants**, each targeting the row above that it should turn red; update the § Testing rows:
  - `ssh-reconnect-gives-up-for-good`;
  - `sbatch-not-started-reads-as-failed`;
  - `sacct-whoami-unchecked`;
  - `context-caches-not-started`.
- **Verify:** `sh spec/ssh-reconnect-loop/verify/gate.sh P2`.
- **Touches:** `tests/ssh_server.py`, `src/rcac_mcp/tools/slurm.py`, `src/rcac_mcp/resources.py`,
  `tests/test_ssh_exec.py`, `tests/mutants.py`, `AGENTS.md` (§ Testing).

## Phase P3 — Full gate and the deferral ledger
**Satisfies:** R6 · **Depends on:** P2
**Goal:** The profile's full `verify` in both eras, the cycle's mutation cases with the runner's
self-check, and every deferral recorded where `AGENTS.md` § Factory says.

- [ ] Run the profile's full `verify`, which runs both eras. R6 requires the new rows to run there:
      confirm each id in `verify/gate.sh` ran in both era runs, and none was skipped.
- [ ] Run `sh spec/ssh-reconnect-loop/verify/gate.sh mutants` (with `--self-check`). Every case is
      caught.
- [ ] **Deferral ledger.**
  - Walk P1, P2, PLAN §5 and the review findings for "not fixed here", "known limitation",
    "left as a follow-up", "seed" or "deferred". Each must have an `issues/` file and a
    `ROADMAP.md` entry.
  - Known so far: the half-open link (Q5), already filed at plan time as
    `issues/ssh-half-open-link.md` with its ROADMAP entry. The gate's `test -f` and `grep -q`
    check both.
  - A new deferral found in build gets its seed and entry here, or goes to the security lane
    (`AGENTS.md` § Factory → Where deferred work goes) if it describes an unremediated weakness.
- [ ] Confirm `issues/ssh-reconnect-loop.md` still reads `status: adopted:ssh-reconnect-loop`
      (`code-roadmap` retires it after landing, not this cycle).
- **Verify:** `uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/ssh-reconnect-loop/verify/gate.sh mutants && test -f issues/ssh-half-open-link.md && grep -q 'issues/ssh-half-open-link.md' ROADMAP.md && bin/sandbox.sh uv run rcac-mcp --help`.
- **Touches:** `issues/`, `ROADMAP.md` (only for a deferral found in build).

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
