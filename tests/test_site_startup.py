# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Startup reads /etc/agents.d once, where the mode says, within its bound, and refuses a budget too small."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import rcac_mcp
import rcac_mcp.executor.ssh
from rcac_mcp import MCPServerApp, site
from rcac_mcp.server import SERVER_INSTRUCTIONS
from test_cli import CLI, SANDBOX
from test_executors import FakeConnection

SENTINEL = site.SiteContext(files=(site.SiteFile('z.md', 'Sentinel rules.'),))


def start(argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
          reads: list[tuple[str, Any]], env: dict[str, str] | None = None, hang: float = 0.0) -> dict:
    """Drive `MCPServerApp.main(argv)` with the server stubbed and both readers recording."""
    built: list[dict] = []

    class Stub:
        def run(self, **kwargs: Any) -> None:
            built[-1]['run'] = kwargs

    def create(auth_mode: str | None = None, middlewares: list | None = None, **kwargs: Any) -> Stub:
        built.append({'site': kwargs.get('site'), 'budget': kwargs.get('budget')})
        site.compose(SERVER_INSTRUCTIONS, kwargs['site'], kwargs['budget'])
        return Stub()

    def local(*args: Any, **kwargs: Any) -> site.SiteContext:
        reads.append(('local', None))
        time.sleep(hang)
        return SENTINEL

    def remote(executor: Any, *args: Any, **kwargs: Any) -> site.SiteContext:
        reads.append(('remote', executor))
        time.sleep(hang)
        return SENTINEL

    monkeypatch.setattr(rcac_mcp, 'create_mcp_server', create)
    monkeypatch.setattr(site, 'read_local', local)
    monkeypatch.setattr(site, 'read_remote', remote)
    monkeypatch.setattr(rcac_mcp.executor.ssh, 'Connection', type('Fake', (FakeConnection,), {'made': []}))
    (tmp_path / 'map').write_text('alice ualice\n')
    monkeypatch.setenv('RCAC_USER_MAP', str(tmp_path / 'map'))
    monkeypatch.delenv('RCAC_INSTRUCTIONS_BUDGET', raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    began = time.monotonic()
    with caplog.at_level(logging.INFO, logger='rcac_mcp'):
        status = MCPServerApp.main(argv)
    return {'status': status, 'built': built, 'seconds': time.monotonic() - began,
            'errors': [r.getMessage() for r in caplog.records if r.levelno >= logging.CRITICAL]}


@pytest.mark.unit
@pytest.mark.parametrize(('argv', 'where'), [
    pytest.param(['-t', 'http', '--ssh-host', 'cluster.invalid'], 'remote', id='ssh'),
    pytest.param(['-t', 'http', '-e', 'local'], 'local', id='local'),
    pytest.param(['-t', 'http', '-a', 'jwt'], 'local', id='delegate'),
])
def test_startup_reads_once_where_the_mode_says(argv: list[str], where: str, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path, caplog: pytest.LogCaptureFixture, guard) -> None:
    reads: list[tuple[str, Any]] = []
    if '-a' in argv:
        monkeypatch.setenv('JWT_SECRET', 'startup-test-secret-0123456789abcdef0123')
    observed = start(argv, monkeypatch, tmp_path, caplog, reads)
    assert observed['status'] == 0, observed['errors']
    assert [kind for kind, _ in reads] == [where], reads
    if where == 'remote':
        assert type(reads[0][1]).__name__ == 'SSHExecutor'
    assert [b['site'] for b in observed['built']] == [SENTINEL]
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize(('argv', 'budget'), [
    pytest.param(['-t', 'http', '-e', 'local'], None, id='local'),
    pytest.param(['-t', 'http', '--ssh-host', 'cluster.invalid'], None, id='ssh'),
    pytest.param(['-t', 'http', '-e', 'local'], 'minimum', id='local-at-the-good-minimum'),
])
def test_a_hung_read_does_not_hold_startup(argv: list[str], budget: str | None, monkeypatch: pytest.MonkeyPatch,
                                           tmp_path: Path, caplog: pytest.LogCaptureFixture, guard) -> None:
    monkeypatch.setattr(site, 'READ_TIMEOUT', 0.5)
    reads: list[tuple[str, Any]] = []
    if budget:
        argv = [*argv, '--instructions-budget', str(site.minimum_budget(SERVER_INSTRUCTIONS, SENTINEL))]
    observed = start(argv, monkeypatch, tmp_path, caplog, reads, hang=5.0)
    assert observed['status'] == 0 and observed['seconds'] < 1.0, observed
    [built] = observed['built']
    assert built['site'].failed and 'did not finish within 0.5 s' in built['site'].reason


@pytest.mark.unit
@pytest.mark.parametrize(('flags', 'env', 'budget', 'status', 'reason'), [
    pytest.param([], {}, site.DEFAULT_BUDGET, 0, None, id='default'),
    pytest.param(['--instructions-budget', '9000'], {}, 9000, 0, None, id='flag'),
    pytest.param([], {'RCAC_INSTRUCTIONS_BUDGET': '7000'}, 7000, 0, None, id='env'),
    pytest.param(['--instructions-budget', '9000'], {'RCAC_INSTRUCTIONS_BUDGET': '7000'}, 9000, 0, None, id='flag-wins'),
    pytest.param([], {'RCAC_INSTRUCTIONS_BUDGET': 'lots'}, None, 2, 'whole number', id='env-not-a-number'),
    pytest.param(['--instructions-budget', '0'], {}, None, 2, 'over 0', id='zero'),
    pytest.param(['--instructions-budget', '50'], {}, None, 2, 'at least', id='too-small'),
])
def test_budget_resolution(flags: list[str], env: dict, budget: int | None, status: int, reason: str | None,
                           monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
                           guard) -> None:
    reads: list[tuple[str, Any]] = []
    observed = start(['-t', 'http', '-e', 'local', *flags], monkeypatch, tmp_path, caplog, reads, env=env)
    assert observed['status'] == status, observed['errors']
    if status == 0:
        assert [b['budget'] for b in observed['built']] == [budget]
    else:
        assert any(reason in e for e in observed['errors']), observed['errors']
    if reason == 'at least':
        minimum = site.minimum_budget(SERVER_INSTRUCTIONS, SENTINEL)
        assert any(f'at least {minimum}' in e for e in observed['errors']), observed['errors']


@pytest.mark.integration
def test_cli_refuses_a_budget_too_small(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != 'RCAC_INSTRUCTIONS_BUDGET'}
    done = subprocess.run([str(SANDBOX), str(CLI), '-e', 'local', '-t', 'http', '-p', '9', '--instructions-budget', '100'],
                          capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
    minimum = site.minimum_budget(SERVER_INSTRUCTIONS, site.read_local('this host'))
    assert done.returncode == 2, done
    assert f'instructions budget 100 is too small' in done.stderr and f'at least {minimum}' in done.stderr, done.stderr
