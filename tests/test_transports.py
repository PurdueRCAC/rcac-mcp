# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Every supported transport starts under `bin/sandbox.sh` and a client lists the tools from it.

Each row starts the installed `rcac-mcp -e local` on one transport inside the sandbox, lists its
tools from a client in one protocol era, and stops it. stdio and http serve both eras; sse serves
only the handshake era (revision 2026-07-28 deprecates it), so it has a legacy row only. HTTP and SSE
servers bind loopback on a free port and are stopped by process group: `bin/sandbox.sh` does not
forward SIGTERM to its child, so stopping the sandbox's shell alone leaves the server listening.
After every row the server is gone: its port refuses connections and its sandbox directory, created
under the test's own `TMPDIR`, has been removed.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Sequence

import pytest

from harness import Listed, era_mismatch, stdio_tools, url_tools
from test_cli import CLI, SANDBOX
from test_registry import EXPECTED_TOOLS
from rcac_mcp.server import SERVER_INSTRUCTIONS
from rcac_mcp.site import compose, read_local

# What a server started here serves: the base and this host's own /etc/agents.d, at the default budget.
HOST_INSTRUCTIONS = compose(SERVER_INSTRUCTIONS, read_local('this host'))

HOST = '127.0.0.1'
START_TIMEOUT = 30.0
STOP_TIMEOUT = 10.0
PATHS = {'http': '/mcp', 'sse': '/sse'}


def transport_violations(listed: Listed, transport: str, era: str) -> list[str]:
    """Every way a listing from a `transport` server differs from `EXPECTED_TOOLS` served in `era`."""
    violations: list[str] = []
    tools = set(listed.tools)
    if len(tools) != len(listed.tools):
        violations.append(f'{transport}: a tool is listed twice: {sorted(listed.tools)}')
    missing, unexpected = sorted(EXPECTED_TOOLS - tools), sorted(tools - EXPECTED_TOOLS)
    if missing or unexpected:
        violations.append(f'{transport}: tools differ from EXPECTED_TOOLS: missing {missing}, unexpected {unexpected}')
    if listed.instructions is not None and listed.instructions != HOST_INSTRUCTIONS:
        violations.append(f'{transport}: instructions differ from the composed text: {listed.instructions!r:.120}')
    mismatch = era_mismatch(era, listed.protocol_version, listed.has_initialize_result)
    if mismatch is not None:
        violations.append(f'{transport}: {mismatch}')
    return violations


def free_port() -> int:
    """A loopback port nothing was listening on a moment ago."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def listening(port: int) -> bool:
    try:
        socket.create_connection((HOST, port), timeout=0.5).close()
    except OSError:
        return False
    return True


def sandbox_dirs(tmp: Path) -> list[Path]:
    """The sandbox directories `bin/sandbox.sh` made under `tmp` (its `TMPDIR`) and has not removed."""
    return sorted(tmp.glob('cluster-mcp-sandbox.*'))


def stop(server: subprocess.Popen[bytes]) -> None:
    """Stop the sandbox and the server in it, by process group; force it if TERM is not enough."""
    try:
        os.killpg(server.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        server.wait(STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(server.pid, signal.SIGKILL)
        server.wait(STOP_TIMEOUT)


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def serve_and_list(transport: str, era: str, tmp_path: Path) -> tuple[Listed, list[str]]:
    """Start `rcac-mcp -e local -t <transport>` under the sandbox, list its tools in `era`, stop it.

    Returns the listing and every way the stop left something behind.
    """
    port = free_port()
    log = tmp_path / f'{transport}.log'
    env = {**os.environ, 'TMPDIR': str(tmp_path)}
    with log.open('wb') as stderr:
        server = subprocess.Popen(
            [str(SANDBOX), str(CLI), '-e', 'local', '-t', transport, '-H', HOST, '-p', str(port)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, env=env, start_new_session=True)
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while not listening(port):
            if server.poll() is not None or time.monotonic() > deadline:
                tail = log.read_text(errors='replace')[-800:]
                pytest.fail(f'{transport}: the server did not start listening on {HOST}:{port} '
                            f'(exit {server.poll()}); stderr ends: {tail}', pytrace=False)
            time.sleep(0.1)
        listed = url_tools(f'http://{HOST}:{port}{PATHS[transport]}', transport, era)
    finally:
        stop(server)
    leftovers: list[str] = []
    deadline = time.monotonic() + STOP_TIMEOUT
    while group_alive(server.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if group_alive(server.pid):
        leftovers.append(f'{transport}: the server process group {server.pid} outlived the stop')
    if listening(port):
        leftovers.append(f'{transport}: {HOST}:{port} still accepts connections after the stop')
    if sandbox_dirs(tmp_path):
        leftovers.append(f'{transport}: the sandbox directory was not removed: {sandbox_dirs(tmp_path)}')
    return listed, leftovers


def stdio_and_list(era: str, tmp_path: Path) -> tuple[Listed, list[str]]:
    """Run `rcac-mcp -e local -t stdio` under the sandbox as the client's child, list, and close it."""
    env = {**os.environ, 'TMPDIR': str(tmp_path)}
    listed = stdio_tools([str(SANDBOX), str(CLI), '-e', 'local', '-t', 'stdio'], era, env=env)
    leftovers: list[str] = []
    if sandbox_dirs(tmp_path):
        leftovers.append(f'stdio: the sandbox directory was not removed: {sandbox_dirs(tmp_path)}')
    return listed, leftovers


@pytest.mark.integration
@pytest.mark.parametrize(('transport', 'era'), [
    pytest.param('stdio', 'legacy', id='stdio-legacy'),
    pytest.param('stdio', 'modern', id='stdio-modern'),
    pytest.param('http', 'legacy', id='http-legacy'),
    pytest.param('http', 'modern', id='http-modern'),
    pytest.param('sse', 'legacy', id='sse-legacy'),
])
def test_transport_lists_expected_tools(transport: str, era: str, tmp_path: Path) -> None:
    if transport == 'stdio':
        listed, leftovers = stdio_and_list(era, tmp_path)
    else:
        listed, leftovers = serve_and_list(transport, era, tmp_path)
    assert listed.instructions is not None, f'{transport}: no instructions were served'
    assert transport_violations(listed, transport, era) == []
    assert leftovers == []


UNSANDBOXED: list[Sequence[str]] = [
    [str(CLI), '-e', 'local', '-t', 'stdio'],
    ['sh', str(SANDBOX), str(CLI), '-e', 'local', '-t', 'stdio'],
    [],
]


@pytest.mark.unit
def test_harness_refuses_unsandboxed_stdio(guard) -> None:
    # A unit test: were the refusal missing, the unit guard would refuse the launch and record it.
    for argv in UNSANDBOXED:
        with pytest.raises(ValueError, match='must start with'):
            stdio_tools(argv, 'legacy')
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('url', [
    pytest.param('http://cluster.invalid:8000/mcp', id='remote-host'),
    pytest.param('http://0.0.0.0:8000/mcp', id='wildcard'),
])
def test_harness_refuses_a_remote_url(url: str, guard) -> None:
    with pytest.raises(ValueError, match='not a loopback address'):
        url_tools(url, 'http', 'legacy')
    assert guard.refused == []


LISTED = Listed(tuple(sorted(EXPECTED_TOOLS)), '2025-11-25', True)


@pytest.mark.unit
def test_transport_check_accepts_a_clean_listing() -> None:
    assert transport_violations(LISTED, 'http', 'legacy') == []
    assert transport_violations(Listed(LISTED.tools, '2026-07-28', False), 'http', 'modern') == []


@pytest.mark.unit
@pytest.mark.parametrize(('listed', 'era', 'fragment'), [
    pytest.param(Listed(LISTED.tools[1:], '2025-11-25', True), 'legacy', 'missing', id='tool-missing'),
    pytest.param(Listed(LISTED.tools + ('extra_tool',), '2025-11-25', True), 'legacy', 'unexpected',
                 id='tool-unexpected'),
    pytest.param(Listed(LISTED.tools + LISTED.tools[:1], '2025-11-25', True), 'legacy', 'listed twice',
                 id='tool-twice'),
    pytest.param(LISTED, 'modern', 'selected modern', id='legacy-as-modern'),
    pytest.param(Listed(LISTED.tools, '2026-07-28', False), 'legacy', 'selected legacy', id='modern-as-legacy'),
])
def test_transport_check_catches(listed: Listed, era: str, fragment: str) -> None:
    violations = transport_violations(listed, 'http', era)
    assert any(fragment in v for v in violations), violations
