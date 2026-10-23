# Research 03: tests for reconnection, and the half-open link (Q5)

Read-only. Experiments were run offline against the in-process server through a loopback proxy, with no real ssh and no wait longer than 4 s. The scripts are in the session scratchpad and are not part of the repo. Versions: Paramiko 5.0.0, Fabric 3.2.2.

## A1. What the in-process server offers

- `LoopbackSSH(home, shell, jail)` (`tests/ssh_server.py:346-466`) binds `127.0.0.1:0` (`:370-373`) and accepts connections in a daemon thread (`:377-390`). `executor()` (`:444-448`) builds the client: `SSHExecutor(LOOPBACK, port=…, user='loopback', config=Config(overrides={'load_ssh_configs': False}, ssh_config=SSHConfig()), connect_kwargs={'pkey': _key('client'), 'look_for_keys': False, 'allow_agent': False})`.
  - **Host key.** Fabric sets `AutoAddPolicy` and loads no `known_hosts` (`fabric/connection.py:458`), so the client never reads or writes a real `~/.ssh`.
  - **HOME.** HOME moves under `tmp_path` only for `unit` tests (`conftest.py:178-201`).
  - `close()` (`:450-460`) closes the listener and the server-side transports.
- `_drop(transport)` (`:589-594`) shuts the socket down without an SSH close. `DroppingSSH` (`:597-636`) drops the connection during the first `drops` exec requests. The existing fixture is `serve` (`test_ssh_exec.py:62-78`).
- **(a) Drop after startup.** Call `_drop(t)` on each of `server._transports`. Measured: the client's `is_connected` was already False when first polled after the drop. A test still polls with a short deadline, because Paramiko marks the transport inactive from its own thread. A unit test with a fake connection just sets `conn.is_connected = False`.
- **(b) Host unreachable.** Close the listener. The next `open()` then raises `NoValidConnectionsError` within 0.001 s; `startup.connect_failure_message` classifies it as `connection refused` (`test_startup.py:251`).
- **(c) Bring the host back on the same host:port.** This works only with `SO_REUSEADDR`. Rebinding without it failed with `EADDRINUSE`, because of TIME_WAIT from the dropped connection. With it, the bind succeeded and the next `run` reconnected and returned the right output in 0.033 s. `LoopbackSSH` has neither a port parameter nor a stop/restart method. **Recommendation:** add two methods to `ssh_server.py`: `refuse()` (close the listener and `_drop` every transport) and `serve_again()` (bind the same port with `SO_REUSEADDR`, then restart `_accept`). Both were proven in the experiment as a 10-line subclass. Two alternatives are worse:
  - Pointing the executor at a new port means writing `_connect_kwargs` from the test, a private attribute.
  - A listener that accepts and then closes yields a banner error rather than "refused".

## A2. Injecting time

- **What the fix should read.** Read both clocks through the module attribute: `time.monotonic()` for the call's deadline and `time.sleep()` for the backoff. That is how all of `ssh.py` uses them today (`:157, :164, :191, :207, :472`). Do not use `from time import …`, a default argument bound at definition, or `time.time()` (Paramiko's clock).
- **How tests install the fake.** Tests do `monkeypatch.setattr(ssh, 'time', clock)`. They must not patch the global `time.sleep`, as the shaper's repro does: that also changes Paramiko's threads and asyncio. If the fix reaches for any other attribute, the namespace fake raises `AttributeError`, which is loud.
- **A tripwire, not a hang.** The current loop must turn a test red, not hang it. `tests/mutants.py` has no child timeout, the project has no pytest-timeout, and the unbounded loop with a fake sleep spins forever. So the clock raises `Runaway(BaseException)` once fake time passes a ceiling, for example `2 × bound` (90 s). It derives from `BaseException` so that no `except (SSHException, OSError)` or `except Exception` in the fix can swallow it.
- **Fake-clock shape** (a module helper or fixture in `test_ssh_exec.py`):
  ```python
  class FakeClock:
      def __init__(self, ceiling=90.0):
          self.offset, self.sleeps, self.ceiling = 0.0, [], ceiling
          self.start = _REAL_MONOTONIC()
      def monotonic(self): return _REAL_MONOTONIC() + self.offset
      def advance(self, s):
          self.offset += s
          if self.monotonic() - self.start > self.ceiling:
              raise Runaway(f'still reconnecting {self.monotonic() - self.start:.0f} s after the call started')
      def sleep(self, s):
          if s < ssh.INITIAL_RECONNECT_DELAY: return _REAL_SLEEP(s)   # `_wait`'s 0.02 s stays real
          self.sleeps.append(s); self.advance(s)
  ```
  Real time plus an offset keeps `_collect` and `_stopped` working when a command runs after a reconnect (R2, R3). Passing short sleeps through to the real sleep stops `_wait` from spinning. R2 and R3 calls should pass no `timeout`: a timeout gives `_collect` a deadline that offset jumps could reach early.
- **An attempt that uses its whole connect bound.** In a unit test, a scripted fake `Connection` (pattern: `FakeConnection`, `test_executors.py:544-569`) handles this. Its `open()` takes the next step from a script:
  - `'ok'` sets `is_connected = True`.
  - `(spent, exc)` calls `clock.advance(spent)` and then raises `exc`. For example, `(15, TimeoutError('timed out'))` stands for a black-holed host, and `(0, NoValidConnectionsError(...))` for a refused one.

  The fake also carries `connect_timeout`, so a fix that reads the bound from the connection is covered. The first step is the constructor's own `open()`. The tests can reuse the `CAUSES` rows from `test_startup.py:248-259`.
- **Unit-test caveats.**
  - `guard` refuses `socket.connect` by raising `OSError` (`conftest.py:138-145`). The reconnect loop would read that as an unreachable host and retry. Every test should assert `guard.refused == []`.
  - In integration tests, use a counting subclass of the real `Connection` (`ssh.Connection = Counting`) whose `open()` calls `server.serve_again()` on its Nth attempt, rather than hooking `sleep`. That way the test does not depend on whether the fix sleeps before a retry.

## A3. Tool level, eras, markers, mutants

- **Eras.** `CLUSTER_MCP_TEST_ERA` (`harness.py:124-142`) sets the era of every harness client. `verify` runs both eras, so a test in the selected era covers both. `in_memory(server, era=…)` (`:311`) pins one era per row; the precedent is `test_startup_failure_is_answered[legacy|modern]`.
- **Calling a tool.** Through the MCP client, `_tool(executor, tool, args)` (`test_ssh_exec.py:442-446`) runs `in_memory(shared_server(executor))` and `call` and returns `Outcome(is_error, text)`. Tool names are `run_command`, `sbatch` (`script_path`), `write_file`, `upload_file` (`local_path`, `remote_path`), `download_file`, and `read_file`.
- **Fence.** An integration test that calls a server must request `executes` or `launches`. A unit test is fenced by the guard.
- **Precedent to copy.** `test_a_lost_connection_is_never_did_not_start` (`test_ssh_exec.py:873-905`) asserts -1003 with `LOST_NOTE`, and that `sbatch` says "may have been submitted" and never "Failed". The R4 test is its mirror image: -1002, the `cannot connect to <host>: <cause>` text, an explicit not-run statement, and `server.received == []`. For `sbatch` there must be no "may have been submitted".
  - `sbatch` today turns any non-zero exit into `Failed to submit job: {stderr}` (`tools/slurm.py:184-188`). So "nothing was submitted" has to come from the -1002 stderr or a new branch.
  - `run()` sends every result through `launch.separate`, which rewrites a missing-marks result to `NOT_STARTED_NOTE` (`launch.py:362-371`). A -1002 from the reconnect must bypass that, or it loses the host and the cause.
- **Markers.** Every test needs `unit` or `integration` (`conftest.py:43-50`). Use `unit` for the fake-connection and clock rows, including tool-level rows through `_tool`, which the guard fences. Use `integration` for `LoopbackSSH` rows. Put them in `test_ssh_exec.py` in a new section next to "A lost connection", with `reconnect` in every test name. Update its entry in AGENTS § Testing, and the `ssh_server.py` docstring for `refuse` and `serve_again`.
- **Mutants.**
  - `test_mutation_anchor_applies` requires each case's `old` text to occur exactly once. Anchors near the fix:
    - `open()`: `:446` (`startup-no-connect-timeout`)
    - `close`: `:449`
    - `_host: str`: `:418`
    - `run()`: `:511`, `:512`, `:515`
    - `_exec`: `:532`, `:537-538`
    - `time.sleep(wait)`: `:157`

    Keep these lines byte-identical and unique, or re-plant the case in the same commit.
  - Proposed new cases:
    - `ssh-reconnect-unbounded`: drop the deadline check. Target: the R1 unit row; why: `still reconnecting`.
    - `ssh-reconnect-reads-as-lost`: return `CONNECTION_LOST_EXIT_CODE`. Target: the R4 row; why: `not started`.
    - Optionally `ssh-reconnect-gives-up-for-good`: a sticky failure flag. Target: R3.

    Each child takes about 2 s, and the tripwire means a surviving mutant fails instead of hanging.

## A4. Narrowed verify (measured on this worktree)

- **Full file.** `uv run pytest -q tests/test_ssh_exec.py` took **110 s** (80 tests, legacy era), too slow for a phase. `test_startup.py` took 3.8 s and `test_mutations.py` 1.6 s.
- **Proposed command:**
  ```
  K='reconnect or lost or connect_failure or connect_is_bounded or startup_failure or mutation'
  F='tests/test_ssh_exec.py tests/test_startup.py tests/test_mutations.py tests/test_executors.py'
  uv run pytest -q ${=F} -k "$K" && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q ${=F} -k "$K"
  ```
  Today it selects 215 tests and takes **4.0 s and 3.8 s** (10 s wall for both eras). The new `reconnect` rows add well under 1 s each.
- **Final phase.** Also run `uv run python tests/mutants.py --self-check <new ids> ssh-bootstrap-dropped startup-no-connect-timeout` (about 2 s per case).

## B. Q5: a dead link the transport still reports as active

- **Detection.** `is_connected` is `transport.active` (`fabric/connection.py:581-587`). Nothing marks a silent peer dead:
  - Paramiko's keepalive is off by default (`transport.py:1189-1204`; measured: packetizer interval `0`).
  - Fabric never turns it on: no keepalive/ServerAlive in `fabric/`.
  - The socket's `SO_KEEPALIVE` is `0` (measured).
  - The transport thread's 0.1 s socket timeout (`:344`, `:480`) only loops.

  So `_ensure_connected` returns at once.
- **Demonstration.** A loopback proxy stopped forwarding without closing either socket.
  - `is_connected` stayed True.
  - `run` raised `SSHException('Timeout opening channel.')` at 2.03 s with `OPEN_TIMEOUT` patched to 2. `is_connected` was still True afterwards.
  - `put` was still blocked after 4 s, and it returned (`EOFError`) only when the proxy closed the sockets.
- **Verdict: bounded for `run`, unbounded for SFTP.**
  - **`run` / `run_command` / `sbatch` / every Slurm tool:** bounded at **`OPEN_TIMEOUT` (30 s)**. `open_session(timeout=30)` waits in a 0.1 s loop and then raises (`transport.py:1043-1056`). The tool returns an `SSHException` error, which is not the R1 text. Every later call pays the same 30 s until the kernel's TCP retransmission gives up: minutes, with Linux's default `tcp_retries2` at about 15 min; not measured on macOS. There are two exceptions to the bound:
    - If the link dies after the channel opens, `exec_command` waits with no timeout (`channel.py:255`, `_wait_for_event` at `:1216-1224`). After that, `_collect` with `timeout=None` waits without limit. `check_timeout(None)` returns None (`launch.py:437-440`), and `run_command` and `sbatch` pass None by default. This is the `-1003` window, a non-goal, but it is unbounded.
    - A full socket send buffer makes `Packetizer.write_all` retry `socket.timeout` indefinitely (`packet.py:348-386`) while it holds the write lock. That can block even the `open_session` send of a concurrent call.
  - **`write_file` / `upload_file` / `download_file` (SFTP):** **unbounded in practice.** `open_sftp` (`client.py:596`) opens its session with no timeout, so it waits `channel_timeout = 3600 s` (`transport.py:537, :1016`; `sftp_client.py:164-169`). Then `invoke_subsystem` waits with no timeout (`channel.py:281`), and SFTP replies are read with `Channel.timeout` None (`sftp.py:196`). Only the kernel's TCP give-up, or a closed socket, ends it.
  - **Seed.** Record a seed: a half-open link holds SFTP tools for up to an hour (in practice until TCP gives up), and holds `run` for 30 s per call with a non-R1 error. A candidate fix is `set_keepalive` plus a bounded `open_sftp`. Not fixed here (Q5).

## Recommendation

- **R1** (`unit`). Scripted fake `Connection` plus `FakeClock`. Rows: refused (0 s per attempt), black-holed (15 s per attempt, `TimeoutError`), banner timeout, unknown host, authentication failed, and `connect_timeout=30` (the bound grows as R5 states). Assert that the call returns before `Runaway`, within 45 s of fake time from the call's start, as -1002 with `cannot connect to <host>: <cause>`. Cover `run` directly and, through `_tool`, `run_command`, `sbatch`, `write_file`, `upload_file` and `download_file`; the SFTP tools must give an error with the same text. Today's code goes red through `Runaway`.
- **R2** (`integration`, `LoopbackSSH`). Record a baseline `run`, then `refuse()`. The counting `Connection` calls `serve_again()` on attempt 2. The same command then returns a result identical to the baseline, with no client retry, under `FakeClock` with no timeout.
- **R3** (`integration`). One `in_memory` session. With the host refused for the whole window, call 1 returns -1002 within the fake bound. Then `serve_again()`, and call 2 on the same server object succeeds.
- **R4.** In the R1 and R3 rows, assert -1002 and never -1003, the not-run text, `server.received == []`, and that `sbatch` says nothing was submitted and never "may have been submitted".
- **R6.** All rows run in the selected era, so `verify` covers both. Optionally add `era=` rows for `run_command`, as the startup precedent does. Use the narrowed verify above (~10 s, both eras), plus the two new mutation cases.
- **Fake-clock design.** Patch `ssh.time` with the offset `FakeClock` above: a `BaseException` tripwire, short sleeps passed through to the real sleep. The fix reads `time.monotonic` and `time.sleep` only through the module.
- **Q5.** Bounded for `run` by `OPEN_TIMEOUT` (30 s per call, with a non-R1 error), apart from the exec and full-buffer gaps above. **Unbounded for SFTP** (3600 s channel open, then waits with no timeout). Record it as a seed, not in scope.
