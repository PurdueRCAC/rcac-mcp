# PLAN — A lost SSH connection fails a call within a bound, never hangs it

> **Status:** Signed off at the PLAN/TECH gate with the foreman's rulings (§3 bullet, Q6, Q7/R7) · **Last updated:** 2026-10-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Each call that has to reconnect gets a deadline: three times the connect bound from the call's start,
45 s by default. Its connect attempts run on a worker thread that the call waits for against that
deadline, so no step can hold the call past it, whether DNS, an SSH agent, several keys, or a
ProxyJump hop. A call that runs out of time returns `NOT_STARTED_EXIT_CODE` with
`cannot connect to <host>: <cause>`, or raises that message for an SFTP transfer. A connection
becomes the executor's only once it has opened, under a lock, so the next call starts clean. At
most one connect attempt is in flight at a time: a call that arrives during one joins it under its
own deadline (R7), so a stall nothing can abort leaves one thread, not one per call.

The change is centred on `executor/ssh.py`, plus two caller fixes (`sbatch`, `sacct`), a guard in
`resources.py` and test infrastructure. It stays within `small`: three phases, the first carrying
the core.

## 2. Design

### 2.1 The bounded reconnect (`src/rcac_mcp/executor/ssh.py`)

**New pieces.**

- **Constants:**
  - `RECONNECT_FACTOR: Final = 3`.
  - `ATTEMPT_POLL: Final = 0.1`: how often, in seconds, the call checks an attempt against the
    deadline.
  - `FINAL_ATTEMPT_LEAD: Final = 2.0`: the last attempt starts at least this many seconds before
    the deadline.
- **Kept and documented:** `INITIAL_RECONNECT_DELAY` and `MAX_RECONNECT_DELAY`, as the backoff
  schedule.
- **`self._lock: threading.Lock`**, **`self._window: float`** and **`self._inflight: _Pending | None`**
  (the one attempt in flight, R7), all made in `__init__`.
- **`_bound(conn) -> float`** returns
  `max(CONNECT_TIMEOUT, conn.connect_timeout or 0, the explicit connect_kwargs timeout or 0)`.
  Fabric has already resolved `conn.connect_timeout` from the caller's `connect_timeout`, the
  ssh_config `ConnectTimeout`, or `_bounded`'s default (research 01 §2).
  - `self._window = RECONNECT_FACTOR * _bound(conn)` is set from each connection that is
    published, the startup one first. A call's deadline therefore never needs a new `Connection`
    built on the calling thread (review finding 1).
- **`_new() -> Connection`** builds `Connection(self._host, **_bounded(self._connect_kwargs))`.
  - Building one is **not** free. Fabric's `SSHConfig.lookup` runs any `Match exec` command, and
    with `CanonicalizeHostname` it also resolves names. So `_new()` runs only inside an attempt,
    never on the calling thread of a reconnect (review finding 1).
- **`_attempt() -> Connection`**: one connect, `conn = self._new(); conn.open()`, run on a worker
  thread.
  - On any `Exception`, from building or from opening, it releases the connection and re-raises.
  - It never touches `self._conn`.
- **`_release(conn)`** is the one way a connection is let go, best effort and **outside** the lock.
  - It closes `conn.client` (Fabric's `close()` does nothing on a connection whose transport never
    opened, or has died).
  - It then walks the `conn.gateway` chain, closing each gateway `Connection`'s client, so a
    ProxyJump hop never outlives its attempt (review findings 9 and 11).
- **`_publish(conn) -> Transport`**, under the lock:
  - If `self._conn`'s transport is active, another thread has already published a live
    connection: hand back that transport, and `conn` is released.
  - Otherwise keep the dead `self._conn` aside to release, assign `conn`, refresh `self._window`,
    and hand back `conn`'s transport.
  - Every release happens after the lock is let go.

**`open()`**, the startup path (safe-execution R9, unchanged in behaviour):

- returns at once when connected;
- otherwise runs one `conn = self._new(); conn.open()` on the calling thread and publishes it;
- a failure releases the connection and re-raises its own exception, as today, so `startup.py`'s
  message and exit status are unchanged.

**`_ensure_connected(deadline: float) -> Transport | str`.** It runs at most one connect attempt at a
time for the whole executor (R7, Q7). A call that arrives while an attempt runs joins it and never
starts a second one.

- **Fast path.** Capture `self._conn`'s transport once. If it is active, return it. Callers use
  what is returned and never re-read `self._conn`, so another thread's `_publish` can't swap a
  transport out from under a call (review finding 11).
- **The holder.** `_Pending` is shared by everyone waiting on one attempt. It holds:
  - `done`, a `threading.Event`;
  - the outcome: the published transport, the `Exception`, or a `BaseException`;
  - `conn`, which the worker sets as soon as it has built the connection, so a waiter can reach
    its transport;
  - `waiters`, a count.

  `self._inflight` holds the attempt in flight, or `None`. Both it and `waiters` change only under
  `self._lock`.
- **The loop**, until the call succeeds or gives up:
  1. **Join or start.** Under the lock:
     - If `self._inflight` is set, join it: `waiters += 1`.
     - Otherwise create a `_Pending`, make it `self._inflight` with `waiters = 1`, and start
       `_attempt` on a daemon thread named `cluster-mcp-reconnect`.

     A call never starts an attempt while another is in flight, so K calls during a stall leave one
     thread, not K.
  2. **The worker owns its outcome.** When the attempt ends, the worker takes the lock and records
     its outcome:
     - On success it calls `_publish`, which adopts the connection if its transport is active and
       releases it otherwise. The returned transport is recorded.
     - On failure it records the exception.

     Then, still under the lock, it clears `self._inflight` and sets `done`. A success that lands
     after every waiter has left is still published, never leaked: the connection is the
     executor's whether or not anyone is waiting (review finding 6). No caller ever publishes or
     releases an attempt's connection itself.
  3. **Wait.** Each call waits through a module function, `_await(event, seconds)`, which is
     `event.wait(seconds)` in production. It waits `ATTEMPT_POLL` at a time and checks its own
     deadline on `time.monotonic()` between waits. A test replaces `_await` to advance the fake
     clock by `seconds` instead (review finding 7).
  4. **The call's own deadline passes before `done`.** Under the lock, `waiters -= 1`.
     - If no waiter is left, the call takes the attempt's `conn` and, outside the lock, closes
       `conn.client.get_transport()` and the gateway chain. That aborts kex, auth or a gateway's
       channel open within about 0.1 s (research 01 §3). Fabric's `conn.transport` is never the
       one closed: it stays `None` until `open()` returns.
     - The call then gives up, with the last cause it saw, or `timed out`.
     - The slot stays occupied until the worker finishes. Where nothing can be closed (in DNS or
       an SSH-agent query), a later call joins that same stuck attempt, waiting under its own
       deadline, and never starts a second one.
  5. **Success** (`done`, with a transport). Return it.
  6. **`AuthenticationException`.** Give up at once. Under Q4, an auth timeout and a refusal share
     that class, and the next call retries. Every waiter on that attempt gives up with it.
  7. **Any other failure.**
     - If `now >= deadline − FINAL_ATTEMPT_LEAD`, give up.
     - Otherwise, sleep `min(delay, deadline − FINAL_ATTEMPT_LEAD − now)` through `time.sleep`, then
       double the delay up to `MAX_RECONNECT_DELAY` and go back to step 1.
     - After a shared failure, the first call to wake starts the next attempt and the rest join it.
     - The last attempt a call joins or starts begins no later than `FINAL_ATTEMPT_LEAD` before
       its deadline. A host that is back by then is used: R2's retry window runs to
       `deadline − FINAL_ATTEMPT_LEAD` (review finding 5).
- **Giving up returns a message; it never raises.** The message is
  `connect_failure_message(self._host, cause)`, a `str`. Each caller turns it into its own form.
- **A `BaseException` outcome** (an interrupt, or a test's tripwire) is re-raised by every waiter.
- **The clock.** Time is read only through the module attribute, as `time.monotonic` and
  `time.sleep`, and every wait on an attempt goes through `_await`. A test swaps `ssh.time` and
  `ssh._await` (research 03 §A2).
- **Research check (Q7).** Neither brief 01 nor brief 02 conflicts with one attempt in flight.
  - Brief 02 §4 rejected holding the lock across the whole reconnect, because a queued call would
    exceed its bound. Here the lock is held only for bookkeeping. Each call waits on the shared
    event under its own deadline, so that objection doesn't apply.
  - Brief 01's per-call worker thread becomes a per-attempt worker shared by its joiners.

**Callers inside `SSHExecutor`.** Every public method takes its deadline at entry:
`time.monotonic() + self._window`, computed from the cached window with no I/O (review finding 1).

- **`run`** stays the Protocol method and wraps `_run(command, cwd, timeout, deadline)`.
  - **On a message**, `_run` returns
    `CommandResult('', f'cluster-mcp: {message}; the command was not run\n', NOT_STARTED_EXIT_CODE, self._host)`
    directly. It never goes through `launch.separate`, which would replace it with
    `NOT_STARTED_NOTE` (research 03 §A3).
  - **The exec string** is still built, and refused when oversize, before connecting
    (`test_an_oversize_program_is_refused_before_connecting`).
- **`write`**:
  - returns that −1002 result when its first connect fails;
  - its inner commands (the parent `mkdir`, the place step, `_reason`'s probe) call `_run` with
    write's own deadline, so one `write` spends at most one window;
  - `_reason` treats −1002 as "no reason" (research 02 §1).
- **`put` and `get`** raise `ConnectionError(f'{message}; nothing was transferred')`, with one
  argument, so the tool error reads `Error calling tool 'upload_file': cannot connect to …` in both
  eras.

**The message helper.** `connect_failure_message` moves from `startup.py` into `executor/ssh.py`,
together with its helpers `_chain`, `_refused`, `_cause_class`, `_detail` and `CHAIN_DEPTH`.
`startup.py` imports the function from there, so no backend imports the CLI's module (research
02 §2). Its text is unchanged, and `tests/test_startup.py` pins it end to end.

### 2.2 Callers outside the executor

- **`tools/slurm.py` `sbatch`.** Both of its executor calls map a −1002 to
  `The job was not submitted: {stderr}`. This is R4's branch, and it is true for every −1002
  cause.
  - **`run(argv)`** gets a `NOT_STARTED_EXIT_CODE` branch beside the −1003 one.
  - **The `script_content` path's `write`** gets one too. That `write` is the first call an outage
    meets on that path (review finding 4).
- **`tools/slurm.py` `sacct`.** It checks `whoami`'s exit, and on a non-zero one raises
  `Failed to get the current user: {stderr}`. Without that check an outage costs two windows.
- **`resources.py` `load_cluster_context`.** On any −1002, from the listing or from a file's `cat`,
  it stops and returns `''` without caching. An outage then neither empties nor truncates the
  context for the process's life (review finding 3).
  - Reads get no executor today (§ Invariants §8), so this is latent. This change would create it.

**One window per executor call: Q6, as ruled by the foreman (GOAL Q6).**

- R1's bound is the wait on one failed reconnect window.
- Each `run`, `write`, `put` and `get` spends at most one window, and every tool stops at its first
  −1002, so a tool spends at most one failed window.
- A tool whose earlier executor call succeeded may take that call's own time plus one window. That
  includes a call that reconnected, under R2.
- There is no `ContextVar` deadline, and `middleware.py` doesn't change.

### 2.3 Texts (same-commit; R5)

**The bound statement**, word for word, in two places:

- the `SSHExecutor` class docstring;
- the `executor/ssh.py` entry of AGENTS.md § Invariants → Footguns, where it replaces today's
  "reconnect backoff … never gives up" clause.

It reads:

> A call that finds the connection lost keeps trying to reconnect, with backoff, and gives up at
> most three times the connect bound after the call began: 45 s with the default 15 s
> (`CONNECT_TIMEOUT`), its last attempt starting at least 2 s before that. An ssh_config
> `ConnectTimeout` (or a caller's `connect_timeout`) longer than 15 s raises the bound to three times
> that value; a shorter one never lowers it. A refused authentication gives up at once. The call
> then reports `cannot connect to <host>: <cause>` and runs nothing, and the next call tries again.

**Other texts that change:**

- **AGENTS.md:**
  - **§3, the `NOT_STARTED_EXIT_CODE` bullet**, gains a third cause: over `ssh`, the connection
    could not be re-established within the bound, with stderr
    `cluster-mcp: cannot connect to <host>: <cause>; the command was not run`. See the deviation
    table.
  - **The repository-map rows** for `startup.py` and `executor/ssh.py`.
  - **§ Testing**, the rows for:
    - the new tests;
    - `ssh_server.py`'s `refuse` and `serve_again`;
    - every mutant case added or re-planted.
- **`executor/base.py`:**
  - the −1002 comment and the `Executor.run` Returns text gain the same cause;
  - `put` and `get` gain a `Raises: ConnectionError` line.
- **`executor/ssh.py`:** the module, constants, `_ensure_connected`, `run`, `write`, `put` and `get`
  docstrings.

**Texts that stay as they are:** `run_command`'s description, `SERVER_INSTRUCTIONS`, and
`tests/fixtures/served_surface.json`. "−1002 means the command never started" stays true.

### 2.4 Tests and mutants (R6)

**Harness** (review findings 2, 7 and 8).

- **`FakeClock`** follows research 03 §A2: real time plus an offset, with sleeps under
  `INITIAL_RECONNECT_DELAY` passed through to the real sleep.
  - **Its `_await` stand-in** advances the clock by `seconds` and returns `event.is_set()`, so the
    clock moves only on the caller's thread. A hang never moves the clock, and the elapsed fake time
    at the give-up is deterministic.
  - **`Runaway(BaseException)`**, past 2 × the bound, is raised only on the test's own thread, in
    executor-level rows. Tool-level rows run the call under a real-time watchdog instead (the call
    in a thread, `join(10)`): a call still running fails the test with "still reconnecting". A
    regression or a surviving mutant then fails the gate instead of hanging it.
- **A scripted fake `Connection`.** Each `open()` takes one of three actions:
  - `'ok'`;
  - `(spent, exc)`;
  - `'hang'`, which blocks until the test releases it.
- **Each unit row starts from a published connection that has gone dead**, not from `_conn=None`,
  so `_publish`'s release of the dead one runs.
- **`tests/ssh_server.py`** gains two methods:
  - `refuse()` closes the listener **and** `_drop()`s every server transport;
  - `serve_again()` rebinds the same port with `SO_REUSEADDR`.

  Integration rows wait, with a short real deadline, until the client's transport reads inactive
  before they call. They assert that the server accepted a second connection and that
  `executor._conn` is a new object, so a fast-path success can't pass for a reconnect.

**Unit rows against real Fabric and Paramiko** (review findings 1 and 8). These open no socket, or
only to 127.0.0.1.

- **`_bound` resolution.** A real `_new()` with ssh_config `ConnectTimeout 30` gives a bound of 30.
  So does one with `connect_kwargs={'timeout': 30}`.
- **The growth row** asserts that an attempt starts after 45 s of fake time and that the give-up is
  at 90 s or before.
- **Building blocks.** A real `_new()` whose ssh_config sets `CanonicalizeHostname yes`, with
  `socket.gethostbyname` patched to block, still returns −1002 at the bound. The construction runs
  on the abandoned worker.
- **Abandonment, integration.** A silent accept-and-hold listener on 127.0.0.1, with
  `ssh.CONNECT_TIMEOUT` patched to 1 before the executor is built, and `banner_timeout=30`. The call
  returns within about 3.5 s real, and the worker thread exits within 1 s more. This pins
  closing `client.get_transport()` rather than `conn.transport`.

**Mutants** (review finding 10). Each is a case in `tests/mutants.py` whose target is an
executor-level row, or a tool row under the watchdog.

- **New in P1:**
  - `ssh-reconnect-unbounded-poll`: the deadline check in the attempt wait is dropped.
  - `ssh-reconnect-unbounded-retry`: the deadline check after a failure is dropped.
  - `ssh-reconnect-reads-as-lost`: a failed reconnect returns −1003.
  - `ssh-reconnect-retries-refused-auth`: Q4's give-up-at-once is dropped.
  - `ssh-reconnect-late-success-leaks`: a success that lands after every waiter left is neither published nor released.
  - `ssh-reconnect-attempt-per-call`: each call starts its own attempt instead of joining the one in flight (R7).
- **New in P2:**
  - `ssh-reconnect-gives-up-for-good`: a failure is remembered across calls.
  - `sbatch-not-started-reads-as-failed`.
  - `sacct-whoami-unchecked`.
  - `context-caches-not-started`.
- **Re-planted**, each named in the commit body as § Testing requires:
  - `startup-host-unnamed`, on the helper's new home;
  - `startup-no-connect-timeout`, on `_new`'s `Connection(...)` line;
  - `staged-checksum-unchecked`, on `write`'s place step, now a `_run` call;
  - `transfer-cause-unnamed`, on the `_reason` call.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | §2.1: a deadline taken at each executor call's entry, with no I/O; attempts, construction included, on a worker that a call stops waiting for at its own deadline; `connect_failure_message`. §2.2: every tool stops at its first −1002 (`sacct`, `sbatch`, the context loader); per executor call, as Q6 rules. Both eras, through tool rows. |
| R2 | §2.1: every non-auth failure is retried until `deadline − FINAL_ATTEMPT_LEAD`; `_publish` hands the call a fresh transport, and the command runs on the normal path. |
| R3 | §2.1: `_publish` runs only on success, so a failed or auth-refused connection never reads as connected; nothing is cached as failed; the fast path re-checks the transport on every call. |
| R4 | §2.1: −1002 with `the command was not run`, never −1003 and never through `launch.separate`; `nothing was transferred` for SFTP. §2.2: `sbatch` says "not submitted" on both its paths. |
| R5 | §2.3: the bound statement in the `SSHExecutor` docstring and in AGENTS.md's `executor/ssh.py` footgun entry, replacing the defect's text and saying how ssh_config `ConnectTimeout` changes the bound; pinned by a text test. |
| R6 | §2.4: unit rows under `FakeClock` with fakes and real `_new()`; integration rows on `LoopbackSSH` with `refuse` and `serve_again`; tool rows through the era-switched harness. All run in the profile's `verify`, in both eras. |
| R7 | §2.1: `self._inflight`, where a call joins the attempt in flight and never starts a second, and the worker owns its outcome; §2.4/§6: K concurrent calls during a stall start one attempt; the mutant `ssh-reconnect-attempt-per-call`. |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (from the GOAL) and again against this design.

- **High-blast-radius paths.** `executor/ssh.py` and `executor/base.py` (docstrings only) are on
  the list, so research ran (trigger 2). A CONFIRMED review finding in either needs Geoffrey's
  decision before landing.
- **§1 Per-request isolation.**
  - The lock and the published connection are state of the one shared `SSHExecutor` instance,
    which `ssh` mode shares by design (`SharedExecutorMiddleware`). They are not module globals,
    and no `ContextVar` changes.
  - No tool constructs an executor.
  - `ToolErrorMiddleware` already turns the `put`/`get` `ConnectionError` into a tool error, in both
    eras.
- **§2 Executor Protocol.**
  - No member is added and no public signature changes. `_run` and `_attempt` are private to
    `SSHExecutor`, so the three implementations need not move together.
  - `put`/`get` documenting `ConnectionError` adds a Raises line. §2's bullet for them is silent on
    errors, so it is not contradicted.
  - `write` keeps returning a `CommandResult` for a connect failure, so §2's `OSError` list for
    `write` is unchanged.
  - `open()`/`close()` stay real for SSH.
- **§3 `CommandResult` and exit codes.**
  - No new reserved code (Q3).
  - The −1002 bullet's text gains a cause: see the deviation table.
  - −1003 is untouched: a failed reconnect happens before any exec request, so "never −1002" for a
    lost connection still holds. The new −1002 is used only when no channel was opened.
  - The timeout contract is untouched: the reconnect deadline and the command `timeout` are
    independent (Q2).
- **§8 Resources.** The guard keeps the `/etc/agents.d` path, glob, order and headers. It adds no
  cache and keys nothing new on `hostname`.
- **§9 Agent-facing surface.** No tool, docstring or instruction changes. `sbatch`'s new error text
  is a result, not served surface.
- **§ Conventions.**
  - Comments carry no R-IDs or P-IDs.
  - Every new test carries `unit` or `integration`.
  - Gates name test ids.
  - CLI drives go through `bin/sandbox.sh`.
  - No version or dependency change.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| **§3's −1002 bullet (a [human gate] contract text) gains a third cause**, a reconnect that could not be made, with its own stderr line. Q3 ruled "no new reserved code, so §3 is unchanged"; the code set is unchanged, but the bullet enumerates −1002's causes and their exact stderr, so its text must change in the same commit. **Approved by the foreman at the PLAN/TECH gate, 2026-10-23**, who holds the human gate under the autonomy decision. It documents a new cause of an existing code, so Q3's "no new code" holds. | Q3 fixes −1002 as the code; a −1002 whose cause §3 does not list would make §3 false. | Leaving §3's text alone: the review grades §3 violations as CRITICAL, and the code would contradict it. |
| Each connect attempt runs on a worker thread, abandoned at the deadline. | DNS, the SSH agent, per-key auth and a ProxyJump channel have no usable bound (research 01 §1), so Q1's 45 s can't be honoured by per-step timeouts. | Per-step timeouts plus "no new attempt after the window": the bound would read "about 45 s, longer if DNS, the agent, several keys or a jump host stall", which fails R1 as worded. |
| A lock around the published connection and the one attempt in flight (R7). | Concurrent reconnects today hand one call another's half-open connection (an `AttributeError` that names no host, which fails R1) and leak live connections (research 02 §4). R7 needs one attempt in flight, so calls during a stall don't pile up threads. | Holding the lock across the whole reconnect: a call queued behind another's window would exceed its own bound. Here the lock covers only bookkeeping, and each call waits on the shared attempt under its own deadline. |
| Changes outside `executor/ssh.py`: `tools/slurm.py`, `resources.py`, `startup.py` (import), `__init__.py` (import), `base.py` docstrings, `tests/ssh_server.py`, `tests/mutants.py`. | R4 needs `sbatch`'s wording on both its paths. R1 needs every tool to stop at its first −1002, and `sacct` would spend two windows. The `resources.py` cache would turn a −1002 into a permanent empty or truncated context. | Fixing only `ssh.py`: R1 and R4 would fail at tool level. |

## 4. Rabbit holes (resolved)

- **Does the MCP client wait 60 s?** Yes.
  - The TypeScript SDK sets `DEFAULT_REQUEST_TIMEOUT_MSEC = 60_000` at
    `packages/core-internal/src/shared/protocol.ts:103` on `main` `951fe56` (v2.4.0), and at `:60`
    at tag `1.20.0`.
  - Progress doesn't reset it by default.
  - 45 s leaves 15 s of margin ([`research/00-digest.md`](research/00-digest.md)).
- **Can per-step timeouts add up to a hard bound?** No, but a deadline with an abandoned worker
  can. Closing the client's transport aborts an attempt in about 0.1 s
  ([`research/01-connect-attempt-bounds.md`](research/01-connect-attempt-bounds.md) §1, §3).
- **Which exceptions does an attempt raise?** `SSHException` and `OSError`, and also `EOFError`, a
  bare `Exception` and `ValueError`. So an attempt catches `Exception` (research 01 §4).
- **Can a backend reuse the startup message without a layering inversion?** Yes, by moving the
  helper into `executor/ssh.py`. No test pins its current home (research 02 §2).
- **Which callers break R1 or R4?** `sbatch`, `sacct`, `_reason`, and the context cache
  ([`research/02-error-surface.md`](research/02-error-surface.md) §1).
- **Can tests pin a 45 s bound without waiting 45 s?** Yes. An offset clock is swapped in as
  `ssh.time`, and `_await` advances that clock in place of the attempt wait. The tripwire sits on
  the test's own thread
  ([`research/03-tests-and-half-open.md`](research/03-tests-and-half-open.md) §A2, and the review
  below).
- **Can the in-process server go away and come back?** Yes, with `SO_REUSEADDR` on the same port
  (research 03 §A1, probed).
- **Q5, the half-open link.**
  - It is unbounded for SFTP: up to 3600 s to open a channel, then no limit.
  - `run` is bounded at 30 s (`OPEN_TIMEOUT`), but its error isn't R1's.
  - It is recorded as a seed and not fixed here: [`issues/ssh-half-open-link.md`](../../issues/ssh-half-open-link.md)
    and `ROADMAP.md` (research 03 §B).

### 4.1 Adversarial design review

The review ran on the first draft as a fresh subagent, given only GOAL and the drafted PLAN, with
the source to check claims against and no research briefs. It returned 11 findings: 2 HIGH, 7
MEDIUM, 2 LOW. Every one is folded in.

| # | Sev. | Finding | What changed |
|---|------|---------|--------------|
| 1 | HIGH | Building a `Connection` runs ssh_config `Match exec` commands, and `CanonicalizeHostname` lookups, on the calling thread. The draft did this before its deadline existed, and no unit row could see it. Probed. | `_new()` runs only inside the worker. The deadline comes from the cached `self._window`, set at each publish, so taking it does no I/O. A row covers a real `_new()` with blocking canonicalisation. |
| 2 | HIGH | R2 could pass on the fast path with no reconnect, and R3 could race the client noticing the drop. | `refuse()` drops every transport. Rows wait for the transport to read inactive, then assert a second accept and a new `_conn`. Unit rows start from a dead published connection. |
| 3 | MED | The bound is per executor call, while R1 says per tool call. Multi-call tools and the context loader could spend several windows. | Every tool stops at its first −1002; the context loader stops on any −1002 and caches nothing. The remaining gap, a loss between two executor calls of one tool, goes to the foreman as **Q6** (§2.2, §5). |
| 4 | MED | `sbatch` with `script_content` meets the outage at `write`, so it never said "not submitted". | Both of `sbatch`'s paths map −1002 to "not submitted", and the row covers both. |
| 5 | MED | With the sleep capped exactly at the deadline, no attempt ran in the window's last ~10 s, so a host back late in the window failed R2. | `FINAL_ATTEMPT_LEAD` (2 s): the last attempt starts at least that long before the deadline, and R5's text says so. An R2 row brings the host back 40 s in. |
| 6 | MED | Who owned a success that landed just as the deadline passed was unclear, and closing a transport inside the lock could block other calls. | At the gate R7 changed this: the worker owns every outcome under the lock. It publishes a success, even one with no waiter left, and records a failure; waiters only read. All closing happens outside the lock. A deterministic row puts the deadline at the instant of success. |
| 7 | MED | The "hang" advanced the clock on its own cadence, so its timing was nondeterministic. `Runaway` on a worker or a FastMCP thread could be lost, or kill the server, and a mutant could hang the gate. | `_await` is a module function that the fake clock replaces. `Runaway` runs only on the test thread. Tool rows run under a real-time watchdog. Mutants target executor-level rows. |
| 8 | MED | With fakes alone, `_bound`'s attribute and abandonment's choice of transport went untested. A growth row asserting only "≤ 90 s" passes a `_bound` that ignores the timeout. | Real-`_new()` rows for ssh_config `ConnectTimeout 30` and `connect_kwargs` `timeout` 30. The growth row asserts an attempt after 45 s. An integration row abandons a real stalled handshake with `CONNECT_TIMEOUT` patched to 1. |
| 9 | MED | ProxyJump attempts leaked authenticated bastion sessions, and an abandoned worker could wait up to an hour on a gateway channel. | `_release` walks the gateway chain on failure, on abandonment and on replacing a dead connection. Abandonment closes the gateways too, which ends a channel-open wait. Risk 3 is corrected. |
| 10 | LOW | Two existing mutant anchors would break unlisted, and the one "unbounded" case was ambiguous. | The `staged-checksum-unchecked` and `transfer-cause-unnamed` re-plants are listed. The unbounded case is split into `-poll` and `-retry`. Cases are added for auth-at-once, a late success leaking, `sbatch`, `sacct` and the context cache. |
| 11 | LOW | `run` re-read `self._conn` after `_ensure_connected`, so a concurrent `_publish` could null its transport. Fabric's `close()` releases nothing on a dead connection. | `_ensure_connected` returns the transport it captured. `_release` closes `client` and the gateway chain, never through Fabric's `close()`. |

## 5. Risks & open questions

1. **Ruled: §3's text change is approved** (deviation table, first row).
2. **Ruled: Q6, the bound is per executor call** (§2.2, GOAL Q6).
3. **A stuck attempt can outlive the calls waiting on it** (R7 limits it to one).
   - When the last waiter leaves, it closes the client's transport and any gateway's, which ends
     kex, auth or a gateway channel's open within about 0.1 s.
   - Inside DNS, an SSH-agent query, a `Match exec` command or the TCP connect there is nothing to
     close. The worker runs until that step returns; for TCP connect, that is the connect bound.
   - Calls that arrive meanwhile join the stuck attempt, so the executor never holds more than one.
   - The cost of R7: while that one attempt is stuck, a new call can't start a fresh one, so it
     fails at its own bound even if the host has come back. Once the step returns, the slot clears
     and the next call tries again (R3).
   - It is a daemon thread. Its success is published, its failure is recorded, and a connection
     it opened but didn't publish is released.
4. **Q4's choice: any `AuthenticationException` ends the reconnect at once, the auth timeout
   included.** A transient auth stall then fails that call quickly instead of waiting out the
   window, and the next call retries. This also keeps a refused key from being retried about eight
   times per call against a login node that may count failures.
5. **`write` after a loss mid-call.**
   - The place step shares write's deadline and reports −1002 when it can't reconnect, so the file
     was not placed.
   - The staging directory may be left behind when `_discard` can't reach the host. That is
     today's behaviour for any loss there.
6. **`FakeClock` and real-time code.** R2 and R3 rows pass no command `timeout`, so `_collect` has
   no deadline for the clock's offset to reach early (research 03 §A2).
7. **The concurrency rows use fakes.** One fake-connection row pins publish-once and the
   deadline-instant handoff. The race isn't driven against the real server.

## 6. Verification strategy

- **Unit rows, executor level** (`tests/test_ssh_exec.py`, marker `unit`). Each uses `FakeClock`,
  starts from a dead published connection, and asserts `guard.refused == []`.
  - **R1 and R4**, one row per failure class: refused, timed out, no banner, unknown host, a
    `_new()` that raises. Each asserts:
    - −1002 with `cannot connect to <host>: <cause>` and `the command was not run`;
    - fake elapsed time no more than the bound, and the last attempt started at least
      `FINAL_ATTEMPT_LEAD` before it;
    - never −1003.
  - **Auth refused** gives up after one attempt.
  - **The bound grows.** `connect_timeout=30` gives an attempt after 45 s and a give-up at 90 s or
    before. A real `_new()` resolves `_bound` to 30 from ssh_config and from `connect_kwargs`.
  - **Hangs.** A call waiting on an attempt that never returns gives up at its bound. When the
    attempt is released later, its success is published, never leaked.
  - **One attempt in flight (R7).** K concurrent calls during a stall (a `'hang'`, or a real
    `_new()` blocked in canonicalisation) start one attempt. Each gives −1002 at its own bound,
    within K × `ATTEMPT_POLL` of fake time, since the calls share one clock. A call arriving after
    they have all left joins the same attempt, still one. Releasing it clears the slot.
  - **A real `_new()` that blocks in canonicalisation** still gives −1002 at the bound.
  - **Concurrency.** Concurrent reconnects publish one connection. A success at the deadline's
    instant is published, never lost.
  - **Transfers.** `put` and `get` raise `ConnectionError` with the text, and `write` returns −1002.
- **Unit rows, tool level**, through `_tool` in the selected era, under a real-time watchdog:
  - `run_command`;
  - `sbatch` on both paths, saying not submitted and never "may have been submitted";
  - `write_file`, `upload_file` and `download_file`;
  - `sacct`, which spends one window.

  `load_cluster_context` doesn't cache on −1002, from the listing or from a `cat`.
- **R5, a text test.** The bound statement appears in the `SSHExecutor` docstring and in AGENTS.md's
  footgun entry, and the old "never gives up" text is gone.
- **Integration rows** (`LoopbackSSH`):
  - **R2:** `refuse()`, then `serve_again()` on the second attempt, and again 40 s into the window.
    Each gives the baseline's result, a second accept and a new `_conn`.
  - **R3:** one `in_memory` session. Call 1 fails with −1002, `serve_again()` runs, and call 2
    succeeds on the same server.
  - **Abandonment:** a real stalled handshake is abandoned within about 3.5 s real.
- **Both eras.** Each phase gate (`verify/gate.sh`) runs its named ids in the handshake era and
  again with `CLUSTER_MCP_TEST_ERA=modern`, then the phase's mutation cases. The last phase runs the
  profile's full `verify` and `gate.sh mutants`, which adds `--self-check`.
- **The CLI drive.** `bin/sandbox.sh uv run rcac-mcp --help` asserts that the server still starts
  its CLI. No drive can reach a real host, so the reconnect path runs only in-process, as R6
  requires.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
