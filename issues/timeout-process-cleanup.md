---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A timed-out command may keep running after its call returns

## Problem

Since `safe-execution`, a command that exceeds its `timeout` returns a result with the captured
output and `TIMEOUT_EXIT_CODE` in every mode (`AGENTS.md` § Invariants §3). Whether the command
itself stops is not settled in two places:

- **`ssh`.** At the deadline the executor asks the server to deliver TERM (an RFC 4254 `signal`
  channel request) and closes the channel. OpenSSH does not stop a non-PTY command when its channel
  closes, and whether the clusters' OpenSSH (8.0p1 on RHEL 8, 8.7p1 on RHEL 9) acts on the `signal`
  request is unmeasured. A command that never writes again (`sleep 3600`) may run to its end.
- **`delegate` under a non-root server.** The server signals the whole process group, but a non-root
  service account can signal only `sudo`; `sudo` relays TERM to the command, so a command that
  ignores TERM, and children it started, can outlive the call. A root server reaches them all.

The result contract holds either way; what is at stake is a process left running on a login node.

## Why it was deferred

Measuring it needs a long-running process on a real login node, which is outside the read-only
dogfood class `safe-execution` was allowed; it waits for Geoffrey's approval. The likely fix (a
remote `timeout -k` wrapper in the bootstrap, or a PTY-free process-group kill) changes the launcher
and wants that measurement first. **Pre-existing:** yes; before `safe-execution` the SSH backend
raised at the deadline and left the command running the same way.

## Outcome / vision

A timed-out command, and what it started, is stopped on the cluster within a bound, in every mode,
and a test pins it without a real host.

## Sketch of the acceptance criteria

- **R1** — WHEN a command exceeds its timeout over SSH, it and its children SHALL be stopped on the
  remote host within a stated bound (measured on a login node, with approval).
- **R2** — WHEN a `delegate` server runs without root, a timed-out command that ignores TERM SHALL
  still be stopped, or the limit SHALL be documented where operators read it.

## Notes

- Related: `spec/safe-execution/` (PLAN risk 4, research 02 §2 and 06 A2); `AGENTS.md` §3.
- Found by: `safe-execution` planning research and adversarial review, filed at P2 (2026-10-17).
