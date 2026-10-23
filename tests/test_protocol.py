# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""How the server answers at the protocol level, and how the harness picks the era it speaks.

**The error boundary.** An exception an executor middleware raises around a tool call (an unmapped
identity's `PermissionError`, a token without an identity claim's `RuntimeError`) reaches the client
as a tool error result whose text is exactly `str(exc)`: no type name, no traceback. FastMCP 4
converts only exceptions from a tool's body, so without `ToolErrorMiddleware` the reason is erased
into a masked protocol error. An `MCPError` is converted too, as the server did before the stack
move. Cancellation is never converted: the boundary catches `Exception`, not `BaseException`.

**The harness era.** `CLUSTER_MCP_TEST_ERA` selects the protocol era every harness client speaks
(default `legacy`); an unknown value is refused, and each connect is checked against the selection.
`call_http` reports a rejection only when the last HTTP answer was a 401.

**Both protocol eras, each pinned.** A client on a handshake revision completes `initialize`; a
client on revision 2026-07-28 is answered on `server/discover` and never sends `initialize`. Each
receives the base instructions composed with the harness's site context (`SERVER_INSTRUCTIONS`
here) and lists exactly `EXPECTED_TOOLS`, in memory and over HTTP, whatever era the run selected. What each end saw is read through `negotiate`, and
`era_violations` decides. The delegate contracts (isolation, the tokenless refusal, a foreign
token's 401) also run in the modern era on every run, so the default handshake-era pass does not
leave them unproven there.

**Delegate state is request-scoped.** The identity `AuthExecutorMiddleware` stores in the context
state for a tool call is visible to that call and to no later request on the same session, in
either era: a per-user value never lands in the session store. `state_scope_violations` decides.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest

import harness
from harness import (MODERN_VERSION, SITE, Negotiated, Outcome, RecordingExecutor, Raises, Rendezvous, StateProbe,
                     call, call_http, delegate_server, drive, http_client, in_memory, mint, negotiate, protocol_error,
                     rejection_status, select_era, serving, session_era, shared_server, tool_names, user_map)
from rcac_mcp.middleware import ToolErrorMiddleware
from rcac_mcp.server import SERVER_INSTRUCTIONS as BASE
from rcac_mcp.site import compose
from test_isolation import isolation_violations, launched_as
from test_no_executor import NO_EXECUTOR, refusal_violations, report
from test_registry import EXPECTED_TOOLS

# What every harness server serves: the base instructions and the harness's site context.
SERVER_INSTRUCTIONS = compose(BASE, SITE)


def boundary_violations(outcome: Outcome | None, raised: BaseException | None, expected: str) -> list[str]:
    """Every way a refused call fails to arrive as a tool error result carrying exactly `expected`.

    `outcome` is what the client got back, `raised` what the client raised instead.
    """
    if raised is not None:
        return [f'protocol error instead of a tool error result: {type(raised).__name__}: {raised}']
    if outcome is None:
        return ['no outcome and nothing raised']
    violations: list[str] = []
    if not outcome.is_error:
        violations.append(f'not an error result: {outcome.text!r}')
    if outcome.text != expected:
        violations.append(f'error text differs: {outcome.text!r}, expected {expected!r}')
    return violations


def cancellation_violations(outcome: str) -> list[str]:
    """`outcome` is how the boundary ended when the chain was cancelled; only `cancelled` is right."""
    return [] if outcome == 'cancelled' else [f'swallowed cancellation: the boundary {outcome}']


REFUSALS = [
    pytest.param(lambda: PermissionError("User 'ghost' is not authorized. Contact administrator to request access."),
                 'memory', id='permission-memory'),
    pytest.param(lambda: PermissionError("User 'ghost' is not authorized. Contact administrator to request access."),
                 'http', id='permission-http'),
    pytest.param(lambda: RuntimeError('Could not extract user identity from token claims.'), 'memory',
                 id='runtime-memory'),
    pytest.param(lambda: protocol_error('the request cannot be served'), 'memory', id='mcp-error-memory'),
]


@pytest.mark.unit
@pytest.mark.parametrize(('make', 'route'), REFUSALS)
def test_middleware_refusal_is_a_tool_error_result(make: Callable[[], Exception], route: str, guard) -> None:
    exc = make()
    executor = RecordingExecutor('refused')

    async def scenario() -> tuple[Outcome | None, BaseException | None]:
        server = shared_server(executor, Raises(exc))
        try:
            if route == 'memory':
                async with in_memory(server) as client:
                    return await call(client, 'run_command', {'command': 'echo refused'}), None
            async with serving(server) as app:
                async with http_client(app, None) as client:
                    return await call(client, 'run_command', {'command': 'echo refused'}), None
        except Exception as raised:
            return None, raised

    outcome, raised = drive(scenario())
    violations = boundary_violations(outcome, raised, str(exc))
    assert violations == [], '\n'.join(violations)
    assert executor.calls == []
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize(('outcome', 'raised', 'fragment'), [
    pytest.param(None, protocol_error('Internal server error'), 'protocol error instead of a tool error result',
                 id='protocol-error'),
    pytest.param(Outcome(True, 'PermissionError: denied'), None, 'error text differs', id='type-name'),
    pytest.param(Outcome(True, 'denied\nTraceback (most recent call last):'), None, 'error text differs',
                 id='traceback'),
    pytest.param(Outcome(False, 'denied'), None, 'not an error result', id='not-an-error'),
])
def test_boundary_check_catches_drift(outcome: Outcome | None, raised: BaseException | None, fragment: str) -> None:
    assert boundary_violations(Outcome(True, 'denied'), None, 'denied') == []
    violations = boundary_violations(outcome, raised, 'denied')
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
def test_boundary_lets_cancellation_through() -> None:
    async def cancelled(context: Any) -> Any:
        raise asyncio.CancelledError()

    # Stepped by hand: the chain raises at once, so no event loop is needed to see what escapes.
    step = ToolErrorMiddleware().on_call_tool(None, cancelled)
    try:
        step.send(None)
        outcome = 'suspended'
    except StopIteration:
        outcome = 'returned'
    except asyncio.CancelledError:
        outcome = 'cancelled'
    except BaseException as exc:
        outcome = f'raised {type(exc).__name__}: {exc}'
    finally:
        step.close()
    violations = cancellation_violations(outcome)
    assert violations == [], violations
    assert cancellation_violations('raised ToolError: ') != []


@pytest.mark.unit
def test_harness_clients_speak_the_selected_era(guard) -> None:
    async def scenario() -> tuple[str | None, bool]:
        server = shared_server(RecordingExecutor('era'))
        async with in_memory(server) as client:
            return session_era(client)

    version, has_initialize_result = drive(scenario())
    assert harness.era_mismatch(harness.ERA, version, has_initialize_result) is None, (version, has_initialize_result)
    other = 'modern' if harness.ERA == 'legacy' else 'legacy'
    assert harness.era_mismatch(other, version, has_initialize_result) is not None, (version, has_initialize_result)
    assert harness.era_mismatch('legacy', harness.MODERN_VERSION, False) is not None
    assert harness.era_mismatch('legacy', '2025-11-25', False) is not None
    assert harness.era_mismatch('modern', '2025-11-25', True) is not None
    assert harness.era_mismatch('modern', harness.MODERN_VERSION, True) is not None
    assert harness.era_mismatch('modern', harness.MODERN_VERSION, False) is None
    assert harness.era_mismatch('legacy', '2025-11-25', True) is None
    assert guard.refused == []


@pytest.mark.unit
def test_harness_refuses_an_unknown_era() -> None:
    # The default is the handshake era, which is what the profile's verify runs.
    assert select_era({}) == 'legacy'
    assert select_era({'CLUSTER_MCP_TEST_ERA': ''}) == 'legacy'
    assert select_era({'CLUSTER_MCP_TEST_ERA': 'legacy'}) == 'legacy'
    assert select_era({'CLUSTER_MCP_TEST_ERA': 'modern'}) == 'modern'
    for bogus in ('bogus', 'Modern', '2026-07-28'):
        with pytest.raises(ValueError, match='CLUSTER_MCP_TEST_ERA'):
            select_era({'CLUSTER_MCP_TEST_ERA': bogus})


@pytest.mark.unit
@pytest.mark.parametrize(('statuses', 'expected'), [
    pytest.param([200, 401], 401, id='401-last'),
    pytest.param([401, 200], None, id='401-then-200'),
    pytest.param([200, 404], None, id='no-401'),
])
def test_http_rejection_is_the_last_answer(statuses: list[int], expected: int | None) -> None:
    assert rejection_status(statuses) == expected
    assert rejection_status([]) is None


def era_violations(negotiated: Negotiated, era: str) -> list[str]:
    """Every way one connection was not served in `era`; empty means it was.

    Legacy: a handshake version, an `InitializeResult`, and the server saw `initialize` and no
    `server/discover`. Modern: revision 2026-07-28, no `InitializeResult`, and the server saw
    `server/discover` and no `initialize`. Both: the instructions are `SERVER_INSTRUCTIONS` and the
    tools exactly `EXPECTED_TOOLS`.
    """
    violations: list[str] = []
    version, methods = negotiated.protocol_version, negotiated.methods
    if era == 'legacy':
        if version is None or version == MODERN_VERSION:
            violations.append(f'legacy: negotiated {version}, expected a handshake version')
        if not negotiated.has_initialize_result:
            violations.append('legacy: no InitializeResult, so the handshake never completed')
        if 'initialize' not in methods:
            violations.append(f'legacy: the server saw no initialize: {list(methods)}')
        if 'server/discover' in methods:
            violations.append(f'legacy: the server saw server/discover: {list(methods)}')
    elif era == 'modern':
        if version != MODERN_VERSION:
            violations.append(f'modern: negotiated {version}, expected {MODERN_VERSION}')
        if negotiated.has_initialize_result:
            violations.append('modern: an InitializeResult, so the handshake ran')
        if 'server/discover' not in methods:
            violations.append(f'modern: the server saw no server/discover: {list(methods)}')
        if 'initialize' in methods:
            violations.append(f'modern: the server saw initialize: {list(methods)}')
    else:
        violations.append(f'{era!r} is not an era')
    if negotiated.instructions != SERVER_INSTRUCTIONS:
        violations.append(f'{era}: instructions differ from SERVER_INSTRUCTIONS: {negotiated.instructions!r:.120}')
    tools = list(negotiated.tools)
    missing, unexpected = sorted(EXPECTED_TOOLS - set(tools)), sorted(set(tools) - EXPECTED_TOOLS)
    duplicated = sorted({name for name in tools if tools.count(name) > 1})
    if missing or unexpected or duplicated:
        violations.append(f'{era}: tools differ from EXPECTED_TOOLS: missing {missing}, unexpected {unexpected}, '
                          f'listed twice {duplicated}')
    return violations


@pytest.mark.unit
@pytest.mark.parametrize(('era', 'route'), [
    pytest.param('legacy', 'memory', id='legacy-memory'),
    pytest.param('legacy', 'http', id='legacy-http'),
    pytest.param('modern', 'memory', id='modern-memory'),
    pytest.param('modern', 'http', id='modern-http'),
])
def test_client_era_is_served(era: str, route: str, guard) -> None:
    async def scenario() -> Negotiated:
        server = shared_server(RecordingExecutor('era'))
        if route == 'memory':
            return await negotiate(server, era)
        async with serving(server) as app:
            return await negotiate(app, era)

    violations = era_violations(drive(scenario()), era)
    assert violations == [], report(violations)
    assert guard.refused == []


LEGACY = Negotiated('2025-11-25', True, SERVER_INSTRUCTIONS, tuple(sorted(EXPECTED_TOOLS)),
                    ('initialize', 'notifications/initialized', 'tools/list'))
MODERN = Negotiated(MODERN_VERSION, False, SERVER_INSTRUCTIONS, tuple(sorted(EXPECTED_TOOLS)),
                    ('server/discover', 'tools/list'))


@pytest.mark.unit
@pytest.mark.parametrize(('negotiated', 'era', 'fragment'), [
    pytest.param(MODERN, 'legacy', 'legacy: the server saw server/discover', id='legacy-discovered'),
    pytest.param(Negotiated(MODERN_VERSION, True, SERVER_INSTRUCTIONS, MODERN.tools,
                            ('initialize', 'server/discover', 'tools/list')),
                 'modern', 'modern: the server saw initialize', id='modern-initialized'),
    pytest.param(LEGACY, 'modern', f'expected {MODERN_VERSION}', id='modern-fell-back'),
    pytest.param(Negotiated(MODERN_VERSION, False, None, MODERN.tools, ('tools/list',)),
                 'modern', 'modern: the server saw no server/discover', id='modern-pinned'),
    pytest.param(Negotiated(LEGACY.protocol_version, True, None, LEGACY.tools, LEGACY.methods),
                 'legacy', 'instructions differ', id='instructions-missing'),
    pytest.param(Negotiated(MODERN_VERSION, False, SERVER_INSTRUCTIONS, MODERN.tools[1:], MODERN.methods),
                 'modern', 'tools differ', id='tools-missing'),
])
def test_era_check_catches_drift(negotiated: Negotiated, era: str, fragment: str) -> None:
    assert era_violations(LEGACY, 'legacy') == []
    assert era_violations(MODERN, 'modern') == []
    violations = era_violations(negotiated, era)
    assert any(fragment in v for v in violations), violations


SECRET = 'protocol-test-secret-0123456789abcdef'
FOREIGN_SECRET = 'a-secret-this-server-never-saw-0123456789'
USER_MAP = 'alice ualice\nbob ubob\n'


@pytest.mark.unit
def test_modern_era_delegate_calls_stay_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> tuple[Outcome, Outcome]:
        server = delegate_server(map_path, Rendezvous(2))
        async with serving(server) as app:
            async with (http_client(app, mint(SECRET, sub='alice'), era='modern') as alice,
                        http_client(app, mint(SECRET, sub='bob'), era='modern') as bob):
                return await asyncio.gather(call(alice, 'run_command', {'command': 'echo from-alice'}),
                                            call(bob, 'run_command', {'command': 'echo from-bob'}))

    for_alice, for_bob = drive(scenario())
    violations = isolation_violations(
        {'echo from-alice': 'ualice', 'echo from-bob': 'ubob'},
        [launched_as(argv) for argv in launches],
        {'echo from-alice': for_alice.text, 'echo from-bob': for_bob.text})
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_modern_era_tokenless_delegate_call_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> Outcome:
        server = delegate_server(map_path)
        async with in_memory(server, era='modern') as client:
            return await call(client, 'run_command', {'command': 'echo ANON'})

    violations = refusal_violations(drive(scenario()), [], launches, NO_EXECUTOR)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_modern_era_rejects_a_foreign_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)

    async def scenario() -> Outcome:
        server = delegate_server(map_path)
        async with serving(server) as app:
            return await call_http(app, mint(FOREIGN_SECRET, sub='alice'), 'run_command',
                                   {'command': 'echo foreign'}, era='modern')

    outcome = drive(scenario())
    violations = []
    if outcome.status != 401:
        violations.append(f'status {outcome.status}, expected 401: {outcome!r}')
    if launches:
        violations.append(f'process launched for a rejected token: {launches!r}')
    assert violations == [], report(violations)
    assert guard.refused == []


STATE_KEYS = ('rcac_user', 'rcac_local_user')
ALICE_STATE = {'rcac_user': 'alice', 'rcac_local_user': 'ualice'}


def state_scope_violations(seen: list[tuple[str, dict[str, Any]]], expected: dict[str, Any]) -> list[str]:
    """Every way the delegate middleware's state fails to be request-scoped; empty means it is.

    `seen` is a `StateProbe`'s record of one session: a tool call, then later requests. The tool call
    must see `expected`, so the state was set and is readable and its absence later is not vacuous;
    no request after the call may see any of it.
    """
    calls = [i for i, (method, _) in enumerate(seen) if method == 'tools/call']
    if not calls:
        return [f'no tool call reached the probe: {[method for method, _ in seen]}']
    violations: list[str] = []
    first = calls[0]
    if seen[first][1] != expected:
        violations.append(f'the tool call saw {seen[first][1]!r}, expected {expected!r}: no state was set')
    later = seen[first + 1:]
    if not later:
        violations.append('no later request on the session reached the probe')
    for method, values in later:
        leaked = {key: value for key, value in values.items() if value is not None}
        if leaked:
            violations.append(f'state outlived its request: {method} saw {leaked!r}')
    return violations


@pytest.mark.unit
@pytest.mark.parametrize('era', ['legacy', 'modern'])
def test_delegate_state_is_request_scoped(era: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)
    probe = StateProbe(*STATE_KEYS)

    async def scenario() -> Outcome:
        server = delegate_server(map_path, probe)
        async with serving(server) as app:
            async with http_client(app, mint(SECRET, sub='alice'), era=era) as client:
                outcome = await call(client, 'run_command', {'command': 'echo scoped'})
                await tool_names(client)
                return outcome

    outcome = drive(scenario())
    violations = state_scope_violations(probe.seen, ALICE_STATE)
    if outcome.is_error:
        violations.append(f'the tool call failed: {outcome.text!r}')
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize(('seen', 'fragment'), [
    pytest.param([('tools/call', ALICE_STATE), ('tools/list', {'rcac_user': 'alice', 'rcac_local_user': None})],
                 'state outlived its request', id='leaked'),
    pytest.param([('tools/call', {'rcac_user': None, 'rcac_local_user': None}),
                  ('tools/list', {'rcac_user': None, 'rcac_local_user': None})],
                 'no state was set', id='never-set'),
    pytest.param([('tools/list', {'rcac_user': None, 'rcac_local_user': None}), ('tools/call', ALICE_STATE)],
                 'no later request', id='no-later-request'),
    pytest.param([('tools/list', {'rcac_user': None, 'rcac_local_user': None})], 'no tool call', id='no-call'),
])
def test_state_scope_check_catches_drift(seen: list[tuple[str, dict[str, Any]]], fragment: str) -> None:
    clean = [('tools/list', {'rcac_user': None, 'rcac_local_user': None}), ('tools/call', ALICE_STATE),
             ('tools/list', {'rcac_user': None, 'rcac_local_user': None})]
    assert state_scope_violations(clean, ALICE_STATE) == []
    violations = state_scope_violations(seen, ALICE_STATE)
    assert any(fragment in v for v in violations), violations
