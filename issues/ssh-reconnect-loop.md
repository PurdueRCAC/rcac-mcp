---
status: adopted:ssh-reconnect-loop
kind: fix
appetite: small
lane: public
---

# A lost SSH connection makes every later tool call wait forever

## Problem

`SSHExecutor._ensure_connected` (`src/rcac_mcp/executor/ssh.py`) reconnects with exponential
backoff while `delay <= MAX_RECONNECT_DELAY`, doubling the delay with `min(delay * 2,
MAX_RECONNECT_DELAY)`. Once the delay reaches the cap (10 s) it stays there, so the condition never
becomes false: the loop never reaches its final attempt or its `ConnectionError`. When the cluster
becomes unreachable after startup (a network drop, a login node rebooted), the next tool call
retries every 10 s for as long as the server runs, and the client sees a call that never returns
instead of an error naming the host.

## Why it was deferred

`safe-execution` replaced how a command runs over an open connection (a raw channel per command)
and left the connection's lifecycle as it was; the GOAL's connect-failure requirement covers only
startup. **Pre-existing:** yes, present on `develop` before `safe-execution` (research 02 §4 of
`spec/safe-execution/`).

## Outcome / vision

A lost connection is retried a bounded number of times, within a bound a client will wait for, and
then the tool call fails with an error naming the host and the cause.

## Sketch of the acceptance criteria

- **R1** — WHEN the SSH connection is lost and cannot be re-established, a tool call SHALL fail
  within a stated bound with an error naming the host, rather than retry without end.
- **R2** — WHEN the connection comes back within the retry window, the call SHALL succeed.

## Notes

- Related: `spec/safe-execution/` (research 02 §4, PLAN risk 7); the startup connect timeouts land
  in `safe-execution` P3.
- Found by: `safe-execution` planning research, filed at P2 (2026-10-17).
