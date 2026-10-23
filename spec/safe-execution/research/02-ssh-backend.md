# Research 02 — SSH backend on fabric 3.2.2 / invoke 2.2.1 / paramiko 5.0.0

Sources: installed libraries under `.venv/lib/python3.14/site-packages/` (cited as `fabric/…`, `invoke/…`,
`paramiko/…`). Every behaviour marked **[probed]** was run against an in-process Paramiko server or
loopback (scripts in the planner's scratchpad, `se-plan/ssh-backend/`); nothing reached a real host.

## 1. What `Connection.run` sends

- No shell wrapping. `Connection.run` → `Context._run` → `_prefix_commands` (`invoke/context.py:252-264`)
  joins `cd <cwd>` / `prefix()` strings with ` && ` (unused by us), then `Remote.start`
  (`fabric/runners.py:48-79`) calls `channel.exec_command(command)` verbatim. The `shell` argument is
  ignored remotely. Only a non-empty `env` changes it: `inline_ssh_env` defaults to `True`
  (`fabric/config.py:319`) and prefixes `export K=V && ` **unquoted** (`fabric/runners.py:62-73`).
  [probed: `env={'A':'b c'}` arrives as `export A=b c && x`.]
- The remote sshd runs the string as `<login shell> -c <string>` (not a login shell, argv0 is the
  basename; OpenSSH `session.c` do_child, from knowledge). So bash/zsh/tcsh/fish parse every
  byte today; rc files still run (`~/.zshenv`, `~/.tcshrc`, `~/.bashrc` via sshd detection).
- `in_stream` is **not byte-exact**: `read_our_stdin` decodes with `errors='replace'`
  (`invoke/runners.py:828-831`, `:1019-1027`) and `write_proc_stdin` re-encodes (`:1017`).
  [probed: `ff fe 00 61 62 63 0a c3 28` arrived as `ef bf bd ef bf bd 00 … ef bf bd 28`.]
  It reads **1 byte per loop** from any non-TTY stream (`invoke/terminals.py:245-248`) with a
  10 ms sleep per iteration (`invoke/runners.py:75`, `:898`): ~100 B/s. EOF *is* sent:
  an empty read calls `close_proc_stdin` → `channel.shutdown_write()` (`runners.py:885-890`,
  `fabric/runners.py:94-95`) [probed].
- **Pre-existing defect, stdio mode**: `in_stream=None` defaults to `sys.stdin`
  (`invoke/runners.py:567-572`) and `SSHExecutor.run` passes nothing (`executor/ssh.py:133`), so a
  stdin thread forwards the MCP client's JSON-RPC bytes to the remote command while it runs.
  [probed with Invoke `Local`, same `Runner.handle_stdin`: a piped `notifications/cancelled` line was
  consumed by the child and the parent then read `''`.] `test_ssh_smoke.py:91-93` already works
  around it. `LocalShellExecutor` has the same leak (`executor/shell.py:74`, no `stdin=`).
- Output decoding is per 1000-byte chunk (`invoke/runners.py:706-710`), so multibyte UTF-8 split
  across a read boundary becomes two U+FFFD. [probed: 6001 bytes of `x`+`é`×3000 → 12 U+FFFD.]

## 2. Timeouts

- Invoke arms `threading.Timer(timeout, self.kill)` (`invoke/runners.py:1069-1075`); `Remote.kill`
  is `channel.close()` (`fabric/runners.py:131-137`); `_finish` raises
  `CommandTimedOut(result, timeout=…)` (`invoke/runners.py:514-516`) — `warn=True` does not stop it.
  `e.result` carries the partial output; `exited` is `-1` (no status). [probed: stdout
  `'partial-out\n'`, stderr `'partial-err\n'`, `exited=-1`, raised at 1.0 s.]
- Closing a channel does **not** kill a non-PTY remote process. OpenSSH `session_close_by_channel`
  only cleans up a PTY when the child is alive (from knowledge of `session.c`); the child's pipes are
  closed, so it dies of SIGPIPE on its next write or runs to completion (`sleep 60` survives).
  OpenSSH ≥ 7.9 accepts an RFC 4254 §6.9 `signal` channel request and, I believe, does
  `killpg(session pid, sig)` (unverified here; RHEL 8 ships 8.0p1, RHEL 9 8.7p1). Paramiko has no
  client API for it, but a raw `Message` works (the probe sent `exit-signal` the same way).

## 3. Raw Paramiko channel

- `transport.open_session(timeout=)`, `chan.exec_command(cmd)` (raises `SSHException` on refusal),
  `sendall` (loops `send`; raises `OSError('Socket is closed')` once the channel closes,
  `paramiko/channel.py:825-846`, `:1189-1197`), `shutdown_write()` sends EOF (`:979-989`).
- `exit-signal` is not handled client-side (`paramiko/channel.py:1151-1153`), so a signal death
  leaves `exit_status` at its initial `-1` (`:133`), returned once the channel closes
  (`:375`, `:1226-1234`). [probed: exit-signal KILL → `exited=-1`.] A transport that dies mid-command
  also closes the channel, yielding `-1` — check `transport.is_active()` after the loop.
- Flow control: stdout and stderr share one receive window (default 2 MiB,
  `paramiko/common.py:232`); `recv_exit_status` can hang if output exceeds it unread
  (`channel.py:385-395`). Writing stdin with `sendall` while not draining output deadlocks against a
  process that writes before reading. So: feed stdin from a thread, drain both streams in the caller.
- `chan.fileno()` gives a select-able pipe for stdout/stderr data (`channel.py:914-942`), but after
  EOF it is readable forever (`:1170-1171`) — select would spin; wait on `status_event` then.

Minimal loop:

```python
def _exec(transport, cmd: str, stdin: bytes | None, timeout: float | None) -> tuple[bytes, bytes, int | None]:
    chan = transport.open_session(timeout=CONNECT_TIMEOUT)
    try:
        chan.exec_command(cmd)
        def feed() -> None:
            try:
                if stdin:
                    chan.sendall(stdin)
                chan.shutdown_write()
            except OSError:
                pass                                     # channel closed under us (timeout)
        threading.Thread(target=feed, daemon=True).start()
        out, err = bytearray(), bytearray()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            while chan.recv_ready():
                out += chan.recv(65536)
            while chan.recv_stderr_ready():
                err += chan.recv_stderr(65536)
            done = chan.closed or (chan.eof_received and chan.exit_status_ready())
            if done and not (chan.recv_ready() or chan.recv_stderr_ready()):
                return bytes(out), bytes(err), chan.recv_exit_status()   # -1 = signal / no status
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                return bytes(out), bytes(err), None      # caller maps None → timeout sentinel
            wait = 0.1 if left is None else min(left, 0.1)
            if chan.eof_received:
                chan.status_event.wait(wait)             # pipe is set_forever after EOF
            else:
                select.select([chan], [], [], wait)
    finally:
        chan.close()
```

Pitfalls: `exit-status` may precede the last data (require EOF *and* status, or `closed`); a
background grandchild holding stdout open means EOF never comes — only the deadline ends it; decode
once at the end with `bytes.decode('utf-8', 'replace')` (never per chunk; never `surrogateescape`,
it does not survive JSON); output is unbounded in memory (as today); on timeout optionally send
`signal` TERM before `close()`.

## 4. Startup connect failures (R9) [probed]

| Case | Exception (MRO) | `str(exc)` | Names host? |
|---|---|---|---|
| unknown alias / DNS | `socket.gaierror` (OSError) | `[Errno 8] nodename nor servname provided, or not known` (Linux: `[Errno -2] Name or service not known`) | no |
| refused | `NoValidConnectionsError` (OSError) | `[Errno None] Unable to connect to port 1 on 127.0.0.1` | resolved IP only |
| key refused | `AuthenticationException` (SSHException) | `Authentication failed.` | no |
| no keys/agent | `SSHException` | `No authentication methods available` | no |
| TCP timeout (`connect_timeout=2`) | `TimeoutError` (OSError) | `timed out` | no |
| port open, no SSH banner | `SSHException` | `Error reading SSH protocol banner` (15 s), or `No existing session` with `connect_timeout` | no |

The default connect timeout is `None` (`fabric/config.py:327`) unless ssh_config sets
`ConnectTimeout` (`fabric/connection.py:443-448`), so a black-holed host waits for the OS TCP
timeout. Fabric uses `AutoAddPolicy` (`connection.py:458`). Paramiko also logs banner errors with a
traceback through the root logger.

Today: `SSHExecutor.__init__` calls `open()` directly with no retry (`executor/ssh.py:56`); the raw
exception escapes `_create_executor` (`__init__.py:233`) before any server exists; cmdkit matches the
`Exception` row (`__init__.py:156`) → `CRITICAL [rcac_mcp] [Errno 8] nodename nor servname provided,
or not known`, **exit 6** [probed: `bin/sandbox.sh uv run rcac-mcp --ssh-host nonexistent-alias.invalid`].
No host is named. Separately, `_ensure_connected` never terminates: once `delay` reaches 10.0,
`min(delay*2, 10.0)` keeps `delay <= MAX_RECONNECT_DELAY` true forever (`executor/ssh.py:88-94`),
so a lost host hangs every later tool call (pre-existing).

## 5. `host:port` host strings

Yes: `derive_shorthand` splits `user@host:port` (`fabric/connection.py:26-46`, `:390-400`) [probed:
`'127.0.0.1:1'` → host `127.0.0.1`, port 1; `localhost:1` tries both families]. The CLI passes
`--ssh-host` straight to `SSHExecutor(ssh_host)`, so `--ssh-host 127.0.0.1:<port>` works. For a
deterministic refusal, bind an ephemeral port and close it before the drive.

## 6. Extending the in-process server

`test_ssh_smoke.py` runs `paramiko.Transport(conn).start_server(_Server)` on `127.0.0.1:0` with
generated RSA keys. `check_channel_exec_request` records the command and replies from a thread after
0.2 s, since Paramiko confirms the exec after the method returns (`:72-84`). The client is isolated:
`HOME=tmp_path`, no agent, `load_ssh_configs=False`, empty `SSHConfig` (`:89-116`).

An executing variant (a separate `integration` module, e.g. `test_ssh_exec.py`, since AGENTS.md
§ Testing pins the smoke test as "no process is launched"): in the reply thread run
`subprocess.Popen([str(SANDBOX), shell, '-c', command], stdin=PIPE, stdout=PIPE, stderr=PIPE,
start_new_session=True)` with `shell` in `('/bin/bash', '/bin/zsh')` (both exist on this machine).
Pump `channel.recv` → `proc.stdin` (close on `b''`), `proc.stdout` → `sendall`, `proc.stderr` →
`sendall_stderr`, then `send_exit_status(proc.wait())` and `close()`. If the client closes first
(timeout), `os.killpg` the group: `bin/sandbox.sh` does not forward SIGTERM (AGENTS.md § Testing,
test_transports row). Rails:
- `sandbox_violations` (`tests/test_hygiene.py:84-142`) accepts only a list/tuple literal whose first
  element is `SANDBOX` or `str(SANDBOX)` (import `SANDBOX` from `harness`) and no literal `sudo`/`ssh`.
- The unit guard refuses `Popen`/`connect`/`getaddrinfo` (`conftest.py:99-122`), so the test is
  `integration`. Do not request `launches` (it replaces `Popen`, `conftest.py:171-181`).
- The PATH shim (`conftest.py:79-96`) sits first on PATH for the child too; harmless unless the
  program calls `ssh`/`sudo`. `base64` resolves from `/usr/bin`.
- `sandbox.sh` gives each launch a fresh `HOME` and cwd that are deleted on exit, so `~` state does
  not persist between two execs; use absolute paths under `tmp_path`. Its `set -e` turns a signalled
  child into `128+n`, so exit-signal cannot be exercised through it.
This lets ssh mode cover byte-exact stdin writes, literal argv, `[ a == a ]` under a zsh login shell,
and the timeout sentinel, all without a real host.

## 7. `base64 -d`

macOS 27 `/usr/bin/base64` accepts `-d`, `-D`, `--decode`; GNU coreutils accepts `-d`/`--decode`
(every RHEL login node). Only the remote side needs it. Portability notes for the bootstrap:
`$(…)` strips trailing newlines from the decoded program (harmless for bash text; do not rely on a
final newline); the base64 alphabet is inert in sh/bash/zsh/tcsh/fish (`=` only trails); the whole
exec string and the decoded program are each one argv element, capped by Linux `MAX_ARG_STRLEN`
(128 KiB), so `run_command` scripts are capped near 96 KiB once encoded. File content on stdin is
not affected.

## Recommendation

1. Replace `conn.run` with the raw channel loop above, keeping `Connection` for config, ssh_config
   and `open()`. This fixes binary stdin, the 1 B/s feed, UTF-8 chunk splitting, stdin theft and
   `CommandTimedOut` at once. Map a `None` status to the timeout sentinel; keep `-1` for
   signal/no-status, and raise `ConnectionError` if `transport.is_active()` is false after the loop.
2. On timeout send a best-effort `signal` TERM request, then `close()`. Confirm `killpg` on a login
   node during the read-only dogfood (`sleep 300`, `timeout=2`, then `pgrep -u $USER sleep`); if
   it does not work, document that a timed-out remote command may outlive the call.
3. R9: catch `Exception` around `SSHExecutor(...)` and format
   `cannot connect to <--ssh-host as typed>: <cause>`, with a per-class cause (`unknown host or SSH
   alias`, `connection refused`, `authentication failed`, `timed out`, else `str(exc)`). Set a default
   `timeouts.connect` (e.g. 15 s) through the Config so ssh_config `ConnectTimeout` still wins. Fix the
   endless `_ensure_connected` loop in the same change. Test with `host:port` for a refused port,
   `*.invalid` for DNS (assert the class and host, not the platform text), and the in-process server
   for auth.
4. Pass `stdin=subprocess.DEVNULL` (or the bytes) in the local and delegate backends too.
5. Add the executing loopback server (§6) as the ssh-mode leg of R2/R4/R7/R8.
