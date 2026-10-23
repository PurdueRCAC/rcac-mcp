# Research 01: anatomy and bounds of one connect attempt

Installed: fabric 3.2.2, paramiko 5.0.0, invoke 2.2.1 (`.venv`, Python 3.14). Paths below are
relative to `site-packages/`. [probed] = confirmed against 127.0.0.1 listeners with 1-4 s bounds,
empty `SSHConfig`, temp `HOME`, no agent.

## 1. Steps of one attempt and what bounds each

`SSHExecutor.open()` builds a **new** `Connection` every time (`executor/ssh.py:446`) and calls
`Connection.open()` -> `SSHClient.connect(**kwargs)` (`fabric/connection.py:665`).

| Step | Bound | Source |
|---|---|---|
| DNS (`getaddrinfo`) | **none** (OS resolver) | `paramiko/client.py:192-201, 355` |
| TCP connect, per address | `timeout`; refused/unreachable move to the next address, any other error raises at once | `client.py:361, 374` |
| Banner, first line | `banner_timeout` (then 2 s per extra pre-banner line, up to 100) | `transport.py:2203-2213` |
| Kex | `handshake_timeout` = 15, **not** set by `_bounded`; on expiry `read_all` raises `EOFError` | `transport.py:533, 2037`; `packet.py:319-320` |
| Banner + kex, caller side | `start_client(timeout=timeout)` polls and **returns silently** at `timeout`; the next line raises `SSHException('No existing session')` | `transport.py:700-710`; `client.py:443-445`; `transport.py:879-880` |
| SSH agent (`Agent()`, key listing) | **none** (blocking UNIX socket, no `settimeout`) | `client.py:707`; `agent.py:111-116, 227-229` |
| Auth | `auth_timeout` **per request**: each `auth_publickey` (explicit key, agent identity, default `~/.ssh` key, then password) waits afresh | `client.py:678, 696, 716, 755`; `auth_handler.py:200-216` |
| `channel_timeout` | not used by a direct connect; it bounds a ProxyJump gateway's `direct-tcpip` channel (default 3600 s) | `transport.py:537, 1016`; `fabric/connection.py:700-705` |

[probed] 3 key files against a server that never answers auth, `auth_timeout=1`: failed at 3.30 s.

**Worst case of one attempt under `_bounded` defaults** (timeout, banner, auth = 15; handshake 15):

- black-holed host: TCP 15 s -> `TimeoutError` (safe-execution research 02 §4).
- accepts TCP, no banner: 15 s (`start_client` returns at 15) -> `'No existing session'` [probed at 2 s].
- banner then silent in kex: 15 s, same error [probed]; the transport thread lives on to
  `handshake_timeout`.
- stalls after kex (auth): 15 s x N auth requests. Total: DNS + 15 + 15 + 15N, plus an
  unbounded agent query.
- **ProxyJump**: the hop `Connection` is built with `config.clone()` (`fabric/connection.py:516`), so
  it inherits `timeouts.connect` = 15 and the banner/auth defaults [probed], plus the hop's own
  ssh_config `ConnectTimeout`. It does **not** inherit a caller `connect_timeout`, and with a caller
  `connect_kwargs={'timeout': ...}` the hop gets **no** TCP bound [probed: `connect_timeout None`].
  The hop is a full attempt of its own. Then `open_channel('direct-tcpip')` has no Fabric timeout ->
  `channel_timeout` 3600 s, so a dead target behind a live jump host waits for the jump host's own
  TCP give-up (minutes on Linux defaults). The target's `timeout` then bounds only `start_client`,
  because `sock` is given and the TCP step is skipped (`client.py:352`).
- **ProxyCommand**: `ProxyCommand.recv` honours `settimeout` (`proxy.py:88-113`), so the banner wait
  is bounded. The subprocess's own connect is bounded only by its own options.

## 2. How the connect bound grows

`Connection.connect_timeout` = caller `connect_timeout`, else ssh_config `connecttimeout`, else
`config.timeouts.connect`, then `int()` (`fabric/connection.py:443-450`). It reaches Paramiko as
`timeout` only if truthy (`:639-640`), so it bounds TCP and `start_client`. `_bounded` sets
`timeouts.connect = 15` only when nothing else does, and always sets banner/auth to 15 unless they
are given (`executor/ssh.py:241-249`). [probed] ssh_config `ConnectTimeout 40` -> `connect_timeout 40`,
banner/auth stay 15; caller `connect_timeout=40` gives the same. A longer bound therefore lengthens
TCP and kex only, not auth. Production passes no kwargs (`__init__.py:241: SSHExecutor(ssh_host)`),
so only ssh_config moves the bound there.

## 3. Per-attempt timeout and watchdog close

- Per attempt: a fresh `Connection` is cheap and already the pattern. Without rebuilding config,
  set `conn.connect_timeout = t` and `conn.connect_kwargs['banner_timeout'/'auth_timeout'] = t`
  after construction and before `open()`; `open()` reads them at call time (`:625-640`). Pitfall:
  `if self.connect_timeout:` treats 0 as no bound, so never pass a remaining time that is <= 0.
  This shrinks each step, not the attempt: auth still multiplies by N, and DNS and the agent stay
  unbounded.
- Watchdog `conn.close()` is a **no-op mid-attempt**: `is_connected` is False until `open()`
  returns (`fabric/connection.py:666, 725`). Use the client's transport instead:
  - `client.get_transport().close()` during kex aborts in about 0.07 s with **`EOFError`** [probed];
    during auth it aborts in about 0.1 s with `SSHException` (the last saved exception) [probed].
  - `client.close()` during auth sets `client._transport = None`, so the next key's attempt raises
    **`AttributeError: 'NoneType' object has no attribute '_log'`** [probed].
  - During DNS or TCP there is no transport yet (`client.py:391`), so there is nothing to close.
- Risks: (a) a failed attempt leaves the transport and its thread alive: after an auth failure
  until the server drops it [probed: active, thread alive]; after a kex timeout until
  `handshake_timeout`. Fabric's `close()` will not reach it (it gates on `is_connected`), so call
  `conn.client.close()` after every failure. (b) A watchdog firing just after `open()` returns kills
  a good connection, and a late success must not be assigned to `self._conn`. Decide under one lock
  and close the loser. (c) Replacing `self._conn` never closes the old Connection, and with
  ProxyJump each attempt builds a new hop `Connection`, so a live old hop leaks a socket and thread.

## 4. Exceptions per failure class

| Class | Raised | `SSHException`/`OSError`? | Retry can fix? |
|---|---|---|---|
| DNS / unknown alias | `socket.gaierror` [probed] | OSError | maybe: offline DNS after sleep raises the same `EAI_NONAME` |
| refused | `NoValidConnectionsError` [probed] | OSError | yes (sshd restarting) |
| TCP timeout | `TimeoutError` (= `socket.timeout`) | OSError | yes |
| no banner / kex timeout | `SSHException('No existing session')` [probed] | yes | yes |
| reset during banner / kex | `SSHException('Error reading SSH protocol banner...')`, `ConnectionResetError` [probed] | yes | yes |
| **clean close (FIN) during kex, or watchdog close in kex** | **`EOFError`** [probed] | **no** | yes |
| auth refused | `AuthenticationException` | yes | no |
| auth timeout / EOF in auth | `AuthenticationException('Authentication timeout.' / '...saw EOF')` (same class!) | yes | yes |
| no auth methods | `SSHException('No authentication methods available')` (`client.py:780`) | yes | no |
| host-key mismatch | unreachable: Fabric loads no known_hosts and uses `AutoAddPolicy` (`connection.py:458`) | - | - |
| other | any `Exception` from the transport thread is re-raised by `start_client` (`transport.py:2150-2153`); bare `Exception` at `client.py:438`; `AttributeError` (watchdog); `ValueError` (kwarg conflict, `connection.py:625-629`) | **no** | no (config) |

Note that `str(exc)` after a multi-key auth failure is the last saved exception, which may be
misleading (probed: `'encountered RSA key, expected OPENSSH key'` after timeouts). The current
`except (SSHException, OSError)` misses `EOFError` and the others: catch `Exception`. If Q4 fails
fast, key it on auth refused (an `AuthenticationException` other than the timeout/EOF messages,
`BadAuthenticationType`, `PasswordRequiredException`) and on "No authentication methods". Do not
fail fast on `gaierror`.

## 5. `is_connected` and state after a failure

`is_connected` is `self.transport.active if self.transport else False` (`fabric/connection.py:581-587`).
`self.transport` is set only after `client.connect` returns (`:666`). After a failed `open()`,
`self._conn` is the new Connection (assigned at `executor/ssh.py:446`, before `open()`), with
`transport None`. Its `client._transport` may still be alive (§3a). Calling `open()` again on a
dropped Connection would reuse the same `SSHClient`, overwrite `_transport` without closing it
(`client.py:391`), and keep a stale cached `_sftp` (`fabric/connection.py:873-885`). The fresh
Connection per attempt avoids this, and the executor uses `client.open_sftp()` directly
(`ssh.py:574, 649, 669`).

## Recommendation

Per-step bounds cannot give a hard total: DNS and the agent have no bound, auth multiplies by N, and
a ProxyJump channel waits up to 3600 s. The simplest mechanism that does is a **deadline with each
attempt run in a worker thread**:

1. `deadline = start + 3 * bound`, where `bound = max(CONNECT_TIMEOUT, conn.connect_timeout or 0)`
   (45 s at the default).
2. Each attempt runs `Connection(...).open()` in a daemon thread. The caller `join`s it for
   `deadline - now`. On expiry, close that attempt's `client.get_transport()` (best effort) and mark
   it abandoned under a lock, so a late success closes itself instead of being assigned. Then fail
   with `cannot connect to <host>: <last cause>`.
3. After any failed attempt, call `conn.client.close()`. Catch `Exception`. Cap each backoff sleep
   at `deadline - now`, and stop when the time left is under about 1 s.

Optionally, also shrink `connect_timeout`, banner and auth to the time left, so abandoned threads
die sooner. Only the caller's `self._conn` assignment touches shared state.

R5 sentence: "If the connection to the cluster is lost, a tool call that needs it keeps trying to
reconnect for at most 45 seconds from the call's start (three times the 15-second connect timeout),
then fails with `cannot connect to <host>: <cause>` and nothing run; if the SSH config sets a longer
`ConnectTimeout` (or the caller passes a longer `connect_timeout`), the limit is three times that
value instead." This holds only with the thread-and-deadline mechanism. Per-step timeouts alone
could only promise "about 45 s, longer if DNS, the SSH agent, a ProxyJump host, or several keys
stall".
