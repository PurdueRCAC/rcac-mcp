"""Static half of the gate: what this fix must leave alone, read against the base revision.

    uv run python spec/sdist-hygiene-truncated-gzip/verify/static.py [--base REV]

Run from the repository root. Red when:
- `.github/workflows/ci.yml` differs from the base, or the release recipe's hygiene step is not
  still exactly `- uv run python bin/sdist_hygiene.py dist/` (one invocation each, unchanged);
- `FURNITURE`, `SDIST_SUFFIX`, `member_violations` or `main` in bin/sdist_hygiene.py differ from
  the base, compared as parsed code (comments and layout aside);
- any tracked Python file but the script, outside tests/ and spec/, names `FURNITURE` (a second copy);
- AGENTS.md § Testing's entry for the module no longer says what it now pins (truncated and
  damaged streams, and that the others are still checked).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = 'bin/sdist_hygiene.py'
STEP = '    - uv run python bin/sdist_hygiene.py dist/'
KEPT = ('FURNITURE', 'SDIST_SUFFIX', 'member_violations', 'main')


def show(rev: str, rel: str) -> str:
    return subprocess.run(['git', 'show', f'{rev}:{rel}'], capture_output=True, text=True, check=True).stdout


def definitions(source: str) -> dict[str, str]:
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            found[node.name] = ast.dump(node)
        elif isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id] = ast.dump(node)
    return found


def problems(base: str) -> list[str]:
    found = []
    if subprocess.run(['git', 'diff', '--quiet', base, '--', '.github/workflows/ci.yml']).returncode:
        found.append('.github/workflows/ci.yml differs from the base')
    if subprocess.run(['git', 'diff', '--quiet', '--', '.github/workflows/ci.yml']).returncode:
        found.append('.github/workflows/ci.yml has uncommitted changes')
    agents = Path('AGENTS.md').read_text()
    if agents.splitlines().count(STEP) != 1:
        found.append(f'AGENTS.md: the recipe step {STEP.strip()!r} is not there exactly once')
    old, new = definitions(show(base, SCRIPT)), definitions(Path(SCRIPT).read_text())
    for name in KEPT:
        if name not in new:
            found.append(f'{SCRIPT}: {name} is gone')
        elif old[name] != new[name]:
            found.append(f'{SCRIPT}: {name} differs from the base')
    tracked = subprocess.run(['git', 'ls-files'], capture_output=True, text=True, check=True).stdout.split()
    for rel in tracked:
        if rel.endswith('.py') and rel != SCRIPT and not rel.startswith(('tests/', 'spec/')) and 'FURNITURE' in Path(rel).read_text():
            found.append(f'{rel}: a second FURNITURE definition')
    # Prose wraps, so the entry is read with its whitespace collapsed.
    entry = ' '.join(re.split(r'\n\s*- ', agents.split('`tests/test_sdist_hygiene.py`', 1)[-1], maxsplit=1)[0].split())
    for word in ('truncated', 'damaged', 'still checked'):
        if word not in entry:
            found.append(f'AGENTS.md § Testing: the test_sdist_hygiene entry does not say {word!r}')
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='5153e47')
    found = problems(parser.parse_args().base)
    for line in found:
        print(f'static: {line}', file=sys.stderr)
    if found:
        return 1
    print('static: ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
