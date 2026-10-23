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
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

import pytest

import argv_echo
from harness import ERAS, Outcome, RecordingExecutor, call, drive, in_memory, shared_server
from rcac_mcp.executor import UserPath
from rcac_mcp.executor.delegate import DelegatingExecutor
from rcac_mcp.executor.shell import LocalShellExecutor

# Values that a shell would split, expand or read as an option.
HOSTILE_ARGS = ['two words', '-v', '--', '$HOME', '*', 'line\nbreak', "it's", '']
EXTRA = ['--exclusive', '--mail-type', 'END,FAIL']


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
    if not re.fullmatch(rf'~/jobs/{re.escape(stem)}_\d{{8}}_\d{{6}}\.sh', path) or not isinstance(path, UserPath):
        violations.append(f'the script path is not a ~/jobs UserPath named from {stem!r}: {path!r}')
    if write != ('write', path, content.encode(), False, '~/jobs', True):
        violations.append(f'the script is not written through the executor\'s write, exactly and executable: '
                          f'{write!r:.300}')
    if job_name is not None and submit[1][1:3] != ['-J', job_name]:
        violations.append(f'-J does not carry the job name literally: {submit[1]!r:.200}')
    violations += sbatch_violations(submit[1], path, ['a b'], ['--exclusive'])
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


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
