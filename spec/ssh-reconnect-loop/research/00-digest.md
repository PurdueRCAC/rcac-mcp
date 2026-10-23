# Research 00 — Digest: ssh-reconnect-loop

Three briefs, fanned out because the fix lands in `executor/ssh.py`, a high-blast-radius path
(code-plan trigger 2). Each recommendation below resolves the briefs into one decision.

- [`01-connect-attempt-bounds.md`](01-connect-attempt-bounds.md): what one connect attempt costs.
- [`02-error-surface.md`](02-error-surface.md): where a failed reconnect surfaces, and what each
  caller shows the agent.
- [`03-tests-and-half-open.md`](03-tests-and-half-open.md): the test harness, the injected clock,
  and the half-open link (Q5).

## External fact: the client's default request timeout

Q1 relied on a recollection that the MCP TypeScript SDK's default request timeout is 60 s. The
SDK source confirms it.

- On `main` (`951fe56`, release `v2.4.0`, 2026-10-23), `packages/core-internal/src/shared/protocol.ts:103`
  reads `export const DEFAULT_REQUEST_TIMEOUT_MSEC = 60_000;`.
- `Protocol.request` applies it at `:1582`: `const timeout = options?.timeout ?? DEFAULT_REQUEST_TIMEOUT_MSEC;`.
- `resetTimeoutOnProgress` defaults to `false` (`:131`, `:1585`), so a server's progress
  notifications don't extend it unless the client opts in.
- The v1 line has the same value: tag `1.20.0`, `src/shared/protocol.ts:60`, `= 60000`.

The 45 s bound therefore lands 15 s inside that default.

## Decisions

1. **Per-step timeouts can't bound a reconnect; a deadline can** (01 §1, §3).
   - DNS and the SSH-agent query have no timeout.
   - Auth waits `auth_timeout` once per key, so N keys cost N × 15 s.
   - Behind ProxyJump, the channel to the target waits up to `channel_timeout`, which is 3600 s.

   So each call gets a deadline, and every attempt runs on a daemon worker thread. The caller
   polls that thread against the deadline. When the deadline passes, the caller closes the
   attempt's transport, which aborts kex or auth within about 0.1 s, and marks the attempt
   abandoned under the executor's lock. A late success then closes itself and is never published.
   Each backoff sleep is capped at the time left.
2. **The bound is `3 × max(CONNECT_TIMEOUT, the connection's own connect bound)`**, measured from
   the call's start: 45 s by default.
   - A longer ssh_config `ConnectTimeout`, or a longer caller `connect_timeout` or
     `connect_kwargs['timeout']`, raises it to three times that value (Q1).
   - A shorter one never lowers it, so the retry window R2 relies on never shrinks below 45 s.
   - Production passes no connect arguments (`__init__.py:241`), so there only ssh_config can
     move the bound.
3. **Catch `Exception` in an attempt, not `(SSHException, OSError)`** (01 §4). A clean close
   during kex raises `EOFError`, and Paramiko has bare-`Exception` and `ValueError` paths. A
   `BaseException` (an interrupt, or the tests' tripwire) is handed back and re-raised in the
   calling thread.
4. **Q4: an `AuthenticationException` ends the reconnect at once.**
   - The auth timeout and an outright refusal share that class (01 §4), and telling them apart by
     message text would be fragile.
   - Failing fast on both still meets R1, and the next call retries (R3).
   - It also keeps a refused key from being re-presented about eight times per call to a login
     node that may count failures.
   - Everything else retries until the deadline. A DNS failure isn't failed fast: an offline laptop
     raises the same error as an unknown alias (01 §4).
5. **Build each connection locally and publish it only on success, under a lock** (02 §4, §5;
   01 §5).
   - Today `open()` assigns `self._conn` before it connects. Two concurrent reconnects can then
     hand one call another's half-open connection, which raises an `AttributeError` that names
     neither host nor cause.
   - After an authentication failure the transport stays active, so the failed connection reads
     as connected.
   - A failed attempt's client is closed, best effort.
   - A dead connection being replaced is closed too.
   - If another thread has already published a live connection, the loser closes its own and uses
     that one.
6. **What the agent sees** (Q3; 02 §1, Recommendation):
   - **`run`** returns `CommandResult('', 'cluster-mcp: cannot connect to <host>: <cause>; the command was not run\n', NOT_STARTED_EXIT_CODE, host)`.
     This result skips `launch.separate`, which would otherwise replace it with the generic
     not-started note (03 §A3).
   - **`write`** returns the same result when its first connect fails.
   - **`put` and `get`** raise `ConnectionError('cannot connect to <host>: <cause>; nothing was transferred')`
     with a single argument, so the tool error reads `Error calling tool 'upload_file': cannot connect …`
     in both eras.
   - **The message helper** is `connect_failure_message`. It moves from `startup.py` into
     `executor/ssh.py`, and `startup.py` imports it back from there. That avoids a backend
     importing the CLI's module.
7. **Callers that would break R1 or R4** (02 §1):
   - `sbatch` gets a `NOT_STARTED_EXIT_CODE` branch that says the job was not submitted.
   - `sacct` checks `whoami`'s exit; today an unchecked failure costs a second window.
   - `write`'s `_reason` probe treats −1002 as "no reason".
   - `load_cluster_context` doesn't cache a −1002 result. A resource read gets no executor today
     (§ Invariants §8), so this is latent, but the change would otherwise create it.
8. **One deadline per call inside `write`.** `write`'s inner commands (the parent `mkdir`, the
   place step, the probe) share the deadline its first connect started. They go through a private
   `_run(…, deadline)` that the public `run` wraps, so the Protocol signature is unchanged (§2).
9. **Tests use an offset clock, swapped in through the module** (03 §A2).
   - The fix reads `time.monotonic` and `time.sleep` only as `ssh.time.*`.
   - Tests replace `ssh.time` with a clock that runs on real time plus an offset. Short sleeps
     pass through to the real sleep.
   - The clock trips a `BaseException` past 2 × the bound, so the old loop fails a test instead of
     hanging it.
   - Waiting on an attempt is a poll on a short real interval (`ATTEMPT_POLL`, 0.1 s) that reads
     the injected clock, so an attempt that hangs is abandoned in a fraction of a real second.
   - R2 and R3 run against `LoopbackSSH` with two new methods, `refuse()` and `serve_again()`.
     `serve_again()` rebinds the same port with `SO_REUSEADDR`.
10. **Q5: unbounded for SFTP, so it's a seed and isn't fixed here** (03 §B).
    - Paramiko keepalive is off, Fabric never turns it on, and `SO_KEEPALIVE` is 0, so a
      half-open link reads as connected.
    - `run` is bounded at `OPEN_TIMEOUT` (30 s), but its error isn't the R1 text.
    - The SFTP tools wait up to 3600 s to open a channel, and then without limit.
    - An exec that has already started waits without limit when `timeout` is None.
    - Filed as `issues/ssh-half-open-link.md`.

## Contradictions resolved

- 01 suggested a bound of `3 × max(CONNECT_TIMEOUT, connect_timeout)`. 02 left the formula open.
  The plan adopts 01's formula and also counts `connect_kwargs['timeout']` (01 §2), which
  `_bounded` already honours.
- 02 offered either a lock held across the whole reconnect or one held only to publish. The plan
  holds it only to publish (decision 5). That way no call waits on another call's window, and
  each call's bound stays its own.
- 03's verify used a `-k` selection. The plan names the test ids instead (§ Conventions: a
  selection can match nothing and still pass).
