---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A dead SSH link that still reads as connected holds file transfers without limit

## Problem

In `ssh` mode the server keeps one connection open. When the peer goes silent and no reset ever
arrives (a NAT entry expiring, a laptop sleeping through a network change, a login node powered off
mid-session), nothing notices that the link is dead:

- Paramiko's keepalive is off by default (`transport.py:1189-1204`), and Fabric never turns it on.
- The socket's `SO_KEEPALIVE` is 0.

So `Connection.is_connected` (`transport.active`) stays true, and `SSHExecutor._ensure_connected`
returns the dead connection without trying to reconnect.

What a call then does depends on the tool. These were probed offline through a loopback proxy that
stopped forwarding without closing either socket, against Paramiko 5.0.0 and Fabric 3.2.2:

- **SFTP tools (`write_file`, `upload_file`, `download_file`, and `sbatch` with `script_content`)
  are effectively unbounded.**
  - `client.open_sftp()` opens its channel with no timeout, so it waits up to
    `Transport.channel_timeout` (3600 s).
  - `invoke_subsystem` and every SFTP reply then wait with no timeout.
  - In the probe, `put` was still blocked after 4 s and returned only when the sockets were closed.
- **Commands (`run_command`, the Slurm tools) wait about 30 s per call.**
  - `open_session(timeout=OPEN_TIMEOUT)` raises `SSHException('Timeout opening channel.')`, which
    is not the `cannot connect to <host>: <cause>` form a failed reconnect gives.
  - Every later call pays the same 30 s until the kernel's TCP retransmission gives up, which on
    Linux defaults is on the order of 15 minutes.
- **Two command paths have no bound at all.**
  - A link that dies after the channel opens leaves `exec_command` waiting with no timeout
    (`channel.py:255`).
  - `_collect` with `timeout=None` waits without limit, and `run_command` and `sbatch` pass `None`
    by default.

  The second is `safe-execution`'s −1003 window.
- **A full send buffer stalls writes indefinitely.** `Packetizer.write_all` retries a
  `socket.timeout` without limit while it holds the transport's write lock, which can also stall a
  concurrent call's channel open.

## Why it was deferred

The `ssh-reconnect-loop` cycle ruled it out of scope (its GOAL's first non-goal, clarification
Q5). That cycle bounds the reconnect that starts once a lost connection has been noticed. This
seed covers the connection that is never noticed as lost. It is **pre-existing**, present on
`develop` before that cycle, and found by its planning research (`spec/ssh-reconnect-loop/research/03-tests-and-half-open.md` §B).

## Outcome / vision

A link that has gone silent is noticed within a stated bound, and a call that meets it fails the way
a failed reconnect does: an error naming the host and the cause, the command not run. A call never
waits on a dead peer longer than a client will wait for it, whichever tool it is.

## Sketch of the acceptance criteria

- **R1** — WHEN the link to the host has gone silent without a reset, a tool call SHALL fail
  within a stated bound with an error naming the host, in both protocol eras.
- **R2** — The SFTP paths (`write`, `put`, `get`) SHALL bound the channel open and each reply wait.
- **R3** — WHEN a command's channel was already open as the link went silent, the call SHALL still
  end within the command's timeout or a stated bound, reported as `CONNECTION_LOST_EXIT_CODE`.

## Notes

- Candidate fixes:
  - `Transport.set_keepalive` on every published connection;
  - a timeout on the SFTP channel open (`open_session(timeout=…)` + `invoke_subsystem('sftp')` +
    `SFTPClient`, in place of `open_sftp()`) and on its replies (`Channel.settimeout`);
  - a liveness probe (a cheap global request) before reuse.

  Any of them touches `executor/ssh.py`, a high-blast-radius path.
- The probe needs no real host: a loopback proxy in front of `tests/ssh_server.py` that stops
  forwarding without closing either socket reproduces it in seconds.
- Related: `spec/ssh-reconnect-loop/` (the bounded reconnect this complements);
  `spec/safe-execution/` (−1003 and the connect bound); `issues/timeout-process-cleanup.md`.
- Found by: `ssh-reconnect-loop` planning research, 2026-10-23.
