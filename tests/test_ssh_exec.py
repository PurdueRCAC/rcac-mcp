# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The `ssh` executor against a loopback server that executes what it is sent, under each login shell.

`SSHExecutor` sends every command as the launcher's fixed bootstrap carrying one base64 word, so
whatever the user's login shell (bash, zsh, sh or tcsh here), the command runs under the pinned
login bash and the login shell parses no byte of it. The `integration` rows run it against
`tests/ssh_server.py`, which executes each exec string as `<login shell> -c <string>` inside
`bin/sandbox.sh`, from the user's home, with an sshd-like minimal environment, and serves SFTP over
the same files: the interpreter probe passes for every login shell; stdin arrives byte for byte and
its end is delivered; a timeout returns what was captured and `TIMEOUT_EXIT_CODE`, never an
exception; hostile values arrive literally; nothing a startup file prints reaches a result; and
`put`/`get` land exactly where named, with `~/` (and `~//`) meaning the login home and a local name
taken literally. No startup file, the login bash's or the remote login shell's own, moves where a
command starts, reads the caller's stdin (it is sent only once the program has started) or holds the
call open; the one documented exception, a remote rc file's background job writing into a result,
is pinned with the texts that state it. The server does not kill a process when its channel closes,
as OpenSSH does not; it kills what is left when the test ends.

The `unit` rows drive the channel loop (`_collect`) with a fake channel: an exit status that arrives
before the end of output is waited on, not spun on, a command that exited before the deadline keeps
its own status, and what a command prints after the TERM at its deadline is read. An exec string
over the per-argument limit is refused before connecting.
"""

from __future__ import annotations

import os
import threading
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
from test_launch import HOSTILE, SEPARATION_CASES, chatty_home

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
def test_stdin_arrives_exactly_and_closes(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    data = b'line one\r\nno final newline \x00 \xc3\xa9' * 5000
    result = _run(serve(shell), ['cat'], stdin=data, timeout=8)
    if result.exit_code == TIMEOUT_EXIT_CODE:
        pytest.fail(f'{shell}: cat did not return: the end of stdin (EOF) was not delivered: {result.stdout[:80]!r}')
    assert (result.stdout.encode(), result.exit_code) == (data, 0), f'{shell}: cat read {result!r:.200}, not the stdin sent'


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
}


@pytest.mark.integration
@pytest.mark.parametrize('shell', LOGIN_SHELLS)
def test_startup_output_is_separated(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    quiet, chatty = serve(shell, 'quiet'), serve(shell, 'chatty')
    chatty_home(Path(chatty.home))
    violations = []
    for case, (command, stdin) in SEPARATION_CASES.items():
        want = _run(quiet, command, stdin=stdin, timeout=20)
        got = _run(chatty, command, stdin=stdin, timeout=20)
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

# The remote login shell's rc file runs before the bootstrap, on the channel's own stdin.
RC_READS = 'read -r line; printf %s "$line" > "$HOME/rc-read"\n'


@pytest.mark.integration
@pytest.mark.parametrize('rc', list(REMOTE_RCS))
def test_a_remote_rc_never_reads_the_callers_stdin(rc: str, serve: Callable[..., LoopbackSSH]) -> None:
    shell, name = REMOTE_RCS[rc]
    quiet, reader = serve(shell, 'quiet'), serve(shell, 'reader')
    rc_read = Path(reader.home) / 'rc-read'
    (Path(reader.home) / name).write_text(RC_READS)
    violations = []
    # With no stdin to send, its end goes at once: the rc file reads nothing and the command runs.
    for case in ('argv', 'exit', 'empty'):
        command, _ = SEPARATION_CASES[case]
        want, got = _run(quiet, command, timeout=20), _run(reader, command, timeout=20)
        if (got.stdout, got.stderr, got.exit_code) != (want.stdout, want.stderr, want.exit_code):
            violations.append(f'{case}: the result differs from the quiet home: {got!r} != {want!r}')
    # With stdin to send, nothing goes before the program has started, so an rc file that reads
    # stdin waits: the call ends at its timeout, no byte of the caller's stdin reached the rc file,
    # and the command never ran, so it wrote nothing, not even once the rc file's read ended.
    rc_read.unlink(missing_ok=True)
    written = Path(reader.home) / 'written'
    sent = b'line one\nline two\n'
    got = _run(reader, ['/bin/sh', '-c', 'cat > "$HOME/written"'], stdin=sent, timeout=2)
    if (got.stdout, got.stderr, got.exit_code) != ('', '', TIMEOUT_EXIT_CODE):
        violations.append(f"the rc file consumed the caller's stdin: the write returned {got!r}, "
                          'not a timeout with nothing sent')
    deadline = time.monotonic() + 5
    while not rc_read.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(1)
    if rc_read.exists() and rc_read.read_bytes() != b'':
        violations.append(f"the rc file consumed the caller's stdin: it read {rc_read.read_bytes()!r}")
    if not rc_read.exists():
        violations.append("the rc file's read never ended once the channel closed")
    if written.exists():
        violations.append(f'the timed-out call wrote partial content: {written.read_bytes()!r}')
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


# What a caller is told about stdin over ssh when the remote rc file reads it (the row above).
STDIN_RULE = 'a call that sends stdin times out and never writes partial content'


@pytest.mark.unit
def test_stdin_texts_state_the_rc_timeout() -> None:
    root = Path(__file__).resolve().parent.parent
    texts = {
        'AGENTS.md §2': _section((root / 'AGENTS.md').read_text(), '### 2. ', '### 3. '),
        'README.md': (root / 'README.md').read_text(),
        'the Executor.run docstring': Executor.run.__doc__ or '',
    }
    missing = [name for name, text in texts.items() if STDIN_RULE not in ' '.join(text.split())]
    assert missing == [], f'a text describing stdin over ssh does not state the rc timeout ({STDIN_RULE!r}): {missing}'
    # Each also states the bound on the wait for the start, as the code has it.
    bound = f'{ssh.START_TIMEOUT:g} s'
    unbounded = [name for name, text in texts.items()
                 if bound not in text or 'with nothing arriving' not in ' '.join(text.split())]
    assert unbounded == [], f'a text describing stdin over ssh does not state the start bound ({bound}): {unbounded}'


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


# A result is complete unless only holders remain. On a slow link sshd sends the exit status as soon
# as the command exits, while up to a window of its output is still queued behind it: the call keeps
# reading while that output arrives, and stops early only once nothing has arrived for the grace.
# Each rate: (bytes a second, bytes read). At the slowest the whole output is queued at the exit; at
# 1 MiB/s a full window (2 MiB) is, two seconds of it; at 4 MiB/s the window drains within a second.
SLOW_LINKS = {
    '256KiBps': (256 * 1024, 640 * 1024),
    '1MiBps': (1024 * 1024, 3 * 1024 * 1024),
    '4MiBps': (4 * 1024 * 1024, 6 * 1024 * 1024),
}


def _numbered(size: int) -> bytes:
    """`size` bytes of numbered lines, so a cut anywhere shows."""
    lines = b''.join(b'%011d\n' % i for i in range(size // 12 + 1))
    return lines[:size]


@pytest.fixture
def slow(tmp_path: Path) -> Iterator[Callable[..., ThrottledSSH]]:
    """Start slow-link servers for the test (a home per name, under `tmp_path`); stop them after."""
    servers: list[ThrottledSSH] = []

    def start(shell: str, rate: int, name: str = 'home') -> ThrottledSSH:
        need_shell(shell)
        home = (tmp_path / name).resolve()
        home.mkdir(exist_ok=True)
        servers.append(ThrottledSSH(home, shell, tmp_path, rate))
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
         f'while the output was still arriving; it ends {got[-40:]!r}')


@pytest.mark.integration
def test_a_slow_link_returns_both_streams_whole(slow: Callable[..., ThrottledSSH]) -> None:
    rate, size = SLOW_LINKS['256KiBps']
    server = slow('/bin/bash', rate)
    data = _numbered(size)
    (Path(server.home) / 'big.txt').write_bytes(data)
    result = _run(server, 'cat big.txt; echo DONE >&2', timeout=60)
    got = result.stdout.encode()
    assert (len(got), got == data, result.stderr, result.exit_code) == (size, True, 'DONE\n', 0), \
        (f'the result was cut while the output was still arriving: stdout {len(got)} of {size} bytes, '
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
    fed = _run(server, ['cat'], stdin=sent, timeout=60)
    violations = []
    if (ran.stdout, ran.stderr, ran.exit_code) != ('', '', 0) or not (home / 'ran').exists():
        violations.append(f'a command that ran was reported as {ran!r} (~/ran exists: {(home / "ran").exists()})')
    if (fed.stdout, fed.stderr, fed.exit_code) != (sent.decode('utf-8', 'replace'), '', 0):
        violations.append(f'a late start lost the stdin sent: {fed!r}')
    assert violations == [], ('a late sentinel was not counted as started (the read was cut before it arrived): '
                              + '\n'.join(violations))


@pytest.mark.integration
@pytest.mark.parametrize('shell', ['/bin/bash', '/bin/zsh'])
def test_a_holder_that_keeps_writing_ends_at_the_timeout(shell: str, serve: Callable[..., LoopbackSSH]) -> None:
    server = serve(shell)
    result, elapsed = timed(lambda: _run(server, WRITER, timeout=1.5))
    violations = writer_violations('ssh', result, elapsed, 1.5)
    assert violations == [], '\n'.join(violations)


# Every call that holds the caller's stdin has a bound: over ssh it is sent only once the program
# has started, so an rc file that reads stdin would otherwise hold the call forever. The bound is
# lowered for the test; the call carries no timeout of its own.
START_BOUND = 1.5


def _bounded_call(run: Callable[[], Any], within: float) -> tuple[Any, float]:
    box: list[Any] = []
    start = time.monotonic()
    thread = threading.Thread(target=lambda: box.append(run()), daemon=True)
    thread.start()
    thread.join(within)
    return (box[0] if box else None), time.monotonic() - start


@pytest.mark.integration
@pytest.mark.parametrize('rc', list(REMOTE_RCS))
@pytest.mark.parametrize('call_kind', ['run', 'write_file', 'sbatch'])
def test_a_call_holding_stdin_is_bounded(call_kind: str, rc: str, serve: Callable[..., LoopbackSSH], executes: Any,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh, 'START_TIMEOUT', START_BOUND, raising=False)
    shell, name = REMOTE_RCS[rc]
    server = serve(shell)
    home = Path(server.home)
    (home / name).write_text(RC_READS)
    argv_echo.install(home, 'sbatch')
    executor = server.executor()
    calls = {
        'run': lambda: executor.run(['/bin/sh', '-c', 'cat > "$HOME/written"'], stdin=b'line one\nline two\n'),
        'write_file': lambda: _tool(executor, 'write_file', {'path': '~/written', 'content': 'line one\nline two\n'}),
        'sbatch': lambda: _tool(executor, 'sbatch', {'script_content': '#!/bin/bash\necho hi\n', 'job_name': 'held'}),
    }
    try:
        got, elapsed = _bounded_call(calls[call_kind], START_BOUND + 10)
    finally:
        executor.close()
    violations = []
    if got is None:
        violations.append(f'{call_kind}: a call holding stdin never returned ({elapsed:.1f} s): it has no bound')
    elif isinstance(got, CommandResult) and (got.stdout, got.stderr, got.exit_code) != ('', '', TIMEOUT_EXIT_CODE):
        violations.append(f'{call_kind}: returned {got!r}, not a timeout with nothing sent')
    elif isinstance(got, Outcome) and not (got.is_error and str(TIMEOUT_EXIT_CODE) in got.text):
        violations.append(f'{call_kind}: returned {got!r}, not a failure naming the timeout ({TIMEOUT_EXIT_CODE})')
    written = [p.name for p in (home / 'written', *sorted((home / 'jobs').glob('held_*'))) if p.exists()]
    if written:
        violations.append(f'{call_kind}: the timed-out call wrote {written!r}')
    if argv_echo.calls(home):
        violations.append(f'{call_kind}: sbatch was called: {argv_echo.calls(home)!r}')
    assert violations == [], '\n'.join(violations)


# ---------------------------------------------------------------------------------------------
# The channel loop (unit), against a fake channel.

class FakeChannel:
    """A channel whose exit status, output and end of output arrive at set times after creation."""

    def __init__(self, status: int, status_at: float, eof_at: float | None, data: bytes = b'out') -> None:
        self.start = time.monotonic()
        self.status, self.status_at, self.eof_at = status, status_at, eof_at
        self.data = data
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
        return False

    def recv_stderr(self, n: int) -> bytes:
        return b''

    def exit_status_ready(self) -> bool:
        return self._now() >= self.status_at

    def recv_exit_status(self) -> int:
        return self.status if self.exit_status_ready() else -1

    def fileno(self) -> int:
        raise AssertionError('the loop selected on a channel whose exit status had arrived')


@pytest.mark.unit
def test_drain_waits_instead_of_spinning() -> None:
    # The exit status arrives at once; the end of output 0.5 s later.
    channel = FakeChannel(7, 0.0, 0.5)
    out, err, code, note = ssh._collect(channel, 5)
    assert (out, err, code, note) == (b'out', b'', 7, b''), (out, err, code, note)
    assert channel.polls < 100, f'the loop busy-waited: {channel.polls} polls in 0.5 s'


@pytest.mark.unit
def test_a_completed_command_at_the_deadline_keeps_its_status() -> None:
    # The command exited, but something still holds its output open past the deadline.
    channel = FakeChannel(0, 0.0, None)
    start = time.monotonic()
    out, err, code, note = ssh._collect(channel, 0.3)
    assert code == 0, f'a command that exited before the deadline reported {code}'
    assert (out, note) == (b'out', launch.HELD_NOTE) and time.monotonic() - start < 2, (out, note)


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
    # The exit status arrives at once; the output follows a piece every 0.4 s for 2.4 s, then ends.
    pieces = [b'%d\n' % i for i in range(6)]
    channel = TrickleChannel(pieces, 0.4)
    out, err, code, note = ssh._collect(channel, 30)
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
