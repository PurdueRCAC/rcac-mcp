# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""P4's gate: R10's documentation, the retired cache, the fixture deltas, the seeds and the new mutants.

Run from the root of the tree being built:
`uv run python spec/agents-d-instructions/verify/docs_gate.py [docs|mutants|all] [BASE_REF]`.
`docs` is the phase-only check (seconds); `mutants` runs only this cycle's mutation cases (about
2 s each, one child at a time); `all` (the default) runs both. BASE_REF (default: the merge base of HEAD and `origin/develop`)
is what the fixture and the seed ledger are compared with.

Footprint: one Python process; `docs` starts one `bin/sandbox.sh uv run rcac-mcp --help`, `mutants`
one `tests/mutants.py` run whose children are sequential. Under 300 MB; nothing left running.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []
# This cycle's mutation cases (PLAN.md § 6); each must exist in tests/mutants.py and be killed.
MUTANTS = ('instructions-dropped', 'site-agents-md-included', 'site-byte-order', 'site-budget-overflow',
           'site-read-unbounded', 'startup-ssh-unbounded', 'context-rereads', 'context-through-executor')
RETIRED = ('_cluster_context_cache', 'load_cluster_context', 'get_cached_context', 'clear_context_cache',
           'LIST_CONTEXT')


def check(ok: bool, what: str) -> None:
    if not ok:
        FAILURES.append(what)
        print(f'docs_gate: {what}', file=sys.stderr)


def section(text: str, start: str, end: str) -> str:
    """The text from the heading line starting `start` up to the next line starting `end`."""
    match = re.search(rf'^{re.escape(start)}.*?(?=^{re.escape(end)})', text, re.S | re.M)
    return match.group(0) if match else ''


def has(text: str, *needles: str) -> bool:
    flat = ' '.join(text.split())
    return all(' '.join(n.split()) in flat for n in needles)


def check_docs(base: str) -> None:
    agents = Path('AGENTS.md').read_text()
    s8 = section(agents, '### 8.', '### 9.')
    s9 = section(agents, '### 9.', '### Conventions')
    config = section(agents, '## Configuration', '## Testing')
    testing = section(agents, '## Testing', '## Packaging')
    check(bool(s8 and s9 and config and testing), 'AGENTS.md: a section the gate reads is missing')
    check(has(s8, 'AGENTS.md') and re.search(r'(?i)\bskip|\bexclud|\bbut `?AGENTS\.md', s8) is not None,
          '§8 does not state that AGENTS.md is skipped')
    check(re.search(r'(?i)\bonce\b', s8) is not None and re.search(r'(?i)startup', s8) is not None,
          '§8 does not say the files are read once at startup')
    check(has(s8, 'cluster_context') and has(s8, 'rcac://context'), '§8 does not name the tool and the resource')
    check(re.search(r'(?i)budget', s8 + s9) is not None, '§8/§9 do not describe the budget')
    flat8 = ' '.join(s8.split())
    for pattern, what in ((r'_cluster_context_cache', 'the retired cache by name'),
                          (r'(?i)\bcache\b[^.]*\bkeyed by\b[^.]*hostname', 'a cache keyed by hostname'),
                          (r'(?i)process-global[^.]*keyed', 'a process-global keyed store'),
                          (r'No executor configured', 'the resource-read failure')):
        check(re.search(pattern, flat8) is None, f'§8 still describes {what}')
    check(re.search(r'(?i)byte order|LC_ALL=C', s8) is not None, '§8 does not state the order (byte order of the names)')
    footguns = ' '.join(section(agents, '#### Footguns', '### 1.').split())
    check(re.search(r'(?i)context cache|keyed by a non-unique hostname', footguns) is None,
          'the Footguns list still describes the retired cache')
    check(re.search(r'(?i)site context', s9) is not None and has(s9, 'SERVER_INSTRUCTIONS'),
          '§9 does not describe the composed instructions')
    check(has(config, 'RCAC_INSTRUCTIONS_BUDGET') and has(config, '--instructions-budget') and has(config, '4096'),
          '§ Configuration lacks the budget variable, its flag or its default')
    for name in ('test_site.py', 'test_context.py', 'test_site_startup.py'):
        check(name in testing, f'§ Testing does not describe {name}')
    check(has(testing, 'tools.cluster_context') or has(testing, '`cluster_context`'), '§ Testing lacks the fixture deltas')

    server = Path('src/rcac_mcp/server.py').read_text()
    check(re.search(r'^- cluster_context:', server, re.M) is not None, 'SERVER_INSTRUCTIONS does not list cluster_context')
    readme = Path('README.md').read_text()
    check('cluster_context' in readme, 'README.md does not list cluster_context')
    check(re.search(r'(?i)byte order|LC_ALL=C', readme) is not None and has(readme, 'AGENTS.md'),
          'README.md does not state the order or the AGENTS.md exclusion')
    check(has(readme, 'CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH') and has(readme, '--instructions-budget'),
          'README.md does not tell Claude Code users how to raise both limits')
    for path in Path('src').rglob('*.py'):
        text = path.read_text()
        for name in RETIRED:
            check(name not in text, f'{path}: {name} is still there')
    helped = subprocess.run(['bin/sandbox.sh', 'uv', 'run', 'rcac-mcp', '--help'], capture_output=True, text=True,
                            timeout=120, stdin=subprocess.DEVNULL)
    check(helped.returncode == 0 and '--instructions-budget' in helped.stdout
          and 'RCAC_INSTRUCTIONS_BUDGET' in helped.stdout, 'rcac-mcp --help does not document the budget')

    fixture = subprocess.run([sys.executable, str(Path(__file__).with_name('fixture_deltas.py')), 'check', base],
                             capture_output=True, text=True)
    check(fixture.returncode == 0, f'fixture deltas: {fixture.stderr.strip()[-400:]}')

    for seed in ('issues/agents-d-instructions.md', 'issues/context-cache-cross-user.md'):
        check(re.search(r'^status: adopted:agents-d-instructions$', Path(seed).read_text(), re.M) is not None,
              f'{seed} is not adopted:agents-d-instructions')
    added = subprocess.run(['git', 'diff', '--name-only', '--diff-filter=A', f'{base}...HEAD', '--', 'issues/'],
                           capture_output=True, text=True, check=True).stdout.split()
    roadmap = Path('ROADMAP.md').read_text()
    for seed in added:
        check(f'({seed})' in roadmap, f'deferral ledger: {seed} has no ROADMAP.md entry')
        check(re.search(r'^status: unshaped$', Path(seed).read_text(), re.M) is not None,
              f'deferral ledger: {seed} is not status: unshaped')


def check_mutants() -> None:
    sys.path.insert(0, 'tests')
    import mutants  # noqa: E402
    known = {case.id for case in mutants.CASES}
    for case in MUTANTS:
        check(case in known, f'mutation case {case} is missing from tests/mutants.py')
    present = [c for c in MUTANTS if c in known]
    if present:
        done = subprocess.run([sys.executable, 'tests/mutants.py', '--self-check', *present], capture_output=True,
                              text=True, timeout=900)
        check(done.returncode == 0 and 'FAILED' not in done.stdout + done.stderr,
              f'mutation cases: {(done.stdout + done.stderr).strip()[-600:]}')


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else 'all'
    base = argv[1] if len(argv) > 1 else subprocess.run(['git', 'merge-base', 'HEAD', 'origin/develop'], check=True,
                                                        capture_output=True, text=True).stdout.strip()
    if mode in ('docs', 'all'):
        check_docs(base)
    if mode in ('mutants', 'all'):
        check_mutants()
    print(f'docs_gate {mode}: {"FAILED, " + str(len(FAILURES)) + " check(s)" if FAILURES else "ok"}',
          file=sys.stderr)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
