# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""`extra_args` on `squeue`, `scancel`, `sacct` and `sinfo` is a list: one argument per element.

Each element reaches the program as exactly one literal argument, an option on purpose: never
interpreted by a shell, never split. The elements follow the tool's own options (for `scancel`,
before the `--` that precedes the job ids); an element `--` is refused before anything runs. A
string is refused by schema validation, in both protocol eras, with an error naming `extra_args`
and the list type. (`sbatch`'s `extra_args` is pinned with its other parameters in
`tests/test_sbatch.py`.)

The `unit` rows read the argv each tool hands a `RecordingExecutor`; the `integration` rows run
each tool in `local` mode (the `executes` fixture) against an argv-echo stand-in
(`tests/argv_echo.py`), so they see the arguments exactly as the program received them.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

import argv_echo
from harness import ERAS, Outcome, RecordingExecutor, call, drive, in_memory, shared_server
from rcac_mcp.executor.shell import LocalShellExecutor

TOOLS = ('squeue', 'scancel', 'sacct', 'sinfo')

# Each tool's own arguments for the rows (scancel needs a target), and the argv the tool's own
# options make, which the extra options must follow.
OWN = {
    'squeue': ({'partition': 'gpu'}, ['squeue', '--me', '-p', 'gpu']),
    'scancel': ({'job_ids': [41, 42], 'name': 'a job'}, ['scancel', '-n', 'a job']),
    'sacct': ({'user': 'alice', 'format': 'JobID,State'}, ['sacct', '-X', '-u', 'alice', '-o', 'JobID,State']),
    'sinfo': ({'state': 'idle'}, ['sinfo', '-t', 'idle']),
}

# Positionals that follow the extra options.
AFTER = {'scancel': ['--', '41', '42']}

# Options a shell would split, expand or glob; each must arrive as one argument.
EXTRA = ['--format', '%i "%j" $USER `id` *', 'two words', '-l', 'line\nbreak', '']


def extra_violations(tool: str, argv: Sequence[str], extra: Sequence[str]) -> list[str]:
    """Every way `argv` does not carry `extra` as its own elements right after the tool's own options."""
    expected = [*OWN[tool][1], *extra, *AFTER.get(tool, [])]
    if list(argv) != expected:
        return [f'{tool}: the extra options are not their own elements after the tool\'s options: '
                f'{list(argv)!r:.300} != {expected!r:.300}']
    return []


@pytest.mark.unit
@pytest.mark.parametrize(('argv', 'fragment'), [
    pytest.param(['sinfo', '-t', 'idle', ' '.join(EXTRA)], 'not their own elements', id='joined'),
    pytest.param(['sinfo', *EXTRA, '-t', 'idle'], 'not their own elements', id='before-own-options'),
    pytest.param(['sinfo', '-t', 'idle', EXTRA[0], *EXTRA[1].split(), *EXTRA[2:]], 'not their own elements', id='split'),
])
def test_extra_check_catches_planted_argvs(argv: list[str], fragment: str) -> None:
    assert extra_violations('sinfo', ['sinfo', '-t', 'idle', *EXTRA], EXTRA) == []
    violations = extra_violations('sinfo', argv, EXTRA)
    assert any(fragment in v for v in violations), violations


def _call(tool: str, args: dict[str, Any], era: str | None = None) -> tuple[Outcome, RecordingExecutor]:
    executor = RecordingExecutor(tool)

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor), era=era) as client:
            return await call(client, tool, args)

    return drive(scenario()), executor


@pytest.mark.unit
@pytest.mark.parametrize('tool', TOOLS)
def test_extra_args_follow_the_tools_own_options(tool: str, guard) -> None:
    outcome, executor = _call(tool, {**OWN[tool][0], 'extra_args': EXTRA})
    assert not outcome.is_error, outcome.text
    [(_, argv, *_)] = [c for c in executor.calls if c[1] != ['whoami']]
    violations = extra_violations(tool, argv, EXTRA)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('era', ERAS)
@pytest.mark.parametrize('tool', TOOLS)
def test_a_string_extra_args_is_refused_by_the_schema(tool: str, era: str, guard) -> None:
    outcome, executor = _call(tool, {**OWN[tool][0], 'extra_args': '--long --format %i'}, era=era)
    violations = [] if outcome.is_error else [f'{tool}: extra_args as a string was accepted, not refused']
    if outcome.is_error and not ('extra_args' in outcome.text and 'valid list' in outcome.text):
        violations.append(f'{tool}: the error does not name extra_args and the list type: {outcome.text[:300]!r}')
    if executor.calls:
        violations.append(f'{tool}: the executor was reached: {executor.calls!r:.200}')
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('tool', TOOLS)
def test_an_extra_dashdash_is_refused_before_anything_runs(tool: str, guard) -> None:
    outcome, executor = _call(tool, {**OWN[tool][0], 'extra_args': ['--long', '--']})
    assert outcome.is_error and 'extra_args' in outcome.text, outcome.text
    assert executor.calls == []
    assert guard.refused == []


@pytest.mark.integration
@pytest.mark.parametrize('tool', TOOLS)
def test_extra_args_reach_the_program_literally(tool: str, executes: Any) -> None:
    argv_echo.install(executes.server_home, tool)

    async def scenario() -> Outcome:
        async with in_memory(shared_server(LocalShellExecutor())) as client:
            return await call(client, tool, {**OWN[tool][0], 'extra_args': EXTRA})

    outcome = drive(scenario(), timeout=60)
    assert not outcome.is_error, outcome.text
    received = argv_echo.calls(executes.server_home)
    violations = [] if len(received) == 1 else [f'{tool} ran {len(received)} times: {received!r:.300}']
    violations += [v for program, argv in received for v in extra_violations(tool, [program, *argv], EXTRA)]
    assert violations == [], '\n'.join(violations)
