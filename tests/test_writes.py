# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""`write_file` writes exactly the bytes sent, and refuses invalid content before touching anything.

The content goes through the executor's `write`, never in an argv, so no byte of it is ever parsed
as shell and no newline is added: in `local` and `delegate` it reaches a fixed script on the stdin
the launch keeps parked, and over `ssh` it goes by SFTP to a private temporary file that a program
moves into place. Under `encoding="utf-8"` the bytes are the text's UTF-8 encoding;
under `encoding="base64"` they are the decoded bytes, with ASCII space, tab, CR and LF ignored (RFC
2045 line breaks, as GNU `base64` wraps) and every other character outside the alphabet, or bad
padding, refused. A refusal names the parameter and leaves the target exactly as it was: the
content is validated and encoded before any executor call.

The `integration` rows call the tool through the served schema (`in_memory(shared_server(…))`) in
all three modes, against real files: `local` and `delegate` through the `executes` fixture (fenced
to bash, inside `bin/sandbox.sh`, the `sudo` prefix asserted and replaced by a relaying stand-in
with its own mapped home), `ssh` against the executing loopback server (`tests/ssh_server.py`, a
bash login shell). Every write is checked by sha256 against the bytes sent, in overwrite and append
mode: text with no final newline, empty content, a line that looks like a heredoc terminator, CRLF,
NUL, 1 MiB, and binary sent as base64 (plain, with whitespace, and GNU-wrapped). `create_dirs`
creates a parent with spaces in its name, or one ending in a newline. A refused write leaves an
existing target byte-identical and an absent one absent. `sbatch` writes `script_content` the same
way: the job script it submits (to an argv-echo `sbatch`, `tests/argv_echo.py`) holds exactly the
bytes sent, in all three modes, and is executable. The `unit` rows record the executor
calls: the content reaches one `write` exactly and no argv element, and a refused write reaches no
executor at all.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import pytest

import argv_echo
from conftest import need_sftp_server
from harness import Outcome, RecordingExecutor, call, drive, in_memory, shared_server
from rcac_mcp.executor import Executor
from rcac_mcp.executor.delegate import DelegatingExecutor
from rcac_mcp.executor.shell import LocalShellExecutor
from rcac_mcp.scripts import script_argv
from rcac_mcp.tools.filesystem import LIST
from ssh_server import LoopbackSSH

MODES = ('local', 'delegate', 'ssh')

# Binary that is not valid UTF-8 and holds every byte value.
BINARY = bytes(range(256)) + b'\xff\xfe\x00\x80 no final newline'


@dataclass(frozen=True)
class Content:
    """One `content` value, how it is sent, and the bytes the file must hold."""

    name: str
    content: str
    encoding: str
    data: bytes


def _text(name: str, text: str) -> Content:
    return Content(name, text, 'utf-8', text.encode('utf-8'))


def _spaced(encoded: str) -> str:
    # Space, tab, CR and LF between groups: the four characters a base64 content may carry.
    chunks = [encoded[i:i + 16] for i in range(0, len(encoded), 16)]
    return ''.join(chunk + ' \t\r\n'[i % 4] for i, chunk in enumerate(chunks))


CONTENTS = (
    _text('no-final-newline', 'first line\nlast line has no newline'),
    _text('empty', ''),
    _text('terminator-line', 'before\nRCAC_MCP_EOF\nEOF\nafter\n'),
    _text('crlf', 'one\r\ntwo\r\n'),
    _text('nul', 'a\x00b\x00'),
    _text('unicode', 'héllo — ✓ \U0001f600'),
    _text('one-mib', ('x' * 1023 + '\n') * 1024),
    Content('binary-base64', base64.b64encode(BINARY).decode(), 'base64', BINARY),
    Content('base64-whitespace', _spaced(base64.b64encode(BINARY).decode()), 'base64', BINARY),
    Content('gnu-wrapped-base64', base64.encodebytes(BINARY * 3).decode(), 'base64', BINARY * 3),
)

# What an overwritten target held before: longer than most contents, so a write that did not
# truncate shows.
OLD = b'old content, longer than the new, which an overwrite must replace entirely\n' * 4

# What an appended-to target held before: no final newline, not valid UTF-8.
PREFIX = b'prefix \xff with no final newline'


@dataclass(frozen=True)
class Refusal:
    """Arguments `write_file` must refuse, and the parameter its error must name."""

    name: str
    args: dict[str, Any]
    names: str


REFUSALS = (
    Refusal('invalid-character', {'content': 'YW!jZA==', 'encoding': 'base64'}, 'content'),
    Refusal('vertical-tab', {'content': 'YWJj\x0bZA==', 'encoding': 'base64'}, 'content'),
    Refusal('form-feed', {'content': 'YWJj\x0cZA==', 'encoding': 'base64'}, 'content'),
    Refusal('url-safe', {'content': '-_-_', 'encoding': 'base64'}, 'content'),
    Refusal('bad-padding', {'content': 'YWJjZA=', 'encoding': 'base64'}, 'content'),
    Refusal('unknown-encoding', {'content': 'text', 'encoding': 'latin-1'}, 'encoding'),
    Refusal('lone-surrogate', {'content': 'a \ud800 b'}, 'content'),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exact_violations(name: str, outcome: Outcome, path: Path, sent: bytes) -> list[str]:
    """Every way a write of `sent` did not leave exactly those bytes at `path`."""
    if outcome.is_error:
        return [f'{name}: the write failed: {outcome.text[:300]}']
    if not path.is_file():
        return [f'{name}: no file at {path}, so the file differs from the bytes sent']
    on_disk = path.read_bytes()
    if on_disk != sent:
        return [f'{name}: sha256 of the file is {sha(on_disk)[:16]} ({len(on_disk)} bytes), the bytes sent '
                f'hash {sha(sent)[:16]} ({len(sent)} bytes): the file differs from the bytes sent']
    return []


def refusal_violations(name: str, outcome: Outcome, names: str, before: bytes | None, path: Path) -> list[str]:
    """Every way a refused write was accepted, did not name `names`, or touched its target.

    `before` is what the target held (None: it did not exist, nor did its parent).
    """
    found = []
    if not outcome.is_error:
        found.append(f'{name}: was accepted, not refused')
    elif names not in outcome.text:
        found.append(f'{name}: the error does not name {names!r}: {outcome.text[:200]!r}')
    if before is None:
        if path.parent.exists():
            found.append(f'{name}: the target changed: {path.parent.name!r} was created')
    elif not path.is_file() or path.read_bytes() != before:
        after = path.read_bytes() if path.is_file() else b''
        found.append(f'{name}: the target changed (sha256 {sha(before)[:16]} -> {sha(after)[:16]})')
    return found


def write_violations(calls: Sequence[tuple[Any, ...]], content: str, data: bytes) -> list[str]:
    """Every way a recorded write did not carry `data` through the executor's `write` alone.

    `content` is what the tool was sent and `data` the bytes it stands for. Exactly one executor
    call, a `write` of exactly `data`, and neither `content` nor `data` in any argv element.
    """
    if len(calls) != 1:
        return [f'expected one executor call, got {len(calls)}: {list(calls)!r:.300}']
    kind, command, *rest = calls[0]
    found = []
    if kind == 'write':
        if rest[0] != data:
            found.append(f'the write does not carry the bytes sent: {rest[0]!r:.80} != {data!r:.80}')
        return found
    found.append(f'the content does not go through the executor\'s write: {calls[0]!r:.200}')
    if isinstance(command, str):
        found.append(f'the write is a command string, not an argv: {command!r:.200}')
        command = [command]
    text = data.decode('utf-8', 'replace')
    for i, element in enumerate(command):
        if (content and content in element) or (text and text in element):
            found.append(f'content in argv element {i}: {element!r:.80}')
    return found


# Unit rows: the recorded executor calls.

PAYLOAD = 'payload-5c1e first line\nlast line, no newline'


@pytest.mark.unit
@pytest.mark.parametrize(('args', 'data'), [
    pytest.param({'content': PAYLOAD}, PAYLOAD.encode(), id='overwrite'),
    pytest.param({'content': PAYLOAD, 'append': True}, PAYLOAD.encode(), id='append'),
    pytest.param({'content': PAYLOAD, 'create_dirs': True}, PAYLOAD.encode(), id='create-dirs'),
    pytest.param({'content': base64.b64encode(BINARY).decode(), 'encoding': 'base64'}, BINARY, id='base64'),
])
def test_content_never_travels_in_argv(args: dict[str, Any], data: bytes, guard) -> None:
    executor = RecordingExecutor('writes')

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor)) as client:
            return await call(client, 'write_file', {'path': '~/new dir/target', **args})

    outcome = drive(scenario())
    assert not outcome.is_error, outcome.text
    violations = write_violations(executor.calls, args['content'], data)
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('refusal', REFUSALS, ids=[r.name for r in REFUSALS])
def test_refused_writes_never_reach_the_executor(refusal: Refusal, guard) -> None:
    executor = RecordingExecutor('refusals')

    async def scenario() -> Outcome:
        async with in_memory(shared_server(executor)) as client:
            return await call(client, 'write_file', {'path': '~/target', 'create_dirs': True, **refusal.args})

    outcome = drive(scenario())
    violations = [] if outcome.is_error else [f'{refusal.name}: was accepted, not refused']
    if outcome.is_error and refusal.names not in outcome.text:
        violations.append(f'{refusal.name}: the error does not name {refusal.names!r}: {outcome.text[:200]!r}')
    if executor.calls:
        violations.append(f'{refusal.name}: the executor was reached before the content was validated: '
                          f'{executor.calls!r:.200}')
    assert violations == [], '\n'.join(violations)
    assert guard.refused == []


ARGV = script_argv(LIST, 'list_directory', '/t')
WROTE = ('write', '/t', PAYLOAD.encode(), False, None, False)


@pytest.mark.unit
@pytest.mark.parametrize(('calls', 'fragment'), [
    pytest.param([('run', [*ARGV[:6], '/t', PAYLOAD], None, None)], 'content in argv', id='in-argv'),
    pytest.param([('run', ARGV, None, None)], "does not go through the executor's write", id='no-write'),
    pytest.param([('write', '/t', PAYLOAD.encode() + b'\n', False, None, False)], 'does not carry the bytes sent',
                 id='newline-added'),
    pytest.param([('run', f'cat >/t <<EOF\n{PAYLOAD}\nEOF', None, None)], 'command string', id='string'),
    pytest.param([WROTE, WROTE], 'expected one executor call', id='two-calls'),
    pytest.param([], 'expected one executor call', id='no-call'),
])
def test_write_check_catches_planted_calls(calls: list[tuple[Any, ...]], fragment: str) -> None:
    assert write_violations([WROTE], PAYLOAD, PAYLOAD.encode()) == []
    violations = write_violations(calls, PAYLOAD, PAYLOAD.encode())
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
def test_exact_and_refusal_checks_catch_planted_results(tmp_path: Path) -> None:
    target = tmp_path / 'target'
    target.write_bytes(b'abc')
    assert exact_violations('x', Outcome(False, ''), target, b'abc') == []
    assert 'differs from the bytes sent' in ''.join(exact_violations('x', Outcome(False, ''), target, b'abc\n'))
    assert 'the write failed' in ''.join(exact_violations('x', Outcome(True, 'boom'), target, b'abc'))
    assert refusal_violations('x', Outcome(True, 'content is bad'), 'content', b'abc', target) == []
    assert 'was accepted' in ''.join(refusal_violations('x', Outcome(False, ''), 'content', b'abc', target))
    assert 'does not name' in ''.join(refusal_violations('x', Outcome(True, 'bad'), 'content', b'abc', target))
    assert 'target changed' in ''.join(refusal_violations('x', Outcome(True, 'content'), 'content', b'ab', target))
    assert 'target changed' in ''.join(refusal_violations('x', Outcome(True, 'content'), 'content', None, target))


# Integration rows: real files in each mode.

@dataclass
class Target:
    """The executor of one mode and the home its `~` resolves to."""

    executor: Executor
    home: Path

    def write(self, calls: Sequence[dict[str, Any]], tool: str = 'write_file') -> list[Outcome]:
        """Call `tool` once per argument set, in order, through the served schema."""
        async def scenario() -> list[Outcome]:
            async with in_memory(shared_server(self.executor)) as client:
                return [await call(client, tool, args) for args in calls]
        return drive(scenario(), timeout=120)


@pytest.fixture
def target(request: pytest.FixtureRequest, executes: Any, tmp_path: Path) -> Iterator[Target]:
    """The mode named by the test's `mode` parameter, writing into its own home (`ssh-openssh`:
    over ssh, with OpenSSH's own sftp-server serving SFTP)."""
    mode = request.param
    if mode == 'local':
        yield Target(LocalShellExecutor(), executes.server_home)
    elif mode == 'delegate':
        yield Target(DelegatingExecutor('writer', {'writer': executes.user}), executes.mapped_home)
    else:
        home = (tmp_path / 'ssh-home').resolve()
        home.mkdir()
        sftp_server = need_sftp_server() if mode == 'ssh-openssh' else None
        with LoopbackSSH(home, '/bin/bash', tmp_path, sftp_server=sftp_server) as server:
            executor = server.executor()
            try:
                yield Target(executor, home)
            finally:
                executor.close()


def _modes(*ops: str) -> list[Any]:
    return [pytest.param(mode, op, id=f'{mode}-{op}') for mode in MODES for op in ops] if ops else \
        [pytest.param(mode, id=mode) for mode in MODES]


@pytest.mark.integration
@pytest.mark.parametrize(('target', 'op'), _modes('overwrite', 'append'), indirect=['target'])
def test_writes_are_byte_exact(target: Target, op: str) -> None:
    append = op == 'append'
    calls, expected = [], []
    for c in CONTENTS:
        path = target.home / f'w-{c.name}'
        path.write_bytes(PREFIX if append else OLD)
        calls.append({'path': f'~/w-{c.name}', 'content': c.content, 'encoding': c.encoding, 'append': append})
        expected.append((c.name, path, (PREFIX if append else b'') + c.data))
    if append:
        # Appending to a file that does not exist creates it.
        calls.append({'path': '~/w-missing', 'content': CONTENTS[0].content, 'append': True})
        expected.append(('missing-file', target.home / 'w-missing', CONTENTS[0].data))
    outcomes = target.write(calls)
    violations = [v for (name, path, sent), outcome in zip(expected, outcomes)
                  for v in exact_violations(name, outcome, path, sent)]
    assert violations == [], '\n'.join(violations)


@pytest.mark.integration
@pytest.mark.parametrize('target', _modes(), indirect=True)
def test_job_scripts_are_byte_exact(target: Target) -> None:
    # sbatch is an argv-echo stand-in: it records the script path it was handed and reports a job.
    argv_echo.install(target.home, 'sbatch')
    texts = [c for c in CONTENTS if c.encoding == 'utf-8']
    outcomes = target.write([{'script_content': c.content, 'job_name': f'w-{c.name}'} for c in texts], tool='sbatch')
    submitted = {argv[argv.index('--') + 1]: argv for _, argv in argv_echo.calls(target.home)}
    violations = []
    for c, outcome in zip(texts, outcomes):
        scripts = sorted((target.home / 'jobs').glob(f'w-{c.name}_*.sh'))
        if outcome.is_error or outcome.text != argv_echo.JOB_ID or len(scripts) != 1:
            violations.append(f'{c.name}: not one script submitted: {scripts!r:.200}, {outcome.text[:200]!r}')
            continue
        violations += exact_violations(c.name, outcome, scripts[0], c.data)
        if not os.access(scripts[0], os.X_OK):
            violations.append(f'{c.name}: the script is not executable')
        if str(scripts[0]) not in submitted:
            violations.append(f'{c.name}: sbatch was not handed {scripts[0]}: {sorted(submitted)!r:.300}')
    assert violations == [], '\n'.join(violations)


CREATE_DIRS = (
    ('spaces', '~/new dir/sub dir/x', ('new dir', 'sub dir', 'x')),
    ('newline-dir', '~/ends in newline\n/x', ('ends in newline\n', 'x')),
)


@pytest.mark.integration
@pytest.mark.parametrize('target', _modes(), indirect=True)
def test_create_dirs_makes_the_exact_parent(target: Target) -> None:
    calls, expected = [], []
    for append in (False, True):
        for name, path, parts in CREATE_DIRS:
            where = Path(target.home, *parts[:-1], f'{parts[-1]}-{int(append)}')
            calls.append({'path': f'{path}-{int(append)}', 'content': PAYLOAD, 'create_dirs': True, 'append': append})
            expected.append((f'{name}-{"append" if append else "overwrite"}', where))
    outcomes = target.write(calls)
    violations = []
    for (name, where), outcome in zip(expected, outcomes):
        if outcome.is_error or not where.is_file() or where.read_bytes() != PAYLOAD.encode():
            violations.append(f'{name}: not written at {str(where.relative_to(target.home))!r}: {outcome.text[:200]}')
    assert violations == [], '\n'.join(violations)


@pytest.mark.integration
@pytest.mark.parametrize('target', _modes(), indirect=True)
def test_invalid_write_leaves_the_target_untouched(target: Target) -> None:
    calls, expected = [], []
    for r in REFUSALS:
        kept = target.home / f'keep-{r.name}'
        kept.write_bytes(OLD)
        calls.append({'path': f'~/keep-{r.name}', **r.args})
        expected.append((f'{r.name}-existing', r.names, OLD, kept))
        calls.append({'path': f'~/absent-{r.name}/x', 'create_dirs': True, **r.args})
        expected.append((f'{r.name}-absent', r.names, None, target.home / f'absent-{r.name}' / 'x'))
    outcomes = target.write(calls)
    violations = [v for (name, names, before, path), outcome in zip(expected, outcomes)
                  for v in refusal_violations(name, outcome, names, before, path)]
    assert violations == [], '\n'.join(violations)


# A profile can export functions over the programs a fixed script runs, and export its shell options
# (SHELLOPTS: `noclobber` refuses an overwrite, `pipefail` changes a pipeline's status). A fixed
# script undoes both before it runs, so the write and the listing are the quiet home's. (Bash 3.2
# refuses to import an exported SHELLOPTS, so only a newer bash shows that half.)
HIJACKING_PROFILE = ('cat() { echo hijacked-cat; }; mkdir() { echo hijacked-mkdir; }; chmod() { :; }\n'
                     'export -f cat mkdir chmod\n'
                     'set -C -o pipefail; export SHELLOPTS\n')


@pytest.mark.integration
@pytest.mark.parametrize('target', _modes(), indirect=True)
def test_a_profile_cannot_change_a_fixed_script(target: Target) -> None:
    argv_echo.install(target.home, 'sbatch')
    (target.home / '.bash_profile').write_text((target.home / '.bash_profile').read_text() + HIJACKING_PROFILE)
    (target.home / 'kept').write_bytes(OLD)
    outcomes = target.write([
        {'path': '~/kept', 'content': PAYLOAD},
        {'path': '~/made dir/x', 'content': PAYLOAD, 'create_dirs': True},
    ]) + target.write([{'script_content': PAYLOAD, 'job_name': 'hijack'}], tool='sbatch')
    violations = exact_violations('overwrite', outcomes[0], target.home / 'kept', PAYLOAD.encode())
    violations += exact_violations('create-dirs', outcomes[1], target.home / 'made dir' / 'x', PAYLOAD.encode())
    scripts = sorted((target.home / 'jobs').glob('hijack_*.sh'))
    if outcomes[2].is_error or len(scripts) != 1:
        violations.append(f'job script: not one script submitted: {scripts!r:.200}, {outcomes[2].text[:200]!r}')
    else:
        violations += exact_violations('job script', outcomes[2], scripts[0], PAYLOAD.encode())
        if not os.access(scripts[0], os.X_OK):
            violations.append('job script: not executable')
    assert violations == [], 'a profile changed what a fixed script does:\n' + '\n'.join(violations)


# Over ssh a write refuses what `cat >` refuses, and leaves what it leaves; each row runs in every
# mode, and over ssh with OpenSSH's own sftp-server too, so the modes agree.
#   (the target's setup, the write's path, whether it fails, the file it checks, the bytes and mode it must hold)
REFUSES = ('local', 'delegate', 'ssh', 'ssh-openssh')


def _plant(home: Path) -> None:
    (home / 'read-only').write_bytes(OLD)
    (home / 'read-only').chmod(0o444)
    (home / 'dangling-dir').symlink_to(home / 'no such dir' / 'file')
    (home / 'dangling-file').symlink_to(home / 'missing-target')
    for name, mode in (('setuid', 0o4755), ('setgid', 0o2755)):
        (home / name).write_bytes(OLD)
        (home / name).chmod(mode)


@pytest.mark.integration
@pytest.mark.parametrize('target', [pytest.param(mode, id=mode) for mode in REFUSES], indirect=True)
def test_a_write_refuses_what_cat_refuses(target: Target) -> None:
    home = target.home
    _plant(home)
    try:
        outcomes = target.write([{'path': f'~/{name}', 'content': PAYLOAD} for name in
                                 ('read-only', 'dangling-dir', 'dangling-file', 'setuid', 'setgid')])
        held = {name: ((home / name).read_bytes() if (home / name).exists() else None,
                       stat.S_IMODE((home / name).stat().st_mode) if (home / name).exists() else None)
                for name in ('read-only', 'setuid', 'setgid', 'missing-target')}
    finally:
        (home / 'read-only').chmod(0o644)
    refused_read_only, refused_dangling, dangling_file, setuid, setgid = outcomes
    violations = []
    if not refused_read_only.is_error or 'Permission denied' not in refused_read_only.text:
        violations.append(f'a read-only target was not refused as `cat >` refuses it: {refused_read_only!r:.300}')
    if held['read-only'] != (OLD, 0o444):
        violations.append(f'a read-only target changed: {held["read-only"]!r:.120}')
    if not refused_dangling.is_error or 'No such file or directory' not in refused_dangling.text:
        violations.append(f'a link whose target directory is missing was not refused: {refused_dangling!r:.300}')
    if target.executor.__class__.__name__ == 'SSHExecutor' and 'no such dir' not in refused_dangling.text:
        violations.append(f'the refusal does not name the missing target: {refused_dangling.text!r:.300}')
    if not (home / 'dangling-dir').is_symlink() or (home / 'no such dir').exists():
        violations.append('a link whose target directory is missing did not stay a link')
    if dangling_file.is_error or held['missing-target'] is None or held['missing-target'][0] != PAYLOAD.encode() \
            or not (home / 'dangling-file').is_symlink():
        violations.append(f'a link to a missing file was not written through, as `cat >` writes it: '
                          f'{dangling_file!r:.200}, the target holds {held["missing-target"]!r:.80}')
    for name, outcome in (('setuid', setuid), ('setgid', setgid)):
        if outcome.is_error or held[name] != (PAYLOAD.encode(), 0o755):
            violations.append(f'a {name} target kept or lost the wrong bits: {outcome!r:.200}, it holds '
                              f'{held[name][0]!r:.40} with mode {held[name][1]:o}, not 755 as `cat >` leaves it')
    assert violations == [], '\n'.join(violations)
