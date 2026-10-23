# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""A delegate-mode call runs as the local user its token's identity maps to, and as no one else.

Identity comes from the first non-empty of `sub`, `email` and `preferred_username` (§ Invariants
§5); reordering them silently re-maps users. A token with none of them is refused, never given a
fallback identity, and an identity the user map does not hold is refused as not authorized, never
given a default user and never retried with the token's next claim. `DelegatingExecutor` re-checks
the map on its own, because it is constructible outside the middleware.

Every call here carries a real JWT over the in-process HTTP route, so the real `JWTVerifier` and
`AuthExecutorMiddleware` decide; launches are recorded at `Popen`, so nothing runs `sudo`. The map
loader (`load_user_map`) is driven directly: a malformed line must fail naming its line number in
the file (comments and blank lines counted), because a partial map is worse than none, and comments
and blank lines must load as nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

import pytest

from harness import Outcome, call_http, delegate_server, drive, launched_script, mint, serving, user_map
from rcac_mcp.executor import launch
from rcac_mcp.executor.delegate import DelegatingExecutor, load_user_map
from test_no_executor import refusal_violations, report

SECRET = 'identity-test-secret-0123456789abcdef'
USER_MAP = 'alice ualice\nal@example.org uemail\nal-pu upu\nbob ubob\n'
COMMAND = 'echo whoami-check'

# What the middleware raises with for a token that names no identity, and for an unmapped one.
NO_IDENTITY = 'Could not extract'
NOT_AUTHORIZED = 'not authorized'


def delegation_violations(outcome: Outcome, launches: Sequence[Sequence[str]], user: str,
                          command: str) -> list[str]:
    """Return every way a delegated call went wrong; empty means `command` ran once, as `user`.

    `launches` holds every process the call started (the recorded `sudo … -u <user> /bin/bash …
    <program>` argv, whose program runs the script, read back with `launched_script`), and the
    result the client got must carry the recorder's `<user>|<command>`.
    """
    violations: list[str] = []
    if outcome.is_error:
        violations.append(f'error result: {outcome.text!r} (status {outcome.status})')
    if len(launches) != 1:
        violations.append(f'launch count {len(launches)}, expected 1: {[list(a) for a in launches]!r}')
    for argv in launches:
        argv = list(argv)
        who = argv[argv.index('-u') + 1] if '-u' in argv[:-1] else argv[0]
        try:
            ran = launched_script(argv)[0]
        except ValueError:
            ran = argv[-1]
        if who != user:
            violations.append(f'{ran!r} ran as {who}, expected {user}')
        if ran != command:
            violations.append(f'launched {ran!r}, expected {command!r}')
    if f'{user}|{command}' not in outcome.text:
        violations.append(f'result lacks {user}|{command}: {outcome.text!r}')
    return violations


def call_as(map_path: str, token: str, command: str = COMMAND) -> Outcome:
    """Build a delegate server inside the driven loop and call `run_command` over HTTP with `token`."""
    async def scenario() -> Outcome:
        server = delegate_server(map_path)
        async with serving(server) as app:
            return await call_http(app, token, 'run_command', {'command': command})

    return drive(scenario())


@pytest.mark.unit
@pytest.mark.parametrize(('claims', 'user'), [
    pytest.param({'sub': 'alice', 'email': 'al@example.org', 'preferred_username': 'al-pu'}, 'ualice',
                 id='all-three'),
    pytest.param({'sub': '', 'email': 'al@example.org'}, 'uemail', id='sub-empty'),
    pytest.param({'email': 'al@example.org', 'preferred_username': 'al-pu'}, 'uemail', id='sub-absent'),
    pytest.param({'email': '', 'preferred_username': 'al-pu'}, 'upu', id='email-empty'),
    pytest.param({'preferred_username': 'al-pu'}, 'upu', id='username-only'),
])
def test_identity_claim_precedence(claims: dict[str, str], user: str, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch, launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    outcome = call_as(user_map(tmp_path, USER_MAP), mint(SECRET, **claims))
    violations = delegation_violations(outcome, launches, user, COMMAND)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('claims', [
    pytest.param({}, id='no-claims'),
    pytest.param({'sub': '', 'email': '', 'preferred_username': ''}, id='all-empty'),
])
def test_token_without_identity_fails(claims: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    outcome = call_as(user_map(tmp_path, USER_MAP), mint(SECRET, **claims))
    violations = refusal_violations(outcome, [], launches, NO_IDENTITY)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_unmapped_identity_is_not_authorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             launches: list[list[str]], guard) -> None:
    monkeypatch.setenv('JWT_SECRET', SECRET)
    outcome = call_as(user_map(tmp_path, USER_MAP), mint(SECRET, sub='ghost'))
    violations = refusal_violations(outcome, [], launches, NOT_AUTHORIZED)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_first_claim_unmapped_does_not_fall_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    launches: list[list[str]], guard) -> None:
    # The first non-empty claim decides. Its email is mapped, but retrying with it would let a token
    # whose subject was never granted access borrow the access of whoever owns that address.
    monkeypatch.setenv('JWT_SECRET', SECRET)
    outcome = call_as(user_map(tmp_path, USER_MAP), mint(SECRET, sub='ghost', email='al@example.org'))
    violations = refusal_violations(outcome, [], launches, NOT_AUTHORIZED)
    assert violations == [], report(violations)
    assert guard.refused == []


@pytest.mark.unit
def test_executor_rejects_unmapped_identity(guard) -> None:
    with pytest.raises(KeyError):
        DelegatingExecutor('ghost', {'alice': 'ualice', 'bob': 'ubob'})
    assert guard.refused == []


@pytest.mark.unit
def test_user_map_rejects_line_without_local_user(tmp_path: Path) -> None:
    # A comment and a blank line come before the malformed one, so its file line (5) differs from
    # its entry count (3) and from its count among the non-comment, non-blank lines (3): only the
    # file's own numbering points an operator at the right line.
    path = user_map(tmp_path, '# granted access\nalice ualice\n\nbob ubob\ncarol\n')
    with pytest.raises(ValueError) as caught:
        load_user_map(path)
    named = re.findall(r'\bline (\d+)\b', str(caught.value))
    assert named == ['5'], f'the error does not name line 5: {caught.value}'


@pytest.mark.unit
def test_user_map_skips_comments_and_blanks(tmp_path: Path) -> None:
    path = user_map(tmp_path, '# header comment\nalice ualice\n    # indented comment\n\n   \t \nbob ubob\n')
    loaded = load_user_map(path)
    expected = {'alice': 'ualice', 'bob': 'ubob'}
    assert loaded == expected, f'map differs: {loaded!r}, expected {expected!r}'


def argv(user: str, command: str) -> list[str]:
    return ['sudo', '-n', '-H', '-u', user, *launch.bash_argv(launch.program(command, start='home')[0])]


GOOD = Outcome(False, f'ualice|{COMMAND}')


@pytest.mark.unit
@pytest.mark.parametrize(('outcome', 'launched', 'fragment'), [
    pytest.param(Outcome(False, f'uemail|{COMMAND}'), [argv('uemail', COMMAND)], 'ran as uemail', id='wrong-user'),
    pytest.param(Outcome(True, "Error calling tool 'run_command': User 'alice' is not authorized."), [],
                 'error result', id='refused'),
    pytest.param(GOOD, [argv('ualice', COMMAND)] * 2, 'launch count 2', id='ran-twice'),
    pytest.param(Outcome(False, 'ubob|echo other'), [argv('ualice', COMMAND)], 'result lacks', id='foreign-result'),
])
def test_delegation_check_catches_drift(outcome: Outcome, launched: list[Any], fragment: str) -> None:
    assert delegation_violations(GOOD, [argv('ualice', COMMAND)], 'ualice', COMMAND) == []
    violations = delegation_violations(outcome, launched, 'ualice', COMMAND)
    assert any(fragment in v for v in violations), violations
