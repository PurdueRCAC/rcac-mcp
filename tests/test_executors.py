# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Every backend runs one launcher program under the pinned interpreter, and satisfies `Executor`.

`DelegatingExecutor.run` must launch exactly `sudo -n -H -u <local user>` followed by the launcher's
stage (`/bin/bash --posix -c <stage> cluster-mcp <program>`), which starts the pinned login bash on
the program (§ Invariants §5). `-n` keeps a missing sudoers rule an error instead of a hang on a TTY
nobody has; `-H` gives the target user their own HOME. The interpreter is the constant `/bin/bash`,
whatever `$SHELL` says. The program starts in the mapped user's `$HOME`, changes to a `cwd` given
as one quoted word, and runs a script as one word. The unit rows record the argv at
`subprocess.Popen` with the `launches` fixture, so nothing runs `sudo`; the recorder answers with
the program's sentinel, so the backend's output separation keeps its answer. These argv rows are
`safe-execution`'s deliberate rewrite of the earlier `<$SHELL> -c` rows.

The `integration` rows run the local and delegate backends for real through the `executes` fixture:
fenced to bash, inside `bin/sandbox.sh`, with the `sudo` prefix asserted and replaced by a relaying
stand-in that gives the command a mapped home distinct from the server's. They pin the timeout
contract (a result with the captured output and `TIMEOUT_EXIT_CODE`, never an exception), `~` and
relative paths as the mapped user's home in delegate mode (cwd and both sides of a transfer, never
the server's `HOME`), transfers that return the path they wrote, stdin delivered exactly and
closed, bash after the login initialisation whatever `$SHELL` is, startup output that never
reaches a result, and a profile that `cd`s, reads stdin or leaves a background job running changing
neither where the command starts, nor what it reads, nor how long the call takes. A signal death
keeps `-1`; `bin/sandbox.sh` reports a signalled child as
`128+n`, so that control is a unit row on a recorded launch.

The three backends (`SSHExecutor`, `LocalShellExecutor`, `DelegatingExecutor`) must each satisfy
the runtime-checkable `Executor` protocol (§ Invariants §2), which checks member presence only.
`SSHExecutor` connects in its constructor, so its row replaces the module global
`rcac_mcp.executor.ssh.Connection` (an `rcac_mcp` internal, bound to Fabric) with a fake that
records it was built and opened for the host: no host is reached.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

import rcac_mcp.executor.ssh
from harness import launched_script
from rcac_mcp.executor import TIMEOUT_EXIT_CODE, CommandResult, Executor, UserPath
from rcac_mcp.executor import launch
from rcac_mcp.executor.delegate import DelegatingExecutor
from rcac_mcp.executor.shell import LocalShellExecutor
from rcac_mcp.executor.ssh import SSHExecutor
from test_launch import SEPARATION_CASES, STAGE_ARGV, chatty_home, launch_violations

COMMAND = 'echo $HOME; ls *'
USER_MAP = {'bob': 'ubob'}

# The protocol's members, named in a failure so a missing one is visible.
PROTOCOL_MEMBERS = ('hostname', 'run', 'put', 'get', 'open', 'close', '__enter__', '__exit__')

# The position of each element in the expected argv, for naming a difference.
SUDO_ROLES = ('program', 'non-interactive flag', 'HOME flag', 'user flag', 'local user')
STAGE_ROLES = ('interpreter', 'POSIX-mode flag', 'command flag', 'stage', 'stage name')


def launch_argv_violations(argv: Sequence[str], user: str | None, command: list[str] | str, cwd: str | None,
                           start: str | None) -> list[str]:
    """Return every difference between `argv` and the backend's launch of `command`; empty means exact.

    The expected argv is `['sudo', '-n', '-H', '-u', user]` (none when `user` is None), then the
    launcher's stage `['/bin/bash', '--posix', '-c', <stage>, 'cluster-mcp']`, then one program that
    runs `command` exactly (`launch_violations`): a dropped or added flag, another user or
    interpreter, or a script or cwd quoted or rewritten in any way is a difference.
    """
    got = list(argv)
    prefix = ([] if user is None else ['sudo', '-n', '-H', '-u', user]) + STAGE_ARGV
    roles = (() if user is None else SUDO_ROLES) + STAGE_ROLES
    if len(got) != len(prefix) + 1:
        missing = [part for part in prefix if part not in got]
        extra = [part for part in got[:-1] if part not in prefix]
        return [f'argv differs: {len(got)} elements, expected {len(prefix) + 1}; '
                f'missing {missing!r}, extra {extra!r}: {got!r}']
    found = [f'argv differs at {i} ({role}): {g!r}, expected {e!r}'
             for i, (role, g, e) in enumerate(zip(roles, got, prefix)) if g != e]
    try:
        _, sentinel = launched_script(got)
    except ValueError as exc:
        return found + [f'argv differs: the last element is not a launcher program ({exc})']
    found += [f'argv differs: the program: {v}' for v in launch_violations(command, cwd, start, got[-1], sentinel)]
    return found


@pytest.mark.unit
@pytest.mark.parametrize('cwd', [
    pytest.param(None, id='no-cwd'),
    pytest.param('/data/my dir', id='cwd-spaces'),
    pytest.param("/data/x; rm -rf / $(id) 'q'", id='cwd-metacharacters'),
])
def test_sudo_argv(cwd: str | None, launches: list[list[str]], guard) -> None:
    result = DelegatingExecutor('bob', USER_MAP).run(COMMAND, cwd=cwd)
    assert len(launches) == 1, f'launch count {len(launches)}, expected 1: {launches!r}'
    violations = launch_argv_violations(launches[0], 'ubob', COMMAND, cwd, 'home')
    if result.stdout != f'ubob|{COMMAND}':
        violations.append(f'the result lacks the launch marker: {result!r}')
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


BACKEND_ARGV = {
    # The local program starts in this process's working directory, written into it as a literal.
    'local': (LocalShellExecutor, None, 'cwd'),
    'delegate': (lambda: DelegatingExecutor('bob', USER_MAP), 'ubob', 'home'),
}


@pytest.mark.unit
@pytest.mark.parametrize('backend', list(BACKEND_ARGV))
def test_interpreter_is_pinned(backend: str, monkeypatch: pytest.MonkeyPatch, launches: list[list[str]], guard) -> None:
    # The interpreter is /bin/bash, whatever $SHELL said when the executor was built or says now.
    build, user, start = BACKEND_ARGV[backend]
    start = os.getcwd() if start == 'cwd' else start
    monkeypatch.setenv('SHELL', '/bin/zsh')
    executor = build()
    monkeypatch.setenv('SHELL', '/opt/test-shell')
    executor.run(['printf', '%s', 'a b'], cwd='~/sub')
    assert len(launches) == 1, f'launch count {len(launches)}, expected 1: {launches!r}'
    violations = launch_argv_violations(launches[0], user, ['printf', '%s', 'a b'], '~/sub', start)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


def _good_argv(cwd: str | None) -> list[str]:
    prog, _ = launch.program(COMMAND, cwd, start='home')
    return ['sudo', '-n', '-H', '-u', 'ubob', *STAGE_ARGV, prog]


def _swap_program(argv: list[str], old: str, new: str) -> list[str]:
    assert argv[-1].count(old) == 1, (old, argv[-1])
    return argv[:-1] + [argv[-1].replace(old, new)]


GOOD_ARGV = _good_argv('/data/my dir')
DRIFTS: dict[str, tuple[Callable[[list[str]], list[str]], str]] = {
    'n-dropped': (lambda a: [x for x in a if x != '-n'], "missing ['-n']"),
    'H-dropped': (lambda a: [x for x in a if x != '-H'], "missing ['-H']"),
    'command-quoted': (lambda a: _swap_program(a, shlex.quote(COMMAND), shlex.quote(shlex.quote(COMMAND))),
                       'not verbatim'),
    'cwd-unquoted': (lambda a: _swap_program(a, "'/data/my dir'", '/data/my dir'), 'reaches cd as'),
    'wrong-interpreter': (lambda a: a[:5] + ['/bin/zsh'] + a[6:], '(interpreter)'),
    'wrong-user': (lambda a: a[:4] + ['ualice'] + a[5:], '(local user)'),
    'not-in-home': (lambda a: _swap_program(a, 'command cd -- "${HOME:?HOME is not set}" || exec /usr/bin/false\n', ''),
                    'does not start in $HOME'),
}


@pytest.mark.unit
@pytest.mark.parametrize('drift', list(DRIFTS))
def test_argv_check_catches_drift(drift: str) -> None:
    clean = launch_argv_violations(GOOD_ARGV, 'ubob', COMMAND, '/data/my dir', 'home')
    assert clean == [], clean
    mutate, fragment = DRIFTS[drift]
    violations = launch_argv_violations(mutate(list(GOOD_ARGV)), 'ubob', COMMAND, '/data/my dir', 'home')
    assert any('argv differs' in v and fragment in v for v in violations), violations


@pytest.mark.unit
@pytest.mark.parametrize('backend', list(BACKEND_ARGV))
def test_a_sighup_death_is_minus_one_not_the_marker(backend: str, monkeypatch: pytest.MonkeyPatch,
                                                    launches: list[list[str]], guard) -> None:
    # bin/sandbox.sh reports a signalled child as 128+n, so a real SIGHUP death cannot reach a backend
    # through it; the recorded launch answers as a command killed by SIGHUP would.
    monkeypatch.setattr(subprocess.Popen, 'answer_code', -signal.SIGHUP)
    result = BACKEND_ARGV[backend][0]().run('kill -HUP $$')
    assert result.exit_code == -1, f'a SIGHUP death reported {result.exit_code}, not -1'
    assert result.exit_code != TIMEOUT_EXIT_CODE
    assert guard.refused == []


# ---------------------------------------------------------------------------------------------
# Real executions (integration), through the `executes` fixture.

def _home_of(executes: Any, backend: str) -> Path:
    return executes.mapped_home if backend == 'delegate' else executes.server_home


def _executor(executes: Any, backend: str) -> Executor:
    if backend == 'delegate':
        return DelegatingExecutor('carol', {'carol': executes.user})
    return LocalShellExecutor()


def timeout_violations(result: CommandResult | BaseException, elapsed: float, bound: float) -> list[str]:
    """Every way a timed-out `echo part; echo perr >&2; sleep …` broke the timeout contract.

    It must return a result, never raise; carry what was captured; and report `TIMEOUT_EXIT_CODE`,
    which no completed command (0..255) and no signal death (-1..-64) can produce.
    """
    if isinstance(result, BaseException):
        return [f'run raised {result!r} at the timeout instead of returning a result']
    found = []
    if result.exit_code != TIMEOUT_EXIT_CODE:
        found.append(f'a timeout returned exit code {result.exit_code}, not the marker {TIMEOUT_EXIT_CODE}')
    if -64 <= TIMEOUT_EXIT_CODE <= 255:
        found.append(f'the timeout marker {TIMEOUT_EXIT_CODE} is indistinguishable from a completed or '
                     'signal-killed command')
    if result.stdout != 'part\n' or not result.stderr.startswith('perr\n'):
        found.append(f'the timeout lost the captured output: {result.stdout!r}, {result.stderr!r}')
    if elapsed > bound:
        found.append(f'the timeout took {elapsed:.1f} s (bound {bound:.1f} s)')
    return found


@pytest.mark.unit
@pytest.mark.parametrize(('result', 'fragment'), [
    pytest.param(TimeoutError('timed out'), 'raised', id='raised'),
    pytest.param(CommandResult('part\n', 'perr\n', -1, 'h'), 'not the marker', id='minus-one'),
    pytest.param(CommandResult('', '', TIMEOUT_EXIT_CODE, 'h'), 'lost the captured output', id='output-lost'),
])
def test_timeout_check_catches_drift(result: CommandResult | BaseException, fragment: str) -> None:
    assert timeout_violations(CommandResult('part\n', 'perr\nmore', TIMEOUT_EXIT_CODE, 'h'), 1, 3) == []
    violations = timeout_violations(result, 1, 3)
    assert any(fragment in v for v in violations), violations


def _gone(pid: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_timeout_returns_the_captured_output(backend: str, executes: Any) -> None:
    pidfile = _home_of(executes, backend) / 'pid'
    start = time.monotonic()
    try:
        result: CommandResult | BaseException = _executor(executes, backend).run(
            'echo part; echo perr >&2; echo $$ > "$HOME/pid"; sleep 30', timeout=0.5)
    except Exception as exc:
        result = exc
    elapsed = time.monotonic() - start
    try:
        violations = timeout_violations(result, elapsed, 0.5 + 2 * launch.GRACE + 2)
        if not _gone(int(pidfile.read_text()), 3):
            violations.append('the timed-out command is still running')
        assert violations == [], '\n'.join(violations)
    finally:
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_stdin_arrives_exactly_and_closes(backend: str, executes: Any) -> None:
    data = b'line one\r\nno final newline \x00 \xc3\xa9'
    executor = _executor(executes, backend)
    sent = executor.run(['cat'], stdin=data, timeout=20)
    empty = executor.run(['cat'], timeout=20)
    assert (sent.stdout.encode(), sent.exit_code) == (data, 0), f'cat read {sent!r}, not the stdin sent'
    assert (empty.stdout, empty.exit_code) == ('', 0), f'with no stdin, cat read {empty!r}: stdin was not empty and closed'


# The interpreter probe: bash, not in POSIX mode, after the login initialisation (the marker a quiet
# .bash_profile exports), where `[ a == a ]` is valid.
PROBE = '[ -n "$BASH_VERSION" ] && ! shopt -qo posix && [ "$CLUSTER_MCP_TEST_LOGIN" = 1 ] && [ a == a ]'
LOGIN_MARKER = 'export CLUSTER_MCP_TEST_LOGIN=1\n'


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_bash_runs_whatever_shell_is_set(backend: str, executes: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('SHELL', '/bin/zsh')
    (_home_of(executes, backend) / '.bash_profile').write_text(LOGIN_MARKER)
    result = _executor(executes, backend).run(PROBE, timeout=20)
    assert result.exit_code == 0, f'{backend}: the probe failed under SHELL=/bin/zsh, not under the pinned interpreter: {result!r}'


@pytest.mark.integration
@pytest.mark.parametrize('case', list(SEPARATION_CASES))
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_startup_output_is_separated(backend: str, case: str, executes: Any) -> None:
    command, stdin = SEPARATION_CASES[case]
    executor = _executor(executes, backend)
    want = executor.run(command, stdin=stdin, timeout=20)
    chatty_home(_home_of(executes, backend))
    got = executor.run(command, stdin=stdin, timeout=20)
    assert got == want, f'{backend}-{case}: the result differs from the quiet home: {got!r} != {want!r}'


# A startup file changes nothing about where a command starts, what it reads or how long it takes.

def _start_of(executes: Any, backend: str) -> Path:
    """Where a command starts: the mapped user's home (delegate), the server's directory (local)."""
    return executes.mapped_home if backend == 'delegate' else executes.local_cwd


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_a_profile_cd_does_not_move_the_start(backend: str, executes: Any) -> None:
    home, start = _home_of(executes, backend), _start_of(executes, backend)
    (home / 'elsewhere').mkdir()
    (start / 'rel').write_text('at the start\n')
    (home / 'elsewhere' / 'rel').write_text('where the profile went\n')
    # bash 4 and later bind a login bash's positional parameters before its startup files run, so a
    # profile can rewrite them; the start must not ride on them.
    (home / '.bash_profile').write_text('set -- clobbered; shift; cd "$HOME/elsewhere"\n')
    executor = _executor(executes, backend)
    got = [executor.run(['pwd'], timeout=20), executor.run(['cat', UserPath('rel')], timeout=20)]
    want = [(f'{start}\n', '', 0), ('at the start\n', '', 0)]
    assert [(r.stdout, r.stderr, r.exit_code) for r in got] == want, \
        f'{backend}: a profile cd moved where the command started: {got!r}, not {want!r}'


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_a_profile_cannot_read_stdin(backend: str, executes: Any) -> None:
    home = _home_of(executes, backend)
    (home / '.bash_profile').write_text('read -r line; printf %s "$line" > "$HOME/profile-read"\n'
                                        'cat > "$HOME/profile-rest"\n')
    sent = b'first line\nsecond line\n\0 no final newline \xc3\xa9'
    result = _executor(executes, backend).run(['cat'], stdin=sent, timeout=20)
    seen = [(home / name).read_bytes() for name in ('profile-read', 'profile-rest')]
    assert (result.stdout.encode(), result.stderr, result.exit_code) == (sent, '', 0), \
        f'{backend}: cat read {result!r}, not the stdin sent'
    assert seen == [b'', b''], f"{backend}: the profile read the caller's stdin: {seen!r}"


# The background job a profile leaves running: it holds every descriptor the profile had open.
HOLDER = 'sleep 6 & echo $! > "$HOME/holder.pid"\n'


def kill_holder(home: Path) -> None:
    try:
        os.kill(int((home / 'holder.pid').read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


# What a result says when the call stopped reading output still held open after the command exited
# (nothing had arrived for the grace), and when that output was still arriving at the timeout.
HELD_NOTE = ('cluster-mcp: the command exited, but something left running held its output open; nothing '
             'arrived for 1 s, so reading stopped there\n')
LATE_NOTE = ('cluster-mcp: the command exited {code}, but its output was still arriving at the timeout; '
             'reading stopped there\n')


def held_violations(mode: str, quiet: tuple[CommandResult, float], held: tuple[CommandResult, float]) -> list[str]:
    """Every way a startup file's background job changed a call past the stated stop.

    The result is the quiet home's, with the note that reading stopped while the output was held
    open (a stop without the end of output is never silent), and the call lasts at most the grace
    longer.
    """
    (want, quiet_s), (got, held_s) = quiet, held
    found = []
    stated = CommandResult(want.stdout, want.stderr + HELD_NOTE, want.exit_code, want.hostname)
    if got != stated:
        found.append(f'{mode}: the result differs from the quiet home\'s plus the held-output note: {got!r} != {stated!r}')
    if held_s - quiet_s > launch.GRACE + 1.5:
        found.append(f"{mode}: a startup file's background job held the call for {held_s:.1f} s "
                     f'(quiet {quiet_s:.1f} s, grace {launch.GRACE:g} s)')
    return found


def timed(run: Callable[[], CommandResult]) -> tuple[CommandResult, float]:
    start = time.monotonic()
    result = run()
    return result, time.monotonic() - start


# A command that exits at once and leaves a job writing to its output for 8 s, every 0.2 s: the
# output never goes quiet, so only the call's timeout ends it.
WRITER = '(i=0; while [ $i -lt 40 ]; do echo tick; i=$((i+1)); sleep 0.2; done) & echo done'


def writer_violations(mode: str, result: CommandResult, elapsed: float, timeout: float) -> list[str]:
    """Every way a call whose output kept arriving after the command exited broke the timeout contract.

    It ends within the grace of the timeout, as a timeout (`TIMEOUT_EXIT_CODE`), with the output read
    so far and the note naming the command's own status.
    """
    found = []
    bound = timeout + launch.GRACE + 1.5
    if elapsed > bound:
        found.append(f'{mode}: output still arriving held the call for {elapsed:.1f} s (bound {bound:.1f} s)')
    if result.exit_code != TIMEOUT_EXIT_CODE:
        found.append(f'{mode}: output still arriving at the timeout returned exit code {result.exit_code}, '
                     f'not the marker {TIMEOUT_EXIT_CODE}')
    if not result.stdout.startswith('done\ntick\n'):
        found.append(f'{mode}: the output read so far was lost: {result.stdout[:60]!r}')
    if not result.stderr.endswith(LATE_NOTE.format(code=0)):
        found.append(f'{mode}: the stop at the timeout is silent: stderr {result.stderr[-200:]!r}')
    return found


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_a_holder_that_keeps_writing_ends_at_the_timeout(backend: str, executes: Any) -> None:
    executor = _executor(executes, backend)
    result, elapsed = timed(lambda: executor.run(WRITER, timeout=1.5))
    violations = writer_violations(backend, result, elapsed, 1.5)
    assert violations == [], '\n'.join(violations)


# A command whose background job writes in bursts, each within the grace of the last: all of it is
# the result, which ends when the job does.
BURSTS = '(sleep 0.6; echo late; sleep 0.6; echo later) & echo now'


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_output_after_the_exit_is_read_while_it_arrives(backend: str, executes: Any) -> None:
    result = _executor(executes, backend).run(BURSTS, timeout=30)
    assert (result.stdout, result.stderr, result.exit_code) == ('now\nlate\nlater\n', '', 0), \
        f'{backend}: output arriving after the exit, each part within the grace, was cut: {result!r}'


@pytest.mark.integration
@pytest.mark.parametrize('backend', ['local', 'delegate'])
def test_a_profile_background_job_does_not_hold_the_call(backend: str, executes: Any) -> None:
    home = _home_of(executes, backend)
    executor = _executor(executes, backend)
    command = 'echo out; echo err >&2; exit 3'
    quiet = timed(lambda: executor.run(command, timeout=30))
    (home / '.bash_profile').write_text(HOLDER)
    try:
        held = timed(lambda: executor.run(command, timeout=30))
    finally:
        kill_holder(home)
    assert (quiet[0].stdout, quiet[0].exit_code) == ('out\n', 3), quiet
    violations = held_violations(backend, quiet, held)
    assert violations == [], '\n'.join(violations)


# Delegate mode: `~`, relative paths and transfers mean the mapped user's home, never the server's.

def _not_mapped(what: str, got: str, want: Path, executes: Any) -> list[str]:
    if got == str(want):
        return []
    where = " (the server's HOME)" if got.startswith(str(executes.server_home)) else ''
    return [f'{what} resolved to {got!r}{where}, not the mapped home path {str(want)!r}']


@pytest.fixture
def delegated(executes: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """`executes`, with the server process's HOME set to its own home, apart from the mapped one."""
    monkeypatch.setenv('HOME', str(executes.server_home))
    return executes


@pytest.mark.integration
@pytest.mark.parametrize(('cwd', 'under'), [
    pytest.param('~', '', id='tilde'),
    pytest.param('~/sub dir', 'sub dir', id='tilde-sub-dir'),
    pytest.param('~name', '~name', id='tilde-name'),
])
def test_delegate_cwd_resolves_in_the_mapped_home(cwd: str, under: str, delegated: Any) -> None:
    for home in (delegated.mapped_home, delegated.server_home):
        (home / under).mkdir(exist_ok=True)
    result = _executor(delegated, 'delegate').run('printf %s "$PWD"', cwd=cwd, timeout=20)
    want = delegated.mapped_home / under if under else delegated.mapped_home
    violations = _not_mapped(f'cwd {cwd!r}', result.stdout, want, delegated)
    assert violations == [], '\n'.join(violations) + f'\n{result!r}'


@pytest.mark.integration
def test_delegate_starts_in_the_mapped_home(delegated: Any) -> None:
    result = _executor(delegated, 'delegate').run('printf %s "$PWD"', timeout=20)
    violations = _not_mapped('the working directory', result.stdout, delegated.mapped_home, delegated)
    assert violations == [], '\n'.join(violations) + f'\n{result!r}'


TRANSFERS = {
    # (operation, source, destination, where it must land under the mapped home)
    'put': ('put', '~/src file', '~/copy', 'copy'),
    'get': ('get', '~/src file', '~/copy', 'copy'),
    'into-directory': ('put', '~/src file', '~/sub dir', 'sub dir/src file'),
    'relative': ('get', 'src file', 'rel copy', 'rel copy'),
    'tilde-name': ('put', '~/src file', '~name/x', '~name/x'),
    'newline-name': ('get', '~/src file', '~/ends\n', 'ends\n'),
}


@pytest.mark.integration
@pytest.mark.parametrize('transfer', list(TRANSFERS))
def test_delegate_transfers_land_in_the_mapped_home(transfer: str, delegated: Any) -> None:
    op, source, destination, lands = TRANSFERS[transfer]
    for home, content in ((delegated.mapped_home, b'mapped\n'), (delegated.server_home, b'server\n')):
        (home / 'src file').write_bytes(content)
        (home / 'sub dir').mkdir()
        (home / '~name').mkdir()
    executor = _executor(delegated, 'delegate')
    try:
        returned = getattr(executor, op)(source, destination)
    except Exception as exc:
        returned = f'<raised {exc!r}>'
    want = delegated.mapped_home / lands
    violations = _not_mapped(f'{op}({source!r}, {destination!r})', returned, want, delegated)
    if not (want.is_file() and want.read_bytes() == b'mapped\n'):
        violations.append(f'{op} wrote nothing at {str(want)!r}, so the copy is not the mapped home path')
    if (delegated.server_home / lands).exists():
        violations.append(f"{op} wrote under the server's HOME, not the mapped home path")
    assert violations == [], '\n'.join(violations)


@pytest.mark.integration
def test_a_failed_delegate_transfer_raises(delegated: Any) -> None:
    with pytest.raises(RuntimeError, match='No such file'):
        _executor(delegated, 'delegate').put('~/missing', '~/copy')


@pytest.mark.integration
def test_local_transfers_return_the_absolute_destination(executes: Any, monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    monkeypatch.setenv('HOME', str(executes.server_home))
    monkeypatch.chdir(tmp_path)
    (executes.server_home / 'src').write_bytes(b'local\n')
    (executes.server_home / 'dir').mkdir()
    executor = LocalShellExecutor()
    put = executor.put('~/src', '~/dir')
    got = executor.get('~/src', 'rel copy')
    assert put == str(executes.server_home / 'dir' / 'src'), put
    assert Path(got).resolve() == (tmp_path / 'rel copy').resolve() and Path(got).is_absolute(), got
    assert (tmp_path / 'rel copy').read_bytes() == b'local\n'


class FakeConnection:
    """Stands in for `fabric.Connection`: records that it was built and opened, reaches no host."""

    made: list[FakeConnection]

    def __init__(self, host: str, **kwargs: Any) -> None:
        self.host = host
        self.kwargs = kwargs
        self.opened = 0
        self.is_connected = False
        self.made.append(self)

    def open(self) -> None:
        self.opened += 1
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('the fake connection runs nothing')

    def put(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('the fake connection transfers nothing')

    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError('the fake connection transfers nothing')


BACKENDS = {
    'ssh': lambda: SSHExecutor('cluster.invalid'),
    'local': LocalShellExecutor,
    'delegate': lambda: DelegatingExecutor('bob', USER_MAP),
}


@pytest.mark.unit
@pytest.mark.parametrize('backend', list(BACKENDS))
def test_backend_satisfies_executor_protocol(backend: str, monkeypatch: pytest.MonkeyPatch, guard) -> None:
    made: list[FakeConnection] = []
    monkeypatch.setattr(rcac_mcp.executor.ssh, 'Connection', type('FakeConnection', (FakeConnection,), {'made': made}))
    executor = BACKENDS[backend]()
    missing = [name for name in PROTOCOL_MEMBERS if not hasattr(executor, name)]
    assert isinstance(executor, Executor), f'{type(executor).__name__} does not satisfy Executor; missing {missing}'
    if backend == 'ssh':
        built = [(c.host, c.opened) for c in made]
        assert built == [('cluster.invalid', 1)], f'expected one fake connection opened once for the host: {built!r}'
    assert guard.refused == []
