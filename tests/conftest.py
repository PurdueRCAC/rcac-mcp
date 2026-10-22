# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Collection rules shared by every test module, and the guards every test runs under.

The collection hook fails the run on a test carrying neither `unit` nor `integration`. The fixtures
below it act on every test without adding or reading markers for any other purpose, so they cannot
launder that hook: a session-wide PATH shim for `sudo` and `ssh`, the unit guard (no process, no
network, no real HOME), the `launches` recorder, and a check that no executor leaks into the main
context.
"""

from __future__ import annotations

import multiprocessing
import os
import pty
import shutil
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterator

import _posixsubprocess
import pytest

import rcac_mcp.context


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """A collected test carrying neither the `unit` nor the `integration` marker fails the run."""
    unmarked = [item.nodeid for item in items
                if not any(item.get_closest_marker(name) for name in ('unit', 'integration'))]
    if unmarked:
        listing = '\n  '.join(unmarked)
        raise pytest.UsageError(
            f'every test must carry @pytest.mark.unit or @pytest.mark.integration; unmarked:\n  {listing}')


# Environment the unit guard blanks, as bin/sandbox.sh does: no real cluster, identity map or
# signing secret can reach a unit test.
BLANKED_ENV = ('RCAC_SSH_HOST', 'RCAC_USER_MAP', 'JWT_SECRET', 'OIDC_CONFIG_URL', 'OIDC_CLIENT_ID',
               'OIDC_CLIENT_SECRET', 'MCP_BASE_URL')

# Every way a unit test could start a process or reach a host, as (owner, attribute name).
PROCESS_ENTRY_POINTS = (
    [(subprocess, 'Popen'), (os, 'system'), (os, 'popen'), (os, 'posix_spawn'), (os, 'posix_spawnp'),
     (os, 'fork'), (os, 'forkpty'), (pty, 'fork'), (pty, 'spawn'), (_posixsubprocess, 'fork_exec'),
     (multiprocessing.Process, 'start')]
    + [(os, name) for name in sorted(dir(os)) if name.startswith(('exec', 'spawn')) and callable(getattr(os, name))]
)
NETWORK_ENTRY_POINTS = [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                        (socket, 'create_connection'), (socket, 'getaddrinfo')]


def need_git() -> None:
    """Return if git is on PATH; otherwise skip the test, or fail it on CI.

    Every test that builds a git repository goes through here. On CI (`CI=true`, as GitHub Actions
    sets it) git is always installed, so a missing one fails the test, naming it: a skipped test
    there would be coverage silently lost. Elsewhere it skips.
    """
    if shutil.which('git'):
        return
    if os.environ.get('CI', '').lower() == 'true':
        pytest.fail('git is not on PATH here, and on CI it must be', pytrace=False)
    pytest.skip('git is not on PATH here')


SHIM_MESSAGE = 'refused by the test PATH shim'
SHIM_SCRIPT = f"""#!/bin/sh
echo '{SHIM_MESSAGE}' >&2
exit 126
"""


class UnitGuard:
    """The refusals a test provoked; contract tests assert `refused == []` inside their body."""

    def __init__(self, active: bool) -> None:
        self.active = active
        self.refused: list[tuple[str, tuple[Any, ...]]] = []

    def refuser(self, name: str) -> Callable[..., Any]:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            self.refused.append((name, args[:2]))
            raise OSError(f'unit guard: {name} refused in a unit test')
        # The harness fence accepts a Popen carrying this flag: nothing behind it can launch.
        refuse.test_fence = True  # type: ignore[attr-defined]
        refuse.unit_guard = True  # type: ignore[attr-defined]
        return refuse


@pytest.fixture(scope='session', autouse=True)
def path_shim(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Put `sudo` and `ssh` stand-ins first on PATH for every test and every child it starts.

    bin/sandbox.sh keeps PATH, so a sandboxed CLI drive or mutation child resolves the same
    stand-ins; neither name can reach the real binary from inside the suite.
    """
    shim = tmp_path_factory.mktemp('path-shim')
    for name in ('sudo', 'ssh'):
        script = shim / name
        script.write_text(SHIM_SCRIPT)
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    saved = os.environ.get('PATH', '')
    os.environ['PATH'] = f'{shim}{os.pathsep}{saved}'
    try:
        yield shim
    finally:
        os.environ['PATH'] = saved


@pytest.fixture(autouse=True)
def guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[UnitGuard]:
    """Make the `unit` marker's promise executable: no process, no network, no real HOME.

    Integration tests launch processes on purpose, so they get an inactive guard; their CLI drives
    go through bin/sandbox.sh, which test_hygiene checks statically.
    """
    active = request.node.get_closest_marker('unit') is not None
    g = UnitGuard(active)
    if not active:
        yield g
        return
    home = tmp_path / 'home'
    for var, path in (('HOME', home), ('XDG_CONFIG_HOME', home / '.config'),
                      ('XDG_CACHE_HOME', home / '.cache'), ('XDG_DATA_HOME', home / '.local' / 'share')):
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(path))
    for var in BLANKED_ENV:
        monkeypatch.delenv(var, raising=False)
    for owner, name in PROCESS_ENTRY_POINTS + NETWORK_ENTRY_POINTS:
        monkeypatch.setattr(owner, name, g.refuser(f'{owner.__name__}.{name}'))
    yield g
    if g.refused:
        pytest.fail(f'unit guard: the test tried to launch a process or reach a host: {g.refused}', pytrace=False)


class RecordedPopen:
    """A `subprocess.Popen` stand-in that records the argv and answers at once, launching nothing.

    It answers stdout with `<user>|<last argv element>`, where the user follows `-u` (or is the
    program name when there is none), so a tool result can be traced to the exact launch.
    """

    test_fence = True
    store: list[list[str]]

    def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
        argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        self.store.append(argv)
        self.args = args
        self.pid = 4242
        self.returncode = 0
        self._text = bool(kwargs.get('text') or kwargs.get('universal_newlines')
                          or kwargs.get('encoding') or kwargs.get('errors'))
        self._pipes = (kwargs.get('stdout') == subprocess.PIPE, kwargs.get('stderr') == subprocess.PIPE)
        user = argv[argv.index('-u') + 1] if '-u' in argv[:-1] else argv[0]
        self._out = f'{user}|{argv[-1]}'

    def communicate(self, input: Any = None, timeout: float | None = None) -> tuple[Any, Any]:
        out: Any = self._out if self._text else self._out.encode()
        err: Any = '' if self._text else b''
        return (out if self._pipes[0] else None, err if self._pipes[1] else None)

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        pass

    def terminate(self) -> None:
        pass

    def __enter__(self) -> RecordedPopen:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


@pytest.fixture
def launches(guard: UnitGuard, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every argv the test's code tried to launch, recorded at `subprocess.Popen`; nothing runs.

    Works for unit and integration tests alike, so no in-process server call can reach a real
    `sudo` whatever its marker.
    """
    store: list[list[str]] = []
    recorder = type('RecordedPopen', (RecordedPopen,), {'store': store})
    monkeypatch.setattr(subprocess, 'Popen', recorder)
    return store


@pytest.fixture(autouse=True)
def main_context_holds_no_executor() -> Iterator[None]:
    """After every test the main context must hold no executor.

    Executors are set per tool call inside each request's task. One left in the main context would
    reach every later drive (asyncio.run copies the main context) and hide a missing executor there.
    """
    yield
    leaked = rcac_mcp.context.executor_var.get()
    if leaked is not None:
        rcac_mcp.context.executor_var.set(None)
        pytest.fail(f'an executor leaked into the main context: {leaked!r}', pytrace=False)
