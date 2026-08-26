---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Executor.run has two different contracts depending on the backend

## Problem

`Executor.run()` (`executor/base.py:36`) documents one return contract: "Returns: CommandResult with
stdout, stderr, exit_code, and hostname." Two of the three backends honour it on timeout; one does
not.

`LocalShellExecutor.run` (`executor/shell.py:87`) and `DelegatingExecutor.run`
(`executor/delegate.py:150`) both catch `subprocess.TimeoutExpired` and return a `CommandResult` with
`exit_code=-1`:

```python
except subprocess.TimeoutExpired as exc:
    return CommandResult(stdout=exc.stdout or '', stderr=exc.stderr or f'Command timed out after {timeout}s',
                         exit_code=-1, hostname=self._hostname)
```

`SSHExecutor.run` (`executor/ssh.py:133`) passes `timeout` straight to Fabric and catches nothing, so
Invoke raises `CommandTimedOut` and it propagates out of the tool.

So `run_command("sleep 60", timeout=1)` returns a `CommandResult` in `local` mode and raises in `ssh`
mode — **the default mode**, and the one the README recommends. A tool or client that handles a
timed-out command correctly in development fails in production.

There is a second, smaller problem stacked on the first: `exit_code=-1` is overloaded. A real
subprocess killed by SIGHUP also returns `-1`, so a caller cannot distinguish "timed out" from "died
on signal 1". `AGENTS.md` § *Executors* and `invariants.md` §3 both record the current state and the
rule that new "never ran" sentinels go below `-1000`.

## Why it was deferred

Not deferred — found while writing the executor invariants and filed rather than fixed, because the
harness port was not a cycle and this one touches the `Executor` protocol, where all three backends
must move together.

**Pre-existing:** yes — on `main` as of `2def4a4`. No test covers `timeout` in any backend.

## Outcome / vision

One contract, documented on the protocol and satisfied by all three implementations. The promotion
conversation has to choose *which* contract, and both directions are defensible:

- **Always return a `CommandResult`** — matches the current majority and the docstring, keeps tools
  branch-free, but needs a timeout marker that is not `-1`.
- **Always raise a shared `CommandTimeout` exception** — a timeout arguably is not a command result,
  and it forces callers to handle it rather than silently treating `-1` as an exit code.

Whichever is chosen, the `-1` collision should be resolved in the same cycle: a distinct sentinel
below `-1000`, or an explicit field, so "timed out" and "killed by SIGHUP" are distinguishable.

## Sketch of the acceptance criteria

- **R1** — WHEN a command exceeds its `timeout`, all three executors SHALL behave identically, per the
  single contract chosen at promotion.
- **R2** — The chosen contract SHALL be stated in the `Executor` protocol docstring
  (`executor/base.py`), which is the only place all three implementations share.
- **R3** — A timed-out command SHALL be distinguishable from a command killed by SIGHUP.
- **R4** — Tests SHALL cover the timeout path for the local and delegating executors without invoking
  `sudo`, and for the SSH executor without a real cluster (a stubbed connection is acceptable —
  `.agents/factory/bin/sandbox.sh` cannot reach one by design).
- **R5** — `AGENTS.md` § *Executors* and `invariants.md` §3 SHALL be updated in the same commit; they
  currently describe the asymmetry as the state of the world.

## Notes

- Exception provenance: Fabric's timeout surfaces as `invoke.exceptions.CommandTimedOut`. Confirm the
  exact type and module against the installed version during `/cm-plan` rather than trusting this
  note.
- Related: this is the third of the four defects found during the harness port, and the only one
  touching the protocol — so it is the only one of the four where all three backends must change
  together.
- Found by: the harness port, 2026-08-26 (out of cycle).
