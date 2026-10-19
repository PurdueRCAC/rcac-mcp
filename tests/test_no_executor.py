# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""A tool call with no executor set for its own request fails where the client sees it, and nothing runs.

`get_executor()` raises when the request's context holds no executor (§ Invariants §1): a server
with no executor middleware, or a delegate-mode call that carries no access token. The refusal must
name that reason. Any error is not enough: a tool that fell back to a local executor and then
failed on a missing file would also return an error, having run locally. And it must hold right
after, and while, another request on the same server or process did set an executor; a module
global, a fallback executor, or an executor set at middleware construction would hand the other
request's executor to this one.

Servers are built inside the driven coroutine, as production builds and serves in one context, so a
construction that set an executor would reach these calls exactly as it would in production.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Sequence

import pytest

from harness import (Outcome, RecordingExecutor, Rendezvous, bare_server, call, delegate_server, drive,
                     http_client, in_memory, launched_script, mint, serving, shared_server, user_map)
from rcac_mcp.executor import launch
from test_registry import EXPECTED_TOOLS
from test_tools import TOOL_CASES

# What `get_executor()` raises with when the request's context holds no executor.
NO_EXECUTOR = 'No executor configured'

SECRET = 'no-executor-test-secret-0123456789abcdef'
USER_MAP = 'alice ualice\nbob ubob\n'

# The command the request that does set an executor runs, and the one the request without runs.
OTHER = 'echo from-the-other-request'
ANON = 'echo ANON'


def refusal_violations(outcome: Outcome, calls: Sequence[Any], launches: Sequence[Sequence[str]],
                       reason: str) -> list[str]:
    """Return every way a call that must be refused was not; empty means refused, and nothing ran.

    `calls` is what any executor recorded for this call, `launches` every process it started, and
    `reason` a fragment the client's error must carry (case-insensitive), so an error raised for
    some other cause, after running somewhere, does not count as the refusal.
    """
    violations: list[str] = []
    if not outcome.is_error:
        violations.append(f'not an error: the call succeeded with {outcome.text!r}')
    elif reason.lower() not in outcome.text.lower():
        violations.append(f'error lacks reason {reason!r}: {outcome.text!r}')
    for c in calls:
        violations.append(f'executor ran: {c!r}')
    for argv in launches:
        violations.append(f'process launched: {list(argv)!r}')
    return violations


def other_request_violations(outcome: Outcome, ran: Sequence[tuple[str, str]], owner: str) -> list[str]:
    """Return every way the request that did set an executor went wrong; empty means it ran once, as `owner`.

    `ran` holds the (owner, command) pairs seen where commands leave the process.
    """
    violations: list[str] = []
    if outcome.is_error:
        violations.append(f'the other request failed: {outcome.text!r}')
    elif f'{owner}|{OTHER}' not in outcome.text:
        violations.append(f'the other request lacks its own result {owner}|{OTHER}: {outcome.text!r}')
    seen = [who for who, command in ran if command == OTHER]
    if seen != [owner]:
        violations.append(f'the other request ran as {seen}, expected once as {owner}')
    return violations


def launched_as(argv: Sequence[str]) -> tuple[str, str]:
    """The (local user, script) of a recorded `sudo … -u <user> /bin/bash … <program>` launch.

    The script is read back from the program with `launched_script`; an argv that carries no
    launcher program reports its last element.
    """
    argv = list(argv)
    try:
        script = str(launched_script(argv)[0])
    except ValueError:
        script = argv[-1]
    return (argv[argv.index('-u') + 1] if '-u' in argv[:-1] else argv[0]), script


def report(violations: list[str]) -> str:
    # A joined string, not the list: pytest abbreviates a long list's repr, which can cut a
    # violation out of the failure text.
    return '\n'.join(violations)


@pytest.mark.unit
@pytest.mark.parametrize('tool', sorted(EXPECTED_TOOLS))
def test_tool_without_executor_middleware_fails(tool: str, tmp_path: Path, launches: list[list[str]],
                                                guard) -> None:
    async def scenario() -> Outcome:
        server = bare_server()
        async with in_memory(server) as client:
            return await call(client, tool, TOOL_CASES[tool].arguments(tmp_path))

    outcome = drive(scenario())
    violations = refusal_violations(outcome, [], launches, NO_EXECUTOR)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_no_executor_after_earlier_request(launches: list[list[str]], guard) -> None:
    executor = RecordingExecutor('earlier')

    async def scenario() -> tuple[Outcome, Outcome]:
        shared, bare = shared_server(executor), bare_server()
        async with in_memory(shared) as client:
            first = await call(client, 'run_command', {'command': OTHER})
        async with in_memory(bare) as client:
            return first, await call(client, 'run_command', {'command': ANON})

    first, anon = drive(scenario())
    ran = [('earlier', c[1]) for c in executor.calls]
    violations = refusal_violations(anon, [c for c in executor.calls if c[1] != OTHER], launches, NO_EXECUTOR)
    violations += other_request_violations(first, ran, 'earlier')
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_no_executor_while_other_request_in_flight(launches: list[list[str]], guard) -> None:
    # Both calls are held at one rendezvous inside the executor middleware, so the other request has
    # set its executor before this one's tool body runs.
    executor = RecordingExecutor('in-flight')

    async def scenario() -> tuple[Outcome, Outcome]:
        rendezvous = Rendezvous(2)
        shared, bare = shared_server(executor, rendezvous), bare_server(rendezvous)
        async with in_memory(shared) as other, in_memory(bare) as client:
            return await asyncio.gather(call(other, 'run_command', {'command': OTHER}),
                                        call(client, 'run_command', {'command': ANON}))

    first, anon = drive(scenario())
    ran = [('in-flight', c[1]) for c in executor.calls]
    violations = refusal_violations(anon, [c for c in executor.calls if c[1] != OTHER], launches, NO_EXECUTOR)
    violations += other_request_violations(first, ran, 'in-flight')
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_delegate_without_token_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      launches: list[list[str]], guard) -> None:
    # The in-memory client carries no access token, so the delegate middleware has no identity and
    # sets nothing; the tool must then find no executor, not a default identity's.
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> Outcome:
        server = delegate_server(map_path)
        async with in_memory(server) as client:
            return await call(client, 'run_command', {'command': ANON})

    outcome = drive(scenario())
    violations = refusal_violations(outcome, [], launches, NO_EXECUTOR)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_delegate_without_token_after_authenticated_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> tuple[Outcome, Outcome]:
        server = delegate_server(map_path)
        async with serving(server) as app:
            async with http_client(app, mint(SECRET, sub='alice')) as alice:
                first = await call(alice, 'run_command', {'command': OTHER})
            async with in_memory(server) as client:
                return first, await call(client, 'run_command', {'command': ANON})

    first, anon = drive(scenario())
    ran = [launched_as(argv) for argv in launches]
    violations = refusal_violations(anon, [], [a for a in launches if launched_as(a)[1] != OTHER], NO_EXECUTOR)
    violations += other_request_violations(first, ran, 'ualice')
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_delegate_without_token_while_authenticated_request_in_flight(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> tuple[Outcome, Outcome]:
        server = delegate_server(map_path, Rendezvous(2))
        async with serving(server) as app:
            async with http_client(app, mint(SECRET, sub='alice')) as alice, in_memory(server) as client:
                return await asyncio.gather(call(alice, 'run_command', {'command': OTHER}),
                                            call(client, 'run_command', {'command': ANON}))

    first, anon = drive(scenario())
    ran = [launched_as(argv) for argv in launches]
    violations = refusal_violations(anon, [], [a for a in launches if launched_as(a)[1] != OTHER], NO_EXECUTOR)
    violations += other_request_violations(first, ran, 'ualice')
    assert violations == [], report(violations)
    assert guard.refused == []


REFUSED = Outcome(True, f"Error calling tool 'run_command': {NO_EXECUTOR}. Set executor via set_executor() first.")
ANON_ARGV = ['sudo', '-n', '-H', '-u', 'ualice', *launch.bash_argv(launch.program(ANON, start='home')[0])]


@pytest.mark.unit
@pytest.mark.parametrize(('outcome', 'calls', 'launched', 'fragment'), [
    pytest.param(Outcome(False, f'ualice|{ANON}'), [], [], 'not an error', id='not-an-error'),
    pytest.param(Outcome(True, "[Errno 2] No such file or directory: '/remote/results.csv'"), [], [],
                 'error lacks reason', id='lacks-reason'),
    pytest.param(REFUSED, [('run', ANON, None, None, None)], [], 'executor ran', id='executor-ran'),
    pytest.param(REFUSED, [], [ANON_ARGV], 'process launched', id='process-launched'),
])
def test_refusal_check_catches_execution(outcome: Outcome, calls: list[Any], launched: list[list[str]],
                                         fragment: str) -> None:
    assert refusal_violations(REFUSED, [], [], NO_EXECUTOR) == []
    violations = refusal_violations(outcome, calls, launched, NO_EXECUTOR)
    assert any(fragment in v for v in violations), violations
