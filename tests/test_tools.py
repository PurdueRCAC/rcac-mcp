# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Every served tool does its work on the executor the middleware installed, and on nothing else.

Tools never construct an executor; they call `get_executor()` (§ Invariants §1, §7). Each tool in
`EXPECTED_TOOLS` is called through the in-memory MCP client on a server whose
`SharedExecutorMiddleware` installs a `RecordingExecutor`. The call must reach that executor, carry
the tool's own argument there, and hand the executor's answer back to the client, while the unit
guard refuses every process and connection made anywhere else. A tool that built its own
`LocalShellExecutor` or `SSHExecutor` would trip the guard, which this module reports as a launch
outside the executor.

A tool hands the executor an argv, never a command string (only `run_command` passes a script), and
a shell argv may run only a registered fixed script (`rcac_mcp.scripts.FIXED_SCRIPTS`), so no
caller value is ever formatted into shell text. The filesystem, Slurm and site tools and the context
resource are also pinned to their exact argvs, with each path a `UserPath`: each option value is its
own element, after its option, and a positional value follows `--`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

from harness import Outcome, RecordingExecutor, call, drive, in_memory, shared_server
from rcac_mcp import resources
from rcac_mcp.executor import UserPath
from rcac_mcp.scripts import script_argv, script_of
from rcac_mcp.tools import filesystem
from test_registry import EXPECTED_TOOLS

# The one tool that hands the executor a command string: `run_command`, whose command is the
# caller's script.
PASSES_A_SCRIPT = frozenset({'run_command'})

# Interpreters whose `-c` argument is shell text.
SHELLS = frozenset({'bash', 'sh', 'zsh', 'ksh', 'dash', 'csh', 'tcsh'})

# What the installed executor answers when a case gives no responder of its own; a result carrying
# it can only have come from that executor.
ANSWER = 'answered-by-the-installed-executor'

# Stands for the test's tmp_path in a case's arguments and expectations.
TMP = '{tmp}'

LS_LINES = ('total 8\n'
            'drwxr-xr-x 2 alice lab 4096 2026-10-16 12:00 installed-executor-entry\n'
            '-rw-r--r-- 1 alice lab   12 2026-10-16 12:00 notes.txt\n')

QUOTA_TABLE = ('Type        Location        Size    Limit   Use     Files   Limit   Use\n'
               '==============================================================================\n'
               'home        alice           14.6GB  25.0GB  58.6%   -       -       -\n'
               'scratch     alice           1.2TB   200TB   0.6%    10k     2000k   0.5%\n'
               'depot       lab             3.0TB   10TB    30.0%   -       -       -\n')


def answer(command: Sequence[str] | str) -> str:
    return ANSWER


def ls_listing(command: Sequence[str] | str) -> str:
    # The listing script prints the directory it changed into, a NUL, then the listing.
    if script_of(command) == filesystem.LIST:
        return f'{command[command.index("-c") + 3]}\0{LS_LINES}'
    return ANSWER


def quota_and_scratch(command: Sequence[str] | str) -> str:
    if command == ['findscratch']:
        return '/scratch/test/installed-executor-scratch'
    return QUOTA_TABLE if command == ['myquota'] else ANSWER


def submitted(command: Sequence[str] | str) -> str:
    return 'Submitted batch job 987654321' if command[:1] == ['sbatch'] else ANSWER


def cluster(command: Sequence[str] | str) -> str:
    # The answers the Slurm and site tools parse: the user, the scratch path, the quota, a job id.
    if command == ['whoami']:
        return 'alice\n'
    return submitted(command) if command[:1] == ['sbatch'] else quota_and_scratch(command)


@dataclass(frozen=True)
class ToolCase:
    """How one tool is called, what the installed executor answers, and what must come back.

    `reaches` is a fragment some recorded executor call must carry (the tool's own argument, or the
    command it wraps); `expect` must appear in the result text, or is None for a tool that returns
    nothing; `stdin`, when given, is what some call must carry on stdin. `TMP` in either stands for
    the test's tmp_path.
    """

    args: dict[str, Any]
    reaches: str
    expect: str | None = ANSWER
    responder: Callable[[Sequence[str] | str], str] = field(default=answer)
    stdin: bytes | None = None

    def arguments(self, tmp_path: Path) -> dict[str, Any]:
        """The call's arguments with `TMP` resolved to `tmp_path`."""
        return {k: v.replace(TMP, str(tmp_path)) if isinstance(v, str) else v for k, v in self.args.items()}

    def expected(self, tmp_path: Path) -> str | None:
        return None if self.expect is None else self.expect.replace(TMP, str(tmp_path))


TOOL_CASES: dict[str, ToolCase] = {
    'average_wait': ToolCase({'partition': 'wait-partition'}, 'wait-partition'),
    'download_file': ToolCase({'remote_path': '/remote/results.csv', 'local_path': f'{TMP}/results.csv'},
                              '/remote/results.csv', expect=f'installed:{TMP}/results.csv'),
    'jobcmd': ToolCase({'job_id': 4242001}, '4242001'),
    'jobenv': ToolCase({'job_id': 4242002}, '4242002'),
    'jobinfo': ToolCase({'job_id': 4242003}, '4242003'),
    'jobscript': ToolCase({'job_id': 4242004}, '4242004'),
    'list_directory': ToolCase({'path': '/data/listed-dir'}, '/data/listed-dir',
                               expect='/data/listed-dir/installed-executor-entry', responder=ls_listing),
    'myquota': ToolCase({}, 'myquota'),
    'read_file': ToolCase({'path': '/data/read-me.txt'}, '/data/read-me.txt'),
    'run_command': ToolCase({'command': 'echo tool-check'}, 'echo tool-check'),
    'sacct': ToolCase({'user': 'sacct-user', 'job_ids': [4242005], 'extra_args': ['--parsable2']}, '4242005'),
    'sbatch': ToolCase({'script_path': '/data/job.sh', 'partition': 'batch-partition', 'args': ['job-arg one'],
                        'extra_args': ['--exclusive']}, 'job-arg one', expect='987654321', responder=submitted),
    'scancel': ToolCase({'job_ids': [4242006], 'extra_args': ['--signal', 'USR1']}, '4242006'),
    'scontrol_show_job': ToolCase({'job_id': 4242007}, '4242007'),
    'scontrol_show_node': ToolCase({'node': 'node-a000'}, 'node-a000'),
    'sfeatures': ToolCase({}, 'sfeatures'),
    'showpartitions': ToolCase({}, 'showpartitions'),
    'sinfo': ToolCase({'partition': 'info-partition', 'extra_args': ['--Node']}, 'info-partition'),
    'slist': ToolCase({}, 'slist'),
    'squeue': ToolCase({'partition': 'queue-partition', 'extra_args': ['--long']}, 'queue-partition'),
    'storage_paths': ToolCase({}, 'findscratch', expect='/scratch/test/installed-executor-scratch',
                              responder=quota_and_scratch),
    'upload_file': ToolCase({'local_path': '/data/upload-in.csv', 'remote_path': '/remote/upload-out.csv'},
                            '/remote/upload-out.csv', expect='installed:/remote/upload-out.csv'),
    'write_file': ToolCase({'path': '/data/written.txt', 'content': 'written-by-the-tool'}, '/data/written.txt',
                           expect=None, stdin=b'written-by-the-tool'),
}


def tool_violations(outcome: Outcome, calls: Sequence[tuple[Any, ...]], refused: Sequence[Any],
                    expect: str | None, reaches: str | None = None, scripts_allowed: bool = False,
                    stdin: bytes | None = None) -> list[str]:
    """Return every way a tool call bypassed or ignored its installed executor; empty means it did not.

    `calls` is what the installed executor recorded, `refused` what the unit guard refused (a
    process or connection made anywhere else), `expect` a fragment the client's result must carry
    (None for a tool that returns nothing), `reaches` a fragment some recorded call must carry, and
    `stdin` the bytes some call must carry on stdin. Unless `scripts_allowed`, every command must be
    an argv; in any case a shell argv must run a registered fixed script.
    """
    violations: list[str] = []
    if outcome.is_error:
        violations.append(f'error result: {outcome.text!r}')
    if not calls:
        violations.append('the tool never reached the executor: it recorded no call')
    elif reaches is not None and not any(reaches in str(part) for c in calls for part in c[1:]):
        violations.append(f'the tool never reached the executor with {reaches!r}: {list(calls)!r}')
    runs = [c for c in calls if c[0] == 'run']
    for _, command, *rest in runs:
        if isinstance(command, str):
            if not scripts_allowed:
                violations.append(f'passed a command string, not an argv: {command!r:.200}')
            continue
        if command and str(command[0]).rsplit('/', 1)[-1] in SHELLS:
            script = command[command.index('-c') + 1] if '-c' in command[:-1] else None
            if script_of(command) is None:
                violations.append(f'unregistered script: the shell argv runs {script!r:.200}, '
                                  'which is not in FIXED_SCRIPTS behind the reset')
    if stdin is not None and not any(c[4] == stdin for c in runs):
        violations.append(f'no executor call carried {stdin!r} on stdin: {list(calls)!r:.300}')
    for attempt in refused:
        violations.append(f'launched outside the executor: {attempt!r}')
    if expect is not None and expect not in outcome.text:
        violations.append(f'result lacks {expect!r}: {outcome.text!r}')
    return violations


@pytest.mark.unit
@pytest.mark.parametrize('tool', sorted(EXPECTED_TOOLS))
def test_tool_reaches_only_the_installed_executor(tool: str, tmp_path: Path, guard) -> None:
    case = TOOL_CASES[tool]
    executor = RecordingExecutor('installed', responder=case.responder)

    async def scenario() -> Outcome:
        server = shared_server(executor)
        async with in_memory(server) as client:
            return await call(client, tool, case.arguments(tmp_path))

    outcome = drive(scenario())
    violations = tool_violations(outcome, executor.calls, guard.refused, case.expected(tmp_path), case.reaches,
                                 scripts_allowed=tool in PASSES_A_SCRIPT, stdin=case.stdin)
    # A joined string, not the list: pytest abbreviates a long list's repr, which can cut a
    # violation out of the failure text.
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_tool_cases_cover_expected_tools() -> None:
    assert set(TOOL_CASES) == set(EXPECTED_TOOLS), sorted(set(TOOL_CASES) ^ set(EXPECTED_TOOLS))


GOOD = Outcome(False, f'{ANSWER} for sinfo')
CALLS = [('run', ['sinfo', '-p', 'gpu'], None, None, None)]
REGISTERED = script_argv(filesystem.WRITE, 'write_file', '/data/out')


@pytest.mark.unit
@pytest.mark.parametrize(('outcome', 'calls', 'refused', 'fragment'), [
    pytest.param(GOOD, [], [], 'never reached the executor', id='no-executor-call'),
    pytest.param(GOOD, CALLS, [('subprocess.Popen', (['/bin/sh', '-c', 'sinfo -p gpu'],))],
                 'launched outside the executor', id='outside-launch'),
    pytest.param(Outcome(False, 'something else'), CALLS, [], 'result lacks', id='result-dropped'),
    pytest.param(Outcome(True, f'{ANSWER} for sinfo'), CALLS, [], 'error result', id='error-result'),
])
def test_tool_check_catches_bypass(outcome: Outcome, calls: list[tuple[Any, ...]], refused: list[Any],
                                   fragment: str) -> None:
    assert tool_violations(GOOD, CALLS, [], ANSWER, 'gpu') == []
    assert tool_violations(Outcome(False, ''), CALLS, [], None) == []
    violations = tool_violations(outcome, calls, refused, ANSWER, 'gpu')
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
def test_tool_check_requires_the_tool_argument_at_the_executor() -> None:
    violations = tool_violations(GOOD, [('run', ['sinfo'], None, None, None)], [], ANSWER, 'gpu')
    assert any('never reached the executor' in v for v in violations), violations


@pytest.mark.unit
@pytest.mark.parametrize(('calls', 'kwargs', 'fragment'), [
    pytest.param([('run', 'sinfo -p gpu', None, None, None)], {}, 'passed a command string', id='command-string'),
    pytest.param([('run', ['/bin/bash', '--posix', '-c', 'cat >"/data/gpu"', 'write_file'], None, None, b'x')], {},
                 'unregistered script', id='interpolated-script'),
    pytest.param([('run', ['sh', '-c', 'sinfo -p gpu'], None, None, None)], {}, 'unregistered script',
                 id='plain-shell'),
    pytest.param([('run', ['bash', 'job-gpu.sh'], None, None, None)], {}, 'unregistered script', id='shell-no-c'),
    pytest.param([('run', [*REGISTERED, 'gpu'], None, None, b'other')], {'stdin': b'gpu'}, 'on stdin',
                 id='stdin-missing'),
])
def test_tool_check_catches_strings_and_scripts(calls: list[tuple[Any, ...]], kwargs: dict[str, Any],
                                                fragment: str) -> None:
    # Controls: an argv, a registered script with its stdin, `head -c` (not a shell), and a string
    # where scripts are allowed all pass.
    assert tool_violations(GOOD, [('run', [*REGISTERED, 'gpu'], None, None, b'gpu')], [], ANSWER, 'gpu',
                           stdin=b'gpu') == []
    assert tool_violations(GOOD, [('run', ['head', '-c', '9', '--', 'gpu'], None, None, None)], [], ANSWER, 'gpu') == []
    assert tool_violations(GOOD, [('run', 'sinfo -p gpu', None, None, None)], [], ANSWER, 'gpu',
                           scripts_allowed=True) == []
    violations = tool_violations(GOOD, calls, [], ANSWER, 'gpu', **kwargs)
    assert any(fragment in v for v in violations), violations


def argv_violations(calls: Sequence[tuple[Any, ...]], expected: Sequence[tuple[Any, ...]],
                    paths: Sequence[tuple[int, int]] = ()) -> list[str]:
    """Every way the recorded calls differ from `expected`; `paths` are (call, element) UserPaths."""
    violations = []
    if [tuple(c) for c in calls] != [tuple(c) for c in expected]:
        violations.append(f'argv differs: recorded {list(calls)!r:.400}, expected {list(expected)!r:.400}')
    for at, element in paths:
        if at < len(calls) and not isinstance(calls[at][1][element], UserPath):
            violations.append(f'argv differs: element {element} of call {at} is not a UserPath: {calls[at][1]!r:.200}')
    return violations


def _run(command: list[str], stdin: bytes | None = None) -> tuple[Any, ...]:
    return ('run', command, None, None, stdin)


FILESYSTEM_ARGVS = {
    'read-cat': ('read_file', {'path': '-n x'}, [_run(['cat', '--', '-n x'])], [(0, 2)]),
    'read-head': ('read_file', {'path': '~/log', 'max_size': 64}, [_run(['head', '-c', '64', '--', '~/log'])],
                  [(0, 4)]),
    'list': ('list_directory', {'path': '~/d d', 'show_hidden': True},
             [_run(script_argv(filesystem.LIST, 'list_directory', '~/d d', '-a'))], [(0, 6)]),
    'write': ('write_file', {'path': 'out', 'content': 'c'},
              [_run(script_argv(filesystem.WRITE, 'write_file', 'out'), b'c')], [(0, 6)]),
    'append': ('write_file', {'path': 'out', 'content': 'c', 'append': True},
               [_run(script_argv(filesystem.APPEND, 'write_file', 'out'), b'c')], [(0, 6)]),
    'write-in-dir': ('write_file', {'path': '~/a b/out', 'content': '', 'create_dirs': True},
                     [_run(script_argv(filesystem.WRITE_IN_DIR, 'write_file', '~/a b/out', '~/a b'), b'')],
                     [(0, 6), (0, 7)]),
    'append-in-dir': ('write_file', {'path': '/x/y', 'content': 'c', 'append': True, 'create_dirs': True},
                      [_run(script_argv(filesystem.APPEND_IN_DIR, 'write_file', '/x/y', '/x'), b'c')], [(0, 6), (0, 7)]),
    'create-dirs-no-parent': ('write_file', {'path': 'out', 'content': 'c', 'create_dirs': True},
                              [_run(script_argv(filesystem.WRITE, 'write_file', 'out'), b'c')], [(0, 6)]),
    'upload': ('upload_file', {'local_path': '~/in', 'remote_path': '~/out'}, [('put', '~/in', '~/out')], []),
    'download': ('download_file', {'remote_path': '~/in', 'local_path': '~/out'}, [('get', '~/in', '~/out')], []),
}


@pytest.mark.unit
@pytest.mark.parametrize('row', sorted(FILESYSTEM_ARGVS))
def test_filesystem_argvs(row: str, guard) -> None:
    tool, args, expected, paths = FILESYSTEM_ARGVS[row]
    executor = RecordingExecutor('argvs', responder=ls_listing)

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor)) as client:
            return await call(client, tool, args)

    outcome = drive(scenario())
    assert not outcome.is_error, outcome.text
    violations = argv_violations(executor.calls, expected, paths)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


SBATCH_ALL = {'script_path': '-x y', 'args': ['a b', '-v'], 'extra_args': ['--exclusive'], 'job_name': 'j k',
              'account': 'acct', 'partition': '-p', 'qos': 'standby', 'time': '1:00:00', 'nodes': 2, 'ntasks': 3,
              'cpus_per_task': 4, 'mem': '4G', 'gpus': 'mi210:2', 'constraint': 'a|b', 'output': '~/o %j.out',
              'error': 'e.err'}

SLURM_ARGVS = {
    'sbatch': ('sbatch', SBATCH_ALL,
               [_run(['sbatch', '-J', 'j k', '-A', 'acct', '-p', '-p', '-q', 'standby', '-t', '1:00:00', '-N', '2',
                      '-n', '3', '-c', '4', '--mem', '4G', '--gpus', 'mi210:2', '-C', 'a|b', '-o', '~/o %j.out',
                      '-e', 'e.err', '--exclusive', '--', '-x y', 'a b', '-v'])], [(0, 24), (0, 26), (0, 29)]),
    'squeue-default': ('squeue', {}, [_run(['squeue', '--me'])], []),
    'squeue': ('squeue', {'user': 'u v', 'account': 'a', 'partition': '-x', 'state': 'PD', 'job_ids': [1, 2],
                          'extra_args': ['--long', '-o', '%i %j']},
               [_run(['squeue', '-u', 'u v', '-A', 'a', '-p', '-x', '-t', 'PD', '-j', '1,2', '--long', '-o', '%i %j'])],
               []),
    'scancel': ('scancel', {'job_ids': [7, 8], 'me': True, 'user': 'u', 'account': 'a', 'partition': 'p',
                            'state': 's', 'name': 'n m', 'extra_args': ['--signal', 'USR1']},
                [_run(['scancel', '--me', '-u', 'u', '-A', 'a', '-p', 'p', '-t', 's', '-n', 'n m', '--signal', 'USR1',
                       '--', '7', '8'])], []),
    'sacct-me': ('sacct', {}, [_run(['whoami']), _run(['sacct', '-X', '-u', 'alice'])], []),
    'sacct': ('sacct', {'user': 'u', 'account': 'a', 'job_ids': [5], 'name': '-n x', 'state': 'F',
                        'starttime': '2024-01-01', 'endtime': 'now', 'format': 'JobID,JobName%30',
                        'extra_args': ['--parsable2']},
              [_run(['sacct', '-X', '-u', 'u', '-A', 'a', '-j', '5', '--name', '-n x', '-s', 'F', '-S', '2024-01-01',
                     '-E', 'now', '-o', 'JobID,JobName%30', '--parsable2'])], []),
    'sinfo': ('sinfo', {'partition': 'p', 'nodes': 'a[000-003]', 'state': 'idle', 'format': '%P "%a" $x',
                        'extra_args': ['--Node']},
              [_run(['sinfo', '-p', 'p', '-n', 'a[000-003]', '-t', 'idle', '-o', '%P "%a" $x', '--Node'])], []),
    'scontrol-job': ('scontrol_show_job', {'job_id': 5}, [_run(['scontrol', 'show', 'job', '5'])], []),
    'scontrol-node': ('scontrol_show_node', {'node': '-x'}, [_run(['scontrol', 'show', 'node', '--', '-x'])], []),
    'scontrol-all-nodes': ('scontrol_show_node', {}, [_run(['scontrol', 'show', 'node'])], []),
    'slist': ('slist', {}, [_run(['slist'])], []),
    'sfeatures': ('sfeatures', {}, [_run(['sfeatures'])], []),
    'myquota': ('myquota', {}, [_run(['myquota'])], []),
    'jobinfo': ('jobinfo', {'job_id': 5}, [_run(['jobinfo', '5'])], []),
    'jobcmd': ('jobcmd', {'job_id': 5}, [_run(['jobcmd', '5'])], []),
    'jobenv': ('jobenv', {'job_id': 5}, [_run(['jobenv', '5'])], []),
    'jobscript': ('jobscript', {'job_id': 5}, [_run(['jobscript', '5'])], []),
    'showpartitions': ('showpartitions', {}, [_run(['showpartitions'])], []),
    'average-wait': ('average_wait', {'partition': 'gpu', 'account': '-a'},
                     [_run(['average_wait', '--partition', 'gpu', '-A', '-a'])], []),
    'storage-paths': ('storage_paths', {}, [_run(['findscratch']), _run(['myquota'])], []),
}


@pytest.mark.unit
@pytest.mark.parametrize('row', sorted(SLURM_ARGVS))
def test_slurm_and_site_argvs(row: str, guard) -> None:
    tool, args, expected, paths = SLURM_ARGVS[row]
    executor = RecordingExecutor('argvs', responder=cluster)

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor)) as client:
            return await call(client, tool, args)

    outcome = drive(scenario())
    assert not outcome.is_error, outcome.text
    violations = argv_violations(executor.calls, expected, paths)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


def _empty(*names: str) -> dict[str, str]:
    return {name: '' for name in names}


# An optional scalar that is empty was not given: an option's value, an optional positional (a node
# name, a script path, a directory to list) or a cwd. The calls are the tool's default ones. Each
# row sets every such parameter of the tool to '' (or the one named), with what a call that sets
# none of them sends, and, for a tool that cannot run without the parameter, the refusal it gives
# instead (no call at all).
EMPTY_OPTIONALS = {
    'squeue-user': ('squeue', _empty('user'), [_run(['squeue', '--me'])], None),
    'squeue': ('squeue', _empty('user', 'account', 'partition', 'state'), [_run(['squeue', '--me'])], None),
    'scancel': ('scancel', {'job_ids': [7], **_empty('user', 'account', 'partition', 'state', 'name')},
                [_run(['scancel', '--', '7'])], None),
    'sacct': ('sacct', _empty('user', 'account', 'name', 'state', 'starttime', 'endtime', 'format'),
              [_run(['whoami']), _run(['sacct', '-X', '-u', 'alice'])], None),
    'sinfo': ('sinfo', _empty('partition', 'nodes', 'state', 'format'), [_run(['sinfo'])], None),
    'sbatch': ('sbatch', {'script_path': '/j.sh', **_empty('job_name', 'account', 'partition', 'qos', 'time', 'mem',
                                                          'gpus', 'constraint', 'output', 'error')},
               [_run(['sbatch', '--', '/j.sh'])], None),
    'sbatch-script-path': ('sbatch', _empty('script_path'), [], 'Either script_path or script_content'),
    'scontrol-node': ('scontrol_show_node', _empty('node'), [_run(['scontrol', 'show', 'node'])], None),
    'average-wait': ('average_wait', _empty('partition', 'account'), [_run(['average_wait'])], None),
    'list-directory': ('list_directory', _empty('path'), [_run(script_argv(filesystem.LIST, 'list_directory', '.'))],
                       None),
    'read-file-encoding': ('read_file', {'path': '/f', **_empty('encoding')}, [_run(['cat', '--', '/f'])], None),
    'run-command-cwd': ('run_command', {'command': 'pwd', **_empty('cwd')}, [('run', 'pwd', None, None, None)], None),
}

# Optional string parameters whose empty value is a value, not "not given": `script_content` is
# content, and an empty one is an empty job script, written byte for byte.
EMPTY_IS_A_VALUE = frozenset({('sbatch', 'script_content')})


@pytest.mark.unit
@pytest.mark.parametrize('row', sorted(EMPTY_OPTIONALS))
def test_empty_optional_scalars_mean_not_given(row: str, guard) -> None:
    tool, args, expected, refusal = EMPTY_OPTIONALS[row]
    executor = RecordingExecutor('empty', responder=cluster)

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor)) as client:
            return await call(client, tool, args)

    outcome = drive(scenario())
    violations = argv_violations(executor.calls, expected)
    if refusal is None and outcome.is_error:
        violations.append(f'error result: {outcome.text!r:.300}')
    if refusal is not None and not (outcome.is_error and refusal in outcome.text):
        violations.append(f'not refused as a call without {", ".join(args)}: {outcome.text!r:.300}')
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


def optional_scalars(tools: Sequence[Any]) -> set[tuple[str, str]]:
    """Every (tool, parameter) of `tools` that is an optional scalar string, not an enum."""
    found = set()
    for tool in tools:
        required = set(tool.parameters.get('required', []))
        for name, prop in tool.parameters['properties'].items():
            string = prop.get('type') == 'string' or {'type': 'string'} in prop.get('anyOf', [])
            if string and name not in required and 'enum' not in prop:
                found.add((tool.name, name))
    return found


@pytest.mark.unit
def test_empty_rows_cover_every_optional_scalar_option() -> None:
    # Every optional string parameter of every tool, an option's value or a positional, except
    # an enum (the schema refuses '' like any other non-member) and content.
    from rcac_mcp.tools import TOOL_REGISTRY
    expected = optional_scalars(TOOL_REGISTRY) - EMPTY_IS_A_VALUE
    covered = {(tool, name) for tool, args, *_ in EMPTY_OPTIONALS.values() for name, v in args.items() if v == ''}
    assert {('scontrol_show_node', 'node'), ('sbatch', 'script_path'), ('list_directory', 'path')} <= expected
    assert covered == expected, sorted(expected ^ covered)


@pytest.mark.unit
def test_slurm_argvs_cover_the_slurm_and_site_tools() -> None:
    from rcac_mcp.tools import rcac, slurm
    tools = {name for module in (slurm, rcac) for name, value in vars(module).items()
             if type(value).__name__ == 'FunctionTool'}
    assert tools and tools == {tool for tool, *_ in SLURM_ARGVS.values()}, tools


@pytest.mark.unit
def test_argv_check_catches_drift() -> None:
    good = [_run(['cat', '--', UserPath('/f')])]
    assert argv_violations(good, [_run(['cat', '--', '/f'])], [(0, 2)]) == []
    assert any('argv differs' in v for v in argv_violations([_run(['cat', '/f'])], [_run(['cat', '--', '/f'])]))
    assert any('not a UserPath' in v for v in argv_violations([_run(['cat', '--', '/f'])], [_run(['cat', '--', '/f'])],
                                                             [(0, 2)]))


@pytest.mark.unit
def test_context_resource_lists_with_a_fixed_script(guard) -> None:
    files = {'/etc/agents.d/a b.md': 'alpha\n', '/etc/agents.d/z.md': 'zulu\n'}

    def respond(command: Sequence[str] | str) -> str:
        if script_of(command) == resources.LIST_CONTEXT:
            return ''.join(f'{name}\n' for name in files)
        return files.get(command[-1], '') if not isinstance(command, str) else ''

    executor = RecordingExecutor('context-resource', responder=respond)
    resources.clear_context_cache(executor.hostname)
    try:
        context = resources.load_cluster_context(executor)
    finally:
        resources.clear_context_cache(executor.hostname)
    expected = [_run(script_argv(resources.LIST_CONTEXT, 'cluster_context')),
                *[_run(['cat', '--', name]) for name in files]]
    violations = argv_violations(executor.calls, expected)
    violations += tool_violations(Outcome(False, context), executor.calls, guard.refused,
                                  '<!-- Source: a b.md -->\nalpha\n\n<!-- Source: z.md -->\nzulu')
    assert violations == [], '\n'.join(violations)
