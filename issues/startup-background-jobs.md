---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A startup file's background job can delay a result, or reach it over SSH

## Problem

Since `safe-execution`, every command runs in a login bash, and nothing the login initialisation
prints reaches a result: the launch parks the command's streams while startup files run, and each
program prints a per-call sentinel that the backend cuts at (`AGENTS.md` § Invariants §3). Two cases
remain outside what the server can separate without a PTY:

- A startup file that starts a long-lived background process (an agent, a `sleep` in the
  background) leaves it holding the parked descriptors, so the call waits for that process to exit
  or for the timeout before it returns.
  - 2026-10-18: no longer so. Since `safe-execution`'s review remediation, once the command's exit
    status is known each backend reads for a bounded grace (`launch.GRACE`, 1 s) and returns, in all
    three modes; a startup file's background job no longer holds the call (`AGENTS.md` §3).
  - 2026-10-18 (review cycle 2): the read after the exit status now goes on while output arrives,
    and stops once nothing has arrived for `launch.GRACE`; a silent background job a startup file
    left running adds at most that second and a note in the result saying reading stopped there
    (`AGENTS.md` §3, "A result is complete unless only holders remain").
  - 2026-10-22: no timing rule any more. The program reports the command's end on both streams, and
    the call returns at those end records, so a startup file's background job neither holds the call
    nor adds a note (`AGENTS.md` §3, "A result is complete exactly when both end records have
    arrived").
- Over SSH, a background job started by the remote login shell's own rc file (`.zshenv`, `.cshrc`, a
  `.bashrc` that sshd sources), which runs before the bootstrap can park anything, can still write
  into the channel after the sentinel and so into the result.
  - 2026-10-18: still so, and now the one documented exception to the separation: `AGENTS.md` §3,
    the README, the `run_command` description and the `Executor` docstring state it, and
    `tests/test_ssh_exec.py` pins both that it still happens and that every one of those texts says so.
  - 2026-10-22: still so for what such a job writes before the command's end record; what it writes
    after the record is no longer read.

A `run_command` script also runs in a fresh non-login bash, so a profile's unexported shell
variables are not visible to it (exported variables and functions are).

## Why it was deferred

Both need a different launch (a PTY, or a session the server owns and reaps) to fix, which is a
larger change than the cycle's appetite, and neither was seen on the clusters' default startup files
during planning (research 01). **Pre-existing:** the first case is new with the login bash; the
second existed before (the login shell's rc output reached results unseparated).

## Outcome / vision

A startup file's background activity can neither hold a call open past its command nor write into
its result, in any mode.

## Sketch of the acceptance criteria

- **R1** — WHEN a startup file leaves a background process running, the call SHALL return when its
  command ends.
- **R2** — WHEN a remote login shell's rc file starts a job that writes later, none of that output
  SHALL appear in the result.

## Notes

- Related: `spec/safe-execution/` (PLAN §2.2a, risk 5, focused review finding 6).
- Found by: `safe-execution` focused review of the startup separation, filed at P2 (2026-10-17).
