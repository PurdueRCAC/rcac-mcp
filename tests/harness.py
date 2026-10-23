# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Drive the server through its public MCP surface: the only test module that imports FastMCP, mcp or httpx2.

Every test that calls a tool goes through this module, so a stack move rewrites this file and leaves
the contract tests alone. It relies on these FastMCP 4 and MCP SDK 2 touchpoints:
`FastMCP.http_app()` and its router lifespan; `StreamableHttpTransport(httpx_client_factory=...)`,
whose factory must build an `httpx2.AsyncClient` (the SDK's HTTP client; plain `httpx` is no longer
in the stack); `fastmcp.Client` over a server object or a transport, with its `mode` (`'legacy'`
forces the `initialize` handshake, `'auto'` probes `server/discover`) and its negotiated
`protocol_version` and `initialize_result`; `fastmcp.Client.list_tools()`, `.list_resources()` and
`.instructions`, with the MCP SDK 2 field names `input_schema`, `output_schema` and `mime_type`; the
`Middleware.on_call_tool`, `on_message` and `on_request` hooks that `Rendezvous`, `Raises`,
`MethodLog` and `StateProbe` implement; `MiddlewareContext.fastmcp_context` and its async
`Context.get_state` (read by `StateProbe`); `FastMCP.middleware` (read to find a server's `MethodLog`); FastMCP running middleware
added later inside middleware added earlier (the builders' `extra` relies on it); `mcp.MCPError`;
the import path of `JWTVerifier`, re-exported for the test that checks what `create_jwt_auth()`
returns; and, for servers in another process, `StdioTransport(command, args, env=..., keep_alive=...)`
(whose default `keep_alive=True` leaves the server running after the client exits),
`StreamableHttpTransport(url)` and `SSETransport(url)`; `FastMCP.auth`, the provider a server installed
(read by `installed_auth`). In FastMCP 4 a verifier's 401 reaches the client as an `MCPError` raised by connect-time
negotiation, not as an HTTP status error, so `call_http` reads the status from a response recorder.
`test_hygiene.py` fails on a FastMCP, mcp, mcp_types, httpx, httpx2 or httpcore2 import in any other
test module.

**The protocol era.** `CLUSTER_MCP_TEST_ERA` selects the era every harness client speaks: `legacy`
(the default; the `initialize` handshake) or `modern` (revision 2026-07-28, reached through
`server/discover`). Any other value stops the import, naming the variable. After every connect the
harness checks the era the client negotiated and raises `harness era: ...` on a mismatch, so a
selector that stopped reaching the clients fails loudly instead of running one era twice. A
modern client is built with `mode='auto'` and checked, never pinned to a version: a pinned client
skips `server/discover`. `in_memory`, `http_client` and `call_http` take `era=` to speak one era
whatever the selector says (a contract that must hold in both eras on every run), still checked
after connecting. When `CLUSTER_MCP_ERA_LOG` names a file, every in-process connect in the
selected era appends `era=<selected> version=<negotiated> first=<first request the server saw on
that connection>`, read from the server's `MethodLog`; the phase gates read that log to prove each
pass ran in its era. A connect in an era chosen with `era=` is not logged: it does not test the
selector.

**The served surface.** `served_surface` reads what a client in the selected era is served (every
tool's description and input and output schemas, every resource's name, description and MIME type,
and the instructions) in the shape of `tests/fixtures/served_surface.json`, the snapshot taken from
the server before the stack move, so a test compares the two directly.

**Both eras, observed from both ends.** `negotiate` connects in a given era without the
post-connect check and reports what happened: the negotiated version, whether an `InitializeResult`
exists, the instructions and tool names the client received, and the methods the server's
`MethodLog` saw on that connection. A test then decides whether that is the era it asked for.

**Servers in another process.** `stdio_tools` and `url_tools` list the tools of a real `rcac-mcp`
process and report the era it negotiated (`Listed`); `stdio_refusal` connects to one that cannot
start and returns the error the client got (`Refused`), relying on a JSON-RPC error that answers the
handshake reaching the caller as `MCPError`, with its `code` and its message as `str()`.
`stdio_tools` and `stdio_refusal` launch the server themselves, inside FastMCP, where
`test_hygiene.py`'s static launch check cannot see it, so each refuses any argv whose first element
is not this repository's `bin/sandbox.sh`; each closes the server when its client exits. `url_tools`
reaches a server the test started (under the sandbox, where the static check does see the launch)
and refuses any host but loopback. None of them is fenced: they make no in-process server call, so
no executor of this process can run behind them, and the server they reach is its own process,
contained by the sandbox. None writes the era log: the server's `MethodLog` is in another process.

Delegate mode is reached over an in-process ASGI route: a real bearer token goes through the real
`JWTVerifier` and `AuthExecutorMiddleware`, with no socket and no port. Shared mode and tokenless
calls use the in-memory client.

Two rules keep a test honest:

- **Build inside the loop.** Every server is built inside the coroutine handed to `drive`; the
  builders raise otherwise. `drive` lets `asyncio.run` copy the main context, as production's
  `MCPServerApp.run()` builds and serves in one context, so anything a construction sets in the
  context reaches the request tasks exactly as it would in production.
- **The fence.** `serving`, `in_memory`, `http_client`, `call_http` and `negotiate` raise unless
  `subprocess.Popen` is the unit guard's refuser, the `launches` recorder or the `executes`
  fixture's sandbox-only launcher, so no in-process server call can reach a real `sudo`, in a unit
  or an integration test.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence, TypeVar
from urllib.parse import urlsplit

import httpx2
import jwt
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp import MCPError

from rcac_mcp.executor import launch
from rcac_mcp.executor.base import CommandResult, Executor
from rcac_mcp.middleware import AuthExecutorMiddleware, SharedExecutorMiddleware
from rcac_mcp.server import create_mcp_server
from rcac_mcp.site import SiteContext
from rcac_mcp.token import ALGORITHM, AUDIENCE, ISSUER

__all__ = ['Outcome', 'drive', 'serving', 'http_client', 'in_memory', 'call', 'call_http', 'mint',
           'user_map', 'RecordingExecutor', 'Rendezvous', 'Raises', 'MethodLog', 'delegate_server',
           'shared_server', 'bare_server', 'JWTVerifier', 'ERA', 'ERA_VARIABLE', 'MODERN_VERSION',
           'select_era', 'era_mismatch', 'session_era', 'rejection_status', 'served_tools',
           'served_instructions', 'served_surface', 'protocol_error', 'Negotiated', 'negotiate', 'Listed',
           'stdio_tools', 'url_tools', 'Refused', 'stdio_refusal', 'SANDBOX', 'LOOPBACK', 'installed_auth', 'StateProbe', 'tool_names',
           'launched_script', 'read_resource', 'SITE']

T = TypeVar('T')

BASE_URL = 'http://test'

SANDBOX = Path(__file__).resolve().parents[1] / 'bin' / 'sandbox.sh'
LOOPBACK = ('127.0.0.1', '::1', 'localhost')

ERA_VARIABLE = 'CLUSTER_MCP_TEST_ERA'
ERA_LOG_VARIABLE = 'CLUSTER_MCP_ERA_LOG'
ERAS = ('legacy', 'modern')
MODERN_VERSION = '2026-07-28'


def select_era(environ: Mapping[str, str]) -> str:
    """The era `environ` selects: `legacy` when the variable is unset or empty, else its value.

    Raises `ValueError` naming the variable for any value but `legacy` or `modern`, so a mistyped
    selector stops the run instead of quietly running the default era.
    """
    era = environ.get(ERA_VARIABLE) or 'legacy'
    if era not in ERAS:
        raise ValueError(f'{ERA_VARIABLE}={era!r} is not an era; use one of {", ".join(ERAS)}')
    return era


ERA = select_era(os.environ)


def era_mismatch(era: str, version: str | None, has_initialize_result: bool) -> str | None:
    """Why a connection that negotiated `version` is not in `era`, or None when it is.

    Legacy: a handshake version, reached through `initialize`, so an `InitializeResult` exists.
    Modern: revision 2026-07-28, with no `InitializeResult`.
    """
    if era == 'modern':
        if version != MODERN_VERSION or has_initialize_result:
            return (f'selected modern, negotiated {version} '
                    f'{"with" if has_initialize_result else "without"} an InitializeResult')
        return None
    if version is None or version == MODERN_VERSION or not has_initialize_result:
        return (f'selected legacy, negotiated {version} '
                f'{"with" if has_initialize_result else "without"} an InitializeResult')
    return None


def session_era(client: Client) -> tuple[str | None, bool]:
    """A connected client's negotiated protocol version, and whether it holds an `InitializeResult`."""
    return client.protocol_version, client.initialize_result is not None


class MethodLog(Middleware):
    """Record the method of every message the server receives, in order (`methods`)."""

    def __init__(self) -> None:
        super().__init__()
        self.methods: list[str] = []

    async def on_message(self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]) -> Any:
        self.methods.append(str(context.method))
        return await call_next(context)


def _method_log(server: FastMCP) -> MethodLog:
    """The server's `MethodLog`, attached now if it has none."""
    for middleware in server.middleware:
        if isinstance(middleware, MethodLog):
            return middleware
    log = MethodLog()
    server.add_middleware(log)
    return log


# The method log of the server behind each app `serving` yields, for the clients that reach it.
_APP_LOGS: weakref.WeakKeyDictionary[Any, MethodLog] = weakref.WeakKeyDictionary()


def _era(era: str | None) -> str:
    """`era`, or the selected era when None; anything but `legacy` or `modern` raises."""
    era = ERA if era is None else era
    if era not in ERAS:
        raise ValueError(f'harness era: {era!r} is not an era; use one of {", ".join(ERAS)}')
    return era


class _EraClient(Client):
    """A client in `era` that checks, after connecting, the era it negotiated (unless `check` is off).

    `legacy` forces the `initialize` handshake; `modern` probes `server/discover` (`mode='auto'`),
    which falls back to the handshake silently, hence the check.
    """

    def __init__(self, target: Any, log: MethodLog | None, era: str | None = None, check: bool = True) -> None:
        self.era = _era(era)
        super().__init__(target, mode='legacy' if self.era == 'legacy' else 'auto')
        self._method_log = log
        self._check = check

    async def __aenter__(self) -> _EraClient:
        start = len(self._method_log.methods) if self._method_log is not None else 0
        await super().__aenter__()
        try:
            version, has_initialize_result = session_era(self)
            mismatch = era_mismatch(self.era, version, has_initialize_result)
            if self._check and mismatch is not None:
                raise RuntimeError(f'harness era: {mismatch}')
            if self._method_log is not None and self.era == ERA:
                # The first request the server saw since this connect began. Notifications are
                # skipped: the previous connection's `notifications/initialized` is sent without
                # waiting for an answer and can land after this connect began.
                seen = [m for m in self._method_log.methods[start:] if not m.startswith('notifications/')]
                _log_connect(version, seen[0] if seen else None)
        except BaseException:
            await super().__aexit__(None, None, None)
            raise
        return self


def _log_connect(version: str | None, first: str | None) -> None:
    path = os.environ.get(ERA_LOG_VARIABLE)
    if path:
        with open(path, 'a') as log:
            log.write(f'era={ERA} version={version} first={first}\n')


@dataclass(frozen=True)
class Outcome:
    """What the client saw: an error flag, the joined text content, and the HTTP status on rejection."""

    is_error: bool
    text: str
    status: int | None = None


def drive(coro: Coroutine[Any, Any, T], timeout: float = 30) -> T:
    """Run `coro` to completion in a fresh event loop that copies the main context."""
    return asyncio.run(asyncio.wait_for(coro, timeout))


def _require_fence(verb: str) -> None:
    if not getattr(subprocess.Popen, 'test_fence', False):
        raise RuntimeError(
            f'harness fence: {verb} needs subprocess.Popen to be the unit guard, the launches '
            'recorder or the executes launcher; request `launches` or `executes` (or mark the test unit) '
            'before calling a server')


def _require_running_loop(builder: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            f'{builder}: build servers inside the coroutine handed to harness.drive, '
            'where production builds and serves them') from None


@asynccontextmanager
async def _serving(server: FastMCP) -> AsyncIterator[Any]:
    log = _method_log(server)
    app = server.http_app()
    _APP_LOGS[app] = log
    async with app.router.lifespan_context(app):
        yield app


def serving(server: FastMCP) -> Any:
    """Serve `server`'s default stateful streamable-HTTP app (what `mcp.run` serves) for a block."""
    _require_fence('serving')
    return _serving(server)


def http_client(app: Any, token: str | None, statuses: list[int] | None = None, era: str | None = None) -> Client:
    """A client that reaches `app` over in-process ASGI, presenting `token` as a bearer token.

    Every HTTP status the app answers is appended to `statuses` when one is given. The client
    speaks `era` (default: the selected era).
    """
    _require_fence('http_client')
    return _EraClient(_http_transport(app, token, statuses), _APP_LOGS.get(app), era)


def _http_transport(app: Any, token: str | None, statuses: list[int] | None) -> StreamableHttpTransport:
    async def record(response: httpx2.Response) -> None:
        if statuses is not None:
            statuses.append(response.status_code)

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        kwargs: dict[str, Any] = {} if timeout is None else {'timeout': timeout}
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE_URL,
                                  headers=headers, auth=auth, follow_redirects=True,
                                  event_hooks={'response': [record]}, **kwargs)

    return StreamableHttpTransport(f'{BASE_URL}/mcp', auth=token, httpx_client_factory=factory)


def in_memory(server: FastMCP, era: str | None = None) -> Client:
    """A client wired straight to `server`: no HTTP, so no access token reaches the middleware.

    The client speaks `era` (default: the selected era).
    """
    _require_fence('in_memory')
    return _EraClient(server, _method_log(server), era)


async def call(client: Client, tool: str, args: dict[str, Any]) -> Outcome:
    """Call `tool` on a connected client and normalize what came back."""
    result = await client.call_tool(tool, args, raise_on_error=False)
    return Outcome(result.is_error, ''.join(getattr(part, 'text', '') for part in result.content))


async def read_resource(client: Client, uri: str) -> Outcome:
    """Read `uri` on a connected client: its joined text, or the error's text with `is_error` set."""
    try:
        contents = await client.read_resource(uri)
    except Exception as exc:
        return Outcome(True, str(exc))
    return Outcome(False, ''.join(getattr(part, 'text', '') for part in contents))


async def tool_names(client: Client) -> tuple[str, ...]:
    """List the tools on a connected client: a request that is not a tool call, on the same session."""
    return tuple(tool.name for tool in await client.list_tools())


def rejection_status(statuses: Sequence[int]) -> int | None:
    """401 when the last HTTP answer was a 401, else None: an earlier 401 the client got past is not one."""
    return 401 if statuses and statuses[-1] == 401 else None


async def call_http(app: Any, token: str | None, tool: str, args: dict[str, Any], era: str | None = None) -> Outcome:
    """Connect and call in one verb; a connect refused with a 401 becomes `Outcome(True, '', 401)`.

    The verifier's 401 surfaces while the client connects, as an error from the protocol
    negotiation, not in `call_tool`. It is reported as a rejection only when the connect failed and
    the last answer the app gave was a 401; any other failure is re-raised. The client speaks `era`
    (default: the selected era).
    """
    _require_fence('call_http')
    statuses: list[int] = []
    connected = False
    try:
        async with http_client(app, token, statuses, era) as client:
            connected = True
            return await call(client, tool, args)
    except Exception:
        status = rejection_status(statuses)
        if connected or status is None:
            raise
        return Outcome(True, '', status)


async def _served(server: FastMCP) -> tuple[list[str], str | None]:
    async with in_memory(server) as client:
        return [tool.name for tool in await client.list_tools()], client.instructions


def served_tools(server: FastMCP) -> list[str]:
    """The tool names a client in the selected era lists from `server`, in the order served."""
    return drive(_served(server))[0]


def served_instructions(server: FastMCP) -> str | None:
    """The instructions a client in the selected era receives from `server` when it connects."""
    return drive(_served(server))[1]


async def _surface(server: FastMCP) -> dict[str, Any]:
    async with in_memory(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        instructions = client.instructions
    surface = {
        'instructions': instructions,
        'tools': {tool.name: {'description': tool.description, 'inputSchema': tool.input_schema,
                              'outputSchema': tool.output_schema} for tool in tools},
        'resources': {str(resource.uri): {'name': resource.name, 'description': resource.description,
                                          'mimeType': resource.mime_type} for resource in resources},
    }
    # Through JSON, as the snapshot was written, so the two compare as plain data.
    return json.loads(json.dumps(surface))


def served_surface(server: FastMCP) -> dict[str, Any]:
    """What a client in the selected era is served by `server`, in the snapshot's shape.

    `{'instructions': …, 'tools': {name: {'description', 'inputSchema', 'outputSchema'}},
    'resources': {uri: {'name', 'description', 'mimeType'}}}`, the shape of
    `tests/fixtures/served_surface.json`.
    """
    return drive(_surface(server))


@dataclass(frozen=True)
class Negotiated:
    """One connection, seen from both ends.

    `protocol_version` and `has_initialize_result` are the client's negotiated session;
    `instructions` and `tools` (names, in the order served) what it received; `methods` every method
    the server's `MethodLog` saw while the connection was open, notifications included.
    """

    protocol_version: str | None
    has_initialize_result: bool
    instructions: str | None
    tools: tuple[str, ...]
    methods: tuple[str, ...]


async def negotiate(target: Any, era: str, token: str | None = None) -> Negotiated:
    """Connect to `target` in `era`, list its tools, and report what both ends saw.

    `target` is a server (in memory) or an app `serving` yields (over HTTP, presenting `token`).
    Unlike the other clients, nothing checks the negotiated era here: deciding that is the caller's
    job, from what this returns.
    """
    _require_fence('negotiate')
    if isinstance(target, FastMCP):
        if token is not None:
            raise ValueError('negotiate: a token travels only over HTTP; pass the app `serving` yields')
        log = _method_log(target)
        client = _EraClient(target, log, era, check=False)
    else:
        log = _APP_LOGS.get(target)
        if log is None:
            raise ValueError('negotiate: the app was not yielded by `serving`, so no method log reaches it')
        client = _EraClient(_http_transport(target, token, None), log, era, check=False)
    start = len(log.methods)
    async with client:
        version, has_initialize_result = session_era(client)
        instructions = client.instructions
        tools = tuple(tool.name for tool in await client.list_tools())
    return Negotiated(version, has_initialize_result, instructions, tools, tuple(log.methods[start:]))


@dataclass(frozen=True)
class Listed:
    """What a client listed from a server in another process: the tools, the era and the instructions."""

    tools: tuple[str, ...]
    protocol_version: str | None
    has_initialize_result: bool
    instructions: str | None = None


async def _listed(client: _EraClient) -> Listed:
    async with client:
        version, has_initialize_result = session_era(client)
        return Listed(tuple(tool.name for tool in await client.list_tools()), version, has_initialize_result,
                      client.instructions)


def stdio_tools(argv: Sequence[str], era: str, env: Mapping[str, str] | None = None, timeout: float = 60) -> Listed:
    """Launch `argv` as a stdio server, list its tools in `era`, and close it.

    `argv[0]` must be this repository's `bin/sandbox.sh`: FastMCP starts the process where the
    static launch check cannot see it, so the check is made here, before anything launches. The
    server gets `env` (default: this process's environment) and is stopped when the client exits.
    Not fenced: the server is another process, contained by the sandbox.
    """
    if not argv or Path(argv[0]).resolve() != SANDBOX.resolve():
        raise ValueError(f'stdio_tools: the argv must start with {SANDBOX}, which contains the server; '
                         f'got {list(argv[:1])}')
    transport = StdioTransport(str(argv[0]), [str(a) for a in argv[1:]],
                               env=dict(os.environ if env is None else env), keep_alive=False)
    return drive(_listed(_EraClient(transport, None, era)), timeout)


@dataclass(frozen=True)
class Refused:
    """The error a client got connecting to a server in another process: its code and its text."""

    code: int
    message: str


async def _refusal(client: _EraClient) -> Refused:
    try:
        async with client:
            pass
    except MCPError as exc:
        return Refused(exc.code, str(exc))
    raise RuntimeError('stdio_refusal: the client connected; the server was expected to refuse every request')


def stdio_refusal(argv: Sequence[str], era: str, env: Mapping[str, str] | None = None, timeout: float = 60) -> Refused:
    """Launch `argv` as a stdio server that cannot start, connect in `era`, and return the error.

    The connect must fail with an MCP error, whose code and text are returned; a connect that
    succeeds raises. `argv[0]` must be this repository's `bin/sandbox.sh`, as for `stdio_tools`, and
    the server is stopped when the client exits. Not fenced: the server is another process.
    """
    if not argv or Path(argv[0]).resolve() != SANDBOX.resolve():
        raise ValueError(f'stdio_refusal: the argv must start with {SANDBOX}, which contains the server; '
                         f'got {list(argv[:1])}')
    transport = StdioTransport(str(argv[0]), [str(a) for a in argv[1:]],
                               env=dict(os.environ if env is None else env), keep_alive=False)
    return drive(_refusal(_EraClient(transport, None, era, check=False)), timeout)


def url_tools(url: str, transport: str, era: str, timeout: float = 60) -> Listed:
    """List the tools of the server listening at `url` (`transport` is `http` or `sse`) in `era`.

    Only a loopback host is reached: a test drives a server it started itself, never a real one. Not
    fenced: the server is another process, which the test started under the sandbox.
    """
    host = urlsplit(url).hostname
    if host not in LOOPBACK:
        raise ValueError(f'url_tools: {url} is not a loopback address; a test reaches only a server it started')
    if transport == 'http':
        client_transport: Any = StreamableHttpTransport(url)
    elif transport == 'sse':
        client_transport = SSETransport(url)
    else:
        raise ValueError(f'url_tools: {transport!r} is not a URL transport; use http or sse')
    return drive(_listed(_EraClient(client_transport, None, era)), timeout)


def installed_auth(server: FastMCP) -> Any:
    """The auth provider `server` installed, or None when it serves without one."""
    return server.auth


def protocol_error(message: str) -> MCPError:
    """An `MCPError` (a JSON-RPC internal error) whose text is `message`."""
    return MCPError(code=-32603, message=message)


def mint(secret: str, *, iss: str = ISSUER, aud: str = AUDIENCE, alg: str = ALGORITHM, exp_in: float = 300,
         **claims: Any) -> str:
    """A JWT carrying exactly `claims` plus the registered ones; no implicit `sub`."""
    now = dt.datetime.now(dt.timezone.utc)
    payload = {'iss': iss, 'aud': aud, 'iat': now, 'exp': now + dt.timedelta(seconds=exp_in), **claims}
    return jwt.encode(payload, secret, algorithm=alg)


def user_map(tmp_path: Path, text: str) -> str:
    """Write a user-map file under `tmp_path` and return its path."""
    path = tmp_path / 'user_map'
    path.write_text(text)
    return str(path)


# The launcher's program, as `launch.program` renders it: the prologue, then the start sentinel
# printed in two halves on both streams, then a subshell that changes to the start and any cwd and
# runs the body (an `exec` of the argv, or the script run by a fresh bash), then the status and the
# end record, its nonce in two halves, on both streams.
_SENTINEL_LINE = re.compile(r'printf %s%s ([0-9a-f]{16}) ([0-9a-f]{16}); printf %s%s \1 \2 >&2')
_END_LINE = re.compile(r'printf %s%s%s\. ([0-9a-f]{16}) ([0-9a-f]{16}) "\$cluster_mcp_r"')
_SCRIPT_PREFIX = ('while IFS= read -r cluster_mcp_fn; do export -f -- "$cluster_mcp_fn"; '
                  'done < <(compgen -A function); unset cluster_mcp_fn; BASH_ENV= ENV= exec /bin/bash --norc -c -- ')


def launched_script(argv: Sequence[str]) -> tuple[str | list[str], launch.Marks]:
    """The command a recorded launch runs, and the nonces its program prints (`launch.Marks`).

    `argv` is any launch whose last element is a launcher program (`sudo … /bin/bash … <program>`,
    `/bin/bash … <program>`, or `[<program>]` for one an SSH server decoded). The command is the
    script for a `run_command`-style launch, or the argv words of an argv launch. A last element that
    is not a launcher program is a `ValueError`.
    """
    prog = argv[-1] if argv else ''
    line = _SENTINEL_LINE.search(prog) if isinstance(prog, str) and prog.startswith('\\unalias -a\n') else None
    end = _END_LINE.search(prog) if line else None
    if line is None or end is None:
        raise ValueError(f'not a launcher program: {prog!r:.120}')
    marks = launch.Marks((line[1] + line[2]).encode('ascii'), (end[1] + end[2]).encode('ascii'))
    body = prog[line.end():end.start()]
    if _SCRIPT_PREFIX in body:
        words = shlex.split(body[body.index(_SCRIPT_PREFIX) + len(_SCRIPT_PREFIX):].partition('\n')[0])
        if len(words) != 1:
            raise ValueError(f'the script is not one word: {words!r:.120}')
        return words[0], marks
    # The argv line is the subshell's line starting `exec `; the cd lines before it end `|| exit`.
    at = body.find('\nexec ')
    if at < 0:
        raise ValueError(f'no command in the program: {body!r:.120}')
    return shlex.split(body[at + len('\nexec '):].partition('\n')[0]), marks


@dataclass
class RecordingExecutor:
    """An `Executor` that records every call and answers `<name>|<command>` unless given a responder.

    A call is recorded as given: `('run', command, cwd, timeout)`, with the command a list for an
    argv and a `str` for a script, and `('write', path, data, append, parent, executable)`; `put`
    and `get` return `<name>:<destination>`, so a path a tool returns can be traced to this executor.
    """

    name: str
    responder: Callable[[Any], str] | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    @property
    def hostname(self) -> str:
        return f'{self.name}.invalid'

    def run(self, command: Sequence[str] | str, cwd: str | None = None, timeout: float | None = None) -> CommandResult:
        self.calls.append(('run', command if isinstance(command, str) else list(command), cwd, timeout))
        stdout = self.responder(command) if self.responder else f'{self.name}|{command}'
        return CommandResult(stdout=stdout, stderr='', exit_code=0, hostname=self.hostname)

    def write(self, path: str, data: bytes, append: bool = False, parent: str | None = None,
              executable: bool = False) -> CommandResult:
        self.calls.append(('write', path, data, append, parent, executable))
        return CommandResult(stdout='', stderr='', exit_code=0, hostname=self.hostname)

    def put(self, local_path: str, remote_path: str) -> str:
        self.calls.append(('put', local_path, remote_path))
        return f'{self.name}:{remote_path}'

    def get(self, remote_path: str, local_path: str) -> str:
        self.calls.append(('get', remote_path, local_path))
        return f'{self.name}:{local_path}'

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> RecordingExecutor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class Rendezvous(Middleware):
    """Hold each tool call until `parties` calls are in flight together, then let them all proceed.

    Added after the executor middleware, it sits inside it (FastMCP applies middleware in reverse),
    so every call has set its executor before any call's tool body runs. Without it, sync tools
    never yield and two "concurrent" calls run one after the other, which a module global survives.
    """

    def __init__(self, parties: int, timeout: float = 5.0) -> None:
        super().__init__()
        self.timeout = timeout
        self._barrier = asyncio.Barrier(parties)

    async def on_call_tool(self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]) -> Any:
        try:
            async with asyncio.timeout(self.timeout):
                await self._barrier.wait()
        except TimeoutError:
            raise RuntimeError('rendezvous: calls were never in flight together') from None
        return await call_next(context)


class Raises(Middleware):
    """Raise `exc` on every tool call, as an executor middleware's refusal does."""

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def on_call_tool(self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]) -> Any:
        raise self.exc


class StateProbe(Middleware):
    """Record, for every request, the method and the context state each of `keys` holds as it arrives.

    Added after the executor middleware, it runs inside it like every `extra`: a tool call shows the
    state the executor middleware set for that call, and any other request (a tool listing, which no
    executor middleware sees) shows what an earlier request left behind on the session. A key with
    no value reads as None.
    """

    def __init__(self, *keys: str) -> None:
        super().__init__()
        self.keys = keys
        self.seen: list[tuple[str, dict[str, Any]]] = []

    async def on_request(self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]) -> Any:
        state = context.fastmcp_context
        values = {key: (await state.get_state(key)) if state is not None else None for key in self.keys}
        self.seen.append((context.method or '', values))
        return await call_next(context)


# The site context every builder serves unless given another: none, as on a host without /etc/agents.d.
SITE = SiteContext.missing('this host')


def delegate_server(map_path: str, *extra: Middleware, site: SiteContext = SITE) -> FastMCP:
    """A `jwt` server with `AuthExecutorMiddleware` over `map_path`; the caller sets `JWT_SECRET`.

    `extra` middleware (a `Rendezvous`, say) is added after the executor middleware, so it runs
    inside it; the same holds for the other builders. Each builder adds a `MethodLog` last.
    """
    _require_running_loop('delegate_server')
    return create_mcp_server('jwt', middlewares=[AuthExecutorMiddleware('jwt', map_path), *extra, MethodLog()], site=site)


def shared_server(executor: Executor, *extra: Middleware, site: SiteContext = SITE) -> FastMCP:
    """An unauthenticated server whose `SharedExecutorMiddleware` installs `executor` per call."""
    _require_running_loop('shared_server')
    return create_mcp_server('none', middlewares=[SharedExecutorMiddleware(executor), *extra, MethodLog()], site=site)


def bare_server(*extra: Middleware, site: SiteContext = SITE) -> FastMCP:
    """An unauthenticated server with no executor middleware at all."""
    _require_running_loop('bare_server')
    return create_mcp_server('none', middlewares=[*extra, MethodLog()], site=site)
