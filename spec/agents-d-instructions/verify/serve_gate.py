# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""P2's and P3's gate: the real CLI, started over stdio and HTTP on planted /etc/agents.d layouts.

Run from the root of the tree being built:
`uv run python spec/agents-d-instructions/verify/serve_gate.py instructions|context|all`.

- `instructions` (P2): in both protocol eras, the served instructions are the base followed by the
  site context composed by an oracle written here, at the default budget, at a flag budget and at
  an environment budget; a budget too small exits 2 naming the minimum; a missing, empty or
  unreadable directory serves the base and one line; a read that never finishes still lets the
  server answer within the bound, with the timeout line; and the site is read exactly once.
- `context` (P3): in both eras, `cluster_context` and `rcac://context` return the full text (or
  `''`, or the failure line) whatever the budget, after many calls the site has still been read
  once, and over HTTP in `delegate` mode two users get identical text, instructions included.

Footprint: one Python process plus one server at a time, each `bin/sandbox.sh uv run python
serve_shim.py …` (about 150 MB), stopped and its process group killed before the next starts; the
HTTP rows bind 127.0.0.1 on a free port. No network beyond loopback (each server gets
`FASTMCP_CHECK_FOR_UPDATES=off`, which stops FastMCP's PyPI check), no real cluster: every server
runs `-e local` or `-e delegate` against a planted directory. About 1.5 min in all, a third of it
the two 10 s timeout rows. Planted directories live in a temporary directory removed on exit.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, 'tests')

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport  # noqa: E402

from harness import era_mismatch, mint, session_era  # noqa: E402
from rcac_mcp.server import SERVER_INSTRUCTIONS  # noqa: E402

SANDBOX = Path('bin/sandbox.sh').resolve()
SHIM = Path(__file__).resolve().with_name('serve_shim.py')
ERAS = ('legacy', 'modern')
SECRET = 'serve-gate-secret-0123456789abcdef0123456789'
FAILURES: list[str] = []
# Root reads a mode-0 directory anyway: the unreadable rows would fail for the wrong reason.
ROOT = os.geteuid() == 0
HEADING = '## Site context (/etc/agents.d)'


def check(ok: bool, what: str) -> None:
    if not ok:
        FAILURES.append(what)
        print(f'serve_gate: {what}', file=sys.stderr)


def u16(text: str) -> int:
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def full_text(files: list[tuple[str, str]]) -> str:
    return '\n\n'.join(f'<!-- Source: {n} -->\n{t}' for n, t in files)


def reasons(where: str, detail: str = 'x' * 80) -> list[str]:
    """PLAN.md § 2.1's four one-line reasons, word for word."""
    return [f'No site rules are served: /etc/agents.d does not exist on {where}.',
            f'No site rules are served: /etc/agents.d on {where} holds no rules (no non-empty *.md file but AGENTS.md).',
            f'No site rules are served: /etc/agents.d on {where} could not be read ({detail}).',
            f'No site rules are served: reading /etc/agents.d on {where} did not finish within 10 s.']


def oracle(files: list[tuple[str, str]], budget: int) -> str | None:
    """PLAN.md § 2.2's composition, re-implemented here; None when the budget is refused."""
    head = f'{SERVER_INSTRUCTIONS}\n\n{HEADING}\n\n'
    if budget < u16(head) + max(u16(r) for w in ('the cluster', 'this host') for r in reasons(w)):
        return None

    def render(shown: list[str], left: list[str]) -> str:
        parts = [f'<!-- Source: {n} -->\n{t}' for n, t in files if n in shown]
        if left:
            parts.append(f'Left out here for length: {", ".join(left)}. '
                         f'Call the `cluster_context` tool for the full site context.')
        return head + '\n\n'.join(parts)

    names = [n for n, _ in files]
    if u16(render(names, [])) <= budget:
        return render(names, [])
    if u16(render([], names)) > budget:
        return None
    shown: list[str] = []
    for name in names:
        trial = [*shown, name]
        if u16(render(trial, [n for n in names if n not in trial])) <= budget:
            shown.append(name)
    return render(shown, [n for n in names if n not in shown])


def minimum(files: list[tuple[str, str]]) -> int:
    return next(b for b in range(0, 100_000) if oracle(files, b) is not None)


# Layouts: sizes chosen so the default budget holds the base, the first file and the note, but not all.
BIG = [('10-policies.md', 'Never run heavy work on a login node. ' * 30),
       ('20-storage.md', 'Scratch is purged; keep results elsewhere. ' * 60),
       ('30-slurm.md', 'Use `sbatch` with an account. ' * 20)]
# The product drops each file's trailing whitespace; the oracle's inputs are trimmed once, here.
LAYOUTS = {'big': [(n, t.rstrip()) for n, t in BIG], 'small': [('a.md', 'Alpha.'), ('b.md', 'Bravo `rule` é 😀.')]}


def plant(root: Path, files: list[tuple[str, str]]) -> Path:
    root.mkdir(parents=True)
    for name, text in files:
        (root / name).write_text(text + '\n')
    (root / 'AGENTS.md').write_text('The assembled file: must never appear.\n')
    return root


class Server:
    """One shim server under bin/sandbox.sh, in its own process group, killed whole on exit."""

    def __init__(self, directory: Path, log: Path, argv: list[str], env: dict[str, str] | None = None,
                 hang: bool = False) -> None:
        # bin/sandbox.sh blanks the auth environment, so variables the server needs go through `env`
        # inside it; the gate's own RCAC_INSTRUCTIONS_BUDGET never reaches a server.
        # FastMCP's banner checks PyPI for updates unless told not to: the gate stays on loopback.
        assigned = [f'{k}={v}' for k, v in {'FASTMCP_CHECK_FOR_UPDATES': 'off', **(env or {})}.items()]
        self.argv = [str(SANDBOX), 'env', '-u', 'RCAC_INSTRUCTIONS_BUDGET', *assigned, 'uv', 'run', 'python',
                     str(SHIM), str(directory), str(log), *(['--hang'] if hang else []), '--', *argv]
        self.env = dict(os.environ)


def stdio_session(server: Server, era: str, steps):
    async def go():
        transport = StdioTransport(server.argv[0], server.argv[1:], env=server.env, keep_alive=False)
        async with Client(transport, mode='legacy' if era == 'legacy' else 'auto') as client:
            mismatch = era_mismatch(era, *session_era(client))
            check(mismatch is None, f'stdio {era}: {mismatch}')
            return await steps(client)
    return asyncio.run(asyncio.wait_for(go(), 90))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def http_sessions(server: Server, port: int, era: str, tokens: list[str | None], steps) -> list:
    proc = subprocess.Popen(server.argv, env=server.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    break
            time.sleep(0.2)
        else:
            check(False, f'http {era}: the server did not start listening')
            return []

        async def go():
            out = []
            for token in tokens:
                transport = StreamableHttpTransport(f'http://127.0.0.1:{port}/mcp', auth=token)
                async with Client(transport, mode='legacy' if era == 'legacy' else 'auto') as client:
                    mismatch = era_mismatch(era, *session_era(client))
                    check(mismatch is None, f'http {era}: {mismatch}')
                    out.append(await steps(client))
            return out
        return asyncio.run(asyncio.wait_for(go(), 90))
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


async def instructions_of(client) -> str:
    return client.instructions or ''


def reads(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def read_once(log: Path) -> bool:
    """One startup read: `read_local` once, the directory listed at most once, nothing else."""
    seen = reads(log)
    return seen.count('read_local') == 1 and seen.count('list') <= 1 and set(seen) <= {'read_local', 'list'}


def check_instructions(tmp: Path) -> None:
    for name, files in LAYOUTS.items():
        directory = plant(tmp / f'i-{name}', files)
        for label, argv, env, budget in (
                ('default', [], {}, 4096),
                ('flag', ['--instructions-budget', '20000'], {}, 20000),
                ('env', [], {'RCAC_INSTRUCTIONS_BUDGET': str(minimum(files))}, minimum(files))):
            want = oracle(files, budget)
            for era in ERAS:
                log = tmp / f'log-{name}-{label}-{era}'
                got = stdio_session(Server(directory, log, ['-e', 'local', '-t', 'stdio', *argv], env), era,
                                    instructions_of)
                check(got == want, f'{name} {label} {era}: instructions differ from the oracle: '
                                   f'{got[-160:]!r} vs {(want or "")[-160:]!r}')
                check(read_once(log), f'{name} {label} {era}: reads {reads(log)}')
        if name == 'big':
            check('Left out here for length' in oracle(files, 4096), 'gate layout: big does not overflow 4096')
    # A budget too small: exit 2, naming the minimum, before serving.
    files = LAYOUTS['big']
    directory = tmp / 'i-big'
    done = subprocess.run(Server(directory, tmp / 'log-small', ['-e', 'local', '-t', 'http', '-p', '9',
                                                                '--instructions-budget', '100']).argv,
                          capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
    check(done.returncode == 2, f'a budget of 100 exited {done.returncode}, expected 2')
    check(f'at least {minimum(files)}' in done.stderr, f'the refusal does not name {minimum(files)}: '
                                                       f'{done.stderr[-300:]!r}')
    # No site rules: missing, empty, unreadable; then a read that never finishes.
    (tmp / 'empty').mkdir()
    (tmp / 'locked').mkdir()
    (tmp / 'locked' / 'a.md').write_text('a\n')
    rows = [('missing', tmp / 'nope', 'does not exist'), ('empty', tmp / 'empty', 'holds no rules'),
            ('unreadable', tmp / 'locked', 'could not be read')]
    if ROOT:
        rows.pop()
    (tmp / 'locked').chmod(0)
    try:
        for label, directory, fragment in rows:
            for era in ERAS:
                got = stdio_session(Server(directory, tmp / f'log-{label}-{era}', ['-e', 'local', '-t', 'stdio']),
                                    era, instructions_of)
                head = f'{SERVER_INSTRUCTIONS}\n\n{HEADING}\n\n'
                line = got[len(head):]
                check(got.startswith(head) and fragment in line and '\n' not in line
                      and line.startswith('No site rules are served'), f'{label} {era}: {got[-160:]!r}')
    finally:
        (tmp / 'locked').chmod(0o755)
    # A read that never finishes: the server answers about 10 s later than it does without the hang,
    # with the timeout line, even at the smallest budget a good read accepts.
    tight = ['--instructions-budget', str(minimum(LAYOUTS['small']))]
    for era in ERAS:
        began = time.monotonic()
        stdio_session(Server(tmp / 'i-small', tmp / f'log-base-{era}', ['-e', 'local', '-t', 'stdio', *tight]), era,
                      instructions_of)
        baseline = time.monotonic() - began
        began = time.monotonic()
        got = stdio_session(Server(tmp / 'i-small', tmp / f'log-hang-{era}', ['-e', 'local', '-t', 'stdio', *tight],
                                   hang=True), era, instructions_of)
        took = time.monotonic() - began
        check(got.endswith('did not finish within 10 s.'), f'hung read {era}: no timeout line: {got[-160:]!r}')
        check(took < baseline + 10 + 3, f'hung read {era}: answered after {took:.1f} s, {baseline:.1f} s without it')


async def _seen(client) -> tuple[str, str, str]:
    tool = await client.call_tool('cluster_context', {}, raise_on_error=False)
    text = ''.join(getattr(p, 'text', '') for p in tool.content)
    resource = ''.join(getattr(p, 'text', '') for p in await client.read_resource('rcac://context'))
    return ('error:' if tool.is_error else '') + text, resource, client.instructions or ''


def disturb(directory: Path) -> dict[Path, bytes]:
    """Change what the planted directory holds; return what to put back. A re-read would now differ."""
    saved: dict[Path, bytes] = {}
    try:
        for path in sorted(directory.glob('*.md')):
            saved[path] = path.read_bytes()
            path.write_text('Changed after startup.\n')
        (directory / '00-added.md').write_text('Added after startup.\n')
    except OSError:
        pass  # A missing or unreadable directory: nothing to change.
    return saved


def restore(directory: Path, saved: dict[Path, bytes]) -> None:
    (directory / '00-added.md').unlink(missing_ok=True) if directory.is_dir() and os.access(directory, os.W_OK) else None
    for path, data in saved.items():
        path.write_bytes(data)


def context_steps(directory: Path):
    """Read once, change the directory, read five more times: every answer must equal the first."""
    async def steps(client) -> tuple[str, str, str]:
        first = await _seen(client)
        saved = disturb(directory)
        try:
            for _ in range(5):
                again = await _seen(client)
                check(again == first, f'an answer changed after the directory did: {again[0][:80]!r}')
        finally:
            restore(directory, saved)
        return first
    return steps


def check_context(tmp: Path) -> None:
    files = LAYOUTS['big']
    directory = plant(tmp / 'c-big', files)
    (tmp / 'c-empty').mkdir()
    (tmp / 'c-locked').mkdir()
    (tmp / 'c-locked').chmod(0)
    rows = [('files-default', directory, [], full_text(files)),
            ('files-tight', directory, ['--instructions-budget', str(minimum(files))], full_text(files)),
            ('missing', tmp / 'c-nope', [], ''), ('empty', tmp / 'c-empty', [], ''),
            ('unreadable', tmp / 'c-locked', [], None)]
    if ROOT:
        rows.pop()
    try:
        for label, path, argv, want in rows:
            for era in ERAS:
                log = tmp / f'log-c-{label}-{era}'
                tool, resource, instructions = stdio_session(
                    Server(path, log, ['-e', 'local', '-t', 'stdio', *argv]), era, context_steps(path))
                if want is None:
                    line = instructions.rpartition('\n\n')[2]
                    check(line.startswith('No site rules are served') and tool == line == resource,
                          f'{label} {era}: tool {tool[:80]!r}, resource {resource[:80]!r}, line {line[:80]!r}')
                else:
                    check(tool == want and resource == want, f'{label} {era}: tool {tool[:80]!r} / resource '
                                                            f'{resource[:80]!r} differ from {want[:80]!r}')
                check(read_once(log), f'{label} {era}: reads {reads(log)} after 12 requests')
    finally:
        (tmp / 'c-locked').chmod(0o755)
    # Delegate over HTTP: two mapped users, one hostname, identical text, the directory changed between
    # them; nothing run as either. An authenticated user absent from the map gets the instructions and
    # the resource, and the tool's refusal, never a default identity (§5; GOAL clarification pending).
    user_map = tmp / 'user-map'
    user_map.write_text(f'alice {os.environ.get("USER", "nobody")}\nbob {os.environ.get("USER", "nobody")}\n')
    for era in ERAS:
        port = free_port()
        log = tmp / f'log-delegate-{era}'
        server = Server(directory, log, ['-e', 'delegate', '-a', 'jwt', '-t', 'http', '-p', str(port)],
                        {'JWT_SECRET': SECRET, 'RCAC_USER_MAP': str(user_map)})
        tokens = [mint(SECRET, sub='alice'), mint(SECRET, sub='bob'), mint(SECRET, sub='mallory')]
        seen = http_sessions(server, port, era, tokens, context_steps(directory))
        want = (full_text(files), full_text(files), oracle(files, 4096))
        check(len(seen) == 3 and seen[0] == want and seen[1] == want,
              f'delegate {era}: mapped users saw different or wrong text')
        if len(seen) == 3:
            tool, resource, instructions = seen[2]
            check(tool.startswith('error:') and 'not authorized' in tool and resource == want[1]
                  and instructions == want[2], f'delegate {era}: the unmapped user saw {tool[:80]!r}')
        check(read_once(log), f'delegate {era}: reads {reads(log)}')


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else 'all'
    with tempfile.TemporaryDirectory(prefix='serve-gate-', ignore_cleanup_errors=True) as name:
        if mode in ('instructions', 'all'):
            check_instructions(Path(name) / 'i')
        if mode in ('context', 'all'):
            check_context(Path(name) / 'c')
    leftovers = subprocess.run(['pgrep', '-f', str(SHIM)], capture_output=True, text=True).stdout.split()
    check(leftovers == [], f'shim servers left running: {leftovers}')
    print(f'serve_gate {mode}: {"FAILED, " + str(len(FAILURES)) + " check(s)" if FAILURES else "ok"}',
          file=sys.stderr)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
