# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""F3 (review 1, GOAL C12): in `delegate` mode the server serves only what others can read.

`uv run python spec/agents-d-instructions/remediation/F3-delegate-others.py [all|docs]`, from the
tree root (`all` is the default; `docs` is only the documentation part, seconds).

- Library, by mode bits: for every others-permission of the directory (0o700-0o707) and of a file
  (0o600-0o607), a read for others is unreadable exactly when the directory lacks others' read or
  search bit or the file others' read bit, and then serves one line and no site text; a read for
  this process serves the files whatever the others bits.
- CLI: the real server in `delegate` mode over HTTP (JWT, two mapped users and one unmapped), both
  eras, with a file and then the directory others cannot read: the tool, the resource and the
  instructions carry the line and no file's text. In `local` mode over stdio, the same directories
  serve the files (the file system's answer). An open directory serves the files in `delegate` mode.
- Docs: AGENTS.md §8 and README.md state the rule; no live text still calls the served files
  world-readable by assumption.
- The class's mutants: `site-others-unchecked`, `delegate-reads-for-itself`.

Footprint: one Python process, one server at a time (HTTP rows bind 127.0.0.1 on a free port), the
mutation children; about 1.5 min, under 300 MB. Root reads anything, so as root the CLI rows still
hold (the rule is the mode bits, not the process's access).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

from common import check, finish, mutants, plant, site, unlock

import serve_gate as gate  # noqa: E402

FILES = {'a.md': 'Alpha rule.\n', 'b.md': 'Bravo rule.\n'}
LINE = 'No site rules are served: /etc/agents.d on this host could not be read ({}).'


def library(tmp: Path) -> None:
    for other in range(8):
        for target in ('.', 'b.md'):
            mode = (0o700 if target == '.' else 0o600) | other
            root = plant(tmp / f'lib-{target.strip(".") or "dir"}-{other}', FILES, {target: mode})
            try:
                shared = site.read_local('this host', str(root), for_others=True)
                own = site.read_local('this host', str(root))
            finally:
                unlock(root)
            need = 0o5 if target == '.' else 0o4
            label = f'{target} {mode:o}'
            check([f.name for f in own.files] == ['a.md', 'b.md'], f'{label}: a read for itself lost files: {own}')
            if other & need == need:
                check([f.name for f in shared.files] == ['a.md', 'b.md'], f'{label}: others can read, yet {shared}')
            else:
                detail = 'not readable by others' if target == '.' else 'b.md: not readable by others'
                check(shared == site.SiteContext(reason=LINE.format(detail), failed=True)
                      and shared.text == shared.reason, f'{label}: others cannot read, yet {shared}')


def cli(tmp: Path) -> None:
    user_map = tmp / 'user-map'
    who = os.environ.get('USER', 'nobody')
    user_map.write_text(f'alice {who}\nbob {who}\n')
    rows = [('file', {'b.md': 0o600}, 'b.md: not readable by others'),
            ('directory', {'.': 0o700}, 'not readable by others'), ('open', {}, None)]
    for label, modes, detail in rows:
        root = plant(tmp / f'cli-{label}', FILES, modes)
        try:
            for era in gate.ERAS:
                port = gate.free_port()
                server = gate.Server(root, tmp / f'log-{label}-{era}', ['-e', 'delegate', '-a', 'jwt', '-t', 'http',
                                                                     '-p', str(port)],
                                     {'JWT_SECRET': gate.SECRET, 'RCAC_USER_MAP': str(user_map)})
                tokens = [gate.mint(gate.SECRET, sub=s) for s in ('alice', 'bob', 'mallory')]
                seen = gate.http_sessions(server, port, era, tokens, steps)
                check(len(seen) == 3, f'delegate {label} {era}: {len(seen)} sessions')
                for user, (tool, resource, instructions) in zip(('alice', 'bob', 'mallory'), seen):
                    if detail:
                        line = LINE.format(detail)
                        check(instructions.endswith(f'{site.HEADING}\n\n{line}') and resource == line
                              and (tool == line or user == 'mallory'),
                              f'delegate {label} {era} {user}: {tool[:60]!r} / {resource[:60]!r}')
                        check('rule.' not in tool + resource + instructions,
                              f'delegate {label} {era} {user}: site text served')
                    else:
                        check('Bravo rule.' in resource and 'Bravo rule.' in instructions,
                              f'delegate {label} {era} {user}: the open directory was not served')
                local = gate.stdio_session(gate.Server(root, tmp / f'log-local-{label}-{era}',
                                                       ['-e', 'local', '-t', 'stdio']), era, steps)
                check(local[0] == local[1] and 'Alpha rule.' in local[1] and 'Bravo rule.' in local[1],
                      f'local {label} {era}: the file system answer was not served: {local[1][:80]!r}')
        finally:
            unlock(root)
    check(gate.FAILURES == [], f'serve helpers: {gate.FAILURES}')


async def steps(client) -> tuple[str, str, str]:
    result = await client.call_tool('cluster_context', {}, raise_on_error=False)
    tool = ''.join(getattr(p, 'text', '') for p in result.content)
    contents = await client.read_resource('rcac://context')
    return tool, ''.join(getattr(c, 'text', '') for c in contents), client.instructions or ''


def docs() -> None:
    agents = Path('AGENTS.md').read_text()
    s8 = ' '.join(gate_section(agents, '### 8.', '### 9.').split())
    check('serves only what others can read' in s8 and 'for_others=True' in s8,
          '§8 does not state that delegate mode serves only what others can read')
    check(re.search(r'`local` and `ssh` read as the user', s8) is not None, '§8 does not keep local and ssh as they are')
    readme = ' '.join(Path('README.md').read_text().split())
    check('serves only what every account could read' in readme, 'README.md does not state the delegate rule')
    for path in [Path('AGENTS.md'), Path('README.md'), *Path('src').rglob('*.py'), *Path('tests').glob('*.py')]:
        check('world-readable administrative markdown' not in ' '.join(path.read_text().split()),
              f'{path}: still says the files are world-readable by assumption')


def gate_section(text: str, start: str, end: str) -> str:
    match = re.search(rf'^{re.escape(start)}.*?(?=^{re.escape(end)})', text, re.S | re.M)
    return match.group(0) if match else ''


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else 'all'
    docs()
    if mode == 'all':
        with tempfile.TemporaryDirectory(prefix='f3-') as name:
            library(Path(name))
            cli(Path(name))
        mutants('site-others-unchecked', 'delegate-reads-for-itself')
    return finish(f'F3 delegate others ({mode})')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
