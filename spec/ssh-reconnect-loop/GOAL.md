# GOAL — A lost SSH connection fails a call within a bound, never hangs it

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** ssh-reconnect-loop
- **kind:** fix
- **appetite:** small

## Problem

In `ssh` mode, the default, the server keeps one connection to the cluster for its whole life. When
that connection goes after startup (a network drop, a login node rebooted, a laptop that slept),
the next tool call tries to reconnect, and if the host stays unreachable **the call never returns**.
The agent sees a request that hangs until its client gives up, with no error naming the host or the
cause, and every later call does the same. Nothing tells the agent, or the person watching it, that
the cluster is gone rather than slow.

**Confirmed on `develop` (`d34de62`), 2026-10-23.** `SSHExecutor._ensure_connected`
(`src/rcac_mcp/executor/ssh.py:455-482`) is the reconnect path every `run`, `write`, `put` and `get`
takes. Its retry loop runs `while delay <= MAX_RECONNECT_DELAY` with
`delay = min(delay * 2, MAX_RECONNECT_DELAY)` (`:467`, `:473`; constants at `:47-48`); once the delay
reaches the 10 s cap it stays there, so the condition never turns false and neither the "final
attempt" nor the `ConnectionError` after the loop is reachable. A unit reproduction with Fabric's
`Connection` replaced by a fake whose `open()` always raises `OSError`, and `time.sleep` recording
instead of sleeping, was still retrying after 200 attempts (delays 0.5, 1, 2, 4, 8, then 10 s
forever; 1,965 s of simulated sleep) with no exception raised. No real ssh was used. Against a real
black-holed host each attempt also spends up to the connect bound `safe-execution` added
(`CONNECT_TIMEOUT`, 15 s per step), so a real call loops at roughly one attempt every 25 s.

**Pre-existing.** The loop landed in `a2a46ff` (2026-01-30, "Add remote command execution
infrastructure"), before the factory, and `safe-execution` left the connection lifecycle as it was:
its R9 covers only the connect at startup (research 02 §4, PLAN risk 7). There is no test of
reconnection on `develop`.

## Outcome / vision

When the cluster drops out from under a running server, a tool call either gets its connection back
and proceeds, or fails within a bound the client will wait for, with an error that names the host,
says why, and makes clear nothing ran. The server stays up, and the next call tries again, so a host
that returns is used without a restart. The bound is stated where an operator or agent will find
it, and the behaviour is pinned by tests that need no real host.

## Acceptance criteria (the contract)

- **R1** — IF the SSH connection has been lost after startup and cannot be re-established, THEN a
  tool call that needs it SHALL fail within the stated bound (R5) of the call's start with an error
  naming the host and the cause, in both protocol eras, rather than not returning.
  (Q1–Q3)
- **R2** — WHEN the connection has been lost and the host becomes reachable again within the retry
  window, a tool call SHALL succeed with the same result it would give on an unbroken connection,
  with no retry by the client.
- **R3** — WHEN a tool call has failed under R1 and the host is later reachable, the next tool call
  SHALL succeed without the server being restarted; the server process SHALL keep running and
  serving calls after an R1 failure.
- **R4** — WHEN a tool call fails under R1, its error SHALL make clear that the command was not
  started (for `sbatch`, that nothing was submitted), so it is distinguishable from the
  `CONNECTION_LOST_EXIT_CODE` (`-1003`) result, which means the command may have run.
  (Q3)
- **R5** — The bound in R1 SHALL be stated in `AGENTS.md`, replacing the `executor/ssh.py` footgun
  entry that today names this defect, and in the `SSHExecutor` docstring, including how a connect
  timeout set by the SSH config changes it.
- **R6** — The suite SHALL pin R1–R4 using the suite's in-process SSH server or a fake connection,
  never a real host, and these tests SHALL run in the profile's `verify` in both protocol eras.
- **R7** — WHILE a connect attempt is in flight, the executor SHALL start no other: a call that
  needs a connection then joins that attempt, waiting under its own R1 bound, so calls arriving
  during a stall that no attempt can abort (name resolution, the SSH agent) leave at most one
  attempt running, never one per call. The suite SHALL pin it as R6 pins R1–R4. (Q7)

## Non-goals (no-gos)

- **A connection that is dead but not yet noticed** (a half-open link the transport still reports
  as active, so no reconnect starts). No keepalive or dead-peer detection here; if it proves a real
  hang, it is a new seed. (Q5)
- **A connection lost while a command runs.** That is `safe-execution`'s `-1003` result and stays as
  it is.
- **The connect at startup** (`safe-execution` R9) and the `CONNECT_TIMEOUT` value: unchanged.
- **Retrying the command itself** after a lost connection, or replaying a failed call: the agent
  decides whether to call again.
- **Coordinating concurrent calls during an outage** beyond each call meeting R1–R3 on its own
  and R7's one attempt in flight. *Amended 2026-10-23 (Q7): sharing one connect attempt is now
  required.*
- **The `local` and `delegate` backends**, which hold no connection.
- **Making the bound configurable** (a flag or environment variable).

## Clarifications

Ruled by the foreman at the GOAL gate, 2026-10-23.

- **Q1** What is the bound in R1? — **A:** 45 s from the call's start with the default connect
  bound (`CONNECT_TIMEOUT`, 15 s), so the error arrives before a client's default request timeout
  (60 s in the MCP TypeScript SDK; the plan confirms the figure and cites it). Where an ssh_config
  `ConnectTimeout` or a caller's `connect_timeout` sets a longer connect bound, the stated bound may
  grow with it, and R5's text says how.
- **Q2** Does a tool's own `timeout` argument shorten the reconnect? — **A:** No. The reconnect
  bound and the command `timeout` are independent: the `timeout` governs the command once it has
  been sent, as `Executor.run` documents today.
- **Q3** What form does the R1/R4 error take? — **A:** what the agent sees is fixed, the mechanism
  is the plan's: the result names the host and the cause in the startup failure's form
  (`cannot connect to <host>: <cause>`) and says the command was not run. A call that returns a
  `CommandResult` reports it with the existing not-started code (`NOT_STARTED_EXIT_CODE`, `-1002`),
  the suite's one meaning for "nothing ran"; a call with no `CommandResult` (an SFTP transfer)
  raises it as the tool's error. No new reserved code, so § Invariants §3 is unchanged.
- **Q4** Should a failure that retrying cannot fix (authentication refused, unknown host or alias)
  fail at once rather than use the whole window? — **A:** the plan's call; whichever it chooses,
  every failure class meets R1's bound and R3's next-call retry.
- **Q5** Is the dead-but-unnoticed connection (first non-goal) in scope? — **A:** No. It is not the
  reported hang; if the plan's research finds it unbounded, it is recorded as a seed, not fixed
  here.

Ruled by the foreman at the PLAN/TECH gate, 2026-10-23.

- **Q6** Is R1's bound per tool call or per executor call? — **A:** per executor call. R1's bound
  is the wait on one failed reconnect window. A tool spends at most one failed window, because
  every tool stops at its first `-1002`; a tool whose earlier executor call succeeded (including
  one that reconnected, under R2) may take that call's own time plus one window. No `ContextVar`
  deadline and no change to `middleware.py`.
- **Q7** May each call's reconnect leave its own stuck attempt behind? — **A:** No; new
  requirement R7. An attempt blocked where nothing can abort it (`getaddrinfo`, the SSH agent)
  outlives its deadline, so per-call attempts would leave N threads after N calls during a DNS
  stall. The executor keeps at most one connect attempt in flight; a call that arrives while one
  runs joins it with its own deadline and never starts another. A test shows K concurrent calls
  during a stall start one attempt, and a mutant (per-call attempts) is caught. Appetite stays
  `small`.

**Human gate.** The fix lands in `src/rcac_mcp/executor/ssh.py`, which is in the coupled core and on
the high-blast-radius list (`AGENTS.md` § Factory → Profile, § Invariants → High-blast-radius
paths). The plan runs research, and a CONFIRMED review finding there needs a human decision before
the cycle lands.

## Related materials

- Seed: [`issues/ssh-reconnect-loop.md`](../../issues/ssh-reconnect-loop.md); `ROADMAP.md` Part 0,
  "A lost SSH connection makes every later call wait forever".
- [`spec/safe-execution/`](../safe-execution/): research 02 §4 (the finding), GOAL R9 (startup
  connect failure), PLAN risk 7 (deferral).
- Source: `src/rcac_mcp/executor/ssh.py` (`_ensure_connected`, `open`, `_bounded`,
  `MAX_RECONNECT_DELAY`, `INITIAL_RECONNECT_DELAY`, `CONNECT_TIMEOUT`, `OPEN_TIMEOUT`);
  `src/rcac_mcp/startup.py` (`connect_failure_message`); `src/rcac_mcp/executor/base.py`
  (`CONNECTION_LOST_EXIT_CODE`); `tests/ssh_server.py` (the in-process SSH server).
- Origin of the defect: `a2a46ff` (2026-01-30), pre-factory.
