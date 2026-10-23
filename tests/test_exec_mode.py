# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The server app resolves its execution mode by the posture ladder, and installs the middleware
that mode needs.

The ladder (§ Invariants §4) decides who runs a command and where, so its order is the contract:
an explicit `-e/--exec-mode` wins; `stdio` means `ssh`; an SSH host from `--ssh-host` or
`RCAC_SSH_HOST` means `ssh`; `auth == 'none'` means `local`; anything else means `delegate`. In
particular a configured auth mode with no SSH host and no `-e` is `delegate`, never `local`, which
runs as the server's owner with no authentication. `delegate` installs `AuthExecutorMiddleware`,
every other mode `SharedExecutorMiddleware`, and the `ssh` rung with no host fails closed with
`exit_status.bad_argument` before any server is built. An explicit `-e` is decided at rung 1 whatever
every later-rung input says: the rows cross each `-e` value with each such input, and
`test_explicit_mode_decides_alone` crosses it with every value of all of them.

Each row drives `MCPServerApp.main(argv)` in-process (never `rcac_mcp.main`, which falls back to
pytest's own argv on an empty list) and observes the exit status, the `Execution mode: …` record
the app logs on the `rcac_mcp` logger, the middleware classes the server would be built with, the
hosts an SSH connection was made for, and whether the server was started. No server, port or SSH
connection is ever made. That leans on three `rcac_mcp` seams, not FastMCP's: the module-level
name `rcac_mcp.create_mcp_server` is replaced by a stub that records its `middlewares` and whose
`run` returns at once; `rcac_mcp.executor.ssh.Connection` (bound to Fabric) is replaced by a fake
that records its host and reaches nothing; and the log text `Execution mode: <mode>` is read from
`caplog`. All three survive a FastMCP upgrade unless it moves server construction out of
`MCPServerApp.run`, so the `stack-upgrade` seed rewrites this stub only if it does.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

import rcac_mcp
import rcac_mcp.executor.ssh
from rcac_mcp import MCPServerApp
from test_executors import FakeConnection

# Unresolvable by design: even without the fake, no name here reaches a real cluster.
FLAG_HOST = 'flag-host.invalid'
ENV_HOST = 'env-host.invalid'

AUTH = 'AuthExecutorMiddleware'
SHARED = 'SharedExecutorMiddleware'
SSH_HOST_REQUIRED = 'SSH host required'


def row(id: str, argv: list[str], *, mode: str, middleware: str | None = None, ssh_host: str | None = None,
        env_host: str | None = None, status: int = 0, reason: str | None = None) -> Any:
    expected = {'status': status, 'mode': mode, 'middleware': [middleware] if middleware else None,
                'ssh_host': ssh_host, 'started': status == 0}
    if reason:
        expected['reason'] = reason
    return pytest.param(argv, env_host, expected, id=id)


# Rung 1 against every later rung: each value an explicit `-e` takes (`local`, `ssh`, `delegate`)
# meets each input a later rung reads (a `stdio` transport, `--ssh-host`, `RCAC_SSH_HOST`, and the
# auth modes `none`, `jwt` and `oidc`) in a row of its own, and resolves to itself. A rung 1 that
# yields to any one of those inputs, for any one value, turns at least one row red; an `-e ssh`
# with no host fails closed instead of falling through. `-e delegate` with auth `none` builds the
# server with the auth middleware, which then refuses every call (`test_no_executor.py`).
# `test_explicit_mode_decides_alone` below crosses every value of every input at once.
EXPLICIT_CASES = [
    row('explicit-local-over-stdio', ['-t', 'stdio', '-e', 'local'], mode='local', middleware=SHARED),
    row('explicit-local-over-host', ['-t', 'http', '-e', 'local', '--ssh-host', FLAG_HOST],
        mode='local', middleware=SHARED),
    row('explicit-local-over-env-host', ['-t', 'http', '-e', 'local'], env_host=ENV_HOST,
        mode='local', middleware=SHARED),
    row('explicit-local-over-auth-none', ['-t', 'http', '-a', 'none', '-e', 'local'], mode='local', middleware=SHARED),
    row('explicit-local-over-auth', ['-t', 'http', '-a', 'jwt', '-e', 'local'], mode='local', middleware=SHARED),
    row('explicit-local-over-oidc', ['-t', 'sse', '-a', 'oidc', '-e', 'local'], mode='local', middleware=SHARED),
    row('explicit-ssh-over-stdio', ['-t', 'stdio', '-e', 'ssh'], mode='ssh', status=2, reason=SSH_HOST_REQUIRED),
    row('explicit-ssh-over-host', ['-t', 'http', '-a', 'jwt', '-e', 'ssh', '--ssh-host', FLAG_HOST],
        mode='ssh', middleware=SHARED, ssh_host=FLAG_HOST),
    row('explicit-ssh-over-env-host', ['-t', 'http', '-a', 'jwt', '-e', 'ssh'], env_host=ENV_HOST,
        mode='ssh', middleware=SHARED, ssh_host=ENV_HOST),
    row('explicit-ssh-over-auth-none', ['-t', 'http', '-e', 'ssh'], mode='ssh', status=2, reason=SSH_HOST_REQUIRED),
    row('explicit-ssh-over-auth', ['-t', 'http', '-a', 'jwt', '-e', 'ssh'],
        mode='ssh', status=2, reason=SSH_HOST_REQUIRED),
    row('explicit-ssh-over-oidc', ['-t', 'sse', '-a', 'oidc', '-e', 'ssh'],
        mode='ssh', status=2, reason=SSH_HOST_REQUIRED),
    row('explicit-delegate-over-stdio', ['-t', 'stdio', '-a', 'jwt', '-e', 'delegate'],
        mode='delegate', middleware=AUTH),
    row('explicit-delegate-over-host', ['-t', 'http', '-a', 'jwt', '-e', 'delegate', '--ssh-host', FLAG_HOST],
        mode='delegate', middleware=AUTH),
    row('explicit-delegate-over-env-host', ['-t', 'http', '-a', 'jwt', '-e', 'delegate'], env_host=ENV_HOST,
        mode='delegate', middleware=AUTH),
    row('explicit-delegate-over-auth-none', ['-t', 'http', '-e', 'delegate'], mode='delegate', middleware=AUTH),
    row('explicit-delegate-over-auth', ['-t', 'http', '-a', 'jwt', '-e', 'delegate'], mode='delegate', middleware=AUTH),
    row('explicit-delegate-over-oidc', ['-t', 'sse', '-a', 'oidc', '-e', 'delegate'], mode='delegate', middleware=AUTH),
]

# Below rung 1, one row per pair of adjacent rungs it separates; every row would resolve
# differently if the rung it names were moved, dropped or widened.
LADDER_CASES = EXPLICIT_CASES + [
    row('stdio-over-auth',['-t', 'stdio', '-a', 'jwt'], mode='ssh', status=2, reason=SSH_HOST_REQUIRED),
    row('stdio-default', ['-t', 'stdio'], mode='ssh', status=2, reason=SSH_HOST_REQUIRED),
    row('host-flag-over-auth-none', ['-t', 'http', '--ssh-host', FLAG_HOST],
        mode='ssh', middleware=SHARED, ssh_host=FLAG_HOST),
    row('env-host-over-auth-none', ['-t', 'sse'], env_host=ENV_HOST, mode='ssh', middleware=SHARED, ssh_host=ENV_HOST),
    row('host-over-jwt', ['-t', 'http', '-a', 'jwt', '--ssh-host', FLAG_HOST],
        mode='ssh', middleware=SHARED, ssh_host=FLAG_HOST),
    row('auth-none-http', ['-t', 'http'], mode='local', middleware=SHARED),
    row('auth-none-sse', ['-t', 'sse'], mode='local', middleware=SHARED),
    row('jwt-without-host', ['-t', 'http', '-a', 'jwt'], mode='delegate', middleware=AUTH),
    row('oidc-without-host', ['-t', 'sse', '-a', 'oidc'], mode='delegate', middleware=AUTH),
]


def ladder_violations(observed: dict, expected: dict) -> list[str]:
    """Return every way a resolution differs from its row; empty means the row held.

    `observed` holds `status` (the app's exit status), `mode` (from the `Execution mode:` record,
    or None when there was none), `middleware` (the class names the server was built with, or None
    when it was never built), `ssh_hosts` (every host an SSH connection was made for) and
    `started` (whether the server was run), plus, optionally, `errors` (the messages the app
    logged as critical). `expected` holds `status`, `mode`, `middleware`, `ssh_host` (the one host
    to connect to, or None for no connection at all) and `started`, plus, optionally, `reason`, a
    fragment the refusal must carry, so a row that should fail closed cannot pass by failing for
    another reason.
    """
    violations: list[str] = []
    if observed['mode'] is None:
        violations.append(f"mode: no Execution mode record, expected {expected['mode']}")
    elif observed['mode'] != expected['mode']:
        violations.append(f"mode: resolved {observed['mode']}, expected {expected['mode']}")
    if observed['middleware'] != expected['middleware']:
        violations.append(f"middleware: {observed['middleware']!r}, expected {expected['middleware']!r}")
    want_hosts = [expected['ssh_host']] if expected['ssh_host'] else []
    if list(observed['ssh_hosts']) != want_hosts:
        violations.append(f"ssh host: connected to {list(observed['ssh_hosts'])!r}, expected {want_hosts!r}")
    if observed['status'] != expected['status']:
        violations.append(f"status: exited {observed['status']}, expected {expected['status']}")
    elif expected.get('reason') and not any(expected['reason'] in e for e in observed.get('errors', [])):
        violations.append(f"status: refused without {expected['reason']!r}: {observed.get('errors', [])!r}")
    if observed['started'] != expected['started']:
        violations.append(f"started: server {'was' if observed['started'] else 'was not'} started, "
                          f"expected {'started' if expected['started'] else 'never started'}")
    return violations


class StubServer:
    """What the stubbed `create_mcp_server` returns: `run` records its arguments and returns."""

    def __init__(self, record: dict) -> None:
        self.record = record

    def run(self, **kwargs: Any) -> None:
        self.record['run'] = kwargs


def resolve(argv: list[str], env_host: str | None, monkeypatch: pytest.MonkeyPatch,
            caplog: pytest.LogCaptureFixture, tmp_path: Path) -> dict:
    """Drive the app once with server construction stubbed and SSH faked; return what it did."""
    built: list[dict] = []

    def create_mcp_server(auth_mode: str | None = None, middlewares: list | None = None, **kwargs: Any) -> StubServer:
        record = {'auth': auth_mode, 'middlewares': list(middlewares or []), 'site': kwargs.get('site')}
        built.append(record)
        return StubServer(record)

    made: list[FakeConnection] = []
    monkeypatch.setattr(rcac_mcp, 'create_mcp_server', create_mcp_server)
    monkeypatch.setattr(rcac_mcp.executor.ssh, 'Connection', type('FakeConnection', (FakeConnection,), {'made': made}))
    user_map = tmp_path / 'user-map'
    user_map.write_text('alice ualice\n')
    monkeypatch.setenv('RCAC_USER_MAP', str(user_map))
    if env_host:
        monkeypatch.setenv('RCAC_SSH_HOST', env_host)
    else:
        monkeypatch.delenv('RCAC_SSH_HOST', raising=False)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger='rcac_mcp'):
        status = MCPServerApp.main(argv)
    records = [r for r in caplog.records if r.name == 'rcac_mcp']
    modes = [r.getMessage().removeprefix('Execution mode: ') for r in records
             if r.getMessage().startswith('Execution mode: ')]
    assert len(built) <= 1, f'the server was built {len(built)} times'
    return {
        'status': status,
        'mode': modes[-1] if modes else None,
        'middleware': [type(m).__name__ for m in built[0]['middlewares']] if built else None,
        'ssh_hosts': [c.host for c in made],
        'started': bool(built) and 'run' in built[0],
        'errors': [r.getMessage() for r in records if r.levelno >= logging.CRITICAL],
    }


@pytest.mark.unit
@pytest.mark.parametrize(('argv', 'env_host', 'expected'), LADDER_CASES)
def test_exec_mode_ladder(argv: list[str], env_host: str | None, expected: dict, monkeypatch: pytest.MonkeyPatch,
                          caplog: pytest.LogCaptureFixture, tmp_path: Path, guard) -> None:
    observed = resolve(argv, env_host, monkeypatch, caplog, tmp_path)
    violations = ladder_violations(observed, expected)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


# Every value of every input the later rungs read. The table is the whole input space of
# `_resolve_exec_mode` below rung 1, so an explicit `-e` that yields to any of it is seen here.
TRANSPORTS = ('stdio', 'sse', 'http')
AUTHS = ('none', 'jwt', 'oidc')
FLAG_HOSTS = (None, FLAG_HOST)
ENV_HOSTS = (None, ENV_HOST)


def explicit_expectation(mode: str, flag_host: str | None, env_host: str | None) -> dict:
    """What an explicit `-e <mode>` resolves to, whatever the later rungs would say."""
    if mode == 'local':
        return {'status': 0, 'mode': 'local', 'middleware': [SHARED], 'ssh_host': None, 'started': True}
    if mode == 'delegate':
        return {'status': 0, 'mode': 'delegate', 'middleware': [AUTH], 'ssh_host': None, 'started': True}
    host = flag_host or env_host
    if host:
        return {'status': 0, 'mode': 'ssh', 'middleware': [SHARED], 'ssh_host': host, 'started': True}
    return {'status': 2, 'mode': 'ssh', 'middleware': None, 'ssh_host': None, 'started': False,
            'reason': SSH_HOST_REQUIRED}


@pytest.mark.unit
@pytest.mark.parametrize('mode', ['local', 'ssh', 'delegate'])
def test_explicit_mode_decides_alone(mode: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                                     tmp_path: Path, guard) -> None:
    violations: list[str] = []
    combinations = 0
    for transport in TRANSPORTS:
        for auth in AUTHS:
            for flag_host in FLAG_HOSTS:
                for env_host in ENV_HOSTS:
                    argv = ['-t', transport, '-a', auth, '-e', mode] + (['--ssh-host', flag_host] if flag_host else [])
                    observed = resolve(argv, env_host, monkeypatch, caplog, tmp_path)
                    expected = explicit_expectation(mode, flag_host, env_host)
                    label = ' '.join(argv) + (' with RCAC_SSH_HOST' if env_host else '')
                    violations += [f'{label}: {v}' for v in ladder_violations(observed, expected)]
                    combinations += 1
    assert combinations == len(TRANSPORTS) * len(AUTHS) * len(FLAG_HOSTS) * len(ENV_HOSTS) == 36
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


DELEGATE ={'status': 0, 'mode': 'delegate', 'middleware': [AUTH], 'ssh_hosts': [], 'started': True}
DELEGATE_ROW = {'status': 0, 'mode': 'delegate', 'middleware': [AUTH], 'ssh_host': None, 'started': True}
FAILED_CLOSED = {'status': 2, 'mode': 'ssh', 'middleware': None, 'ssh_hosts': [], 'started': False,
                 'errors': [f'{SSH_HOST_REQUIRED}: use --ssh-host or set RCAC_SSH_HOST']}
FAILED_CLOSED_ROW = {'status': 2, 'mode': 'ssh', 'middleware': None, 'ssh_host': None, 'started': False,
                     'reason': SSH_HOST_REQUIRED}


@pytest.mark.unit
@pytest.mark.parametrize(('observed', 'expected', 'fragment'), [
    pytest.param({**DELEGATE, 'mode': 'local', 'middleware': [SHARED]}, DELEGATE_ROW, 'mode:', id='wrong-mode'),
    pytest.param({**DELEGATE, 'middleware': [SHARED]}, DELEGATE_ROW, 'middleware:', id='wrong-middleware'),
    pytest.param({**DELEGATE, 'middleware': [AUTH, SHARED]}, DELEGATE_ROW, 'middleware:', id='extra-middleware'),
    pytest.param({**DELEGATE, 'ssh_hosts': [FLAG_HOST]}, DELEGATE_ROW, 'ssh host:', id='connected-in-delegate'),
    pytest.param({**FAILED_CLOSED, 'started': True}, FAILED_CLOSED_ROW, 'started:', id='started-when-failing-closed'),
])
def test_ladder_check_catches_drift(observed: dict, expected: dict, fragment: str) -> None:
    for clean, row_ in ((DELEGATE, DELEGATE_ROW), (FAILED_CLOSED, FAILED_CLOSED_ROW)):
        assert ladder_violations(clean, row_) == [], ladder_violations(clean, row_)
    violations = ladder_violations(observed, expected)
    assert any(v.startswith(fragment) for v in violations), violations
