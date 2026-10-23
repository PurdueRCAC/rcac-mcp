# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Every string parameter of every tool, in all three modes, passes exactly the value it was given.

The matrix has one row per (mode, tool, parameter), `test_literal_argument[<mode>-<tool>-<param>]`,
for every parameter whose served schema includes a string or an array of strings, except
`run_command`'s `command` (a script by design). Each row calls its tool through the served schema
(`in_memory(shared_server(…))`) once per value in `VALUES`, concurrently after a first call, in
`local` and `delegate` (the `executes` fixture: real bash, fenced and sandboxed, the `sudo` prefix
replaced by a relaying stand-in with its own mapped home) and `ssh` (the executing loopback server,
a bash login shell). In every mode a relative path means the home the command runs in: the
`local` launch starts in the server's home, a `delegate` program starts in the mapped home, and
sshd starts in the remote home.

`VALUES` merges what a shell would act on into one value that starts like an option (`-n`, space,
quotes, `$( )`, backticks, globs, `;|&<>(){}!#\\`, a newline) and keeps apart the values whose
meaning depends on how they start or what they are: `--help`, `+x`, `--`, the empty string, `~`,
`~/x y`, `~//x`, `~name/x`, and a path component ending in a newline. A lone surrogate is refused
with an error naming the parameter, in every mode, before anything runs. Each string parameter is
also crossed with the parameters that select a different script or argv: `read_file.max_size`,
`list_directory.show_hidden`, `write_file.append` and `create_dirs`, and `sbatch`'s extra option
`-J` (which takes the next argument as its value) against a script path that starts with `-`.

The oracles, by what the parameter is:

- **a path** a real program acts on (`cat`, `head`, `cd`, `cp`, SFTP, the server's own copy): the
  fixture file or directory named exactly the value is the one read, written, listed or entered;
  `~` and `~/…` are the home of the user the command runs as (the mapped home in `delegate`), and
  `~name/x` is the relative path it spells; a server-side `local_path` resolves on the server's side.
- **an option value or a positional** of a cluster program: an argv-echo stand-in records exactly
  the argv it received, and its cwd; the calls must be exactly the expected argvs, the value one
  element right after its option (a path value as the path it names), a positional after `--`.
  `ls` is such a stand-in too, printing a canned GNU long listing that `list_directory` must parse.
- **content**: the file or job script holds exactly the value's UTF-8 bytes (sha256).
- **an enum** (`write_file.encoding`): every value outside it is refused by the schema, naming it.
- **ignored** (`read_file.encoding`): the read is unchanged and the value reaches no command.

An empty optional scalar (an option's value, an optional positional, a directory to list, a `cwd`)
was not given, so the tool's default call applies; an empty list element or required parameter is
one literal empty argument. Every executor call a row makes must also be an argv, never a command
string, and a shell argv may run only a registered fixed script.

`test_literal_matrix_covers_every_string_param` proves the rows cover the schema the live server
serves (never the fixture), walking `anyOf`, `items` and `enum`; planted controls (an extra
optional string, an extra string list, a missing mode) must fail it.
"""

from __future__ import annotations

import asyncio
import copy
import getpass
import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import pytest

import argv_echo
from harness import SITE, Outcome, call, drive, in_memory, served_surface, shared_server
from rcac_mcp.executor import CommandResult, Executor
from rcac_mcp.executor.delegate import DelegatingExecutor
from rcac_mcp.executor.shell import LocalShellExecutor
from rcac_mcp.scripts import script_of
from rcac_mcp.server import create_mcp_server
from ssh_server import LoopbackSSH
from test_tools import SHELLS

MODES = ('local', 'delegate', 'ssh')

# The value classes. `hostile` merges everything a shell would split, expand, glob or run, after a
# leading `-n`; the others differ by how they start or by being exactly what they are.
VALUES = {
    'hostile': "-n My Data 'q' \"q\" $HOME$(id) `id` *?[a] ;|&<>(){}!#\\ two\nlines",
    'help': '--help',
    'plus': '+x',
    'dashdash': '--',
    'empty': '',
    'tilde': '~',
    'tilde-slash': '~/x y',
    'tilde-double-slash': '~//x',
    'tilde-name': '~name/x',
    'newline-component': 'nl\n/x',
}

# Refused, naming the parameter, in every mode: it has no UTF-8 bytes (locally it would become 0x80).
SURROGATE = 'lone \udc80 surrogate'

# The parameter that is a script by design, not a value.
SCRIPT_PARAMS = frozenset({('run_command', 'command')})

# The stand-ins' cluster programs, and `ls`, which prints a canned GNU long listing.
STAND_INS = ('sbatch', 'squeue', 'scancel', 'sacct', 'sinfo', 'scontrol', 'average_wait', 'ls')
LISTED = ('entry with spaces', 'notes.txt')
STAND_IN = f"""#!/bin/sh
printf '%s\\0' "$#" "$PWD" "${{0##*/}}" "$@" >>"$HOME/.matrix-calls/$$" || exit 90
case ${{0##*/}} in
    sbatch) echo 'Submitted batch job {argv_echo.JOB_ID}' ;;
    ls) printf '%s\\n' 'total 8' \\
        'drwxr-xr-x 2 alice lab 4096 2026-10-16 12:00 {LISTED[0]}' \\
        '-rw-r--r-- 1 alice lab   12 2026-10-16 12:00 {LISTED[1]}' ;;
esac
exit 0
"""

# The job script every option row submits, and what a row's sacct reports as the default user.
JOB = '~/job.sh'
ME = getpass.getuser()


# The modes.

class Recorded:
    """An executor that records every call it passes on to `inner`: ('run', command, …), ('write', path, …),
    ('put'|'get', …)."""

    def __init__(self, inner: Executor) -> None:
        self.inner = inner
        self.calls: list[tuple[Any, ...]] = []

    @property
    def hostname(self) -> str:
        return self.inner.hostname

    def run(self, command: Sequence[str] | str, cwd: str | None = None, timeout: float | None = None) -> CommandResult:
        self.calls.append(('run', command if isinstance(command, str) else list(command), cwd))
        return self.inner.run(command, cwd=cwd, timeout=timeout)

    def write(self, path: str, data: bytes, append: bool = False, parent: str | None = None,
              executable: bool = False) -> CommandResult:
        self.calls.append(('write', path, parent))
        return self.inner.write(path, data, append=append, parent=parent, executable=executable)

    def put(self, local_path: str, remote_path: str) -> str:
        self.calls.append(('put', local_path, remote_path))
        return self.inner.put(local_path, remote_path)

    def get(self, remote_path: str, local_path: str) -> str:
        self.calls.append(('get', remote_path, local_path))
        return self.inner.get(remote_path, local_path)

    def open(self) -> None:
        self.inner.open()

    def close(self) -> None:
        self.inner.close()

    def __enter__(self) -> Recorded:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@dataclass
class Mode:
    """One mode: its executor, where the command's paths resolve, and where the server's do.

    `home` is where `~` and a relative path resolve for the command (the mapped home in
    `delegate`); `local_home` is where a server-side `local_path` resolves (the same home in
    `local` and `delegate`, the server's own in `ssh`); `server_home` is the server process's HOME.
    """

    name: str
    executor: Recorded
    home: Path
    local_home: Path
    server_home: Path
    home_name: str = 'home'

    def calls(self, calls: Sequence[tuple[str, dict[str, Any]]]) -> list[Outcome]:
        """Call each (tool, arguments) through the served schema, all at once.

        Over SSH the first call goes alone, so the connection is open before the others share it.
        """
        alone = 1 if self.name == 'ssh' else 0

        async def scenario() -> list[Outcome]:
            async with in_memory(shared_server(self.executor)) as client:
                first = [await call(client, tool, args) for tool, args in calls[:alone]]
                return [*first, *await asyncio.gather(*(call(client, tool, args) for tool, args in calls[alone:]))]
        return drive(scenario(), timeout=180)

    def where(self, value: str, local: bool = False) -> Path:
        """The path `value` names for the command (or, with `local`, on the server's side)."""
        home = self.local_home if local else self.home
        if value == '~':
            return home
        if value.startswith('~/'):
            return home / value[2:].lstrip('/')
        return Path(value) if value.startswith('/') else home / value

    def rendered(self, value: str) -> str:
        """A path value as a program receives it: `~` resolved, a relative path from `./`."""
        if value == '~' or value.startswith('~/'):
            return str(self.where(value))
        return value if value.startswith('/') else f'./{value}'


@pytest.fixture
def mode(request: pytest.FixtureRequest, executes: Any, monkeypatch: pytest.MonkeyPatch,
         tmp_path: Path) -> Iterator[Mode]:
    """The row's mode. The server process runs from its own home, with HOME set to it."""
    name = request.param
    server_home = executes.server_home
    monkeypatch.setenv('HOME', str(server_home))
    monkeypatch.chdir(server_home)
    if name == 'local':
        executes.local_cwd = server_home
        yield Mode(name, Recorded(LocalShellExecutor()), server_home, server_home, server_home)
    elif name == 'delegate':
        executor = DelegatingExecutor('matrix', {'matrix': executes.user})
        home = executes.mapped_home
        yield Mode(name, Recorded(executor), home, home, server_home, 'mapped home')
    else:
        home = (tmp_path / 'ssh-home').resolve()
        home.mkdir()
        with LoopbackSSH(home, '/bin/bash', tmp_path) as server:
            executor = Recorded(server.executor())
            try:
                yield Mode(name, executor, home, server_home, server_home)
            finally:
                executor.close()


def install(home: Path) -> None:
    """Put the stand-ins first on a login bash's PATH in `home`; each records into `.matrix-calls`."""
    (home / '.matrix-calls').mkdir(exist_ok=True)
    bin_dir = home / 'bin'
    bin_dir.mkdir(exist_ok=True)
    for program in STAND_INS:
        path = bin_dir / program
        path.write_text(STAND_IN)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(home / '.bash_profile', 'a') as profile:
        profile.write(argv_echo.PROFILE_LINE)


def received(home: Path) -> list[tuple[str, ...]]:
    """Every stand-in call made in `home`: (cwd, program, *arguments), byte for byte."""
    found = []
    for record in sorted((home / '.matrix-calls').glob('*')):
        fields = [f.decode('utf-8', 'surrogateescape') for f in record.read_bytes().split(b'\0')[:-1]]
        while fields:
            count = int(fields[0])
            found.append(tuple(fields[1:count + 3]))
            fields = fields[count + 3:]
    return found


# The oracles.

def label(tool: str, param: str, value: str) -> str:
    name = next((n for n, v in VALUES.items() if v == value), 'value')
    return f'{tool}.{param}={name}'


def r1_violations(tool: str, calls: Sequence[tuple[Any, ...]]) -> list[str]:
    """Every executor call that is not an argv, or a shell argv that runs an unregistered script."""
    found = []
    for kind, command, *_ in calls:
        if kind != 'run':
            continue
        if isinstance(command, str):
            if tool != 'run_command':
                found.append(f'{tool}: passed a command string, not an argv: {command!r:.200}')
        elif command and str(command[0]).rsplit('/', 1)[-1] in SHELLS:
            script = command[command.index('-c') + 1] if '-c' in command[:-1] else None
            if script_of(command) is None:
                found.append(f'{tool}: unregistered script: {script!r:.200}')
    return found


def refusal_violations(what: str, param: str, outcome: Outcome) -> list[str]:
    """`outcome` must be an error that names `param`."""
    if not outcome.is_error:
        return [f'{what}: was accepted, not refused: {outcome.text[:200]!r}']
    if param not in outcome.text:
        return [f'{what}: the error does not name {param!r}: {outcome.text[:200]!r}']
    return []


def argv_violations(expected: Sequence[tuple[str, tuple[str, ...]]], got: Sequence[tuple[str, ...]]) -> list[str]:
    """The stand-in calls `got` must be exactly `expected` ((what, call) pairs), in any order."""
    left = list(got)
    found = []
    for what, want in expected:
        if want in left:
            left.remove(want)
        else:
            found.append(f'{what}: argv differs: the value reaches the command as none of the calls received, '
                         f'not as {want!r:.300}')
    for extra in left:
        found.append(f'argv differs: the value reaches the command as an unexpected call {extra!r:.300}')
    if found:
        found.append(f'(received: {got!r:.600})')
    return found


def content_violations(what: str, outcome: Outcome, path: Path, data: bytes) -> list[str]:
    if outcome.is_error:
        return [f'{what}: failed: {outcome.text[:300]!r}']
    on_disk = path.read_bytes() if path.is_file() else None
    if on_disk != data:
        digest = 'no file' if on_disk is None else hashlib.sha256(on_disk).hexdigest()[:16]
        return [f'{what}: the file differs from the bytes sent: {path} holds {digest}, the value hashes '
                f'{hashlib.sha256(data).hexdigest()[:16]}']
    return []


def error_violations(what: str, outcome: Outcome, why: str) -> list[str]:
    """`outcome` must be the program's own error, carrying `why`."""
    if not outcome.is_error or why not in outcome.text:
        return [f'{what}: expected the error {why!r}, got {outcome.text[:300]!r}']
    return []


# Each parameter's driver: it sets up the fixture in `mode`, makes the calls and returns violations.

Driver = Callable[[Mode, str, str], list[str]]


def _fixture(mode: Mode, value: str, local: bool = False, directory: bool = False, data: bytes = b'') -> Path:
    """Create the file (or directory) `value` names, with its parents; return its path."""
    path = mode.where(value, local)
    if value in ('', '~'):
        return path
    if directory:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def _values() -> list[str]:
    return list(VALUES.values())


# Option values and positionals of the cluster programs.

# Each tool's own arguments for its rows, and the argv before and after an option's pair, given the
# parameter set and whether it was given (an empty value was not).
def _option_layout(mode: Mode, tool: str, param: str, given: bool) -> tuple[dict[str, Any], list[str], list[str]]:
    if tool == 'squeue':
        return {}, ['squeue'] + ([] if given and param in ('user', 'account') else ['--me']), []
    if tool == 'scancel':
        return {'job_ids': [7]}, ['scancel'], ['--', '7']
    if tool == 'sacct':
        return {}, ['sacct', '-X'] + ([] if given and param in ('user', 'account') else ['-u', ME]), []
    if tool == 'sbatch':
        return {'script_path': JOB}, ['sbatch'], ['--', mode.rendered(JOB)]
    return {}, [tool], []


OPTIONS = {
    'squeue': {'user': '-u', 'account': '-A', 'partition': '-p', 'state': '-t'},
    'scancel': {'user': '-u', 'account': '-A', 'partition': '-p', 'state': '-t', 'name': '-n'},
    'sacct': {'user': '-u', 'account': '-A', 'name': '--name', 'state': '-s', 'starttime': '-S', 'endtime': '-E',
              'format': '-o'},
    'sinfo': {'partition': '-p', 'nodes': '-n', 'state': '-t', 'format': '-o'},
    'sbatch': {'job_name': '-J', 'account': '-A', 'partition': '-p', 'qos': '-q', 'time': '-t', 'mem': '--mem',
               'gpus': '--gpus', 'constraint': '-C', 'output': '-o', 'error': '-e'},
    'average_wait': {'partition': '--partition', 'account': '-A'},
}

# Option values that are paths: the program receives the path they name.
PATH_OPTIONS = frozenset({('sbatch', 'output'), ('sbatch', 'error')})


def drive_option(mode: Mode, tool: str, param: str) -> list[str]:
    install(mode.home)
    calls, expected = [], []
    for value in _values():
        own, before, after = _option_layout(mode, tool, param, bool(value))
        calls.append((tool, {**own, param: value}))
        shown = mode.rendered(value) if (tool, param) in PATH_OPTIONS else value
        pair = [OPTIONS[tool][param], shown] if value else []
        expected.append((label(tool, param, value), (str(mode.home), *before, *pair, *after)))
    calls.append((tool, {**_option_layout(mode, tool, param, True)[0], param: SURROGATE}))
    outcomes = mode.calls(calls)
    found = [f'{what}: failed: {o.text[:300]!r}' for (what, _), o in zip(expected, outcomes) if o.is_error]
    found += refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])
    return found + argv_violations(expected, received(mode.home))


def drive_node(mode: Mode, tool: str, param: str) -> list[str]:
    install(mode.home)
    values = _values()
    outcomes = mode.calls([(tool, {param: v}) for v in values] + [(tool, {param: SURROGATE})])
    expected = [(label(tool, param, v), (str(mode.home), 'scontrol', 'show', 'node', *(['--', v] if v else [])))
                for v in values]
    found = [f'{label(tool, param, v)}: failed: {o.text[:300]!r}' for v, o in zip(values, outcomes) if o.is_error]
    found += refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])
    return found + argv_violations(expected, received(mode.home))


def drive_script_path(mode: Mode, tool: str, param: str) -> list[str]:
    install(mode.home)
    values = _values()
    for v in values:
        _fixture(mode, v, data=b'#!/bin/sh\n')
    hostile = VALUES['hostile']
    calls = [(tool, {param: v}) for v in values]
    # `-J` takes the next argument as its value, so it consumes the `--`: the script path that
    # follows must still not read as an option.
    calls += [(tool, {param: hostile, 'extra_args': ['-J']}), (tool, {param: SURROGATE})]
    outcomes = mode.calls(calls)
    found, expected = [], []
    for v, outcome in zip(values, outcomes):
        if v == '':
            # An empty script path was not given, and sbatch has nothing to submit.
            found += error_violations(label(tool, param, v), outcome, 'Either script_path or script_content')
            continue
        if outcome.is_error:
            found.append(f'{label(tool, param, v)}: failed: {outcome.text[:300]!r}')
        expected.append((label(tool, param, v), (str(mode.home), 'sbatch', '--', mode.rendered(v))))
    expected.append((f'{tool}.{param}=hostile with -J', (str(mode.home), 'sbatch', '-J', '--', mode.rendered(hostile))))
    found += refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])
    got = received(mode.home)
    found += argv_violations(expected, got)
    for cwd, program, *args in got:
        operand = args[-1] if args else ''
        if operand.startswith('-'):
            found.append(f'{tool}.{param}: the script path is readable as an option: {operand!r}')
        elif not os.path.exists(os.path.join(cwd, operand)):
            found.append(f'{tool}.{param}: the value reaches the command as {operand!r}, which names no fixture file')
    return found


# List parameters: every value is one element, in one call.

LISTS = {
    # tool: (own arguments, argv before the extra options, argv after them)
    'squeue': ({}, ['squeue', '--me'], []),
    'scancel': ({'job_ids': [7]}, ['scancel'], ['--', '7']),
    'sacct': ({}, ['sacct', '-X', '-u', ME], []),
    'sinfo': ({}, ['sinfo'], []),
}


def drive_list(mode: Mode, tool: str, param: str) -> list[str]:
    install(mode.home)
    if tool == 'sbatch':
        own, before, after = {'script_path': JOB}, ['sbatch'], ['--', mode.rendered(JOB)]
    else:
        own, before, after = LISTS[tool]
    # `--` is an element of `args` like any other; in `extra_args` it would end the options, so it
    # is refused there.
    elements = [v for v in _values() if param == 'args' or v != '--']
    calls = [(tool, {**own, param: elements}), (tool, {**own, param: ['ok', SURROGATE]})]
    if param == 'extra_args':
        calls.append((tool, {**own, param: ['--long', '--']}))
    outcomes = mode.calls(calls)
    if param == 'args':
        expected = [(f'{tool}.{param}', (str(mode.home), *before, *after, *elements))]
    else:
        expected = [(f'{tool}.{param}', (str(mode.home), *before, *elements, *after))]
    found = [f'{tool}.{param}: failed: {outcomes[0].text[:300]!r}'] if outcomes[0].is_error else []
    found += refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[1])
    if param == 'extra_args':
        found += refusal_violations(f'{tool}.{param} with --', param, outcomes[2])
    return found + argv_violations(expected, received(mode.home))


# Paths that real programs act on.

def _read(mode: Mode, value: str) -> bytes:
    return f'read {value!r}\n'.encode('utf-8', 'surrogateescape')


def drive_read_path(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    for v in values:
        _fixture(mode, v, data=_read(mode, v))
    calls = [(tool, {param: v}) for v in values] + [(tool, {param: v, 'max_size': 5}) for v in values]
    outcomes = mode.calls(calls + [(tool, {param: SURROGATE})])
    found = []
    for (_, args), outcome in zip(calls, outcomes):
        v, size = args[param], args.get('max_size')
        what = label(tool, param, v) + (' with max_size' if size else '')
        if v == '':
            found += error_violations(what, outcome, 'No such file')
        elif v == '~':
            # The home is a directory: the program's error names it.
            found += error_violations(what, outcome, str(mode.home))
        else:
            want = _read(mode, v)[:size].decode()
            if outcome.is_error or outcome.text != want:
                found.append(f'{what}: the value reaches the command as a different file: read {outcome.text[:200]!r}, '
                             f'expected {want!r} from {mode.where(v)}')
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])


def drive_list_path(mode: Mode, tool: str, param: str) -> list[str]:
    install(mode.home)
    values = _values()
    for v in values:
        _fixture(mode, v, directory=True)
    calls = [(tool, {param: v, 'show_hidden': hidden}) for hidden in (False, True) for v in values]
    outcomes = mode.calls(calls + [(tool, {param: SURROGATE})])
    found, expected = [], []
    for (_, args), outcome in zip(calls, outcomes):
        v, hidden = args[param], args['show_hidden']
        what = label(tool, param, v) + (' with show_hidden' if hidden else '')
        # An empty path was not given: the default lists the directory the command starts in.
        directory = mode.home if v == '' else mode.where(v)
        expected.append((what, (str(directory), 'ls', '-l', '--time-style=long-iso', *(['-a'] if hidden else []), '.')))
        want = [{'name': name, 'path': f'{directory}/{name}'} for name in LISTED]
        try:
            got = [{'name': e['name'], 'path': e['path']} for e in json.loads(outcome.text)]
        except (ValueError, KeyError, TypeError):
            got = outcome.text[:300]
        if outcome.is_error or got != want:
            found.append(f'{what}: the value reaches the command as a different directory: {got!r:.300}, '
                         f'expected {want!r:.300}')
    found += refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])
    return found + argv_violations(expected, received(mode.home))


OLD = b'old content, longer than the new, which an overwrite must replace\n'
PREFIX = b'prefix \xff with no final newline'


def _wrote(value: str) -> str:
    return f'wrote {value!r}'


def drive_write_path(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    found = []
    for phase in ('overwrite', 'append', 'create_dirs'):
        for v in values:
            if phase == 'create_dirs':
                # No parent exists: the tool creates it.
                where = mode.where(v)
                if v not in ('', '~') and where.parent != mode.home:
                    shutil.rmtree(where.parent, ignore_errors=True)
                elif where.is_file():
                    where.unlink()
            else:
                _fixture(mode, v, data=PREFIX if phase == 'append' else OLD)
        flags = {'overwrite': {}, 'append': {'append': True}, 'create_dirs': {'create_dirs': True}}[phase]
        calls = [(tool, {param: v, 'content': _wrote(v), **flags}) for v in values]
        outcomes = mode.calls(calls)
        for v, outcome in zip(values, outcomes):
            what = f'{label(tool, param, v)} ({phase})'
            if v == '':
                found += error_violations(what, outcome, 'No such file')
            elif v == '~':
                found += error_violations(what, outcome, str(mode.home))
            else:
                sent = (PREFIX if phase == 'append' else b'') + _wrote(v).encode()
                found += [f'{w} (the value reaches the command as another path)'
                          for w in content_violations(what, outcome, mode.where(v), sent)]
    [outcome] = mode.calls([(tool, {param: SURROGATE, 'content': 'x'})])
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcome)


PWD = 'printf %s "$PWD"'


def drive_cwd(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    for v in values:
        _fixture(mode, v, directory=True)
    outcomes = mode.calls([(tool, {'command': PWD, param: v}) for v in values] + [(tool, {'command': PWD, param: SURROGATE})])
    found = []
    for v, outcome in zip(values, outcomes):
        # An empty cwd was not given: the command starts in the home.
        want = str(mode.home if v == '' else mode.where(v))
        try:
            got = json.loads(outcome.text)['stdout']
        except (ValueError, KeyError, TypeError):
            got = outcome.text[:300]
        if outcome.is_error or got != want:
            where = " (the server's HOME)" if mode.server_home != mode.home and got.startswith(str(mode.server_home)) else ''
            found.append(f'{label(tool, param, v)}: resolved to {got!r}{where}, not the {mode.home_name} path {want!r}')
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])


# Transfers: one side is the value, the other a fixed path.

SOURCE = '~/source dir/src file'


def drive_transfer(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    # The side the value is on: upload's local_path and download's local_path are the server's.
    value_local = param == 'local_path'
    fixed_local = not value_local
    found = []
    if (tool, param) in (('upload_file', 'remote_path'), ('download_file', 'local_path')):
        # The value is the destination; the source is fixed.
        _fixture(mode, SOURCE, local=tool == 'upload_file', data=b'the source\n')
        for v in values:
            target = mode.where(v, value_local)
            if v not in ('', '~'):
                target.parent.mkdir(parents=True, exist_ok=True)
        fixed = 'local_path' if tool == 'upload_file' else 'remote_path'
        outcomes = mode.calls([(tool, {fixed: SOURCE, param: v}) for v in values] + [(tool, {fixed: SOURCE, param: SURROGATE})])
        for v, outcome in zip(values, outcomes):
            what = label(tool, param, v)
            if v == '':
                # An empty path names no file: refused, and nothing is written anywhere.
                found += refusal_violations(what, param, outcome)
                if any('' in c[1:] for c in mode.executor.calls):
                    found.append(f'{what}: the value reaches the executor as an empty path: {mode.executor.calls!r:.300}')
                continue
            # A directory destination receives the file under its own name.
            target = mode.where(v, value_local) / 'src file' if v == '~' else mode.where(v, value_local)
            found += [f'{w} (the value reaches the command as another path)'
                      for w in content_violations(what, outcome, target, b'the source\n')]
            if not outcome.is_error and outcome.text != str(target):
                found.append(f'{what}: returned {outcome.text!r}, not the {mode.home_name} path {str(target)!r}')
    else:
        # The value is the source; each call copies to its own fixed destination.
        for v in values:
            _fixture(mode, v, local=value_local, data=_read(mode, v))
        fixed = 'remote_path' if tool == 'upload_file' else 'local_path'
        dest = {v: f'~/copy-{i}' for i, v in enumerate(values)}
        outcomes = mode.calls([(tool, {param: v, fixed: dest[v]}) for v in values] + [(tool, {param: SURROGATE, fixed: '~/x'})])
        for v, outcome in zip(values, outcomes):
            what = label(tool, param, v)
            if v == '':
                found += refusal_violations(what, param, outcome)
                continue
            if v == '~':
                if not outcome.is_error:
                    found.append(f'{what}: copied from the home, which is a directory: {outcome.text[:200]!r}')
                continue
            found += [f'{w} (the value reaches the command as another file)'
                      for w in content_violations(what, outcome, mode.where(dest[v], fixed_local), _read(mode, v))]
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])


# Content, the enum, and the ignored parameter.

def drive_content(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    found = []
    if tool == 'write_file':
        names = {v: f'~/content-{i}' for i, v in enumerate(values)}
        outcomes = mode.calls([(tool, {'path': names[v], param: v}) for v in values] +
                              [(tool, {'path': '~/content-refused', param: SURROGATE})])
        for v, outcome in zip(values, outcomes):
            found += content_violations(label(tool, param, v), outcome, mode.where(names[v]), v.encode())
        if mode.where('~/content-refused').exists():
            found.append(f'{tool}.{param}=surrogate: the refused write created its target')
    else:
        install(mode.home)
        names = {v: f'm{i}' for i, v in enumerate(values)}
        outcomes = mode.calls([(tool, {param: v, 'job_name': names[v]}) for v in values] +
                              [(tool, {param: SURROGATE, 'job_name': 'refused'})])
        scripts = {Path(args[-1]).name.split('_', 1)[0]: Path(args[-1]) for _, _, *args in received(mode.home)}
        for v, outcome in zip(values, outcomes):
            what = label(tool, param, v)
            script = scripts.get(names[v])
            if script is None:
                found.append(f'{what}: no job script was submitted: {outcome.text[:200]!r}')
                continue
            found += content_violations(what, outcome, script, v.encode())
            if not os.access(script, os.X_OK):
                found.append(f'{what}: the job script is not executable')
        if 'refused' in scripts or list((mode.home / 'jobs').glob('refused_*')):
            found.append(f'{tool}.{param}=surrogate: the refused script was written or submitted')
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])


def drive_enum(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    names = {v: f'~/encoded-{i}' for i, v in enumerate(values)}
    calls = [(tool, {'path': names[v], 'content': 'eA==', param: v}) for v in values]
    calls += [(tool, {'path': '~/encoded-utf8', 'content': 'eA==', param: 'utf-8'}),
              (tool, {'path': '~/encoded-base64', 'content': 'eA==', param: 'base64'}),
              (tool, {'path': '~/encoded-refused', 'content': 'eA==', param: SURROGATE})]
    outcomes = mode.calls(calls)
    found = []
    for v, outcome in zip(values, outcomes):
        found += refusal_violations(label(tool, param, v), param, outcome)
        if mode.where(names[v]).exists():
            found.append(f'{label(tool, param, v)}: the refused write created its target')
    found += content_violations(f'{tool}.{param}=utf-8', outcomes[-3], mode.where('~/encoded-utf8'), b'eA==')
    found += content_violations(f'{tool}.{param}=base64', outcomes[-2], mode.where('~/encoded-base64'), b'x')
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])


def drive_ignored(mode: Mode, tool: str, param: str) -> list[str]:
    values = _values()
    _fixture(mode, '~/plain', data=b'plain text\n')
    outcomes = mode.calls([(tool, {'path': '~/plain', param: v}) for v in values] +
                          [(tool, {'path': '~/plain', param: SURROGATE})])
    found = []
    for v, outcome in zip(values, outcomes):
        if outcome.is_error or outcome.text != 'plain text\n':
            found.append(f'{label(tool, param, v)}: the read changed: {outcome.text[:200]!r}')
    for kind, command, *_ in mode.executor.calls:
        if kind == 'run' and command != ['cat', '--', '~/plain']:
            found.append(f'{tool}.{param}: the value reaches the command as {command!r:.200}')
    return found + refusal_violations(f'{tool}.{param}=surrogate', param, outcomes[-1])


PARAMS: dict[tuple[str, str], Driver] = {
    **{(tool, param): drive_option for tool, options in OPTIONS.items() for param in options},
    ('scontrol_show_node', 'node'): drive_node,
    ('sbatch', 'script_path'): drive_script_path,
    ('sbatch', 'args'): drive_list,
    **{(tool, 'extra_args'): drive_list for tool in ('sbatch', 'squeue', 'scancel', 'sacct', 'sinfo')},
    ('read_file', 'path'): drive_read_path,
    ('list_directory', 'path'): drive_list_path,
    ('write_file', 'path'): drive_write_path,
    ('run_command', 'cwd'): drive_cwd,
    ('upload_file', 'local_path'): drive_transfer,
    ('upload_file', 'remote_path'): drive_transfer,
    ('download_file', 'remote_path'): drive_transfer,
    ('download_file', 'local_path'): drive_transfer,
    ('write_file', 'content'): drive_content,
    ('sbatch', 'script_content'): drive_content,
    ('write_file', 'encoding'): drive_enum,
    ('read_file', 'encoding'): drive_ignored,
}

ROWS = [pytest.param(m, tool, param, id=f'{m}-{tool}-{param}') for (tool, param) in PARAMS for m in MODES]


@pytest.mark.integration
@pytest.mark.parametrize(('mode', 'tool', 'param'), ROWS, indirect=['mode'])
def test_literal_argument(mode: Mode, tool: str, param: str) -> None:
    violations = PARAMS[tool, param](mode, tool, param)
    violations += r1_violations(tool, mode.executor.calls)
    assert violations == [], '\n'.join(violations)


# Coverage, proved from the served schema.

def has_string(schema: Any) -> bool:
    """Whether a JSON schema admits a string, or an array of strings, through anyOf/oneOf/allOf."""
    if not isinstance(schema, dict):
        return False
    types = schema.get('type')
    types = types if isinstance(types, list) else [types]
    if 'string' in types or any(isinstance(e, str) for e in schema.get('enum', [])):
        return True
    if 'array' in types and has_string(schema.get('items')):
        return True
    return any(has_string(s) for key in ('anyOf', 'oneOf', 'allOf') for s in schema.get(key, []))


def string_params(surface: dict[str, Any]) -> set[tuple[str, str]]:
    """Every (tool, parameter) of a served surface whose schema admits a string or a string array."""
    return {(tool, name) for tool, served in surface['tools'].items()
            for name, schema in served['inputSchema'].get('properties', {}).items() if has_string(schema)}


def coverage_violations(surface: dict[str, Any], rows: set[tuple[str, str, str]]) -> list[str]:
    """Every (mode, tool, parameter) the surface needs a row for and has none, and every stray row."""
    needed = {(m, tool, param) for m in MODES for tool, param in string_params(surface) - SCRIPT_PARAMS}
    return ([f'no matrix row for {m}-{tool}-{param}' for m, tool, param in sorted(needed - rows)] +
            [f'a matrix row for {m}-{tool}-{param}, which the surface does not serve as a string'
             for m, tool, param in sorted(rows - needed)])


def _rows() -> set[tuple[str, str, str]]:
    return {(m, tool, param) for tool, param in PARAMS for m in MODES}


@pytest.mark.unit
def test_literal_matrix_covers_every_string_param() -> None:
    surface = served_surface(create_mcp_server('none', site=SITE))
    assert len(string_params(surface)) > 40
    violations = coverage_violations(surface, _rows())
    assert violations == [], '\n'.join(violations)


def _planted(surface: dict[str, Any], tool: str, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    planted = copy.deepcopy(surface)
    planted['tools'][tool]['inputSchema']['properties'][name] = schema
    return planted


@pytest.mark.unit
@pytest.mark.parametrize(('plant', 'fragment'), [
    pytest.param(lambda s, r: (_planted(s, 'squeue', 'planted', {'anyOf': [{'type': 'string'}, {'type': 'null'}]}), r),
                 'no matrix row for ssh-squeue-planted', id='extra-optional-str'),
    pytest.param(lambda s, r: (_planted(s, 'sinfo', 'planted', {'anyOf': [{'items': {'type': 'string'}, 'type': 'array'},
                                                                          {'type': 'null'}]}), r),
                 'no matrix row for local-sinfo-planted', id='extra-list-str'),
    pytest.param(lambda s, r: (_planted(s, 'sinfo', 'planted', {'enum': ['a', 'b'], 'type': 'string'}), r),
                 'no matrix row for delegate-sinfo-planted', id='extra-enum'),
    pytest.param(lambda s, r: (s, {row for row in r if row[0] != 'delegate'}),
                 'no matrix row for delegate-read_file-path', id='missing-mode'),
    pytest.param(lambda s, r: (s, r | {('local', 'jobinfo', 'job_id')}),
                 'which the surface does not serve as a string', id='stray-row'),
])
def test_coverage_check_catches_planted_gaps(plant: Callable[..., Any], fragment: str) -> None:
    surface = served_surface(create_mcp_server('none', site=SITE))
    rows = _rows()
    # A non-string parameter needs no row.
    assert coverage_violations(_planted(surface, 'squeue', 'count', {'type': 'integer'}), rows) == []
    violations = coverage_violations(*plant(surface, rows))
    assert any(fragment in v for v in violations), violations
