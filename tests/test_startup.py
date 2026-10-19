# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""A server whose SSH connection fails at startup says why, to the client, in both protocol eras.

The failure is reported three ways: once on stderr, as `cannot connect to <host>: <cause>`; on stdio,
as a JSON-RPC internal error carrying the same message in answer to every request until the client
closes the stream; and as exit status `exit_status.runtime_error`. Every request is answered because a
2026-07-28 client swallows the error to its `server/discover` probe and retries with `initialize`.

- The responder on its own, over in-memory streams, read by `startup_refusal_violations`, with
  planted negatives it must reject.
- The raw wire, through the installed CLI under `bin/sandbox.sh` against a closed loopback port: a
  `server/discover` request, a notification and an `initialize` request go in, and exactly two error
  lines, the message on stderr and the exit status come out. Only here are the discover answer and
  the exit status both observable.
- A real client in each era (`test_startup_failure_is_answered[legacy|modern]`), each era on its own
  row whatever era the run selected, through the harness verb `stdio_refusal`.
- The cause classes, in-process with the connection stubbed (unit), and the connect bound: the
  defaults reach Fabric and an ssh_config `ConnectTimeout` still wins (unit), and a loopback listener
  that never sends an SSH banner is answered "timed out" within the bound (integration, with the
  bound lowered so the row is fast; Paramiko's own banner wait would answer it only after 15 s).
"""

from __future__ import annotations

import io
import json
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Sequence

import paramiko
import pytest
from cmdkit.app import exit_status
from fabric import Config, Connection
from paramiko.ssh_exception import AuthenticationException, NoValidConnectionsError, SSHException

import rcac_mcp
import rcac_mcp.executor.ssh
from harness import Refused, stdio_refusal
from rcac_mcp import MCPServerApp
from rcac_mcp.startup import answer_until_eof
from test_cli import CLI, SANDBOX

INTERNAL_ERROR = -32603
MESSAGE = 'cannot connect to alias.example: connection refused'
LOOPBACK = '127.0.0.1'

DISCOVER = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {}})
NOTIFICATION = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
INITIALIZE = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': {}})
RAW_WIRE_IN = [DISCOVER, NOTIFICATION, INITIALIZE]

# Every shape the responder must skip or answer, for the in-memory row.
MIXED_IN = [
    DISCOVER,
    NOTIFICATION,
    json.dumps({'jsonrpc': '2.0', 'id': 'two', 'method': 'initialize', 'params': {}}),
    json.dumps({'jsonrpc': '2.0', 'id': 7, 'result': {}}),
    'not json at all',
    '',
    json.dumps({'jsonrpc': '2.0', 'id': 0, 'method': 'tools/list'}),
]


def _requests(lines_in: Sequence[str]) -> list[Any]:
    """The ids of the JSON-RPC requests among `lines_in`, in order."""
    ids = []
    for line in lines_in:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and 'method' in message and 'id' in message:
            ids.append(message['id'])
    return ids


def startup_refusal_violations(lines_in: Sequence[str], lines_out: Sequence[str], message: str) -> list[str]:
    """Every way `lines_out` differs from answering each request in `lines_in` with `message`.

    Each request gets exactly one line, in order: a JSON-RPC 2.0 error with the request's own id,
    code -32603 and exactly `message`. No other line may appear on stdout.
    """
    violations: list[str] = []
    answered: list[Any] = []
    for n, line in enumerate(lines_out, 1):
        try:
            answer = json.loads(line)
        except ValueError:
            answer = None
        error = answer.get('error') if isinstance(answer, dict) else None
        if not isinstance(error, dict) or answer.get('jsonrpc') != '2.0' or 'id' not in answer:
            violations.append(f'stdout line {n} is not an error response: {line[:120]!r}')
            continue
        answered.append(answer['id'])
        if error.get('code') != INTERNAL_ERROR:
            violations.append(f'the answer to {answer["id"]!r} has code {error.get("code")!r}, expected {INTERNAL_ERROR}')
        if error.get('message') != message:
            violations.append(f'the answer to {answer["id"]!r} carries {error.get("message")!r}, not {message!r}')
    expected = _requests(lines_in)
    for request_id in expected:
        if request_id not in answered:
            violations.append(f'request {request_id!r} not answered')
    for request_id in answered:
        if request_id not in expected:
            violations.append(f'answered id {request_id!r}, which no request carried')
    if not violations and answered != expected:
        violations.append(f'answered {answered!r}, expected each of {expected!r} once, in order')
    return violations


def respond(lines_in: Sequence[str], message: str = MESSAGE) -> list[str]:
    stdout = io.StringIO()
    answer_until_eof(io.StringIO(''.join(line + '\n' for line in lines_in)), stdout, message)
    return stdout.getvalue().splitlines()


def error_line(request_id: Any, message: str = MESSAGE, code: int = INTERNAL_ERROR) -> str:
    return json.dumps({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}})


@pytest.mark.unit
def test_responder_answers_every_request(guard) -> None:
    lines_out = respond(MIXED_IN)
    assert lines_out, 'the responder wrote nothing'
    violations = startup_refusal_violations(MIXED_IN, lines_out, MESSAGE)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.unit
def test_responder_reads_nothing_from_a_terminal(guard) -> None:
    stdin, stdout = Terminal(DISCOVER + '\n'), io.StringIO()
    answer_until_eof(stdin, stdout, MESSAGE)
    assert stdin.tell() == 0 and stdout.getvalue() == ''


@pytest.mark.unit
@pytest.mark.parametrize(('lines_out', 'fragment'), [
    pytest.param([error_line(1)], "request 2 not answered", id='first-request-only'),
    pytest.param([error_line(1), error_line(None), error_line(2)], 'which no request carried', id='notification-answered'),
    pytest.param([error_line(1), error_line(3)], 'request 2 not answered', id='wrong-id'),
    pytest.param([MESSAGE, error_line(1), error_line(2)], 'is not an error response', id='extra-stdout'),
    pytest.param([error_line(1), error_line(2, code=-32601)], 'has code -32601', id='wrong-code'),
    pytest.param([error_line(1), error_line(2, message='Connection closed')], "carries 'Connection closed'", id='wrong-message'),
    pytest.param([error_line(2), error_line(1)], 'in order', id='out-of-order'),
])
def test_startup_refusal_check_rejects(lines_out: list[str], fragment: str) -> None:
    violations = startup_refusal_violations(RAW_WIRE_IN, lines_out, MESSAGE)
    assert any(fragment in v for v in violations), violations


def closed_port() -> int:
    """A loopback port nothing listens on: bound once to pick it, then closed."""
    with socket.socket() as sock:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]


def raw_wire_violations(result: subprocess.CompletedProcess[str], host: str, cause: str,
                        lines_in: Sequence[str]) -> list[str]:
    """Every way a CLI run that failed to connect to `host` differs from the reported startup failure.

    It exits `exit_status.runtime_error`; stderr carries one line `cannot connect to <host>: <cause>…`;
    stdout carries exactly the answers to the requests in `lines_in`, each with that same message.
    """
    violations: list[str] = []
    if result.returncode != exit_status.runtime_error:
        violations.append(f'exit status {result.returncode}, expected {exit_status.runtime_error}')
    reported = [line.split('] ', 1)[-1] for line in result.stderr.splitlines() if 'cannot connect' in line]
    if len(reported) != 1:
        violations.append(f'stderr reports the failure {len(reported)} times, expected once: {result.stderr[-400:]!r}')
    if not any(line.startswith(f'cannot connect to {host}: ') for line in reported):
        violations.append(f'stderr does not name the host {host}: {result.stderr[-400:]!r}')
    if not any(cause in line for line in reported):
        violations.append(f'stderr does not name the cause {cause!r}: {result.stderr[-400:]!r}')
    message = reported[0] if reported else ''
    violations.extend(startup_refusal_violations(lines_in, result.stdout.splitlines(), message))
    return violations


@pytest.mark.integration
def test_raw_wire_answers_every_request() -> None:
    host = f'{LOOPBACK}:{closed_port()}'
    result = subprocess.run([str(SANDBOX), str(CLI), '--ssh-host', host],
                            input=''.join(line + '\n' for line in RAW_WIRE_IN),
                            capture_output=True, text=True, timeout=60)
    assert result.stdout, f'nothing on stdout; stderr: {result.stderr[-400:]!r}'
    violations = raw_wire_violations(result, host, 'connection refused', RAW_WIRE_IN)
    assert violations == [], '\n'.join(violations)


def refused_violations(refused: Refused, host: str, cause: str) -> list[str]:
    """Every way the error a client got differs from the startup message for `host` and `cause`."""
    violations: list[str] = []
    if refused.code != INTERNAL_ERROR:
        violations.append(f'the client got code {refused.code}, expected {INTERNAL_ERROR}')
    if not refused.message.startswith(f'cannot connect to {host}: {cause}'):
        violations.append(f'the client was not answered with the startup message: got {refused.message!r}')
    return violations


@pytest.mark.integration
@pytest.mark.parametrize('era', ['legacy', 'modern'])
def test_startup_failure_is_answered(era: str) -> None:
    host = f'{LOOPBACK}:{closed_port()}'
    refused = stdio_refusal([str(SANDBOX), str(CLI), '--ssh-host', host], era)
    violations = refused_violations(refused, host, 'connection refused')
    assert violations == [], '\n'.join(violations)


@pytest.mark.unit
def test_harness_refuses_an_unsandboxed_refusal(guard) -> None:
    # A unit test: were the refusal missing, the unit guard would refuse the launch and record it.
    for argv in ([str(CLI), '--ssh-host', 'h'], ['sh', str(SANDBOX), str(CLI)], []):
        with pytest.raises(ValueError, match='must start with'):
            stdio_refusal(argv, 'legacy')
    assert guard.refused == []


@pytest.mark.unit
def test_refused_check_rejects_a_closed_connection() -> None:
    violations = refused_violations(Refused(-32000, 'Connection closed'), 'h:1', 'connection refused')
    assert any('not answered' in v for v in violations) and any('code -32000' in v for v in violations)


def _banner_timeout() -> SSHException:
    try:
        try:
            raise TimeoutError('timed out')
        except TimeoutError as exc:
            raise SSHException('Error reading SSH protocol banner') from exc
    except SSHException as exc:
        return exc


CAUSES = [
    pytest.param(socket.gaierror(8, 'nodename nor servname provided, or not known'),
                 'unknown host or SSH alias', id='unknown-host'),
    pytest.param(NoValidConnectionsError({(LOOPBACK, 22): ConnectionRefusedError(61, 'Connection refused')}),
                 'connection refused', id='refused'),
    pytest.param(AuthenticationException('Authentication failed.'), 'authentication failed', id='authentication'),
    pytest.param(TimeoutError('timed out'), 'timed out', id='timeout'),
    pytest.param(SSHException('No existing session'), 'timed out', id='negotiation-timeout'),
    pytest.param(_banner_timeout(), 'timed out', id='banner-timeout'),
    pytest.param(SSHException('No authentication methods available'), 'No authentication methods available',
                 id='other'),
]


def run_app(argv: list[str], lines_in: Sequence[str], monkeypatch: pytest.MonkeyPatch,
            caplog: pytest.LogCaptureFixture) -> dict:
    """Run the app in-process with `lines_in` on stdin; return its status, stdout lines and logs."""
    started: list[Any] = []
    monkeypatch.setattr(rcac_mcp, 'create_mcp_server', lambda *a, **k: started.append(a) or pytest.fail('server built'))
    stdin, stdout = io.StringIO(''.join(line + '\n' for line in lines_in)), io.StringIO()
    monkeypatch.setattr('sys.stdin', stdin)
    monkeypatch.setattr('sys.stdout', stdout)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='rcac_mcp'):
        status = MCPServerApp.main(argv)
    return {
        'status': status,
        'stdout': stdout.getvalue().splitlines(),
        'read': stdin.tell(),
        'critical': [r.getMessage() for r in caplog.records if r.name == 'rcac_mcp' and r.levelno >= logging.CRITICAL],
        'started': bool(started),
    }


def startup_failure_violations(observed: dict, host: str, cause: str, lines_in: Sequence[str]) -> list[str]:
    """Every way an in-process run that failed to connect differs from the reported startup failure."""
    violations: list[str] = []
    if observed['status'] != exit_status.runtime_error:
        violations.append(f'exit status {observed["status"]}, expected {exit_status.runtime_error}')
    if observed['started']:
        violations.append('the server was built')
    if len(observed['critical']) != 1:
        violations.append(f'stderr reports the failure {len(observed["critical"])} times, expected once: {observed["critical"]}')
    message = observed['critical'][0] if observed['critical'] else ''
    if not message.startswith(f'cannot connect to {host}: '):
        violations.append(f'the message does not name the host {host}: {message!r}')
    if cause not in message:
        violations.append(f'the message does not name the cause {cause!r}: {message!r}')
    violations.extend(startup_refusal_violations(lines_in, observed['stdout'], message))
    return violations


@pytest.mark.unit
@pytest.mark.parametrize(('exc', 'cause'), CAUSES)
def test_connect_failure_names_host_and_cause(exc: BaseException, cause: str, monkeypatch: pytest.MonkeyPatch,
                                              caplog: pytest.LogCaptureFixture, guard) -> None:
    class Failing:
        def __init__(self, host: str, **kwargs: Any) -> None:
            pass

        def open(self) -> None:
            raise exc

    monkeypatch.setattr(rcac_mcp.executor.ssh, 'Connection', Failing)
    observed = run_app(['--ssh-host', 'alias.example'], RAW_WIRE_IN, monkeypatch, caplog)
    violations = startup_failure_violations(observed, 'alias.example', cause, RAW_WIRE_IN)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_http_startup_failure_exits_without_reading_stdin(monkeypatch: pytest.MonkeyPatch,
                                                          caplog: pytest.LogCaptureFixture, guard) -> None:
    class Refusing:
        def __init__(self, host: str, **kwargs: Any) -> None:
            pass

        def open(self) -> None:
            raise ConnectionRefusedError(61, 'Connection refused')

    monkeypatch.setattr(rcac_mcp.executor.ssh, 'Connection', Refusing)
    observed = run_app(['-t', 'http', '--ssh-host', 'alias.example'], RAW_WIRE_IN, monkeypatch, caplog)
    assert observed['status'] == exit_status.runtime_error, observed
    assert observed['critical'] == ['cannot connect to alias.example: connection refused'], observed
    assert observed['stdout'] == [] and observed['read'] == 0 and not observed['started'], observed
    assert guard.refused == []


class Unopened(Connection):
    """Fabric's real `Connection`, built as the executor builds it, that never opens."""

    made: list[Connection] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        Unopened.made.append(self)

    def open(self) -> None:
        pass


BOUND = rcac_mcp.executor.ssh.CONNECT_TIMEOUT
QUIET_CONFIG = {'load_ssh_configs': False}


def bound_violations(conn: Connection, connect: int, banner: int, auth: int) -> list[str]:
    """Every way the connection's timeouts differ from the expected bound on each step."""
    violations: list[str] = []
    observed = (conn.connect_timeout, conn.connect_kwargs.get('banner_timeout'), conn.connect_kwargs.get('auth_timeout'))
    if observed != (connect, banner, auth):
        violations.append(f'the connect has no bound: connect, banner and auth timeouts are {observed}, '
                          f'expected {(connect, banner, auth)}')
    return violations


@pytest.mark.unit
@pytest.mark.parametrize(('kwargs', 'expected'), [
    pytest.param({}, (BOUND, BOUND, BOUND), id='default'),
    pytest.param({'config': Config(overrides=QUIET_CONFIG, ssh_config=paramiko.SSHConfig.from_text('Host *\n  ConnectTimeout 7\n'))},
                 (7, BOUND, BOUND), id='ssh-config-wins'),
    pytest.param({'connect_timeout': 3}, (3, BOUND, BOUND), id='explicit-wins'),
    pytest.param({'config': Config(overrides=QUIET_CONFIG, ssh_config=paramiko.SSHConfig()),
                  'connect_kwargs': {'look_for_keys': False}}, (BOUND, BOUND, BOUND), id='own-connect-kwargs'),
])
def test_connect_is_bounded(kwargs: dict, expected: tuple[int, int, int], monkeypatch: pytest.MonkeyPatch, guard) -> None:
    Unopened.made = []
    monkeypatch.setattr(rcac_mcp.executor.ssh, 'Connection', Unopened)
    rcac_mcp.executor.ssh.SSHExecutor('alias.example', **kwargs)
    assert len(Unopened.made) == 1
    violations = bound_violations(Unopened.made[0], *expected)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


# The lowered bound for the silent-banner row, and how long the row waits for its answer: well
# under Paramiko's own 15 s banner wait, which answers it only when no bound is set.
SILENT_BOUND = 1
SILENT_DEADLINE = 8.0


@pytest.mark.integration
def test_silent_banner_is_answered_within_the_bound(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                                                    tmp_path: Path) -> None:
    held: list[socket.socket] = []
    listener = socket.socket()
    listener.bind((LOOPBACK, 0))
    listener.listen(1)
    listener.settimeout(30)

    def accept() -> None:
        try:
            held.append(listener.accept()[0])
        except OSError:
            pass

    acceptor = threading.Thread(target=accept, daemon=True)
    acceptor.start()
    host = f'{LOOPBACK}:{listener.getsockname()[1]}'
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('SSH_AUTH_SOCK', raising=False)
    monkeypatch.setattr(rcac_mcp.executor.ssh, 'CONNECT_TIMEOUT', SILENT_BOUND)
    try:
        began = time.monotonic()
        observed = run_app(['--ssh-host', host], [INITIALIZE], monkeypatch, caplog)
        elapsed = time.monotonic() - began
    finally:
        listener.close()
        acceptor.join(timeout=5)
        for sock in held:
            sock.close()
    assert held, 'the listener was never reached'
    violations = startup_failure_violations(observed, host, 'timed out', [INITIALIZE])
    if elapsed >= SILENT_DEADLINE:
        violations.append(f'the connect has no bound: answered after {elapsed:.1f} s, '
                          f'expected within {SILENT_DEADLINE} s of a {SILENT_BOUND} s bound')
    assert violations == [], '\n'.join(violations)
