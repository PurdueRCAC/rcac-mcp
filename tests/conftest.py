# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Collection rules shared by every test module, and the guards every test runs under.

The collection hook fails the run on a test carrying neither `unit` nor `integration`. The fixtures
below it act on every test without adding or reading markers for any other purpose, so they cannot
launder that hook: a session-wide PATH shim for `sudo` and `ssh` (re-pinned inside a login bash
through `BASH_ENV`), the unit guard (no process, no network, no real HOME), the `launches` recorder,
the `executes` launcher (real executions of the local and delegate backends, fenced and sandboxed),
and a check that no executor leaks into the main context.
"""

from __future__ import annotations

import getpass
import multiprocessing
import os
import pty
import shlex
import socket
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Callable, Iterator

import _posixsubprocess
import pytest

import rcac_mcp.context
from rcac_mcp.executor import launch

TESTS = Path(__file__).resolve().parent
SANDBOX = TESTS.parent / 'bin' / 'sandbox.sh'

# The real Popen, captured before any fixture replaces it.
REAL_POPEN = subprocess.Popen


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

def need_shell(shell: str) -> None:
    """Return if `shell` is installed; otherwise skip the test, or fail it on CI.

    Every login-shell row goes through here. On CI (`CI=true`, as GitHub Actions sets it) the
    workflow installs every shell the suite drives, so a missing one fails the row, naming it: a
    skipped row there would be coverage silently lost. Elsewhere it skips.
    """
    if os.access(shell, os.X_OK):
        return
    if os.environ.get('CI', '').lower() == 'true':
        pytest.fail(f'{shell} is not installed here, and on CI every shell the suite drives must be', pytrace=False)
    pytest.skip(f'{shell} is not installed here')


SHIM_MESSAGE = 'refused by the test PATH shim'
SHIM_SCRIPT = f"""#!/bin/sh
echo '{SHIM_MESSAGE}' >&2
exit 126
"""

# The file BASH_ENV names: a login bash reads it after /etc/profile, whose path_helper (macOS) moves
# the system directories ahead of the shim, so it puts the shim first again.
SHIM_BASH_ENV = 'bash_env'


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
    stand-ins; neither name can reach the real binary from inside the suite. A login bash (the
    pinned interpreter) rebuilds PATH in /etc/profile, so `BASH_ENV`, which it reads afterwards,
    names a file that puts the shim first again.
    """
    shim = tmp_path_factory.mktemp('path-shim')
    for name in ('sudo', 'ssh'):
        script = shim / name
        script.write_text(SHIM_SCRIPT)
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (shim / SHIM_BASH_ENV).write_text(f'PATH={shlex.quote(str(shim))}:"$PATH"\nexport PATH\n')
    saved = {name: os.environ.get(name) for name in ('PATH', 'BASH_ENV')}
    os.environ['PATH'] = f'{shim}{os.pathsep}{saved["PATH"] or ""}'
    os.environ['BASH_ENV'] = str(shim / SHIM_BASH_ENV)
    try:
        yield shim
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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

    It answers stdout with `<user>|<command>`, where the user follows `-u` (or is the program name
    when there is none), so a tool result can be traced to the exact launch. For a launcher program
    (the last element of every backend's launch) the command is the script or argv the program runs,
    and both streams start with the program's sentinel, as a real launch's do, so the backend's
    `separate` keeps the answer; for any other argv it is the last element. It exits with
    `answer_code`.
    """

    test_fence = True
    store: list[list[str]]
    answer_code = 0

    def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
        from harness import launched_script

        argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        self.store.append(argv)
        self.args = args
        # Far outside any real pid range, so a stray signal to it reaches nothing.
        self.pid = 2_147_483_100
        self.returncode = self.answer_code
        self._text = bool(kwargs.get('text') or kwargs.get('universal_newlines')
                          or kwargs.get('encoding') or kwargs.get('errors'))
        self._pipes = (kwargs.get('stdout') == subprocess.PIPE, kwargs.get('stderr') == subprocess.PIPE)
        user = argv[argv.index('-u') + 1] if '-u' in argv[:-1] else argv[0]
        try:
            command, sentinel = launched_script(argv)
        except ValueError:
            command, sentinel = argv[-1], b''
        shown = command if isinstance(command, str) else ' '.join(command)
        self._out = sentinel + f'{user}|{shown}'.encode()
        self._err = sentinel

    def communicate(self, input: Any = None, timeout: float | None = None) -> tuple[Any, Any]:
        out: Any = self._out.decode() if self._text else self._out
        err: Any = self._err.decode() if self._text else self._err
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


# PATH inside a fenced launch: the shim first, then what sudo's secure_path and sshd give.
SECURE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

# The stand-in for `sudo -n -H -u <user>`: it runs the command in a session of its own, as a sudo
# that cannot share its process group would, and relays only TERM, INT and HUP to it. It dies of the
# signal that killed the command, as sudo does.
RELAY = """\
import os, signal, subprocess, sys
child = subprocess.Popen(sys.argv[1:], start_new_session=True)
def relay(signum, frame):
    try:
        os.killpg(child.pid, signum)
    except OSError:
        pass
for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(signum, relay)
code = child.wait()
if code < 0:
    signal.signal(-code, signal.SIG_DFL)
    os.kill(os.getpid(), -code)
sys.exit(code)
"""


class Executions:
    """The fenced launches of the `executes` fixture, and the homes they run in.

    `server_home` is the server process's HOME (what a `local` launch sees); `mapped_home` is the
    home `sudo -H` gives the mapped user `user` (what a `delegate` launch sees). Tests may write
    startup files into either before a launch. A `local` command starts in the server process's
    working directory, which the backend writes into its program: `local_cwd`, a fresh directory
    under the test's tree unless a test sets another (setting it changes this process's working
    directory for the test, restored after).
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.server_home = (tmp_path / 'server-home').resolve()
        self.mapped_home = (tmp_path / 'mapped-home').resolve()
        for home in (self.server_home, self.mapped_home, tmp_path / 'server-cwd'):
            home.mkdir(parents=True, exist_ok=True)
        self.user = 'umapped'
        self._monkeypatch = monkeypatch
        self.local_cwd = (tmp_path / 'server-cwd').resolve()
        self.launched: list[list[str]] = []
        self.refused: list[list[str]] = []

    @property
    def local_cwd(self) -> Path:
        return self._local_cwd

    @local_cwd.setter
    def local_cwd(self, path: Path) -> None:
        self._monkeypatch.chdir(path)
        self._local_cwd = Path(path)

    def _refuse(self, argv: list[str], why: str) -> OSError:
        self.refused.append(argv)
        return OSError(f'executes: refused {why}: {argv!r:.200}')

    def sandboxed(self, argv: list[str]) -> list[str]:
        """The sandboxed argv that stands in for `argv`; refuses anything but a (delegated) bash."""
        if argv[:1] == ['sudo']:
            if argv[:5] != ['sudo', '-n', '-H', '-u', self.user]:
                raise self._refuse(argv, f'a sudo argv that does not start exactly sudo -n -H -u {self.user}')
            home, user, rest, relay = self.mapped_home, self.user, argv[5:], [sys.executable, '-I', '-c', RELAY]
        else:
            home, user, rest, relay = self.server_home, getpass.getuser(), argv, []
        if rest[:1] != [launch.BASH]:
            raise self._refuse(argv, f'an argv that does not run {launch.BASH}')
        shim = Path(os.environ['BASH_ENV']).parent
        env = [f'HOME={home}', f'USER={user}', f'LOGNAME={user}', f'PATH={shim}:{SECURE_PATH}',
               f'BASH_ENV={os.environ["BASH_ENV"]}', f'SHELL={os.environ.get("SHELL", "/bin/sh")}']
        return [str(SANDBOX), 'env', '-i', *env, *relay, *rest]


@pytest.fixture
def executes(guard: UnitGuard, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Executions:
    """Run the local and delegate backends for real, fenced: only bash, only inside bin/sandbox.sh.

    It replaces `Popen` in `rcac_mcp.executor.launch` with the fenced launcher. Each launch runs
    under `env -i` with an environment shaped like the real one (HOME, USER, LOGNAME, a
    secure_path-like PATH with the shim first, the shim's BASH_ENV, SHELL). A delegated launch must
    start exactly `sudo -n -H -u <mapped user>`; those five words are replaced by a relaying
    stand-in that gives the command the separate `mapped_home`, from a cwd that is not that home
    (the sandbox's). Any other argv is refused and never launched. No argv names `sudo` when it
    launches, so the PATH shim and the static launch check both hold.

    Everywhere else, `subprocess.Popen` becomes a launcher that refuses any argv not starting with
    bin/sandbox.sh (a loopback SSH server's own launches start there), so the harness fence lets
    an in-process server call through: nothing behind it can launch outside the sandbox.
    """
    if guard.active:
        raise pytest.UsageError('executes launches processes: mark the test integration')
    executions = Executions(tmp_path, monkeypatch)

    class FencedPopen(REAL_POPEN):  # type: ignore[misc, valid-type]
        test_fence = True

        def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
            argv = executions.sandboxed(list(args))
            executions.launched.append(argv)
            super().__init__(argv, *rest, **kwargs)

    class SandboxOnlyPopen(REAL_POPEN):  # type: ignore[misc, valid-type]
        test_fence = True

        def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
            argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
            if not argv or str(argv[0]) != str(SANDBOX):
                raise executions._refuse(argv, f'an argv that does not start with {SANDBOX}')
            super().__init__(argv, *rest, **kwargs)

    proxy = types.ModuleType('subprocess')
    proxy.__dict__.update(subprocess.__dict__)
    proxy.Popen = FencedPopen  # type: ignore[attr-defined]
    monkeypatch.setattr(launch, 'subprocess', proxy)
    monkeypatch.setattr(subprocess, 'Popen', SandboxOnlyPopen)
    return executions


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
