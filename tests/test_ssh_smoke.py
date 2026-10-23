# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The `ssh` executor runs a command over the locked Fabric and Paramiko, against a loopback server.

`SSHExecutor` drives the recommended deployment. Here it connects to an in-process Paramiko server
bound to 127.0.0.1, with keys generated for the test, which decodes each exec request (the
launcher's fixed bootstrap carrying the program as one base64 word), records the command the
program runs, and answers with the program's sentinel, canned output, its end record and a non-zero exit status;
no process is launched and nothing leaves the machine. (`test_ssh_exec.py` runs the programs for
real, against a loopback server that executes them.) The test is isolated from the developer's SSH
setup: `HOME` is under `tmp_path`, `SSH_AUTH_SOCK` is unset, the client neither looks for keys nor
asks an agent, and Fabric is handed an empty SSH config, so no `~/.ssh/config` or
`/etc/ssh/ssh_config` is read. The process's own stdin is left as pytest's, which refuses reads: the
executor never forwards it.
"""

from __future__ import annotations

import base64
import socket
import threading
import time
from pathlib import Path
from typing import Any

import paramiko
import pytest
from fabric import Config

from harness import launched_script
from rcac_mcp.executor import launch
from rcac_mcp.executor.base import CommandResult
from rcac_mcp.executor.ssh import SSHExecutor

LOOPBACK = '127.0.0.1'
EXIT_STATUS = 3
COMMAND = 'echo hi'


def smoke_violations(result: CommandResult, received: list[str], command: str, host: str,
                     exit_status: int) -> list[str]:
    """Every way `result` and the commands the server `received` differ from one exec of `command`.

    `received` holds, per exec request, the command the decoded program runs, or the raw exec string
    when it was not the launcher's bootstrap.
    """
    violations: list[str] = []
    if received != [command]:
        violations.append(f'the server received {received!r}, expected [{command!r}]')
    if result.stdout != f'smoke:{command}':
        violations.append(f'stdout differs: {result.stdout!r}, expected {"smoke:" + command!r}')
    if result.exit_code != exit_status:
        violations.append(f'exit status differs: {result.exit_code!r}, expected {exit_status}')
    if result.hostname != host:
        violations.append(f'hostname differs: {result.hostname!r}, expected {host!r}')
    return violations


def decoded(wire: str) -> tuple[str, launch.Marks | None]:
    """The command a bootstrap exec string's program runs, and its nonces; the raw string otherwise."""
    if not wire.startswith(launch.BOOTSTRAP):
        return wire, None
    try:
        command, marks = launched_script([base64.b64decode(wire[len(launch.BOOTSTRAP):]).decode()])
    except ValueError:
        return wire, None
    return (command if isinstance(command, str) else ' '.join(command)), marks


class _Server(paramiko.ServerInterface):
    """Accept the one client key; answer every exec as its program would: the start sentinel,
    `smoke:<command>`, then the end record carrying EXIT_STATUS, on both streams; and EXIT_STATUS."""

    def __init__(self, client_key: paramiko.PKey, received: list[str]) -> None:
        self.client_key = client_key
        self.received = received

    def get_allowed_auths(self, username: str) -> str:
        return 'publickey'

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        same = key.get_base64() == self.client_key.get_base64()
        return paramiko.AUTH_SUCCESSFUL if same else paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED

    def check_channel_env_request(self, channel: Any, name: bytes, value: bytes) -> bool:
        return True

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        ran, marks = decoded(command.decode())
        self.received.append(ran)
        start, end = (b'', b'') if marks is None else (marks.start, marks.end + b'x%d.' % EXIT_STATUS)

        def reply() -> None:
            # Paramiko confirms the exec request after this method returns; answering before that
            # confirmation reaches the client closes the channel under it.
            time.sleep(0.2)
            channel.sendall(start + b'smoke:' + ran.encode() + end)
            channel.sendall_stderr(start + end)
            channel.send_exit_status(EXIT_STATUS)
            channel.close()

        threading.Thread(target=reply, daemon=True).start()
        return True


@pytest.mark.integration
def test_ssh_executor_runs_over_a_loopback_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('SSH_AUTH_SOCK', raising=False)
    host_key = paramiko.RSAKey.generate(2048)
    client_key = paramiko.RSAKey.generate(2048)
    received: list[str] = []
    transports: list[paramiko.Transport] = []

    with socket.socket() as listener:
        listener.bind((LOOPBACK, 0))
        listener.listen(1)
        listener.settimeout(30)
        port = listener.getsockname()[1]

        def serve() -> None:
            conn, _ = listener.accept()
            transport = paramiko.Transport(conn)
            transports.append(transport)
            transport.add_server_key(host_key)
            transport.start_server(server=_Server(client_key, received))

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        executor = SSHExecutor(LOOPBACK, port=port, user='smoke',
                               config=Config(overrides={'load_ssh_configs': False}, ssh_config=paramiko.SSHConfig()),
                               connect_kwargs={'pkey': client_key, 'look_for_keys': False, 'allow_agent': False})
        try:
            result = executor.run(COMMAND, timeout=30)
        finally:
            executor.close()
            server.join(timeout=5)
            for transport in transports:
                transport.close()

    violations = smoke_violations(result, received, COMMAND, LOOPBACK, EXIT_STATUS)
    assert violations == [], '\n'.join(violations)


@pytest.mark.unit
@pytest.mark.parametrize(('result', 'received', 'fragment'), [
    pytest.param(CommandResult('smoke:echo hi', '', 0, LOOPBACK), [COMMAND], 'exit status differs', id='exit-dropped'),
    pytest.param(CommandResult('', '', EXIT_STATUS, LOOPBACK), [COMMAND], 'stdout differs', id='stdout-lost'),
    pytest.param(CommandResult('smoke:echo hi', '', EXIT_STATUS, 'cluster.invalid'), [COMMAND], 'hostname differs',
                 id='wrong-host'),
    pytest.param(CommandResult('smoke:echo hi', '', EXIT_STATUS, LOOPBACK), ['cd /tmp && echo hi'],
                 'the server received', id='command-rewritten'),
])
def test_smoke_check_catches_drift(result: CommandResult, received: list[str], fragment: str) -> None:
    clean = CommandResult('smoke:echo hi', '', EXIT_STATUS, LOOPBACK)
    assert smoke_violations(clean, [COMMAND], COMMAND, LOOPBACK, EXIT_STATUS) == []
    violations = smoke_violations(result, received, COMMAND, LOOPBACK, EXIT_STATUS)
    assert any(fragment in v for v in violations), violations
