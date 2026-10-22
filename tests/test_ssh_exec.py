# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The `ssh` executor against a loopback server that executes what it is sent, under each login shell.

`SSHExecutor` sends every command as the launcher's fixed bootstrap carrying one base64 word, so
whatever the user's login shell (bash, zsh, sh or tcsh here), the command runs under the pinned
login bash and the login shell parses no byte of it. The `integration` rows run it against
`tests/ssh_server.py`, which executes each exec string as `<login shell> -c <string>` inside
`bin/sandbox.sh`, from the user's home, with an sshd-like minimal environment, and serves SFTP over
the same files: the interpreter probe passes for every login shell; a command's stdin is empty and
its end is delivered at once; a timeout returns what was captured and `TIMEOUT_EXIT_CODE`, never an
exception; hostile values arrive literally; nothing a startup file prints reaches a result; a signal
death is reported as one; and `put`/`get` land exactly where named, with `~/` (and `~//`) meaning
the login home and a local name taken literally. A write's content goes by SFTP, never on a
command's stdin, to a private temporary file beside the target that a program moves into place:
byte-exact, atomic, the target's mode kept, a link followed, nothing left behind. No startup file,
the login bash's or the remote login shell's own, moves where a command starts, reads a write's
content or holds the call past the command's end records; the one documented exception, a remote
rc file's background job writing into a result, is pinned with the texts that state it. On a slow
or stalling link the call reads to the end records, however long they take (`ThrottledSSH`). The
server does not kill a process when its channel closes, as OpenSSH does not; it kills what is left
when the test ends.

The `unit` rows drive the channel loop (`_collect`) with a fake channel: an exit status that arrives
before the end of output is waited on, not spun on, and decides nothing; the read ends at both end
records; a command whose records have not come by the deadline is a timeout, naming its status if
that has arrived; and what a command prints after the TERM at its deadline is read. An exec string
over the per-argument limit is refused before connecting.
"""

from __future__ import annotations

import base64
import os
import stat
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

import argv_echo
from conftest import need_shell
from harness import Outcome, call, drive, in_memory, shared_server
from rcac_mcp.executor import TIMEOUT_EXIT_CODE, CommandResult, Executor, UserPath
from rcac_mcp.executor import launch, ssh
from rcac_mcp.executor.ssh import SSHExecutor
from rcac_mcp.tools.shell import run_command
from ssh_server import LoopbackSSH, ThrottledSSH
from test_executors import (LOGIN_MARKER, PROBE, WRITER, held_violations, timed, timeout_violations,
                            writer_violations)
from test_launch import HOSTILE, LEAVES_A_WRITER, SEPARATION_CASES, chatty_home

LOGIN_SHELLS = [pytest.param(shell, id=Path(shell).name) for shell in ('/bin/bash', '/bin/zsh', '/bin/sh', '/bin/tcsh')]


@pytest.fixture
def serve(tmp_path: Path) -> Iterator[Callable[..., LoopbackSSH]]:
    """Start loopback servers for the test (a home per name, under `tmp_path`); stop them after."""
    servers: list[LoopbackSSH] = []

    def start(shell: str, name: str = 'home') -> LoopbackSSH:
        need_shell(shell)
        home = (tmp_path / name).resolve()
        home.mkdir(exist_ok=True)
        servers.append(LoopbackSSH(home, shell, tmp_path))
        return servers[-1]

    try:
        yield start
    finally:
        for server in servers:
            server.close()


def _run(server: LoopbackSSH, *args: Any, **kwargs: Any) -> CommandResult:
    executor = server.executor()
    try:
        return executor.run(*args, **kwargs)
    finally:
        executor.close()


@pytest.mark.integration
@pytest.mark.parametrize('shell', LOGIN_SHELLS)
def test_bash_runs_whatever_the_login_shell(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    server = serve(shell)
    (Path(server.home) / '.bash_profile').write_text(LOGIN_MARKER)
    result = _run(server, PROBE, timeout=20)
    assert result.exit_code == 0, f'{shell}: the probe failed, so the command did not run under the pinned interpreter: {result!r}'


@pytest.mark.integration
@pytest.mark.parametrize('shell', LOGIN_SHELLS)
def test_stdin_is_empty_and_ends_at_once(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    server = serve(shell)
    result = _run(server, ['cat'], timeout=8)
    if result.exit_code == TIMEOUT_EXIT_CODE:
        pytest.fail(f'{shell}: cat did not return: the end of stdin (EOF) was not delivered')
    assert (result.stdout, result.exit_code) == ('', 0), f'{shell}: cat read {result!r:.200}: stdin was not empty'
    assert server.stdin_bytes == [[0]], f'{shell}: the channel carried stdin bytes: {server.stdin_bytes!r}'


# A program that never starts: the channel ends with no record, and its own status names the exit.
@pytest.mark.integration
def test_a_profile_that_exits_is_not_started(serve: Callable[..., LoopbackSSH]) -> None:
    server = serve('/bin/bash')
    (Path(server.home) / '.bash_profile').write_text('exit 7\n')
    result = _run(server, 'echo ran', timeout=20)
    note = 'cluster-mcp: the command did not start (exit 7); the server log has the details\n'
    assert (result.stdout, result.stderr, result.exit_code) == ('', note, launch.NOT_STARTED_EXIT_CODE), \
        f"the channel's status was not the one read: {result!r}"


# A command killed by a signal, over ssh too: the end record reports the signal (Paramiko reports
# only a lost status for a signal death), and the launch dies of it.
@pytest.mark.integration
@pytest.mark.parametrize('shell', ['/bin/bash', '/bin/zsh'])
def test_a_signal_death_is_reported(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    server = serve(shell)
    got = [_run(server, command, timeout=20) for command in ('echo before; kill -s TERM $$', ['/bin/sh', '-c', 'kill -s HUP $$'])]
    assert [(r.stdout, r.stderr, r.exit_code) for r in got] == [('before\n', '', -15), ('', '', -1)], \
        f'{shell}: a signal death was not reported as one: {got!r}'


@pytest.mark.integration
@pytest.mark.parametrize('shell', LOGIN_SHELLS)
def test_timeout_returns_the_captured_output(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    server = serve(shell)
    start = time.monotonic()
    try:
        result: CommandResult | BaseException = _run(server, 'echo part; echo perr >&2; sleep 30', timeout=1.5)
    except Exception as exc:
        result = exc
    violations = timeout_violations(result, time.monotonic() - start, 1.5 + 3)
    assert violations == [], f'{shell}: ' + '\n'.join(violations)


@pytest.mark.integration
@pytest.mark.parametrize('shell', LOGIN_SHELLS)
def test_values_arrive_literally(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    result = _run(serve(shell), ['printf', '%s\\0', *HOSTILE], timeout=20)
    want = ''.join(v + '\0' for v in HOSTILE)
    assert (result.stdout, result.stderr, result.exit_code) == (want, '', 0), \
        f'{shell}: a value reaches the command as {result.stdout!r}, not {want!r}; {result.stderr!r}'


# What each separation case returns from a quiet home, whatever the login shell.
QUIET = {
    'argv': ('a b\0c\0', '', 0),
    'exit': ('out\n', 'err\n', 4),
    'stdin': ('line one\nline two\n\0�', '', 0),
    'empty': ('', '', 0),
    'slow': ('slow\n', '', 0),
}


@pytest.mark.integration
@pytest.mark.parametrize('shell', LOGIN_SHELLS)
def test_startup_output_is_separated(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    quiet, chatty = serve(shell, 'quiet'), serve(shell, 'chatty')
    chatty_home(Path(chatty.home))
    violations = []
    for case, (command, stdin) in SEPARATION_CASES.items():
        if stdin is not None:
            # Content reaches a command only through a write; read it back.
            command = ['cat', '--', UserPath('~/separated')]
            for server in (quiet, chatty):
                executor = server.executor()
                try:
                    executor.write('~/separated', stdin)
                finally:
                    executor.close()
        want = _run(quiet, command, timeout=20)
        got = _run(chatty, command, timeout=20)
        if case in QUIET and (want.stdout, want.stderr, want.exit_code) != QUIET[case]:
            violations.append(f'{case}: the result differs from the quiet home\'s known result: {want!r}')
        if (got.stdout, got.stderr, got.exit_code) != (want.stdout, want.stderr, want.exit_code):
            violations.append(f'{case}: the result differs from the quiet home: {got!r:.300} != {want!r:.300}')
    assert violations == [], f'{shell}: ' + '\n'.join(violations)


# A startup file changes nothing about where a command starts, what it reads or how long it takes:
# neither the login bash's own (.bash_profile) nor the remote login shell's, which runs before the
# bootstrap (zsh's .zshenv).
STARTUP_FILES = {
    'bash-profile': ('/bin/bash', '.bash_profile'),
    'zshenv': ('/bin/zsh', '.zshenv'),
}


@pytest.mark.integration
@pytest.mark.parametrize('startup', list(STARTUP_FILES))
def test_a_startup_cd_does_not_move_the_start(startup: str, serve: Callable[..., LoopbackSSH]) -> None:
    shell, name = STARTUP_FILES[startup]
    server = serve(shell)
    home = Path(server.home)
    (home / 'elsewhere').mkdir()
    (home / 'rel').write_text('at the start\n')
    (home / 'elsewhere' / 'rel').write_text('where the startup file went\n')
    (home / name).write_text('cd "$HOME/elsewhere"\n')
    got = [_run(server, ['pwd'], timeout=20), _run(server, ['cat', UserPath('rel')], timeout=20)]
    want = [(f'{home}\n', '', 0), ('at the start\n', '', 0)]
    assert [(r.stdout, r.stderr, r.exit_code) for r in got] == want, \
        f'ssh-{startup}: a startup cd moved where the command started: {got!r}, not the login home'


# The remote login shell's own rc file, which runs before the bootstrap: zsh's .zshenv, and the
# .bashrc a bash built to read it for a shell sshd started reads (RHEL's, Debian's and macOS's are;
# the loopback server sets SSH_CLIENT, as sshd does). (login shell, rc file)
REMOTE_RCS = {
    'zshenv': ('/bin/zsh', '.zshenv'),
    'bashrc': ('/bin/bash', '.bashrc'),
}

# The remote login shell's rc file runs before the bootstrap, on the channel's own stdin: in the
# foreground, and from a background job, which zsh, unlike bash, sh and ksh, starts on the real stdin.
RC_READS = ('read -r line; printf %s "$line" > "$HOME/rc-read"\n'
            'head -c 1 > "$HOME/rc-took" &\n')


def _settled(path: Path, within: float = 5) -> None:
    deadline = time.monotonic() + within
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.mark.integration
@pytest.mark.parametrize('rc', list(REMOTE_RCS))
def test_a_remote_rc_never_reads_a_writes_content(rc: str, serve: Callable[..., LoopbackSSH], executes: Any) -> None:
    shell, name = REMOTE_RCS[rc]
    quiet, reader = serve(shell, 'quiet'), serve(shell, 'reader')
    home = Path(reader.home)
    (home / name).write_text(RC_READS)
    argv_echo.install(home, 'sbatch')
    violations = []
    # A command's stdin is empty, its end sent at once: the rc file reads nothing and the command runs.
    for case in ('argv', 'exit', 'empty'):
        command, _ = SEPARATION_CASES[case]
        want, got = _run(quiet, command, timeout=20), _run(reader, command, timeout=20)
        if (got.stdout, got.stderr, got.exit_code) != (want.stdout, want.stderr, want.exit_code):
            violations.append(f'{case}: the result differs from the quiet home: {got!r} != {want!r}')
    # A write's content goes by SFTP: the file holds every byte, and the rc file read none of it.
    sent = bytes(range(256)) * 625
    script = '#!/bin/bash\necho hi\n' * 4000
    executor = reader.executor()
    try:
        wrote = _tool(executor, 'write_file', {'path': '~/written', 'content': base64.b64encode(sent).decode(),
                                               'encoding': 'base64'})
        submitted = _tool(executor, 'sbatch', {'script_content': script, 'job_name': 'took'})
    finally:
        executor.close()
    if wrote.is_error or (home / 'written').read_bytes() != sent:
        violations.append(f'write_file: {wrote!r:.200}; the file holds {len((home / "written").read_bytes())} '
                          f'of {len(sent)} bytes, or other bytes')
    jobs = sorted((home / 'jobs').glob('took_*'))
    if submitted.is_error or len(jobs) != 1 or jobs[0].read_bytes() != script.encode():
        violations.append(f'sbatch: {submitted!r:.200}; job scripts {[j.name for j in jobs]!r}')
    _settled(home / 'rc-took')
    for seen in ('rc-read', 'rc-took'):
        if (home / seen).exists() and (home / seen).read_bytes() != b'':
            violations.append(f"the rc file read a write's content: {seen} holds {(home / seen).read_bytes()!r}")
    carried = [n for (n,) in reader.stdin_bytes if n]
    if carried:
        violations.append(f'a channel carried {carried!r} bytes of stdin: content rode a command\'s stdin')
    assert violations == [], '\n'.join(violations)


HOLDERS = {
    # (login shell, startup file, a background job that holds the output open)
    'bash-profile': ('/bin/bash', '.bash_profile', 'sleep 6 &\n'),
    'zshenv': ('/bin/zsh', '.zshenv', 'sleep 6 &!\n'),
}


@pytest.mark.integration
@pytest.mark.parametrize('startup', list(HOLDERS))
def test_a_startup_background_job_does_not_hold_the_call(startup: str, serve: Callable[..., LoopbackSSH]) -> None:
    shell, name, text = HOLDERS[startup]
    server = serve(shell)
    command = 'echo out; echo err >&2; exit 3'
    quiet = timed(lambda: _run(server, command, timeout=30))
    (Path(server.home) / name).write_text(text)
    held = timed(lambda: _run(server, command, timeout=30))
    assert (quiet[0].stdout, quiet[0].exit_code) == ('out\n', 3), quiet
    violations = held_violations(f'ssh-{startup}', quiet, held)
    assert violations == [], '\n'.join(violations)


# The documented exception to the separation: over SSH, a background job the remote login shell's
# own rc file starts, before anything is parked, can write into a result while the command runs
# (issues/startup-background-jobs.md). The code row shows it still holds; the text row that every
# text promising separation states it, so neither can change without the other.
LEAK = '(sleep 1; echo RC-LEAK-OUT; echo RC-LEAK-ERR >&2) &\n'
EXCEPTION = "a background job started by the remote login shell's own rc file"


@pytest.mark.integration
@pytest.mark.parametrize('rc', list(REMOTE_RCS))
def test_a_remote_rc_background_job_can_write_into_a_result(rc: str, serve: Callable[..., LoopbackSSH]) -> None:
    shell, name = REMOTE_RCS[rc]
    server = serve(shell)
    (Path(server.home) / name).write_text(LEAK)
    result = _run(server, 'sleep 2; echo command-output', timeout=20)
    assert (result.stdout, result.stderr, result.exit_code) == ('RC-LEAK-OUT\ncommand-output\n', 'RC-LEAK-ERR\n', 0), \
        (f'the documented exception no longer holds: {result!r}; the texts that state it (AGENTS.md §3, '
         'README.md, the run_command and Executor docstrings) must change with the code')


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


# What a caller is told about a write's content over ssh (the rows above and below).
CONTENT_RULE = 'by SFTP to a private temporary file'


@pytest.mark.unit
def test_content_texts_state_the_sftp_route() -> None:
    root = Path(__file__).resolve().parent.parent
    texts = {
        'AGENTS.md §2': _section((root / 'AGENTS.md').read_text(), '### 2. ', '### 3. '),
        'README.md': (root / 'README.md').read_text(),
        'the Executor.write docstring': Executor.write.__doc__ or '',
    }
    missing = [name for name, text in texts.items() if CONTENT_RULE not in ' '.join(text.split())]
    assert missing == [], f'a text describing content over ssh does not state its route ({CONTENT_RULE!r}): {missing}'


@pytest.mark.unit
def test_separation_texts_state_the_remote_rc_exception() -> None:
    root = Path(__file__).resolve().parent.parent
    texts = {
        'AGENTS.md §3': _section((root / 'AGENTS.md').read_text(), '### 3. ', '### 4. '),
        'README.md': (root / 'README.md').read_text(),
        'the run_command description': run_command.description or '',
        'the Executor docstring': Executor.__doc__ or '',
    }
    missing = [name for name, text in texts.items() if EXCEPTION not in ' '.join(text.split())]
    assert missing == [], f'a text that promises separation does not state the exception ({EXCEPTION!r}): {missing}'


TRANSFERS = {
    # (operation, remote, local, where the copy must land: ('home' | 'local', relative path))
    'put-tilde': ('put', '~/x', 'local src', ('home', 'x')),
    'put-tilde-double-slash': ('put', '~//x', 'local src', ('home', 'x')),
    'put-into-directory': ('put', '~/dir', 'local src', ('home', 'dir/local src')),
    'get-tilde-double-slash': ('get', '~//there', 'got', ('local', 'got')),
    'get-brace-local-name': ('get', 'there', '{host}.out', ('local', '{host}.out')),
    'get-local-tilde': ('get', '~/there', '~/got', ('server', 'got')),
}


@pytest.mark.integration
@pytest.mark.parametrize('transfer', list(TRANSFERS))
def test_transfers_land_where_named(transfer: str, serve: Callable[..., LoopbackSSH], tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    op, remote, local, (side, lands) = TRANSFERS[transfer]
    server = serve('/bin/bash')
    home = Path(server.home)
    work = (tmp_path / 'work').resolve()
    server_home = (tmp_path / 'server-home').resolve()
    for path in (work, server_home, home / 'dir'):
        path.mkdir()
    monkeypatch.setenv('HOME', str(server_home))
    monkeypatch.chdir(work)
    (work / 'local src').write_bytes(b'from the server\n')
    (home / 'there').write_bytes(b'from the cluster\n')
    want = {'home': home, 'local': work, 'server': server_home}[side] / lands
    executor = server.executor()
    try:
        if op == 'put':
            returned = executor.put(str(work / local), remote)
        else:
            returned = executor.get(remote, local)
        detail = ''
    except Exception as exc:
        returned, detail = None, f'it raised {exc!r}'
    finally:
        executor.close()
    content = b'from the server\n' if op == 'put' else b'from the cluster\n'
    violations = []
    if not (want.is_file() and want.read_bytes() == content):
        violations.append(f'{op}({remote!r}, {local!r}) did not land at {str(want)!r} {detail}')
    elif returned is None or Path(returned).resolve() != want.resolve() or not Path(returned).is_absolute():
        violations.append(f'{op}({remote!r}, {local!r}) returned {returned!r}, not the destination {str(want)!r}')
    assert violations == [], '\n'.join(violations)


# A result is complete exactly when both end records have arrived, however slow the link. On a slow
# link sshd sends the exit status as soon as the command exits, while up to a window of its output
# is still queued behind it: the status decides nothing, and the call reads on to the records.
# Each rate: (bytes a second, bytes read). At the slowest the whole output is queued at the exit; at
# 1 MiB/s a full window (2 MiB) is, two seconds of it; at 4 MiB/s the window drains within a second.
SLOW_LINKS = {
    '256KiBps': (256 * 1024, 640 * 1024),
    '1MiBps': (1024 * 1024, 3 * 1024 * 1024),
    '4MiBps': (4 * 1024 * 1024, 6 * 1024 * 1024),
}

# Links that stall, and links that deliver whole 32 KiB packets slowly: (rate in bytes a second,
# bytes read, packet size, a stall right after the exit status, a stall once so many bytes have gone).
STALLING_LINKS = {
    'stall-1.6s-after-exit': (512 * 1024, 1024 * 1024, 8192, 1.6, None),
    'stall-1.2s-after-exit': (512 * 1024, 1024 * 1024, 8192, 1.2, None),
    'stall-5s-mid-result': (512 * 1024, 1024 * 1024, 8192, 0.0, (256 * 1024, 5.0)),
    'packets-6000Bps': (6000, 48 * 1024, 32 * 1024, 1.6, None),
    'packets-24000Bps': (24000, 256 * 1024, 32 * 1024, 1.6, None),
}


def _numbered(size: int) -> bytes:
    """`size` bytes of numbered lines, so a cut anywhere shows."""
    lines = b''.join(b'%011d\n' % i for i in range(size // 12 + 1))
    return lines[:size]


@pytest.fixture
def slow(tmp_path: Path) -> Iterator[Callable[..., ThrottledSSH]]:
    """Start slow-link servers for the test (a home per name, under `tmp_path`); stop them after."""
    servers: list[ThrottledSSH] = []

    def start(shell: str, rate: int, name: str = 'home', **link: Any) -> ThrottledSSH:
        need_shell(shell)
        home = (tmp_path / name).resolve()
        home.mkdir(exist_ok=True)
        servers.append(ThrottledSSH(home, shell, tmp_path, rate, **link))
        return servers[-1]

    try:
        yield start
    finally:
        for server in servers:
            server.close()


def _tool(executor: Executor, tool: str, args: dict[str, Any]) -> Outcome:
    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor)) as client:
            return await call(client, tool, args)
    return drive(scenario(), timeout=120)


@pytest.mark.integration
@pytest.mark.parametrize('link', list(SLOW_LINKS))
def test_a_slow_link_returns_the_whole_file(link: str, slow: Callable[..., ThrottledSSH], executes: Any) -> None:
    rate, size = SLOW_LINKS[link]
    server = slow('/bin/bash', rate)
    data = _numbered(size)
    (Path(server.home) / 'big.txt').write_bytes(data)
    executor = server.executor()
    try:
        outcome = _tool(executor, 'read_file', {'path': '~/big.txt'})
    finally:
        executor.close()
    got = outcome.text.encode()
    assert (outcome.is_error, len(got), got == data) == (False, size, True), \
        (f'{link}: read_file returned {len(got)} of {size} bytes (error: {outcome.is_error}): the result was cut '
         f'before its end records, while the output was still arriving; it ends {got[-40:]!r}')


@pytest.mark.integration
@pytest.mark.parametrize('link', list(STALLING_LINKS))
def test_a_stalling_link_returns_the_whole_file(link: str, slow: Callable[..., ThrottledSSH], executes: Any) -> None:
    rate, size, piece, after_exit, stall_at = STALLING_LINKS[link]
    server = slow('/bin/bash', rate, piece=piece, stall_after_exit=after_exit, stall_at=stall_at)
    data = _numbered(size)
    (Path(server.home) / 'big.txt').write_bytes(data)
    executor = server.executor()
    try:
        outcome = _tool(executor, 'read_file', {'path': '~/big.txt'})
    finally:
        executor.close()
    got = outcome.text.encode()
    assert (outcome.is_error, len(got), got == data) == (False, size, True), \
        (f'{link}: read_file returned {len(got)} of {size} bytes (error: {outcome.is_error}): the result was cut '
         f'before its end records, on a link that stalled or delivered whole packets slowly; it ends {got[-40:]!r}')


# The double-submit case: a chatty remote rc file (40 KB) ahead of the program on a slow link of
# whole 32 KiB packets that stalls after the exit status. The sentinel arrives seconds after sshd
# sent the exit status; a submission that ran must read as submitted, never as "did not start".
@pytest.mark.integration
def test_a_job_that_ran_is_never_reported_as_not_started(slow: Callable[..., ThrottledSSH], executes: Any) -> None:
    server = slow('/bin/bash', 6000, piece=32 * 1024, stall_after_exit=1.6)
    home = Path(server.home)
    (home / '.bashrc').write_text("head -c 40000 /dev/zero | tr '\\0' z\n")
    argv_echo.install(home, 'sbatch')
    (home / 'job.sh').write_text('#!/bin/bash\necho hi\n')
    executor = server.executor()
    try:
        outcome = _tool(executor, 'sbatch', {'script_path': '~/job.sh'})
    finally:
        executor.close()
    calls = argv_echo.calls(home)
    assert (outcome.is_error, outcome.text, len(calls)) == (False, argv_echo.JOB_ID, 1), \
        (f'a submission that ran read as {outcome!r:.300}; sbatch was called {len(calls)} times: the result was cut '
         'before its end records')


# The timeout bounds a call whose records do not come: the command exited at once (its status is
# in), but its output, and its end records behind it, are still queued on a slow link at the
# deadline. The call is a timeout, with the output read so far and a note naming the status.
@pytest.mark.integration
def test_a_timeout_before_the_records_names_the_status(slow: Callable[..., ThrottledSSH]) -> None:
    server = slow('/bin/bash', 64 * 1024)
    data = _numbered(1024 * 1024)
    (Path(server.home) / 'big.txt').write_bytes(data)
    result = _run(server, ['cat', '--', UserPath('~/big.txt')], timeout=2)
    got = result.stdout.encode()
    assert result.exit_code == TIMEOUT_EXIT_CODE, f'the call ended {result.exit_code}, not as a timeout'
    assert 0 < len(got) < len(data) and data.startswith(got), f'the output read so far was lost: {len(got)} bytes'
    assert result.stderr.endswith(launch.LATE_NOTE.format(code=0)), \
        f'the timeout does not name the status that had arrived: stderr {result.stderr[-200:]!r}'


@pytest.mark.integration
def test_a_slow_link_returns_both_streams_whole(slow: Callable[..., ThrottledSSH]) -> None:
    rate, size = SLOW_LINKS['256KiBps']
    server = slow('/bin/bash', rate)
    data = _numbered(size)
    (Path(server.home) / 'big.txt').write_bytes(data)
    result = _run(server, 'cat big.txt; echo DONE >&2', timeout=60)
    got = result.stdout.encode()
    assert (len(got), got == data, result.stderr, result.exit_code) == (size, True, 'DONE\n', 0), \
        (f'the result was cut before its end records, while the output was still arriving: stdout {len(got)} of {size} bytes, '
         f'stderr {result.stderr!r}, exit code {result.exit_code}')


# A chatty remote rc file (1 MiB, as the suite's chatty home prints) ahead of the program on a slow
# link: the sentinel arrives seconds after sshd sent the exit status of a short command.
CHATTY_RC = "head -c 1048576 /dev/zero | tr '\\0' z\n"


@pytest.mark.integration
@pytest.mark.parametrize('rc', list(REMOTE_RCS))
def test_a_late_sentinel_still_counts_as_started(rc: str, slow: Callable[..., ThrottledSSH]) -> None:
    shell, name = REMOTE_RCS[rc]
    server = slow(shell, 512 * 1024)
    home = Path(server.home)
    (home / name).write_text(CHATTY_RC)
    ran = _run(server, ['touch', '--', UserPath('~/ran')], timeout=60)
    sent = b'line one\nline two\n\0\xff'
    executor = server.executor()
    try:
        wrote = executor.write('~/written', sent)
    finally:
        executor.close()
    violations = []
    if (ran.stdout, ran.stderr, ran.exit_code) != ('', '', 0) or not (home / 'ran').exists():
        violations.append(f'a command that ran was reported as {ran!r} (~/ran exists: {(home / "ran").exists()})')
    if (wrote.stdout, wrote.stderr, wrote.exit_code) != ('', '', 0) or (home / 'written').read_bytes() != sent:
        violations.append(f'a late start lost the write: {wrote!r}')
    assert violations == [], ('a late sentinel was not counted as started (the result was cut before its end records): '
                              + '\n'.join(violations))


@pytest.mark.integration
@pytest.mark.parametrize('shell', ['/bin/bash', '/bin/zsh'])
def test_a_background_writer_does_not_hold_the_call(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    server = serve(shell)
    result, elapsed = timed(lambda: _run(server, WRITER, timeout=10))
    violations = writer_violations('ssh', result, elapsed)
    later, later_s = timed(lambda: _run(server, LEAVES_A_WRITER, timeout=20))
    if (later.stdout, later.stderr, later.exit_code) != ('now\n', '', 0) or later_s > 2:
        violations.append(f'ssh: what a background job printed after the end reached the result, or held the call '
                          f'{later_s:.1f} s: {later!r}')
    assert violations == [], '\n'.join(violations)


# Writes over ssh: the content goes by SFTP into a private directory beside the target, and a
# program moves it into place.

def _write(server: LoopbackSSH, *args: Any, **kwargs: Any) -> CommandResult:
    executor = server.executor()
    try:
        return executor.write(*args, **kwargs)
    finally:
        executor.close()


def _staging_left(*directories: Path) -> list[str]:
    return [str(p) for d in directories for p in d.iterdir() if p.name.startswith(ssh.STAGE_PREFIX)]


@pytest.mark.integration
def test_an_ssh_write_replaces_the_file_whole_and_keeps_its_mode(serve: Callable[..., LoopbackSSH]) -> None:
    server = serve('/bin/bash')
    home = Path(server.home)
    target = home / 'kept.sh'
    target.write_bytes(b'old content\n')
    target.chmod(0o750)
    before = target.stat().st_ino
    with open(target, 'rb') as reader:
        result = _write(server, '~/kept.sh', b'new content, no final newline')
        old_view = reader.read()
    violations = []
    if (result.stdout, result.stderr, result.exit_code) != ('', '', 0):
        violations.append(f'the write returned {result!r}')
    if target.read_bytes() != b'new content, no final newline':
        violations.append(f'the file holds {target.read_bytes()!r}')
    if target.stat().st_ino == before or old_view != b'old content\n':
        violations.append('the file was rewritten in place, not replaced whole: a reader saw '
                          f'{old_view!r} through the old file')
    if stat.S_IMODE(target.stat().st_mode) != 0o750:
        violations.append(f'the file lost its mode: {stat.S_IMODE(target.stat().st_mode):o}, not 750')
    umask = int(_run(server, 'umask', timeout=20).stdout, 8)
    fresh = _write(server, '~/fresh', b'x')
    if fresh.exit_code != 0 or stat.S_IMODE((home / 'fresh').stat().st_mode) != 0o666 & ~umask:
        violations.append(f'a new file has mode {stat.S_IMODE((home / "fresh").stat().st_mode):o}, not what '
                          f'`cat >` gives under umask {umask:o}: {fresh!r}')
    if _staging_left(home):
        violations.append(f'a write left its staging behind: {_staging_left(home)!r}')
    assert violations == [], '\n'.join(violations)


@pytest.mark.integration
def test_an_ssh_write_stages_its_content_privately(serve: Callable[..., LoopbackSSH],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve('/bin/bash')
    home = Path(server.home)
    (home / 'sub').mkdir()
    seen: list[tuple[str, int, int, str]] = []
    upload = ssh._upload

    def watched(sftp: Any, staged: str, data: bytes) -> None:
        upload(sftp, staged, data)
        path = home / staged
        seen.append((str(path.parent.parent), stat.S_IMODE(path.parent.stat().st_mode), stat.S_IMODE(path.stat().st_mode),
                     path.parent.name))
    monkeypatch.setattr(ssh, '_upload', watched)
    first, second = _write(server, '~/sub/a', b'one'), _write(server, '~/sub/a', b'two')
    assert (first.exit_code, second.exit_code, (home / 'sub' / 'a').read_bytes()) == (0, 0, b'two'), (first, second)
    assert [(where, d, f) for where, d, f, _ in seen] == [(str(home / 'sub'), 0o700, 0o600)] * 2, \
        f'the content was not staged privately beside the target: {seen!r}'
    assert len({name for *_, name in seen}) == 2, f'two writes shared a staging name: {seen!r}'


@pytest.mark.integration
def test_an_ssh_write_follows_a_link(serve: Callable[..., LoopbackSSH]) -> None:
    server = serve('/bin/bash')
    home = Path(server.home)
    (home / 'real').mkdir()
    (home / 'real' / 'file').write_bytes(b'old\n')
    (home / 'link').symlink_to(home / 'real' / 'file')
    result = _write(server, '~/link', b'through the link\n')
    assert result.exit_code == 0, result
    assert (home / 'link').is_symlink() and (home / 'real' / 'file').read_bytes() == b'through the link\n', \
        'the write replaced the link instead of writing the file it names'
    assert _staging_left(home, home / 'real') == [], 'a write left its staging behind'


@pytest.mark.integration
def test_an_ssh_write_into_a_closed_directory_writes_through(serve: Callable[..., LoopbackSSH]) -> None:
    server = serve('/bin/bash')
    home = Path(server.home)
    closed = home / 'closed'
    closed.mkdir()
    (closed / 'file').write_bytes(b'old\n')
    (closed / 'file').chmod(0o640)
    closed.chmod(0o555)
    try:
        result = _write(server, '~/closed/file', b'new\n')
        appended = _write(server, '~/closed/file', b'more\n', append=True)
        missing = None
        try:
            missing = _write(server, '~/closed/other', b'x')
        except OSError as exc:
            missing = exc
    finally:
        closed.chmod(0o755)
    assert (result.exit_code, appended.exit_code) == (0, 0), (result, appended)
    assert (closed / 'file').read_bytes() == b'new\nmore\n'
    assert stat.S_IMODE((closed / 'file').stat().st_mode) == 0o640
    assert isinstance(missing, PermissionError) and not (closed / 'other').exists(), missing
    assert _staging_left(home, closed) == [], 'a write left its staging behind'


@pytest.mark.integration
def test_a_failed_ssh_write_leaves_nothing_behind(serve: Callable[..., LoopbackSSH],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve('/bin/bash')
    home = Path(server.home)
    (home / 'a dir').mkdir()
    (home / 'file').write_bytes(b'unchanged\n')
    violations = []
    # The target is a directory: refused before anything is staged.
    try:
        _write(server, '~/a dir', b'x')
        violations.append('a write to a directory was not refused')
    except IsADirectoryError:
        pass
    # The transfer fails part way.
    upload = ssh._upload

    def broken(sftp: Any, staged: str, data: bytes) -> None:
        upload(sftp, staged, data[:3])
        raise OSError('the link dropped')
    monkeypatch.setattr(ssh, '_upload', broken)
    try:
        _write(server, '~/file', b'replacement')
        violations.append('a failed transfer was not reported')
    except OSError:
        pass
    monkeypatch.setattr(ssh, '_upload', upload)
    # The placing program does not start (a profile that exits).
    (home / '.bash_profile').write_text('exit 0\n')
    result = _write(server, '~/file', b'replacement')
    if result.exit_code != launch.NOT_STARTED_EXIT_CODE:
        violations.append(f'a write whose program never started returned {result!r}')
    if (home / 'file').read_bytes() != b'unchanged\n':
        violations.append(f'a failed write changed the file: {(home / "file").read_bytes()!r}')
    if _staging_left(home, home / 'a dir'):
        violations.append(f'a failed write left its staging behind: {_staging_left(home, home / "a dir")!r}')
    assert violations == [], '\n'.join(violations)


# ---------------------------------------------------------------------------------------------
# The channel loop (unit), against a fake channel.

class FakeChannel:
    """A channel whose exit status, output and end of output arrive at set times after creation."""

    def __init__(self, status: int, status_at: float, eof_at: float | None, data: bytes = b'out',
                 err: bytes = b'') -> None:
        self.start = time.monotonic()
        self.status, self.status_at, self.eof_at = status, status_at, eof_at
        self.data, self.err = data, err
        self.polls = 0
        self.closed = False

    def _now(self) -> float:
        return time.monotonic() - self.start

    @property
    def eof_received(self) -> bool:
        return self.eof_at is not None and self._now() >= self.eof_at

    def recv_ready(self) -> bool:
        self.polls += 1
        return bool(self.data)

    def recv(self, n: int) -> bytes:
        data, self.data = self.data[:n], self.data[n:]
        return data

    def recv_stderr_ready(self) -> bool:
        return bool(self.err)

    def recv_stderr(self, n: int) -> bytes:
        err, self.err = self.err[:n], self.err[n:]
        return err

    def exit_status_ready(self) -> bool:
        return self._now() >= self.status_at

    def recv_exit_status(self) -> int:
        return self.status if self.exit_status_ready() else -1

    def fileno(self) -> int:
        raise AssertionError('the loop selected on a channel whose exit status had arrived')


# A program's end nonce, and its records.
END = b'0123456789abcdeffedcba9876543210'
RECORD = END + b'x0.'



@pytest.mark.unit
def test_drain_waits_instead_of_spinning() -> None:
    # The exit status arrives at once; the end of output 0.5 s later.
    channel = FakeChannel(7, 0.0, 0.5)
    out, err, code, note = ssh._collect(channel, 5)
    assert (out, err, code, note) == (b'out', b'', 7, b''), (out, err, code, note)
    assert channel.polls < 100, f'the loop busy-waited: {channel.polls} polls in 0.5 s'


@pytest.mark.unit
def test_the_read_ends_at_both_records() -> None:
    # Both records are in at once; the output never ends and no exit status comes: a background job
    # holds the channel. The read ends at the records, at once.
    channel = FakeChannel(0, 60.0, None, b'out' + RECORD + b'late', RECORD + b'late-err')
    start = time.monotonic()
    out, err, code, note = ssh._collect(channel, 30, END)
    assert (out, err, note) == (b'out' + RECORD + b'late', RECORD + b'late-err', b''), (out, err, note)
    assert time.monotonic() - start < 1, 'the read went on past both end records'
    assert code == -1, f'with no exit status in, the status is unknown (-1), not {code}: the record carries it'


@pytest.mark.unit
def test_one_record_is_not_the_end() -> None:
    # stdout's record is in and the exit status too; stderr's record never comes: the call reads on
    # to its deadline, a timeout naming the status that arrived.
    channel = FakeChannel(0, 0.0, None, b'out' + RECORD, b'err')
    out, err, code, note = ssh._collect(channel, 0.5, END)
    assert (out, err, code) == (b'out' + RECORD, b'err', TIMEOUT_EXIT_CODE), (out, err, code)
    assert note == launch.LATE_NOTE.format(code=0).encode(), note


@pytest.mark.unit
def test_a_command_whose_records_have_not_come_by_the_deadline_is_a_timeout() -> None:
    # The command exited (its status is in), but its records are still queued on the link at the
    # deadline: a timeout, with the output read so far and a note naming the command's status.
    channel = FakeChannel(0, 0.0, None)
    start = time.monotonic()
    out, err, code, note = ssh._collect(channel, 0.3, END)
    assert (out, code) == (b'out', TIMEOUT_EXIT_CODE), (out, code)
    assert note == launch.LATE_NOTE.format(code=0).encode() and time.monotonic() - start < 2, note


class TrickleChannel(FakeChannel):
    """A channel whose exit status is in at once, and whose output arrives a piece every `every` s."""

    def __init__(self, pieces: list[bytes], every: float) -> None:
        super().__init__(0, 0.0, every * (len(pieces) + 0.5), b'')
        self.pieces, self.every, self.released = pieces, every, 0

    def recv_ready(self) -> bool:
        self.polls += 1
        due = min(len(self.pieces), int(self._now() / self.every))
        self.data += b''.join(self.pieces[self.released:due])
        self.released = max(self.released, due)
        return bool(self.data)


@pytest.mark.unit
def test_output_after_the_exit_status_is_read_to_the_end() -> None:
    # The exit status arrives at once; the output follows a piece every 0.4 s for 2.4 s, its record
    # last; stderr's record is in at once. The channel's output never ends.
    pieces = [b'%d\n' % i for i in range(6)] + [RECORD]
    channel = TrickleChannel(pieces, 0.4)
    channel.eof_at = None
    channel.err = RECORD
    out, err, code, note = ssh._collect(channel, 30, END)
    assert (out, code, note) == (b''.join(pieces), 0, b''), \
        f'output arriving after the exit status was cut: {out!r} of {b"".join(pieces)!r}, note {note!r}'


@pytest.mark.unit
def test_a_lost_status_stays_minus_one() -> None:
    channel = FakeChannel(-1, 0.0, 0.0)
    channel.closed = True
    assert ssh._collect(channel, 5) == (b'out', b'', -1, b'')


class LateChannel:
    """A channel that has no exit status and whose output arrives only after `data_at`: what a command
    prints as the TERM at the deadline stops it. Its descriptor never becomes readable."""

    def __init__(self, data_at: float, data: bytes) -> None:
        self.start = time.monotonic()
        self.data_at, self.data = data_at, data
        self._read, self._write = os.pipe()

    def now(self) -> float:
        return time.monotonic() - self.start

    @property
    def closed(self) -> bool:
        return self.now() >= self.data_at + 0.05

    @property
    def eof_received(self) -> bool:
        return self.closed

    def recv_ready(self) -> bool:
        return self.now() >= self.data_at and bool(self.data)

    def recv(self, n: int) -> bytes:
        data, self.data = self.data[:n], self.data[n:]
        return data

    def recv_stderr_ready(self) -> bool:
        return False

    def recv_stderr(self, n: int) -> bytes:
        return b''

    def exit_status_ready(self) -> bool:
        return False

    def recv_exit_status(self) -> int:
        return -1

    def fileno(self) -> int:
        return self._read

    def release(self) -> None:
        os.close(self._read)
        os.close(self._write)


@pytest.mark.unit
def test_a_timeout_drains_what_follows_the_term(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = LateChannel(0.5, b'stopping\n')
    sent: list[tuple[str, float]] = []
    monkeypatch.setattr(ssh, '_signal', lambda chan, name: sent.append((name, channel.now())))
    try:
        out, err, code, _ = ssh._collect(channel, 0.3)
    finally:
        channel.release()
    assert [name for name, _ in sent] == ['TERM'] and sent[0][1] < 0.5, sent
    assert (out, code) == (b'stopping\n', TIMEOUT_EXIT_CODE), \
        f'the timeout lost what followed the TERM: {out!r}, exit code {code}'


@pytest.mark.unit
def test_an_oversize_program_is_refused_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    # A script whose program fits a local launch but whose SSH exec string, bootstrap and base64 word
    # included, is over Linux's 131,072-byte per-argument limit.
    executor = object.__new__(SSHExecutor)
    executor._host, executor._conn, executor._connect_kwargs = 'stub', None, {}
    reached: list[str] = []
    monkeypatch.setattr(SSHExecutor, '_ensure_connected', lambda self: reached.append('connect') or pytest.fail(
        'a program over the per-argument limit reached the connection instead of a ValueError naming it'))
    script = '#' * (98_200 - len(ssh.launch.program('')[0]))
    with pytest.raises(ValueError, match='131072-byte per-argument limit'):
        executor.run(script)
    assert reached == []
