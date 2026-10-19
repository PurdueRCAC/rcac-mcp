# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Apply this cycle's named served-surface deltas to tests/fixtures/served_surface.json, and check them.

Run from the root of the tree being built: `uv run python spec/agents-d-instructions/verify/fixture_deltas.py
apply|check [BASE_REF]`. Footprint: one process, no network, under 200 MB.

The fixture keeps the FastMCP 2 snapshot's shape (AGENTS.md § Invariants §9). Until
`issues/surface-fixture-after-a-surface-change.md` settles the method, a deliberate change is a
scripted edit of named deltas only. This cycle's deltas, and nothing else:

- `instructions`: the base instructions composed with the harness's site context (`harness.SITE`);
- `tools.cluster_context`: the new tool, once it is registered (P3 on). It takes no argument and returns a `str`, exactly as
  `myquota` does, so its schemas are `myquota`'s as the snapshot recorded them; its description is
  its docstring. `test_served_surface_matches_baseline` then checks the copy against what is served;
- `resources.rcac://context.description`: the resource's new description.

`check` compares the fixture with BASE_REF's (default: the merge base of HEAD and `origin/develop`) and fails, naming each path,
when anything outside those deltas differs, or when a delta is not exactly what `apply` writes.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, 'tests')

FIXTURE = Path('tests/fixtures/served_surface.json')
DELTAS = ('instructions', 'tools.cluster_context', 'resources.rcac://context.description')


def expected(base: dict) -> dict:
    """`base` with this cycle's deltas applied, taken from the current tree."""
    from harness import SITE
    from rcac_mcp.resources import RESOURCE_REGISTRY
    from rcac_mcp.server import SERVER_INSTRUCTIONS
    from rcac_mcp.site import compose
    from rcac_mcp.tools import TOOL_REGISTRY

    fixture = json.loads(json.dumps(base))
    fixture['instructions'] = compose(SERVER_INSTRUCTIONS, SITE)
    for tool in (t for t in TOOL_REGISTRY if t.name == 'cluster_context'):
        fixture['tools']['cluster_context'] = {
            'description': inspect.getdoc(tool.fn),
            'inputSchema': base['tools']['myquota']['inputSchema'],
            'outputSchema': base['tools']['myquota']['outputSchema'],
        }
    context = next(r for r in RESOURCE_REGISTRY if r['uri'] == 'rcac://context')
    fixture['resources']['rcac://context']['description'] = context['description']
    return dict(sorted(fixture.items()))


def _paths(a: object, b: object, prefix: str = '') -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            out += _paths(a.get(key), b.get(key), f'{prefix}.{key}' if prefix else key)
        return out
    return [] if a == b else [prefix]


def main(argv: list[str]) -> int:
    mode = argv[0]
    ref = argv[1] if len(argv) > 1 else subprocess.run(['git', 'merge-base', 'HEAD', 'origin/develop'], check=True,
                                                       capture_output=True, text=True).stdout.strip()
    base = json.loads(subprocess.run(['git', 'show', f'{ref}:{FIXTURE}'], check=True, capture_output=True,
                                     text=True).stdout)
    want = expected(base)
    if mode == 'apply':
        FIXTURE.write_text(json.dumps(want, indent=1, sort_keys=True) + '\n')
        return 0
    have = json.loads(FIXTURE.read_text())
    stray = [p for p in _paths(base, have) if not any(p == d or p.startswith(d + '.') for d in DELTAS)]
    wrong = [p for p in _paths(want, have)]
    for p in stray:
        print(f'fixture_deltas: {p} differs from {ref} and is not a named delta', file=sys.stderr)
    for p in wrong:
        print(f'fixture_deltas: {p} is not what the named delta writes', file=sys.stderr)
    # A delta is due when the tree has its source: the instructions always, the tool once registered.
    due = [d for d in DELTAS if _paths(base, want) and any(p == d or p.startswith(d + '.') for p in _paths(base, want))]
    missing = [d for d in due if d not in {q for p in _paths(base, have) for q in DELTAS
                                           if p == q or p.startswith(q + '.')}]
    if 'instructions' not in due:
        print('fixture_deltas: the composed instructions equal the base fixture\'s; nothing was composed',
              file=sys.stderr)
        missing.append('instructions')
    for d in missing:
        print(f'fixture_deltas: the delta {d} was not applied', file=sys.stderr)
    return 1 if stray or wrong or missing else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
