# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""F2 (review 1): no file name can break a served line.

`uv run python spec/agents-d-instructions/remediation/F2-name-lines.py`, from the tree root.

- Library: names carrying every control character class (each C0 code, DEL, each C1 code, U+2028,
  U+2029), read both ways, as readable files and as the unreadable one: no served name holds a
  character of Unicode category Cc, Zl or Zp; every line (`str.splitlines`) of the site section,
  at every budget from the minimum to 400 past it, is a header, a file's text, the note, the reason
  or blank; and the reason is one line.
- CLI: the real server (`-e local`, stdio, both eras) on such names: the instructions' site section
  and the tool's text hold only those lines.
- The class's mutant: `site-name-unescaped`.

Footprint: one Python process, a few sandboxed bash children, one server at a time, one mutation
child; about 40 s, under 300 MB.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from common import both, breaks, check, finish, mutants, plant, site, unlock

import serve_gate as gate  # noqa: E402

# Every C0 code but NUL (no file name holds one) and `/`'s neighbours are fine; DEL; every C1 code;
# the line and paragraph separators. Spread over names so each stays short.
CODES = [*range(1, 0x20), 0x7f, *range(0x80, 0xa0), 0x2028, 0x2029]
NAMES = [f'n{i:02d}' + ''.join(chr(c) for c in CODES[i::8]) + '.md' for i in range(8)]
HEAD = '<!-- Source: '


def section_violations(text: str, context: site.SiteContext, label: str) -> None:
    allowed = {'', *(f.text for f in context.files)}
    for line in text.splitlines():
        if line.startswith(HEAD) and line.endswith(' -->'):
            name = line[len(HEAD):-4]
            check(not any(breaks(c) for c in name), f'{label}: a header holds a control character: {line!r}')
        elif line.startswith('Left out here for length: ') or line.startswith('No site rules are served: '):
            check(not any(breaks(c) for c in line), f'{label}: a line holds a control character: {line!r}')
        else:
            check(line in allowed, f'{label}: a line no name may make: {line!r}')


def library(tmp: Path) -> None:
    root = plant(tmp / 'files', {name: 'Rule.\n' for name in NAMES})
    for how, context in both(root).items():
        check(len(context.files) == len(NAMES), f'files {how}: read {len(context.files)} of {len(NAMES)}')
        check(len(context.text.splitlines()) == 3 * len(context.files) - 1, f'files {how}: the text split apart')
        minimum = site.minimum_budget('Base.', context)
        for budget in range(minimum, minimum + 400, 3):
            section = site.compose('Base.', context, budget).split(f'{site.HEADING}\n\n', 1)[1]
            section_violations(section, context, f'files {how} at {budget}')
        check(len(site.omission_note([f.name for f in context.files]).splitlines()) == 1, f'files {how}: the note split')
    for name in NAMES:
        root = plant(tmp / f'locked-{name[:3]}', {'a.md': 'A.\n', name: 'B.\n'}, {name: 0})
        try:
            reads = both(root)
        finally:
            unlock(root)
        for how, context in reads.items():
            check(context.failed and len(context.reason.splitlines()) == 1
                  and not any(breaks(c) for c in context.reason), f'locked {name[:3]} {how}: {context.reason!r}')


def cli(tmp: Path) -> None:
    root = tmp / 'files'
    context = site.read_local('this host', str(root))

    async def steps(client) -> tuple[str, str]:
        result = await client.call_tool('cluster_context', {})
        return client.instructions or '', ''.join(getattr(p, 'text', '') for p in result.content)

    for era in gate.ERAS:
        for budget in ('4096', str(site.minimum_budget(gate.SERVER_INSTRUCTIONS, context))):
            instructions, tool = gate.stdio_session(gate.Server(root, tmp / f'log-{era}-{budget}',
                                                                ['-e', 'local', '-t', 'stdio', '--instructions-budget',
                                                                 budget]), era, steps)
            section_violations(instructions.split(f'{site.HEADING}\n\n', 1)[1], context, f'cli {era} {budget}')
            section_violations(tool, context, f'cli tool {era}')
            check(tool == context.text, f'cli tool {era}: the text differs from the read')
    check(gate.FAILURES == [], f'serve helpers: {gate.FAILURES}')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='f2-') as name:
        library(Path(name))
        cli(Path(name))
    mutants('site-name-unescaped')
    return finish('F2 name lines')


if __name__ == '__main__':
    sys.exit(main())
