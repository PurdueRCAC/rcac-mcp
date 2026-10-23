# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""F1 (review 1): every budget measurement is in the budget's own unit, UTF-16 code units.

`uv run python spec/agents-d-instructions/remediation/F1-budget-units.py`, from the tree root.

- Library: astral layouts (astral names and contents; an unreadable file with a 60-emoji name, so a
  reason that is 120 units but 60 code points), each read both ways; at every budget from 0 to 400
  past the minimum, a composed text is never more than the budget by an independent UTF-16 count,
  and a failed read's line fits the minimum a good read accepts. Every reason shape, with astral,
  line-break and long details, in both places, holds the same.
- CLI: the real server (`-e local`, stdio, both eras) on the unreadable astral directory at the
  smallest budget a good read accepts, and on the astral files at their own minimum: it starts and
  serves at most the budget.
- The class's mutants: `site-detail-code-points`, `site-reason-unchecked`.

Footprint: one Python process, a few sandboxed bash children, then one server at a time and the
mutation children, sequential; about 1.5 min, under 300 MB.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from common import both, check, finish, mutants, plant, site, u16, unlock

import serve_gate as gate  # noqa: E402
from rcac_mcp.server import SERVER_INSTRUCTIONS  # noqa: E402

ASTRAL = '😀' * 60 + '.md'
GOOD = site.SiteContext(files=(site.SiteFile('a.md', 'Alpha.'),))


def over(context: site.SiteContext, label: str) -> None:
    minimum = site.minimum_budget(SERVER_INSTRUCTIONS, context)
    for budget in range(0, minimum + 400):
        try:
            text = site.compose(SERVER_INSTRUCTIONS, context, budget)
        except site.BudgetError:
            check(budget < minimum, f'{label}: refused {budget} at or over the minimum {minimum}')
            continue
        check(u16(text) <= budget, f'{label}: {u16(text)} units served at a budget of {budget}')
    if not context.files:
        good = site.minimum_budget(SERVER_INSTRUCTIONS, GOOD)
        check(minimum <= good, f"{label}: the line needs {minimum}, over a good read's minimum {good}")


def library(tmp: Path) -> None:
    layouts = {'files': ({ASTRAL: 'Rules 😀.\n', '𝔞.md': 'Fraktur 𝔞.\n', 'b.md': 'B.\n'}, {}),
               'unreadable': ({'a.md': 'A.\n', ASTRAL: 'Locked.\n'}, {ASTRAL: 0})}
    for name, (files, modes) in layouts.items():
        root = plant(tmp / f'lib-{name}', files, modes)
        try:
            reads = both(root)
        finally:
            unlock(root)
        for how, context in reads.items():
            check(bool(context.files) == (name == 'files'), f'{name} {how}: read {context}')
            over(context, f'{name} {how}')
    for detail in ('😀' * 200, 'x' + '😀' * 79, '\n' * 90, ' ' * 50, '𝔞' * 41, 'y' * 300):
        for where in ('the cluster', 'this host'):
            over(site.SiteContext.unreadable(where, detail), f'reason {detail[:3]!r} on {where}')
    for where in ('the cluster', 'this host'):
        for context in (site.SiteContext.missing(where), site.SiteContext.found([], where),
                        site.SiteContext.timed_out(where)):
            over(context, f'{context.reason[:60]!r}')


def cli(tmp: Path) -> None:
    rows = [('unreadable', {'a.md': 'A.\n', ASTRAL: 'Locked.\n'}, {ASTRAL: 0}, None),
            ('files', {ASTRAL: 'Rules 😀.\n', 'b.md': 'B 😀.\n' * 50}, {}, 'own')]
    for name, files, modes, budget in rows:
        root = plant(tmp / f'cli-{name}', files, modes)
        try:
            context = site.read_local('this host', str(root))
            budget = site.minimum_budget(SERVER_INSTRUCTIONS, context if budget == 'own' else GOOD)
            for era in gate.ERAS:
                served = gate.stdio_session(gate.Server(root, tmp / f'log-{name}-{era}',
                                                        ['-e', 'local', '-t', 'stdio', '--instructions-budget',
                                                         str(budget)]), era, gate.instructions_of)
                check(served.startswith(SERVER_INSTRUCTIONS), f'cli {name} {era}: the base was not served')
                check(u16(served) <= budget, f'cli {name} {era}: {u16(served)} units served at {budget}')
                if name == 'unreadable':
                    check(served.endswith('…).') and 'could not be read' in served, f'cli {name} {era}: {served[-120:]!r}')
        finally:
            unlock(root)
    check(gate.FAILURES == [], f'serve helpers: {gate.FAILURES}')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='f1-') as name:
        library(Path(name))
        cli(Path(name))
    mutants('site-detail-code-points', 'site-reason-unchecked')
    return finish('F1 budget units')


if __name__ == '__main__':
    sys.exit(main())
