# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""`sbatch` passes its script path, the job's arguments and the extra options each as one argument.

The argv is `sbatch <the tool's own options> <extra_args…> -- <script path> <args…>`: the job
script's arguments follow its path, each element one argument; `extra_args` is a list of sbatch
options, one argument per element, after the tool's own options and before `--`; an element `--`
is refused. The script path is one literal path, absolute or starting with `./`, so it is never
read as an option, not even when an extra option that takes a value (`-J`) consumes the `--`. A
string `extra_args` or `args` is refused by schema validation, in both protocol eras, with an
error naming the parameter and the list type, before anything runs.

The `unit` rows read the argv the tool hands a `RecordingExecutor`. The `integration` rows run the
tool through the served schema in `local` and `delegate` mode (the `executes` fixture) against an
argv-echo `sbatch` (`tests/argv_echo.py`), so they see each argument exactly as sbatch received it.

A job script written from content is `~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh`, and no two calls
share one: not two calls in the same second (the clock is held inside one second by patching
`slurm.datetime`), whether they have the same job name, none, or names alike once sanitised; not
two calls that overlap, through one server or two servers over one home, with both writes landing
before either submission; and not two calls in separate server processes alike in clock, process
id and host name.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import subprocess
import sys
import threading
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

import argv_echo
import rcac_mcp
from harness import ERAS, SANDBOX, Outcome, RecordingExecutor, Rendezvous, call, drive, in_memory, shared_server
from rcac_mcp.executor import CommandResult, Executor, UserPath
from rcac_mcp.tools import slurm
from rcac_mcp.executor.delegate import DelegatingExecutor
from rcac_mcp.executor.shell import LocalShellExecutor

# Values that a shell would split, expand or read as an option.
HOSTILE_ARGS = ['two words', '-v', '--', '$HOME', '*', 'line\nbreak', "it's", '']
EXTRA = ['--exclusive', '--mail-type', 'END,FAIL']

# One instant, and the timestamp every job script named while the clock is held at it carries.
FROZEN = datetime(2026, 10, 23, 12, 0, 0, 1)
FROZEN_STAMP = '20261023_120000'

# The token that ends a job script's name: 16 lowercase hex digits.
TOKEN = '[0-9a-f]{16}'

# Two calls on one script path: the second write replaces the first call's script.
SHARED = 'calls share a script path'


class FrozenClock(datetime):
    """A `datetime` whose `now()` is always `FROZEN`: every call reads the same second."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return FROZEN


def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold the clock `_job_script_path` reads at `FROZEN` for the rest of the test."""
    monkeypatch.setattr(slurm, 'datetime', FrozenClock)


def script_name(stem: str) -> str:
    """The pattern of a job script path named from `stem` while the clock is frozen."""
    return rf'~/jobs/{re.escape(stem)}_{FROZEN_STAMP}_{TOKEN}\.sh'


def shared_violations(paths: Sequence[str]) -> list[str]:
    """One violation for every script path more than one call used."""
    return [f'{paths.count(p)} {SHARED}: {p!r}' for p in sorted(set(paths)) if paths.count(p) > 1]


def submitted(command: Sequence[str] | str) -> str:
    return f'Submitted batch job {argv_echo.JOB_ID}'


def sbatch_violations(argv: Sequence[str], script: str, args: Sequence[str], extra: Sequence[str]) -> list[str]:
    """Every way an sbatch argv misplaces the script path, the job's arguments or the extra options.

    `script` is the path the script operand must be (as rendered), `args` the job's arguments and
    `extra` the extra options. The argv must end `*extra, '--', script, *args`, each element one
    argument, and the script operand must not start with `-`.
    """
    argv = list(argv)
    if argv[:1] != ['sbatch']:
        return [f'not an sbatch argv: {argv!r:.200}']
    tail = [*extra, '--', script, *args]
    found = []
    if argv[-len(tail):] != tail:
        found.append(f'the argv does not end with the extra options, --, the script and its args: '
                     f'{argv!r:.300} should end {tail!r:.300}')
    # The element after the closing `--` is the operand sbatch reads as the script, also when an
    # extra option that takes a value consumed that `--`.
    operand = argv[-len(args) - 1] if len(argv) > len(args) + 1 else ''
    if operand.startswith('-'):
        found.append(f'the script path is readable as an option: {operand!r}')
    return found


@pytest.mark.unit
@pytest.mark.parametrize(('argv', 'fragment'), [
    pytest.param(['sbatch', *EXTRA, '--', '/j.sh', 'a b -v'], 'does not end', id='args-joined'),
    pytest.param(['sbatch', '--', '/j.sh', *EXTRA, 'a b', '-v'], 'does not end', id='extra-after-script'),
    pytest.param(['sbatch', *EXTRA, '/j.sh', 'a b', '-v'], 'does not end', id='no-dashdash'),
    pytest.param(['sbatch', '--exclusive --mail-type END,FAIL', '--', '/j.sh', 'a b', '-v'], 'does not end',
                 id='extra-joined'),
    pytest.param(['squeue', *EXTRA, '--', '/j.sh', 'a b', '-v'], 'not an sbatch argv', id='wrong-program'),
])
def test_sbatch_check_catches_planted_argvs(argv: list[str], fragment: str) -> None:
    assert sbatch_violations(['sbatch', *EXTRA, '--', '/j.sh', 'a b', '-v'], '/j.sh', ['a b', '-v'], EXTRA) == []
    violations = sbatch_violations(argv, '/j.sh', ['a b', '-v'], EXTRA)
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
def test_sbatch_check_catches_a_dash_script_operand() -> None:
    assert sbatch_violations(['sbatch', '-J', '--', './-x y'], './-x y', [], ['-J']) == []
    violations = sbatch_violations(['sbatch', '-J', '--', '-x y'], '-x y', [], ['-J'])
    assert any('readable as an option' in v for v in violations), violations


def _sbatch(args: dict[str, Any], era: str | None = None) -> tuple[Outcome, RecordingExecutor]:
    executor = RecordingExecutor('sbatch', responder=submitted)

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor), era=era) as client:
            return await call(client, 'sbatch', args)

    return drive(scenario()), executor


@pytest.mark.unit
def test_args_follow_the_script_and_extra_args_precede_dashdash(guard) -> None:
    outcome, executor = _sbatch({'script_path': '~/jobs/run.sh', 'args': HOSTILE_ARGS, 'extra_args': EXTRA,
                                 'job_name': 'j', 'partition': 'p'})
    assert not outcome.is_error, outcome.text
    assert outcome.text == argv_echo.JOB_ID
    [(_, argv, _, _)] = executor.calls
    violations = sbatch_violations(argv, '~/jobs/run.sh', HOSTILE_ARGS, EXTRA)
    if not isinstance(argv[argv.index('--') + 1], UserPath):
        violations.append(f'the script path is not a UserPath: {argv!r:.200}')
    if argv[:5] != ['sbatch', '-J', 'j', '-p', 'p']:
        violations.append(f"the tool's own options do not come first: {argv!r:.200}")
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('era', ERAS)
@pytest.mark.parametrize('param', ['extra_args', 'args'])
def test_a_string_list_parameter_is_refused_by_the_schema(param: str, era: str, guard) -> None:
    outcome, executor = _sbatch({'script_path': '/j.sh', param: '--exclusive --mail-type END'}, era=era)
    violations = [] if outcome.is_error else [f'{param} as a string was accepted, not refused']
    if outcome.is_error and not (param in outcome.text and 'valid list' in outcome.text):
        violations.append(f'the error does not name {param!r} and the list type: {outcome.text[:300]!r}')
    if executor.calls:
        violations.append(f'the executor was reached: {executor.calls!r:.200}')
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('content', [None, '#!/bin/bash\ntrue\n'])
def test_an_extra_dashdash_is_refused_before_anything_runs(content: str | None, guard) -> None:
    source = {'script_content': content} if content else {'script_path': '/j.sh'}
    outcome, executor = _sbatch({**source, 'extra_args': ['--exclusive', '--']})
    assert outcome.is_error and 'extra_args' in outcome.text, outcome.text
    assert executor.calls == []
    assert guard.refused == []


JOB_NAMES = [
    pytest.param('train', 'train', id='plain'),
    pytest.param('../up/a b;c', '_up_a_b_c', id='path-and-shell'),
    pytest.param('-x', 'x', id='leading-dash'),
    pytest.param(None, 'job', id='no-name'),
]


@pytest.mark.unit
@pytest.mark.parametrize(('job_name', 'stem'), JOB_NAMES)
def test_script_content_is_written_then_submitted(job_name: str | None, stem: str, guard) -> None:
    content = '#!/bin/bash\n#SBATCH -c 4\necho done'
    args = {'script_content': content, 'args': ['a b'], 'extra_args': ['--exclusive']}
    if job_name is not None:
        args['job_name'] = job_name
    outcome, executor = _sbatch(args)
    assert not outcome.is_error, outcome.text
    write, submit = executor.calls
    path = write[1]
    violations = []
    if not re.fullmatch(rf'~/jobs/{re.escape(stem)}_\d{{8}}_\d{{6}}_{TOKEN}\.sh', path) or not isinstance(path, UserPath):
        violations.append(f'the script path is not a ~/jobs UserPath named from {stem!r}: {path!r}')
    if write != ('write', path, content.encode(), False, '~/jobs', True):
        violations.append(f'the script is not written through the executor\'s write, exactly and executable: '
                          f'{write!r:.300}')
    if job_name is not None and submit[1][1:3] != ['-J', job_name]:
        violations.append(f'-J does not carry the job name literally: {submit[1]!r:.200}')
    violations += sbatch_violations(submit[1], path, ['a b'], ['--exclusive'])
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


# The docstring names each part of the path; its clock part is spelled in these letters.
STAMP_FIELDS = {'YYYY': '%Y', 'mm': '%m', 'dd': '%d', 'HH': '%H', 'MM': '%M', 'SS': '%S'}


def _docstring_pattern(doc: str, stem: str) -> str:
    """The path the `sbatch` docstring states, as a pattern for a script named from `stem`."""
    template = re.search(r'~/jobs/\S*<token>\S*\.sh', doc)
    width = re.search(r'(\d+) random hex digits', ' '.join(doc.split()))
    if template is None or width is None:
        raise AssertionError('the sbatch docstring states no ~/jobs/...<token>...sh path with its token width')
    pattern = ''
    for literal, field in re.findall(r'([^<]*)(?:<([^>]*)>)?', template.group()):
        pattern += re.escape(literal)
        if field == 'name':
            pattern += re.escape(stem)
        elif field == 'token':
            pattern += f'[0-9a-f]{{{width.group(1)}}}'
        elif field:
            stamp = re.sub('|'.join(STAMP_FIELDS), lambda m: STAMP_FIELDS[m.group()], field)
            if '%' not in stamp:
                raise AssertionError(f'the sbatch docstring names an unknown part <{field}>')
            pattern += re.escape(FROZEN.strftime(stamp))
    return pattern


@pytest.mark.unit
def test_the_docstring_states_the_script_path_the_tool_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)
    doc = inspect.getdoc(slurm.sbatch.fn) or ''
    violations = []
    for job_name, stem in [('train', 'train'), ('../up/a b;c', '_up_a_b_c'), ('-x', 'x'), ('...', 'job'),
                           (None, 'job')]:
        path = str(slurm._job_script_path(job_name))
        pattern = _docstring_pattern(doc, stem)
        if not re.fullmatch(pattern, path):
            violations.append(f'job_name {job_name!r}: {path!r} does not match the docstring ({pattern!r})')
    assert violations == [], '\n'.join(violations)


# Integration rows: the argv sbatch really receives.

def _run_sbatch(executor: Any, calls: list[dict[str, Any]]) -> list[Outcome]:
    async def scenario() -> list[Outcome]:
        async with in_memory(shared_server(executor)) as client:
            return [await call(client, 'sbatch', args) for args in calls]
    return drive(scenario(), timeout=120)


@pytest.fixture(params=['local', 'delegate'])
def mode(request: pytest.FixtureRequest, executes: Any) -> tuple[Any, Path]:
    """The executor of one mode, and the home its programs run in, with an argv-echo sbatch."""
    if request.param == 'local':
        executor, home = LocalShellExecutor(), executes.server_home
    else:
        executor, home = DelegatingExecutor('submitter', {'submitter': executes.user}), executes.mapped_home
    argv_echo.install(home, 'sbatch')
    return executor, home


@pytest.mark.integration
def test_sbatch_receives_each_value_as_one_argument(mode: tuple[Any, Path]) -> None:
    executor, home = mode
    cases = [
        ({'script_path': '-x y', 'args': HOSTILE_ARGS, 'extra_args': EXTRA}, './-x y', HOSTILE_ARGS, EXTRA),
        ({'script_path': '-x y', 'extra_args': ['-J']}, './-x y', [], ['-J']),
        ({'script_path': '~/jobs/run me.sh', 'args': ['$1', '`id`'], 'extra_args': ['-J']},
         f'{home}/jobs/run me.sh', ['$1', '`id`'], ['-J']),
    ]
    outcomes = _run_sbatch(executor, [args for args, *_ in cases])
    received = argv_echo.calls(home)
    violations = [f'call {i}: {o.text[:200]}' for i, o in enumerate(outcomes) if o.is_error or o.text != argv_echo.JOB_ID]
    if len(received) != len(cases):
        violations.append(f'sbatch ran {len(received)} times, expected {len(cases)}: {received!r:.300}')
    for (args, script, job_args, extra), (program, argv) in zip(cases, received):
        violations += sbatch_violations([program, *argv], script, job_args, extra)
    assert violations == [], '\n'.join(violations)


# Calls in one second: each gets its own script path, whatever the job names have in common.

SAME_SECOND = [
    pytest.param('sweep', 'sweep', 'sweep', id='same-name'),
    pytest.param(None, None, 'job', id='no-name'),
    pytest.param('a/b', 'a_b', 'a_b', id='sanitised-alike'),
]


def _content_call(content: str, job_name: str | None) -> dict[str, Any]:
    args: dict[str, Any] = {'script_content': content}
    if job_name is not None:
        args['job_name'] = job_name
    return args


@pytest.mark.unit
@pytest.mark.parametrize(('first', 'second', 'stem'), SAME_SECOND)
def test_same_second_scripts_get_their_own_paths(first: str | None, second: str | None, stem: str,
                                                 monkeypatch: pytest.MonkeyPatch, guard) -> None:
    freeze_clock(monkeypatch)
    contents = ['#!/bin/bash\necho first call\n', '#!/bin/bash\necho second call\n']
    calls = [_content_call(content, name) for content, name in zip(contents, (first, second))]
    executor = RecordingExecutor('sbatch', responder=submitted)

    async def scenario() -> list[Outcome]:
        async with in_memory(shared_server(executor)) as client:
            return [await call(client, 'sbatch', args) for args in calls]

    outcomes = drive(scenario())
    violations = [f'call {i}: {o.text[:200]}' for i, o in enumerate(outcomes) if o.is_error or o.text != argv_echo.JOB_ID]
    writes = [c for c in executor.calls if c[0] == 'write']
    runs = [c for c in executor.calls if c[0] == 'run']
    if len(writes) != 2 or len(runs) != 2:
        violations.append(f'expected two writes and two submissions: {executor.calls!r:.300}')
    violations += shared_violations([write[1] for write in writes])
    for i, (write, run, content) in enumerate(zip(writes, runs, contents)):
        if not re.fullmatch(script_name(stem), write[1]):
            violations.append(f'call {i}: {write[1]!r} is not ~/jobs/{stem}_{FROZEN_STAMP}_<16 hex digits>.sh')
        if write[2] != content.encode():
            violations.append(f'call {i}: wrote {write[2]!r:.100}, not its own content')
        if run[1][run[1].index('--') + 1:][:1] != [write[1]]:
            violations.append(f'call {i}: submitted {run[1]!r:.200}, not the path it wrote, {write[1]!r}')
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


# Overlapping calls: both writes land before either submission, so a shared path would run one
# call's job with the other call's script.

class HoldAfterWrite:
    """An executor that hands every call to `inner`, and holds each write at `barrier`.

    Each write waits there until every party has written, so the order is write, write, run, run.
    It records, under the thread the tool body runs on (one per call), the path and bytes each
    write sent and, at each `sbatch`, the bytes of the script being submitted as they are then.
    The submissions themselves take turns under `lock`: the argv-echo stand-in numbers its records
    by counting those already there, so two at once could write one record.
    """

    def __init__(self, inner: Executor, home: Path, barrier: threading.Barrier,
                 log: list[tuple[str, int, str, bytes | None]], lock: threading.Lock) -> None:
        self.inner, self.home, self.barrier, self.log, self.lock = inner, home, barrier, log, lock

    @property
    def hostname(self) -> str:
        return self.inner.hostname

    def run(self, command: Sequence[str] | str, cwd: str | None = None, timeout: float | None = None) -> CommandResult:
        if not isinstance(command, str) and list(command)[:1] == ['sbatch'] and '--' in command:
            script = str(list(command)[list(command).index('--') + 1])
            on_disk = self.home / script.removeprefix('~/')
            with self.lock:
                self.log.append(('run', threading.get_ident(), script,
                                 on_disk.read_bytes() if on_disk.is_file() else None))
                return self.inner.run(command, cwd=cwd, timeout=timeout)
        return self.inner.run(command, cwd=cwd, timeout=timeout)

    def write(self, path: str, data: bytes, append: bool = False, parent: str | None = None,
              executable: bool = False) -> CommandResult:
        result = self.inner.write(path, data, append=append, parent=parent, executable=executable)
        with self.lock:
            self.log.append(('write', threading.get_ident(), str(path), data))
        self.barrier.wait()
        return result

    def put(self, local_path: str, remote_path: str) -> str:
        return self.inner.put(local_path, remote_path)

    def get(self, remote_path: str, local_path: str) -> str:
        return self.inner.get(remote_path, local_path)

    def open(self) -> None:
        self.inner.open()

    def close(self) -> None:
        self.inner.close()

    def __enter__(self) -> HoldAfterWrite:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


# A second executor of each mode, over the same home as the `mode` fixture's: a second server.
SECOND_EXECUTOR: dict[str, Callable[[Any], Executor]] = {
    'local': lambda executes: LocalShellExecutor(),
    'delegate': lambda executes: DelegatingExecutor('submitter', {'submitter': executes.user}),
}


@pytest.mark.integration
@pytest.mark.parametrize('servers', ['one-server', 'two-servers'])
def test_overlapping_submissions_each_submit_their_own_script(mode: tuple[Any, Path], servers: str,
                                                              request: pytest.FixtureRequest, executes: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)
    executor, home = mode
    contents = ['#!/bin/bash\necho first job\n', '#!/bin/bash\necho second job, a longer script\n']
    barrier, lock = threading.Barrier(2, timeout=60), threading.Lock()
    log: list[tuple[str, int, str, bytes | None]] = []
    first = HoldAfterWrite(executor, home, barrier, log, lock)
    second = first if servers == 'one-server' else \
        HoldAfterWrite(SECOND_EXECUTOR[request.node.callspec.params['mode']](executes), home, barrier, log, lock)

    async def scenario() -> list[Outcome]:
        rendezvous = Rendezvous(2, timeout=60)
        server_a = shared_server(first, rendezvous)
        server_b = server_a if servers == 'one-server' else shared_server(second, rendezvous)
        async with in_memory(server_a) as a, in_memory(server_b) as b:
            return list(await asyncio.gather(*(call(client, 'sbatch', _content_call(content, 'sweep'))
                                               for client, content in zip((a, b), contents))))

    outcomes = drive(scenario(), timeout=120)
    violations = [f'call {i}: {o.text[:200]}' for i, o in enumerate(outcomes) if o.is_error or o.text != argv_echo.JOB_ID]
    writes = {ident: (path, data) for kind, ident, path, data in log if kind == 'write'}
    runs = {ident: (path, data) for kind, ident, path, data in log if kind == 'run'}
    if len(writes) != 2 or len(runs) != 2 or set(writes) != set(runs):
        violations.append(f'expected two calls, each writing and submitting once: {log!r:.400}')
    if [kind for kind, *_ in log] != ['write', 'write', 'run', 'run']:
        violations.append(f'the calls did not overlap as write, write, run, run: {log!r:.400}')
    violations += shared_violations([path for path, _ in writes.values()])
    received = [argv[argv.index('--') + 1] for _, argv in argv_echo.calls(home)]
    if len(received) != 2:
        violations.append(f'sbatch ran {len(received)} times, expected 2: {received!r:.300}')
    violations += shared_violations(received)
    for ident, (path, sent) in writes.items():
        if not re.fullmatch(script_name('sweep'), path):
            violations.append(f'{path!r} is not ~/jobs/sweep_{FROZEN_STAMP}_<16 hex digits>.sh')
        submitted_path, ran = runs.get(ident, (None, None))
        if submitted_path != path:
            violations.append(f'a call wrote {path!r} and submitted {submitted_path!r}')
        if ran != sent:
            violations.append(f'a call ran another call\'s script: it sent {sent!r:.80}, its job was given {ran!r:.80}')
        after = home / path.removeprefix('~/')
        if not after.is_file() or after.read_bytes() != sent:
            violations.append(f'{path!r} no longer holds its own call\'s bytes {sent!r:.80}')
    assert violations == [], '\n'.join(violations)


# Separate server processes: each draws its path alone, alike in clock, process id and host name,
# so a per-process counter or a generator seeded from any of them would repeat.

PROCESS_CHILD = '''\
import os, platform, socket, sys
from datetime import datetime

os.getpid = lambda: 4242
os.getppid = lambda: 1
socket.gethostname = lambda: 'login-node'
platform.node = lambda: 'login-node'

import rcac_mcp
from rcac_mcp.tools import slurm


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat(sys.argv[1])


slurm.datetime = Frozen
print(os.path.dirname(os.path.realpath(rcac_mcp.__file__)))
print(slurm._job_script_path('sweep'))
'''


@pytest.mark.integration
def test_separate_server_processes_name_scripts_apart() -> None:
    package = str(Path(rcac_mcp.__file__).resolve().parent)
    paths, violations = [], []
    for i in range(2):
        child = subprocess.run([str(SANDBOX), sys.executable, '-c', PROCESS_CHILD, FROZEN.isoformat()],
                               capture_output=True, text=True, timeout=120)
        lines = child.stdout.splitlines()
        if child.returncode != 0 or len(lines) != 2:
            violations.append(f'process {i} exited {child.returncode}: {child.stdout[-200:]!r} {child.stderr[-400:]!r}')
            continue
        origin, path = lines
        if origin != package:
            violations.append(f'process {i} imported rcac_mcp from {origin!r}, not {package!r}')
        if not re.fullmatch(script_name('sweep'), path):
            violations.append(f'process {i}: {path!r} is not ~/jobs/sweep_{FROZEN_STAMP}_<16 hex digits>.sh')
        paths.append(path)
    violations += shared_violations(paths)
    assert violations == [], '\n'.join(violations)
