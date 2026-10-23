# R9 — startup failure: what the client sees, and how to serve it

Research brief (read-only), 2026-10-17. Locked stack: mcp 2.3.0, fastmcp 4.1.0, cmdkit 2.7.7.
`SP` = `.venv/lib/python3.14/site-packages`. Experiments ran under the scratchpad
(`se-plan/r9/`) with throwaway responders, a stock `fastmcp.Client` over `StdioTransport`, and
the installed CLI under `bin/sandbox.sh` against `127.0.0.1:<closed port>`. None of it touched
a real host.

## Baseline today (measured)

- `rcac-mcp --ssh-host 127.0.0.1:<closed>` exits **6** (`uncaught_exception`), after about 1 s.
  Its stderr reads `CRITICAL [rcac_mcp] [Errno None] Unable to connect to port N on 127.0.0.1`
  and its stdout is empty. `--ssh-host no-such-host.invalid` gives
  `[Errno 8] nodename nor servname provided, or not known`, which **does not name the host**.
  Cause: `SSHExecutor.__init__` → `open()` (`src/rcac_mcp/executor/ssh.py:56,67-68`) raises
  `NoValidConnectionsError` or `gaierror`, both of which are `OSError`. The error falls through
  `MCPServerApp.exceptions` (`src/rcac_mcp/__init__.py:153-157`) to the `Exception` row.
- Both client eras surface `MCPError('Connection closed')` with code **-32000**. That is the
  SDK-only `CONNECTION_CLOSED` (`SP/mcp_types/jsonrpc.py:87`), and the server's reason is lost.

## 1. What each client era does with an error response

- **Legacy** (`mode='legacy'`): `Client._negotiate` calls `session.initialize()`
  (`SP/fastmcp/client/client.py:876-877`). That runs `send_request`, and a JSON-RPC error is
  raised as `MCPError` (`SP/mcp/client/session.py:540-554,651-662`). `_connect` passes
  `MCPError` through untouched (`client.py:1036-1048`, `_connection_failure` at `:235-248`), and
  `str(MCPError)` is the bare `message` (`SP/mcp/shared/exceptions.py:51`). **Measured:** for
  codes -32603, -32601, -32000 and 1, the client raises `MCPError` whose `str()` is exactly the
  server's message and whose `.code` is the server's code.
- **Modern** (`mode='auto'`, which is what `tests/harness.py:203` builds): `negotiate_auto`
  (`SP/mcp/client/_probe.py:52-96`) sends `server/discover`. **Every `MCPError` falls back to
  `initialize()`, whatever its code** (`_probe.py:71-81`, a "denylist"). The one exception is
  `-32022` (`UNSUPPORTED_PROTOCOL_VERSION`) whose `data.supported` is modern-only and disjoint,
  which is raised; a `-32022` naming a mutual version triggers a re-probe. The discover error is
  **swallowed**. The client surfaces the **fallback `initialize`'s** error (`_probe.py:82-84`
  re-raises any non-`-32022` handshake error). **Measured:** with -32601, -32603, -32000 or 1,
  the server sees `['server/discover', 'initialize']` and the client raises
  `MCPError(<our message>)` with our code. So -32601 and -32603 behave identically.
- **Answer every request, not only the first (measured).** A responder that answers only the
  first request and then exits gives the legacy client our message. The modern client instead
  gets **`'Connection closed'` (-32000)**, because its fallback `initialize` hits EOF.
- **Timeouts.** The discover probe has a 10 s client-side timeout
  (`SP/mcp/client/session.py:68,743-746`). If the SSH connect is slower than that, the probe
  times out (`REQUEST_TIMEOUT` is an `MCPError`) and the client falls back to `initialize`.
  `client_init_timeout` defaults to `None` (`SP/fastmcp/settings.py:181-186`), so that request
  waits, and an answer-every-request responder still delivers the message. Requests the client
  writes before the responder starts sit in the pipe and are answered in order.

## 2. stdio framing: a minimal responder

The wire format is one JSON-RPC message per line, UTF-8 and newline-terminated. The server reads
lines (`SP/mcp/server/stdio.py:187-195`) and writes `model_dump_json() + "\n"` with a flush
(`:203-205`); the client splits on `\n` (`SP/mcp/client/stdio.py:152,175-176`). On exit the
client closes stdin, waits 2 s, then kills the process tree (`client/stdio.py:61,249-259`), so
a responder that stops at EOF exits cleanly.

The responder, which needs only `json` and `sys`:

- Read lines until EOF. Skip blank lines and unparseable ones.
- Answer only objects that have both `method` and `id`; that skips notifications (no `id`) and
  responses (no `method`). Echo the `id` exactly, whether it is an int or a string.
- Write `{"jsonrpc":"2.0","id":<id>,"error":{"code":-32603,"message":<msg>}}\n`, then flush.
- Write nothing else to stdout. The cmdkit logger already writes to stderr; stdout was measured
  empty.

**Code: -32603 `INTERNAL_ERROR`** (`jsonrpc.py:106`). It is standard, it is accurate, and it is
handled the same as any other code. Avoid:

- `-32022`, which `_probe.py` treats as era evidence and acts on.
- `-32000` and `-32001`, which the SDK reserves for "never emitted on the wire" and timeouts
  (`jsonrpc.py:85-91`). A test reading the code could not tell our answer from a dead pipe.
- `-32601` ("method not found"), which is untrue.

## 3. Could FastMCP itself serve the failure? (measured: no, not in the modern era)

A `FastMCP` server with middleware whose `on_message` raises `MCPError(-32603, msg)` for every
non-notification behaved differently in each era, in memory and over stdio:

- **legacy** gets our message.
- **modern** gets `-32022 'connection is serving the 2026-07-28 protocol; the initialize
  handshake is not accepted'`. The low-level runner locks the connection modern on
  `server/discover` before middleware runs, and then rejects the fallback `initialize`
  (`SP/mcp/server/runner.py:771-776`). `negotiate_auto` re-probes once, gets our error again,
  falls back again, and re-raises the second `-32022` (`_probe.py:82-95`).

Letting `server/discover` succeed instead would connect the client "successfully" and fail only
later requests, so it does not meet "the first request receives the error" either. FastMCP is
also heavier: it brings a banner, a lifespan, and the risk of stdout output. **The stand-alone
responder is about 20 lines, has no stack import (it can live in `src/` and be unit-tested
without touching the harness fence), and behaves identically in both eras.**

## 4. CLI hook and exit status

cmdkit `Application.main` (`SP/cmdkit/app.py:94-139`) wraps `run()`. It returns `success` (0)
unless an exception escapes. When one does, the handler is the first `exceptions` entry, in
dict-insertion order, that matches by `isinstance` (`:133-136`). The handler both logs and
returns the status. `exit_status.runtime_error` is **5** (`:31-39`).

Recommended shape:

- In `run()`, wrap only the `SSHExecutor(ssh_host)` construction (`__init__.py:233`). The
  missing-host `ValueError` stays where it is, keeping `test_cli`'s `bad_argument` row.
- On any `Exception` from the constructor, build
  `msg = f'cannot connect to {ssh_host}: {exc or type(exc).__name__}'`. `str(exc)` is empty for
  some Paramiko exceptions, hence the fallback to the type name.
- Then `log.critical(msg)` **first**, so that clients which show server stderr get it at once.
- If `self.transport == 'stdio'`, call `answer_until_eof(sys.stdin, sys.stdout, msg)`.
- Then `raise StartupFailure(msg)`, a `RuntimeError` subclass. Map it **first** in `exceptions`
  to a handler that returns `exit_status.runtime_error` without logging, to avoid a duplicate
  CRITICAL line. Plain `RuntimeError` would log the message twice.
- **http/sse:** no responder, because nothing listens and a client simply gets "connection
  refused". The same CRITICAL line is printed and the CLI exits 5.
- Today's `log.info('SSH connection established …')` stays on the success path.

## 5. Test design

Measured: the responder through `bin/sandbox.sh` keeps the exit status, since `"$@"` returns the
child's status (exit=5 observed), and both eras get the message through the sandbox.

- **(a) Unit, the responder over in-memory streams** (`io.StringIO`): feed a discover request
  with an int id, a notification, an `initialize` with a string id, a client response object, a
  garbage line and a blank line, then EOF. Assert exactly two output lines, the ids echoed,
  code -32603, the exact message, nothing else on stdout, and that the call returns at EOF. Make
  it a pure `startup_refusal_violations(lines_in, lines_out, msg)` check, with planted negatives:
  answers only the first request, answers a notification, wrong id, extra stdout text, wrong
  code.
- **(b) Integration, the raw wire, in a test module with no stack import:**
  `subprocess.run([str(SANDBOX), str(CLI), '--ssh-host', f'127.0.0.1:{port}'], input=<discover
  line + notification + initialize line>, capture_output=True, text=True, timeout=60)`. Assert:
  - `returncode == exit_status.runtime_error`;
  - stderr contains `cannot connect to 127.0.0.1:{port}`;
  - each stdout line is an error response for ids 1 and 2 carrying that same text.

  This is the only place where **"the first request is `server/discover`" and the exit status**
  are both observable. The modern client swallows the discover error, and `StdioTransport` does
  not expose the return code. The hygiene check is satisfied: the argv literal starts with
  `SANDBOX`, and `'--ssh-host'` is neither `ssh` nor `…/ssh` (`tests/test_hygiene.py:45,81`).
  Paramiko does not use the PATH shim.

  To get a closed port, bind `127.0.0.1:0`, read the port, then close the socket. On macOS a
  socket that is bound but not listening does **not** refuse: connect timed out with
  `ETIMEDOUT` (measured).

  An unknown-host row (`no-such-host.invalid`) pins the "names the host" half, but it does a DNS
  lookup. An SSH-config alias inside the sandbox `HOME` avoids the network.
- **(c) Both eras, through a real client:** add a harness verb such as
  `stdio_refusal(argv, era, timeout=60) -> Refused(code, message, stderr)`. Like `stdio_tools`,
  it must refuse any argv whose `argv[0]` is not `SANDBOX`. It builds
  `StdioTransport(..., keep_alive=False, log_file=<tmp Path>)` (the `log_file` captures the
  child's stderr) and an `_EraClient(..., check=False)`. It catches `MCPError` and returns its
  `.code` and `str()`, and raises if the connect succeeds. Test rows `[legacy]` and `[modern]`
  assert the same message, which pins the modern fallback path: the measured first-only
  responder fails that row with `Connection closed`. Add the verb to AGENTS.md's harness verb
  list.
- **Mutants:** plant three cases:
  - answer the first request only (kills the modern row);
  - drop the host from the message (kills (b));
  - return 0 after responding (kills (b)'s returncode).

## Recommendation

The design hypothesis holds as written: on stdio, answer **every** request until EOF, then exit
nonzero. The details to adopt:

- **Code:** -32603 `INTERNAL_ERROR`.
- **Message:** `cannot connect to <host as given>: <cause>`.
- **Where it lives:** a pure, stack-free responder in `src/`.
- **Hook:** wrap only the `SSHExecutor(...)` construction. Log first, respond on stdio, then
  raise a `StartupFailure` that maps to `exit_status.runtime_error` (5) without a second log
  line.
- **http/sse:** log and exit 5.
- **Rejected:** a FastMCP-based failure server, which gives the modern client `-32022` instead
  of the message.
- **Tests:** pin R9 with (a) a unit responder check, (b) a raw-wire sandboxed CLI run
  (`server/discover` first, ids, stderr, exit 5), and (c) a new harness verb run in both eras.
- **Out of scope here:** an optional `connect_timeout` on the startup connect would bound the
  wait for unreachable hosts. With no bound, macOS waits about 75 s on a silent host. This is
  not required for R9.
