"""Show that tests/test_sdist_hygiene.py goes red when bin/sdist_hygiene.py is broken.

    uv run --with pyyaml python spec/ci-workflow/verify/hygiene_mutants.py [--from worktree|HEAD]

Each plant is an exact edit to bin/sdist_hygiene.py in a fresh snapshot (replay.py's), after
which the test module runs there with a JUnit report. A plant is killed only when the named
test ids fail with a `<failure>` (not an `<error>`, which an import or syntax slip produces)
and nothing else is off: a test that passes against a broken check is hollow. The unplanted
snapshot must pass every test in the module first, and every id named here must exist in it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import replay  # noqa: E402

MODULE = 'tests/test_sdist_hygiene.py'
SCRIPT = 'bin/sdist_hygiene.py'

# (name, old, new, test ids that must fail). Each `old` occurs exactly once in the script.
# The last three came from the adversarial design review: each left all of the first 15 tests green.
PLANTS = [
    ('prefix-not-component', 'relative[: len(entry)] == entry', "'/'.join(relative).startswith('/'.join(entry))",
     ['test_lookalikes_pass']),
    ('no-root-check', "if not parts or parts[0] != root or '..' in parts:", "if not parts or '..' in parts:",
     ['test_member_outside_root_is_named']),
    ('no-sdist-passes', "        return [f'{dist}: no sdist (*{SDIST_SUFFIX}) to check']", '        return []',
     ['test_no_sdist_fails']),
    ('drop-security', "FURNITURE: tuple[str, ...] = ('.security', ", 'FURNITURE: tuple[str, ...] = (',
     ['test_each_furniture_entry_is_named[.security]', 'test_furniture_matches_the_recorded_set']),
    ('first-tarball-only', '    for tarball in tarballs:', '    for tarball in tarballs[:1]:',
     ['test_every_tarball_is_checked']),
    ('exit-zero', '        return 1\n', '        return 0\n', ['test_main_names_each_path_and_fails']),
    ('silent', '        print(line, file=sys.stderr)', '        pass', ['test_main_names_each_path_and_fails']),
    ('single-offender-passes', '    if found:\n', '    if len(found) > 1:\n', ['test_main_fails_on_a_single_offender']),
    ('dotdot-allowed', "if not parts or parts[0] != root or '..' in parts:", 'if not parts or parts[0] != root:',
     ['test_parent_reference_is_named']),
    ('root-from-first-member', "member_violations(root, names))",
     "member_violations(PurePosixPath(names[0]).parts[0] if names else root, names))",
     ['test_root_is_the_tarball_name']),
]


def outcomes(repo: Path, env: dict, report: Path) -> dict[str, str]:
    subprocess.run(['uv', 'run', 'pytest', '-q', '-p', 'no:cacheprovider', MODULE, f'--junitxml={report}'],
                   cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not report.is_file():
        return {}
    result = {}
    for case in ET.parse(report).iter('testcase'):
        tags = {child.tag for child in case}
        result[case.get('name')] = ('failure' if 'failure' in tags else 'error' if 'error' in tags
                                    else 'skipped' if 'skipped' in tags else 'passed')
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='source', choices=['worktree', 'HEAD'], default='worktree')
    args = parser.parse_args()
    root = Path(replay.git('rev-parse', '--show-toplevel', cwd=Path.cwd()))
    bad = []
    with tempfile.TemporaryDirectory(prefix='ci-hygiene-mutants.') as tmp:
        scratch = Path(tmp)
        _, repo = replay.snapshot(root, args.source, scratch)
        env = replay.runner_env(scratch)
        subprocess.run(['uv', 'sync', '--locked', '-q'], cwd=repo, env=env, check=True)
        original = (repo / SCRIPT).read_text()
        clean = outcomes(repo, env, scratch / 'clean.xml')
        named = {test for *_, tests in PLANTS for test in tests}
        if not clean or set(clean.values()) != {'passed'} or not named <= set(clean):
            print(f'hygiene-mutants: the unplanted module is not all-passed with every named id: {clean}', file=sys.stderr)
            return 1
        for name, old, new, tests in PLANTS:
            if original.count(old) != 1:
                bad.append(f'{name}: the plant no longer applies ({old!r} occurs {original.count(old)} times)')
                continue
            (repo / SCRIPT).write_text(original.replace(old, new))
            result = outcomes(repo, env, scratch / f'{name}.xml')
            missed = [t for t in tests if result.get(t) != 'failure']
            if missed:
                bad.append(f'{name}: survived; {", ".join(f"{t}={result.get(t)}" for t in missed)}')
            else:
                print(f'hygiene-mutants: killed {name} ({", ".join(tests)})')
        (repo / SCRIPT).write_text(original)
    for line in bad:
        print(f'hygiene-mutants: FAILED {line}', file=sys.stderr)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
