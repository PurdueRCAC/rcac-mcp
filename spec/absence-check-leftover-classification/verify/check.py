"""Checks behind gate.sh for absence-check-leftover-classification. Run from the repository root.

    uv run python spec/absence-check-leftover-classification/verify/check.py static --base REV
    uv run python spec/absence-check-leftover-classification/verify/check.py outcomes JUNIT MODE
    uv run python spec/absence-check-leftover-classification/verify/check.py mutants

static    What the change must leave alone, read against the base revision: the verdict
          (`module_absent` and the body of `test_docs_modules_absent`, compared as parsed code),
          `absence_violations`' signature, every base test id, the paths outside the change, no
          skip in the test module and none in conftest outside its helpers, and AGENTS.md's entry.
outcomes  Every id in ids.txt has the outcome MODE requires in a JUnit report, and the report holds
          no test_registry id ids.txt lacks. MODE is `all-pass` (git present, CI=true), `no-git-local`
          (git ids skip naming git, plain ids pass) or `no-git-ci` (git ids fail or error naming git,
          plain ids pass).
mutants   Each way of breaking the explanation turns named tests red. One exact edit per mutant in a
          fresh snapshot (pyproject.toml, tests/, bin/sandbox.sh), run there with this checkout's
          interpreter, CI=true and a JUnit report. Killed only when every named id fails with a
          `<failure>`; an `<error>` is an import or syntax slip, not a kill, except for the one
          mutant the unit guard kills at teardown. The unmutated snapshot
          passes first. An `old` string that does not occur exactly once is red: the build moved
          the code, and this table moves with it.

Footprint: one pytest child at a time; under 250 MB; about 30 s for the mutants.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = 'tests/test_registry.py'
CONFTEST = 'tests/conftest.py'

# The test ids on the base revision; each must still be collected.
BASE_IDS = [
    'test_registry_matches_expected_tools', 'test_served_tools_match_registry',
    'test_registry_check_catches_drift[added-doc]', 'test_registry_check_catches_drift[missing]',
    'test_registry_check_catches_drift[duplicate]',
    'test_docs_modules_absent[rcac_mcp.docs]', 'test_docs_modules_absent[rcac_mcp.tools.docs]',
    'test_absence_check_catches_namespace_leftover',
    'test_absence_message_for_leftover_bytecode[empty-pycache]',
    'test_absence_message_for_leftover_bytecode[pycache-pyc]',
    'test_absence_message_for_leftover_bytecode[nested-pyc]',
    'test_absence_message_for_leftover_bytecode[bare-pyc]',
    'test_absence_message_for_readded_source[init]', 'test_absence_message_for_readded_source[module]',
    'test_absence_message_for_readded_source[data]',
    'test_absence_message_for_readded_source[nested-module]',
    'test_absence_message_for_readded_source[module-file]',
]

# Paths the change may touch; everything else matches the base.
ALLOWED = {MODULE, CONFTEST, 'AGENTS.md'}
ALLOWED_PREFIXES = ('spec/absence-check-leftover-classification/', 'issues/', 'ROADMAP.md')

# What AGENTS.md's test_registry entry says once the change lands, and what it no longer says.
# Matched as whole words in the entry with its line breaks folded, so a reflow changes nothing.
ENTRY_MUST = ['tracked', 'untracked', 'ignored', 'need_git', 'integration', 'suffix', 'sandbox.sh', '-m unit']
ENTRY_GONE = ['any other file means the module was re-added, and the message says so instead of offering a remedy']

SKIPPERS = {'skip', 'skipif', 'xfail', 'importorskip'}
SKIP_HELPERS = {'need_git', 'need_shell'}

G = 'test_absence_message_for_ignored_files'
U = 'test_absence_message_for_untracked_files'
T = 'test_absence_message_for_tracked_files'
M = 'test_absence_message_for_mixed_files'
F = 'test_absence_message_falls_back_without_git'
W = 'test_absence_message_falls_back_when_git_fails_midway'

# (name, file, old, new, ids that must fail[, the JUnit tag a kill shows when not `failure`])
MUTANTS = [
    ('always-suffix', MODULE, 'classes = git_classes(path)\n', 'classes = None\n',
     [f'{G}[pycache-pyc]', f'{U}[ds-store]', f'{T}[module]', f'{M}[all-three]']),
    ('nothing-ignored-is-no-answer', MODULE, 'checked.returncode not in (0, 1)', 'checked.returncode != 0',
     [f'{U}[empty-directory]']),
    ('removal-names-location', MODULE, '        cover.add(next(a for a in lineage if all_chosen(a)))',
     '        cover.add(path)', [f'{M}[all-three]', f'{M}[tracked-and-ignored]', f'{M}[nested]']),
    ('untracked-dropped', MODULE, "('untracked', ['--others', '--exclude-standard'])",
     "('untracked', ['--others', '--ignored', '--exclude-standard'])", [f'{U}[ds-store]', f'{M}[all-three]']),
    # The unit guard fails a test that tried to launch at teardown, which JUnit reports as an error.
    ('guard-not-read', MODULE, "getattr(subprocess.Popen, 'unit_guard', False) or ", '',
     ['test_absence_message_for_leftover_bytecode[pycache-pyc]', 'test_absence_message_for_readded_source[module]'],
     'error'),
    ('hook-environment-kept', MODULE, "if not k.startswith('GIT_') or k == 'GIT_CEILING_DIRECTORIES'", 'if True',
     ['test_absence_message_ignores_git_hook_environment[all]', 'test_absence_message_ignores_git_hook_environment[git-dir]']),
    ('raises-without-git', MODULE, '    if program is None:\n        return None\n', '',
     [f'{F}[mixed-no-git]', f'{F}[bytecode-no-git]']),
    ('git-dir-kept', MODULE, "k == 'GIT_CEILING_DIRECTORIES'", "k in ('GIT_CEILING_DIRECTORIES', 'GIT_DIR')",
     ['test_absence_message_ignores_git_hook_environment[git-dir]']),
    # A failed ls-files read as an answer is not listed: its empty listing leaves every file unclassified,
    # and the completeness check (`unlisted-file-accepted`) already sends that to the fallback.
    ('check-ignore-fatal-accepted', MODULE, 'checked.returncode not in (0, 1)', 'checked.returncode not in (0, 1, 128)',
     [f'{W}[empty-pycache-check-ignore]']),
    ('symlink-followed', MODULE, "if entry.is_symlink() or not entry.is_dir()", "if not entry.is_dir()",
     ['test_absence_message_for_odd_entries[tracked-symlink]']),
    ('unlisted-file-accepted', MODULE, "if set().union(*listings.values()) != relative.keys() or sum(map(len, listings.values())) != len(relative):",
     "if False:",
     [f'{W}[mixed-omits-untracked]', 'test_absence_message_for_odd_entries[nested-repository]']),
    ('need-git-skips-on-ci', CONFTEST, "    if os.environ.get('CI', '').lower() == 'true':\n        pytest.fail('git",
     "    if False:\n        pytest.fail('git", ['test_a_missing_git_fails_on_ci_and_skips_elsewhere[ci]']),
]


def fail(message: str) -> None:
    print(f'check: {message}', file=sys.stderr)
    raise SystemExit(1)


def ids() -> dict[str, str]:
    rows = [line.split() for line in (HERE / 'ids.txt').read_text().splitlines()
            if line.strip() and not line.startswith('#')]
    return {test_id: kind for test_id, kind in rows}


def show(rev: str, rel: str) -> str:
    return subprocess.run(['git', 'show', f'{rev}:{rel}'], capture_output=True, text=True, check=True).stdout


def function(source: str, name: str) -> ast.FunctionDef:
    found = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(found) != 1:
        fail(f'{name} is defined {len(found)} times')
    return found[0]


def static(base: str) -> None:
    now, then = Path(MODULE).read_text(), show(base, MODULE)
    for name in ('module_absent',):
        if ast.dump(function(now, name)) != ast.dump(function(then, name)):
            fail(f'{name} differs from {base}')
    a, b = function(now, 'test_docs_modules_absent'), function(then, 'test_docs_modules_absent')
    if [ast.dump(s) for s in a.body] != [ast.dump(s) for s in b.body] or ast.dump(a.args) != ast.dump(b.args):
        fail(f'the body of test_docs_modules_absent differs from {base}: the verdict must not move')
    marks = sorted(ast.unparse(d) for d in a.decorator_list)
    if not any(m == 'pytest.mark.integration' for m in marks) or any(m == 'pytest.mark.unit' for m in marks):
        fail(f'test_docs_modules_absent must carry only the integration marker: {marks}')
    if [ast.unparse(d) for d in a.decorator_list if 'parametrize' in ast.unparse(d)] != \
            [ast.unparse(d) for d in b.decorator_list if 'parametrize' in ast.unparse(d)]:
        fail('test_docs_modules_absent checks other modules than on the base')
    # The fallback is today's text: the two messages, as parsed code, are the base's two appends.
    then_texts = [ast.dump(n.args[0]) for n in ast.walk(function(then, 'absence_violations'))
                  if isinstance(n, ast.Call) and ast.unparse(n.func) == 'violations.append']
    now_texts = [ast.dump(n.value) for n in ast.walk(function(now, 'suffix_explanation'))
                 if isinstance(n, ast.Return)]
    if len(then_texts) != 2 or sorted(now_texts) != sorted(then_texts):
        fail(f'suffix_explanation does not return exactly the two messages of {base}')
    then_held = [ast.dump(n) for n in ast.walk(function(then, 'absence_violations'))
                 if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) in ('held', 'sources')]
    now_held = [ast.dump(n) for n in ast.walk(function(now, 'suffix_explanation'))
                if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) in ('held', 'sources')]
    if then_held != now_held:
        fail(f'suffix_explanation computes what it lists differently from {base}')
    sig = function(now, 'absence_violations')
    if ast.unparse(sig.args) != 'name: str' or ast.unparse(sig.returns) != 'list[str]':
        fail('absence_violations no longer takes (name: str) -> list[str]')
    missing = [i for i in BASE_IDS if i not in ids()]
    if missing:
        fail(f'base test ids gone from ids.txt: {missing}')
    changed = subprocess.run(['git', 'diff', '--name-only', base, '--'], capture_output=True, text=True,
                             check=True).stdout.split()
    untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], capture_output=True,
                               text=True, check=True).stdout.split()
    stray = [p for p in changed + untracked if p not in ALLOWED and not p.startswith(ALLOWED_PREFIXES)]
    if stray:
        fail(f'paths outside the change differ from {base}: {stray}')
    for rel, allowed in ((MODULE, set()), (CONFTEST, SKIP_HELPERS)):
        tree = ast.parse(Path(rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in allowed:
                node.body = []
        # A call to a skipper, or a skip or xfail mark anywhere; `pytest.skip.Exception` is neither.
        for node in ast.walk(tree):
            called = node.func if isinstance(node, ast.Call) else None
            leaf = (called.attr if isinstance(called, ast.Attribute)
                    else called.id if isinstance(called, ast.Name) else None)
            marked = (isinstance(node, ast.Attribute) and node.attr in SKIPPERS
                      and ast.unparse(node.value).endswith('mark'))
            if leaf in SKIPPERS or marked:
                fail(f'{rel}:{node.lineno}: {ast.unparse(node)[:60]} outside {sorted(allowed) or "any helper"}')
    text = Path('AGENTS.md').read_text()
    start = text.index('  - `tests/test_registry.py`')
    entry = text[start:text.index('\n  - `tests/', start + 1)]
    entry = ' '.join(entry.split())
    absent = [w for w in ENTRY_MUST if not re.search(rf'(?<![\w-]){re.escape(w)}(?![\w-])', entry)]
    if absent:
        fail(f'AGENTS.md § Testing entry for test_registry.py lacks {absent}')
    stale = [w for w in ENTRY_GONE if w in entry]
    if stale:
        fail(f'AGENTS.md § Testing entry still states the suffix rule as the explanation: {stale}')


def outcomes(report: Path, mode: str) -> None:
    root = ET.parse(report).getroot()
    seen: dict[str, tuple[str, str]] = {}
    for case in root.iter('testcase'):
        if not case.get('classname', '').endswith('test_registry'):
            continue
        result = next((c for c in case if c.tag in ('failure', 'error', 'skipped')), None)
        outcome = 'passed' if result is None else result.tag
        seen[case.get('name', '')] = (outcome, (result.get('message', '') if result is not None else ''))
    listed = ids()
    unlisted = sorted(set(seen) - set(listed))
    if unlisted:
        fail(f'collected but not in ids.txt: {unlisted}')
    for test_id, kind in listed.items():
        if test_id not in seen:
            fail(f'{test_id} was not collected')
        outcome, message = seen[test_id]
        # need_git runs in a fixture, so on CI its failure is reported as a setup error.
        want = {'passed'}
        if kind == 'git' and mode == 'no-git-local':
            want = {'skipped'}
        elif kind == 'git' and mode == 'no-git-ci':
            want = {'failure', 'error'}
        if outcome not in want or (want != {'passed'} and 'git is not on PATH' not in message):
            fail(f'{test_id}: {outcome} ({message[:120]!r}), expected {want} in mode {mode}')


def run_snapshot(where: Path, report: Path) -> None:
    env = {**os.environ, 'CI': 'true'}
    subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', f'--junitxml={report}',
                    MODULE], cwd=where, env=env, capture_output=True)


def junit_results(report: Path) -> dict[str, str]:
    root = ET.parse(report).getroot()
    out = {}
    for case in root.iter('testcase'):
        tags = [c.tag for c in case if c.tag in ('failure', 'error', 'skipped')]
        out[case.get('name', '')] = tags[0] if tags else 'passed'
    return out


def mutants() -> None:
    with tempfile.TemporaryDirectory(prefix='absence-mutants-') as scratch:
        snapshot = Path(scratch) / 'tree'
        snapshot.mkdir()
        shutil.copy('pyproject.toml', snapshot)
        shutil.copytree('tests', snapshot / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
        (snapshot / 'bin').mkdir()
        shutil.copy2('bin/sandbox.sh', snapshot / 'bin')
        report = Path(scratch) / 'clean.xml'
        run_snapshot(snapshot, report)
        clean = junit_results(report)
        bad = {k: v for k, v in clean.items() if v != 'passed'}
        if not clean or bad:
            fail(f'the unmutated snapshot is not green: {bad or "nothing ran"}')
        for name, rel, old, new, killers, *tag in MUTANTS:
            kill = tag[0] if tag else 'failure'
            target = snapshot / rel
            original = target.read_text()
            if original.count(old) != 1:
                fail(f'mutant {name}: its anchor occurs {original.count(old)} times in {rel}')
            unknown = [k for k in killers if k not in clean]
            if unknown:
                fail(f'mutant {name}: named ids not collected: {unknown}')
            target.write_text(original.replace(old, new))
            try:
                report = Path(scratch) / f'{name}.xml'
                run_snapshot(snapshot, report)
                got = junit_results(report) if report.exists() else {}
            finally:
                target.write_text(original)
            survived = [k for k in killers if got.get(k) != kill]
            if survived:
                fail(f'mutant {name} survived: {[(k, got.get(k)) for k in survived]}')
            print(f'mutant {name}: killed')


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == 'static':
        static(sys.argv[sys.argv.index('--base') + 1])
    elif len(sys.argv) == 4 and sys.argv[1] == 'outcomes':
        outcomes(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1:] == ['mutants']:
        mutants()
    else:
        fail(__doc__.splitlines()[2])


if __name__ == '__main__':
    main()
