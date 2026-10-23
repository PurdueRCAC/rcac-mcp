# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The site context a server was built with reaches every client the same way, and nothing reads it again.

`cluster_context` and `rcac://context` return the text read at startup in every mode and both
protocol eras, run nothing through any executor, and give two delegate users whose executors report
the same hostname identical text.

In `delegate` mode the tool reaches only a mapped user: the middleware refuses every tool call from
an authenticated identity absent from the user map, before the body runs (§ Invariants §5), and the
tool is no exception, while the instructions and `rcac://context` reach any client that can
connect. That is intended: the site text is world-readable administrative markdown, so the
difference discloses nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harness import (Outcome, RecordingExecutor, bare_server, call, delegate_server, drive, http_client, in_memory,
                     mint, read_resource, serving, served_instructions, shared_server, user_map)
from rcac_mcp import middleware, site
from rcac_mcp.server import SERVER_INSTRUCTIONS, create_mcp_server

SECRET = 'context-test-secret-0123456789abcdef0123'
USER_MAP = 'alice ualice\nbob ubob\n'
ERAS = ['legacy', 'modern']

SITES = {
    'files': site.SiteContext(files=(site.SiteFile('a.md', 'Alpha `rule` here.'), site.SiteFile('b.md', 'Bravo.'))),
    'missing': site.SiteContext.missing('this host'),
    'empty': site.SiteContext.found([], 'this host'),
    'unreadable': site.SiteContext.unreadable('this host', 'permission denied'),
    'timeout': site.SiteContext.timed_out('this host'),
}
# What the tool and the resource return for each: the files, '' for none, the reason for a failure.
EXPECTED = {'files': '<!-- Source: a.md -->\nAlpha `rule` here.\n\n<!-- Source: b.md -->\nBravo.',
            'missing': '', 'empty': '', 'unreadable': SITES['unreadable'].reason, 'timeout': SITES['timeout'].reason}


class StubDelegate(RecordingExecutor):
    """A delegate executor that records instead of running `sudo`: every user reports one hostname."""

    def __init__(self, identity: str, user_map: dict) -> None:
        super().__init__(f'delegate-{identity}')
        self.local_user = user_map[identity]

    @property
    def hostname(self) -> str:
        return 'shared-host.invalid'


def context_violations(seen: dict[str, Outcome], expected: str, calls: list[Any]) -> list[str]:
    """Every way what each reader saw differs from `expected`, or anything ran."""
    violations = [f'{who}: {o.text!r:.80} (error={o.is_error}), expected {expected!r:.80}'
                  for who, o in seen.items() if o.is_error or o.text != expected]
    if calls:
        violations.append(f'an executor ran: {calls!r:.200}')
    return violations


@pytest.mark.unit
@pytest.mark.parametrize('era', ERAS)
@pytest.mark.parametrize('case', sorted(SITES))
@pytest.mark.parametrize('mode', ['bare', 'shared'])
def test_tool_and_resource_serve_the_startup_text(mode: str, case: str, era: str, guard) -> None:
    executor = RecordingExecutor('installed')

    async def scenario() -> dict[str, Outcome]:
        server = bare_server(site=SITES[case]) if mode == 'bare' else shared_server(executor, site=SITES[case])
        async with in_memory(server, era=era) as client:
            return {'tool': await call(client, 'cluster_context', {}),
                    'resource': await read_resource(client, 'rcac://context'),
                    'tool-again': await call(client, 'cluster_context', {})}

    assert context_violations(drive(scenario()), EXPECTED[case], executor.calls) == []
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('era', ERAS)
def test_delegate_users_get_identical_text(era: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           launches: list[list[str]], guard) -> None:
    made: list[StubDelegate] = []
    monkeypatch.setattr(middleware, 'DelegatingExecutor', lambda i, m: made.append(StubDelegate(i, m)) or made[-1])
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)
    context = SITES['files']

    async def scenario() -> dict[str, Outcome]:
        seen: dict[str, Outcome] = {}
        async with serving(delegate_server(map_path, site=context)) as app:
            for user in ('alice', 'bob'):
                async with http_client(app, mint(SECRET, sub=user), era=era) as client:
                    seen[f'{user}-tool'] = await call(client, 'cluster_context', {})
                    seen[f'{user}-resource'] = await read_resource(client, 'rcac://context')
                    seen[f'{user}-instructions'] = Outcome(False, client.instructions or '')
        return seen

    seen = drive(scenario())
    instructions = site.compose(SERVER_INSTRUCTIONS, context)
    texts = {k: v for k, v in seen.items() if not k.endswith('instructions')}
    violations = context_violations(texts, EXPECTED['files'], [c for e in made for c in e.calls])
    violations += [f'{k}: instructions differ' for k, v in seen.items() if k.endswith('instructions')
                   and v.text != instructions]
    if {e.local_user for e in made} != {'ualice', 'ubob'} or {e.hostname for e in made} != {'shared-host.invalid'}:
        violations.append(f'the stub did not stand in for both users: {[(e.local_user, e.hostname) for e in made]}')
    assert violations == [], violations
    assert launches == [] and guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('case', sorted(SITES))
def test_served_instructions_are_composed_from_the_site(case: str) -> None:
    assert served_instructions(create_mcp_server('none', site=SITES[case])) == site.compose(SERVER_INSTRUCTIONS, SITES[case])


@pytest.mark.unit
def test_nothing_reads_the_site_after_construction(monkeypatch: pytest.MonkeyPatch, guard) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError('the site was read after startup')

    for name in ('read_local', 'read_remote', 'read_bounded', 'parse_remote'):
        monkeypatch.setattr(site, name, refuse)

    async def scenario() -> list[Outcome]:
        server = bare_server(site=SITES['files'])
        async with in_memory(server) as client:
            return [await call(client, 'cluster_context', {}), await read_resource(client, 'rcac://context')] * 3

    assert all(o.text == EXPECTED['files'] and not o.is_error for o in drive(scenario()))
    assert guard.refused == []


@pytest.mark.unit
def test_a_changed_directory_changes_nothing_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard) -> None:
    root = tmp_path / 'agents.d'
    root.mkdir()
    (root / 'a.md').write_text('Alpha.\n')
    monkeypatch.setattr(site, 'AGENTS_D', str(root))
    context = site.read_local()

    async def scenario() -> list[tuple[str, str, str]]:
        server = bare_server(site=context)
        seen = []
        async with in_memory(server) as client:
            seen.append(((await call(client, 'cluster_context', {})).text,
                         (await read_resource(client, 'rcac://context')).text, client.instructions))
            (root / 'a.md').write_text('Changed.\n')
            (root / 'b.md').write_text('Added.\n')
            seen.append(((await call(client, 'cluster_context', {})).text,
                         (await read_resource(client, 'rcac://context')).text, client.instructions))
        async with in_memory(server) as client:
            seen.append(((await call(client, 'cluster_context', {})).text,
                         (await read_resource(client, 'rcac://context')).text, client.instructions))
        return seen

    seen = drive(scenario())
    assert seen[0][0] == '<!-- Source: a.md -->\nAlpha.', seen
    assert len(set(seen)) == 1, f'what is served changed after startup: {seen}'


@pytest.mark.unit
def test_an_unmapped_user_reads_the_resource_and_is_refused_the_tool(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launches: list[list[str]], guard) -> None:
    # Intended, not provisional: the user map gates tool calls in `delegate` mode, this one included,
    # while the instructions and `rcac://context` reach any client that can connect; the site text is
    # world-readable administrative markdown, so the difference discloses nothing.
    monkeypatch.setenv('JWT_SECRET', SECRET)
    map_path = user_map(tmp_path, USER_MAP)
    context = SITES['files']

    async def scenario() -> dict[str, Outcome]:
        async with serving(delegate_server(map_path, site=context)) as app:
            async with http_client(app, mint(SECRET, sub='mallory')) as client:
                return {'tool': await call(client, 'cluster_context', {}),
                        'resource': await read_resource(client, 'rcac://context'),
                        'instructions': Outcome(False, client.instructions or '')}

    seen = drive(scenario())
    assert seen['tool'].is_error and 'not authorized' in seen['tool'].text, seen['tool']
    assert seen['resource'] == Outcome(False, EXPECTED['files'])
    assert seen['instructions'].text == site.compose(SERVER_INSTRUCTIONS, context)
    assert launches == [] and guard.refused == []
