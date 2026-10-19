# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Concurrent tool calls each run on their own request's executor, never on another's.

The executor lives in a `ContextVar` set per tool call (§ Invariants §1). Two calls are held at a
`Rendezvous` inside the executor middleware until both are in flight, so each has set its executor
before either runs its tool; a module global or a cached executor then crosses them, every time.
Sequential calls on one server pin the other half of "per tool call": each executor middleware sets
the executor on every call, not only the first, because each call runs in a context of its own.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping, Sequence

import pytest

from harness import (Outcome, RecordingExecutor, Rendezvous, call, delegate_server, drive, http_client,
                     in_memory, launched_script, mint, serving, shared_server, user_map)

SECRET = 'isolation-test-secret-0123456789abcdef'
USER_MAP = 'alice ualice\nbob ubob\n'


def isolation_violations(expected: Mapping[str, str], ran: Sequence[tuple[str, str]],
                         results: Mapping[str, str]) -> list[str]:
    """Return every way the observed calls crossed; empty means each stayed on its own executor.

    `expected` maps each command to the owner (local user or executor name) it must run under,
    `ran` holds the (owner, command) pairs seen where commands leave the process, and `results`
    maps each command to the text its own caller received, which must carry `<owner>|<command>`.
    """
    violations: list[str] = []
    owners = set(expected.values()) | {owner for owner, _ in ran}
    for command, owner in expected.items():
        seen = [who for who, cmd in ran if cmd == command]
        if len(seen) != 1:
            violations.append(f'{command!r} ran {len(seen)} times, expected once as {owner}')
        for who in seen:
            if who != owner:
                violations.append(f'{command!r} ran as {who}, expected {owner}')
        text = results.get(command, '')
        if f'{owner}|{command}' not in text:
            violations.append(f'result for {command!r} lacks its own marker {owner}|{command}: {text!r}')
        for other in expected:
            if other != command and any(f'{who}|{other}' in text for who in owners):
                violations.append(f'result for {command!r} carries the result of {other!r}: {text!r}')
    for who, command in ran:
        if command not in expected:
            violations.append(f'unexpected run of {command!r} as {who}')
    return violations


def launched_as(argv: Sequence[str]) -> tuple[str, str]:
    """The (local user, script) of a recorded `sudo … -u <user> /bin/bash … <program>` launch."""
    return argv[list(argv).index('-u') + 1], str(launched_script(argv)[0])


@pytest.mark.unit
def test_concurrent_delegate_calls_stay_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> tuple[Outcome, Outcome]:
        server = delegate_server(map_path, Rendezvous(2))
        async with serving(server) as app:
            async with (http_client(app, mint(SECRET, sub='alice')) as alice,
                        http_client(app, mint(SECRET, sub='bob')) as bob):
                return await asyncio.gather(call(alice, 'run_command', {'command': 'echo from-alice'}),
                                            call(bob, 'run_command', {'command': 'echo from-bob'}))

    for_alice, for_bob = drive(scenario())
    violations = isolation_violations(
        {'echo from-alice': 'ualice', 'echo from-bob': 'ubob'},
        [launched_as(argv) for argv in launches],
        {'echo from-alice': for_alice.text, 'echo from-bob': for_bob.text})
    assert violations == [], '\n'.join(violations)
    assert all(argv[:4] == ['sudo', '-n', '-H', '-u'] for argv in launches), launches
    assert guard.refused == []


@pytest.mark.unit
def test_concurrent_shared_servers_stay_isolated(guard) -> None:
    # One server cannot hold two shared executors (a second SharedExecutorMiddleware simply wins), so
    # two servers in one process share the rendezvous; a module global crosses them all the same.
    exec_a, exec_b = RecordingExecutor('exec-a'), RecordingExecutor('exec-b')

    async def scenario() -> tuple[Outcome, Outcome]:
        rendezvous = Rendezvous(2)
        server_a, server_b = shared_server(exec_a, rendezvous), shared_server(exec_b, rendezvous)
        async with in_memory(server_a) as a, in_memory(server_b) as b:
            return await asyncio.gather(call(a, 'run_command', {'command': 'echo from-a'}),
                                        call(b, 'run_command', {'command': 'echo from-b'}))

    for_a, for_b = drive(scenario())
    ran = [('exec-a', c[1]) for c in exec_a.calls] + [('exec-b', c[1]) for c in exec_b.calls]
    violations = isolation_violations({'echo from-a': 'exec-a', 'echo from-b': 'exec-b'}, ran,
                                      {'echo from-a': for_a.text, 'echo from-b': for_b.text})
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_sequential_shared_calls_each_get_the_executor(guard) -> None:
    # The executor is set per tool call, not once per server: each call runs in its own context, so
    # a middleware that set it only on the first call would leave every later call with none.
    executor = RecordingExecutor('shared')
    commands = ['echo first', 'echo second', 'echo third']

    async def scenario() -> list[Outcome]:
        server = shared_server(executor)
        async with in_memory(server) as client:
            return [await call(client, 'run_command', {'command': command}) for command in commands]

    outcomes = drive(scenario())
    violations = isolation_violations({command: 'shared' for command in commands},
                                      [('shared', c[1]) for c in executor.calls],
                                      {command: outcome.text for command, outcome in zip(commands, outcomes)})
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_sequential_delegate_calls_each_get_their_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           launches: list[list[str]], guard) -> None:
    # Alternating identities on one server, one call at a time: every call builds and sets the
    # executor for its own token, so none runs with no executor or with the previous caller's.
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)
    sequence = [('alice', 'echo alice-1'), ('bob', 'echo bob-1'), ('alice', 'echo alice-2')]

    async def scenario() -> list[Outcome]:
        server = delegate_server(map_path)
        async with serving(server) as app:
            async with (http_client(app, mint(SECRET, sub='alice')) as alice,
                        http_client(app, mint(SECRET, sub='bob')) as bob):
                clients = {'alice': alice, 'bob': bob}
                return [await call(clients[who], 'run_command', {'command': command}) for who, command in sequence]

    outcomes = drive(scenario())
    violations = isolation_violations({command: f'u{who}' for who, command in sequence},
                                      [launched_as(argv) for argv in launches],
                                      {command: outcome.text for (_, command), outcome in zip(sequence, outcomes)})
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_rendezvous_fails_when_calls_are_not_concurrent(guard) -> None:
    # A lone call never fills a two-party rendezvous, so the barrier cannot pass vacuously: a server
    # that served calls one at a time would fail the isolation tests instead of passing them.
    executor = RecordingExecutor('lone')

    async def scenario() -> Outcome:
        server = shared_server(executor, Rendezvous(2, timeout=0.5))
        async with in_memory(server) as client:
            return await call(client, 'run_command', {'command': 'echo alone'})

    outcome = drive(scenario())
    assert outcome.is_error, outcome
    assert 'rendezvous' in outcome.text, outcome
    assert executor.calls == []
    assert guard.refused == []


EXPECTED = {'echo A': 'ualice', 'echo B': 'ubob'}
RAN = [('ualice', 'echo A'), ('ubob', 'echo B')]
RESULTS = {'echo A': 'ualice|echo A', 'echo B': 'ubob|echo B'}


@pytest.mark.unit
@pytest.mark.parametrize(('ran', 'results', 'fragment'), [
    pytest.param([('ubob', 'echo A'), ('ubob', 'echo B')], {'echo A': 'ubob|echo A', 'echo B': 'ubob|echo B'},
                 'ran as ubob', id='crossed-user'),
    pytest.param(RAN, {'echo A': 'ualice|echo A', 'echo B': 'ualice|echo A'},
                 "result for 'echo B' carries", id='foreign-result'),
    pytest.param(RAN[:1], RESULTS, "'echo B' ran 0 times", id='missing-launch'),
    pytest.param(RAN + [('ubob', 'echo B')], RESULTS, "'echo B' ran 2 times", id='duplicate-launch'),
    pytest.param(RAN + [('ualice', 'echo C')], RESULTS, "unexpected run of 'echo C'", id='unexpected-launch'),
])
def test_isolation_check_catches_crossing(ran: list[tuple[str, str]], results: dict[str, str], fragment: str) -> None:
    assert isolation_violations(EXPECTED, RAN, RESULTS) == []
    violations = isolation_violations(EXPECTED, ran, results)
    assert any(fragment in v for v in violations), violations
