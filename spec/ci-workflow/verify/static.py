"""Static checks for the ci-workflow cycle: the workflows, the shared hygiene definition, AGENTS.md.

Run from the worktree root with PyYAML on hand (it is not a dependency of this repository):

    uv run --with pyyaml python spec/ci-workflow/verify/static.py [--root DIR] SECTION...
    uv run --with pyyaml python spec/ci-workflow/verify/static.py --steps JOB [--root DIR]

SECTION is any of `hygiene` (the shared sdist definition and the recipe's use of it),
`workflow` (both workflow files), `agents` (the AGENTS.md documentation), and `controls`.
`controls` re-runs every other check on planted copies of the files and requires each plant
to be rejected for its stated reason, and the unplanted files to be accepted: a check that
cannot go red is not a check. `--steps JOB` prints the run steps of one job, NUL-separated,
for replay.sh.

Every check reads the files under --root (default: the current directory), so the final gate
can point it at a fresh clone of the commit instead of the working tree.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

CI = '.github/workflows/ci.yml'
MUTANTS = '.github/workflows/mutants.yml'
HYGIENE = 'bin/sdist_hygiene.py'
HYGIENE_CMD = 'uv run python bin/sdist_hygiene.py dist/'
FURNITURE = ('.security', 'spec', 'issues', 'tests/fixtures', 'bin', '.agents')

# The only actions the workflows may use, each at the commit its release tag names
# (research/01-facts.md records how each was read from GitHub).
PINS = {
    'actions/checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',  # v7.0.1
    'astral-sh/setup-uv': 'c18668ad3cf93ea998bef934396af7bb5c839dc7',  # v10.2.0
}
UV_VERSION = '0.12.17'

MUTANTS_BLOCK = '\n'.join([
    'ids=$(uv run python -c "import sys; sys.path.insert(0, \'tests\'); import mutants; print(*(c.id for c in mutants.CASES))")',
    'test -n "$ids" || { echo \'no mutation cases found in tests/mutants.py\' >&2; exit 1; }',
    'read -ra cases <<< "$ids"',
    'uv run python tests/mutants.py --self-check "${cases[@]}" | tee mutants-report.txt',
    "killed=$(grep -c '^killed ' mutants-report.txt)",
    'echo "killed ${killed} of ${#cases[@]} cases"',
    'test "$killed" -eq "${#cases[@]}"',
]) + '\n'

# Words that would mean the workflow writes outside the runner or reaches a credential.
# Neither workflow evaluates an expression, so `${{` is banned outright: it is how a secret, a token
# or attacker-controlled event text (a branch name, a PR title) reaches a step, including through
# a step `name:` that no linter reads.
BANNED = [
    r'\$\{\{', r'\bsecrets\b', r'secrets\s*[.\[]', r'\bgithub\.token\b', r'GITHUB_TOKEN', r'pull_request_target', r'workflow_run',
    r'continue-on-error', r'\|\|\s*true', r'id-token', r'attestation', r'\bgit\s+(push|tag)\b',
    r'\bgh\s+\w', r'twine\s+upload', r'uv\s+publish', r'\bdocker\b', r'\bpodman\b', r'\bcosign\b',
    r'upload-artifact', r'actions/cache', r'\bcurl\b', r'\bwget\b',
]

# Phrases that say the project has no CI, matched across line wraps.
STALE_NO_CI = [
    r'\bno\s+CI\b', r'fires\s+no\s+CI', r'Until\s+ci-workflow\s+lands', r'none\s+yet', r'no-ci-no-index',
    r'Wiring\s+CI\s+is\s+a\s+tracked\s+seed', r'\bwithout\s+CI\b', r'\bCI\s+does\s+not\s+exist',
]


def flat(text: str) -> str:
    return ' '.join(text.split())


def read(files: dict[str, str], path: str) -> str | None:
    return files.get(path)


def load(root: Path) -> dict[str, str]:
    files = {}
    for path in (CI, MUTANTS, HYGIENE, 'AGENTS.md', 'pyproject.toml'):
        p = root / path
        if p.is_file():
            files[path] = p.read_text()
    wf = root / '.github/workflows'
    files['.github/workflows/*'] = '\n'.join(sorted(p.name for p in wf.iterdir())) if wf.is_dir() else ''
    return files


def code_lines(text: str) -> str:
    """The text without whole-line comments, so a header that says `no secrets` stays legal."""
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


def triggers(doc: dict) -> object:
    # YAML 1.1 reads a bare `on` key as the boolean true.
    return doc.get('on', doc.get(True))


def profile_cell(agents: str, key: str) -> str | None:
    match = re.search(rf'^\| {key} \| `([^`]+)`', agents, re.M)
    return match.group(1) if match else None


def recipe(agents: str) -> dict | None:
    match = re.search(r'^### Release recipe\n.*?^```yaml\n(.*?)^```', agents, re.M | re.S)
    return yaml.safe_load(match.group(1))['release'] if match else None


def expected_gate_steps(agents: str) -> list[str] | None:
    setup, verify, rel = profile_cell(agents, 'setup'), profile_cell(agents, 'verify'), recipe(agents)
    if not (setup and verify and rel):
        return None
    steps: list[str] = []
    for step in [setup, *[s.strip() for s in verify.split('&&')], *rel['gate']]:
        if step not in steps:
            steps.append(step)
    return steps


def hygiene_violations(files: dict[str, str]) -> list[str]:
    found = []
    source = read(files, HYGIENE)
    if source is None:
        return [f'{HYGIENE} is missing']
    match = re.search(r'^FURNITURE[^=]*=\s*\(([^)]*)\)', source, re.M)
    if not match or tuple(re.findall(r"'([^']+)'", match.group(1))) != FURNITURE:
        found.append(f'{HYGIENE} FURNITURE is not {FURNITURE}')
    agents = read(files, 'AGENTS.md') or ''
    rel = recipe(agents)
    gate = rel['gate'] if rel else []
    if [s for s in gate if 'sdist_hygiene' in s] != [HYGIENE_CMD]:
        found.append(f'the release recipe gate does not run `{HYGIENE_CMD}` exactly once')
    # A second copy would be code: a recipe gate step or a workflow run step. Prose may say tarfile.
    if any('tarfile' in step for step in gate):
        found.append('AGENTS.md: a recipe gate step carries a second copy of the hygiene check (tarfile)')
    for path, job in ((CI, 'gate'), (MUTANTS, 'mutants')):
        try:
            runs = job_steps(files, job) if path in files else []
        except (KeyError, TypeError, yaml.YAMLError):
            runs = []
        if any('tarfile' in step for step in runs):
            found.append(f'{path} carries a second copy of the hygiene check (tarfile)')
    return found


def step_violations(job: str, steps: list, want_runs: list[str]) -> list[str]:
    found = []
    uses_steps = [s for s in steps if isinstance(s, dict) and 'uses' in s]
    run_steps = [s for s in steps if isinstance(s, dict) and 'run' in s]
    if len(uses_steps) + len(run_steps) != len(steps):
        found.append(f'{job}: a step is neither uses nor run')
    for s in uses_steps:
        if set(s) - {'uses', 'with'}:
            found.append(f'{job}: step {s["uses"]} carries keys {sorted(set(s) - {"uses", "with"})}')
    for s in run_steps:
        if set(s) - {'run'}:
            found.append(f'{job}: step `{s["run"]}` carries keys {sorted(set(s) - {"run"})}')
    names = [s.get('uses', '').split('@')[0] for s in steps[:2]]
    if names != ['actions/checkout', 'astral-sh/setup-uv']:
        found.append(f'{job}: the first two steps are not checkout then setup-uv ({names})')
    else:
        if steps[0].get('with') != {'persist-credentials': False}:
            found.append(f'{job}: checkout must set exactly persist-credentials: false')
        if steps[1].get('with') != {'version': UV_VERSION, 'python-version': '3.14', 'enable-cache': False}:
            found.append(f'{job}: setup-uv must set exactly version {UV_VERSION}, python-version 3.14, enable-cache false')
    if [s['run'] for s in steps[2:] if 'run' in s] != want_runs or len(steps) != len(want_runs) + 2:
        found.append(f'{job}: run steps {[s.get("run") for s in steps[2:]]} are not {want_runs}')
    return found


def workflow_violations(files: dict[str, str]) -> list[str]:
    found = []
    if files.get('.github/workflows/*') != 'ci.yml\nmutants.yml':
        found.append('.github/workflows/ must hold exactly ci.yml and mutants.yml')
    pyproject = read(files, 'pyproject.toml') or ''
    floor = re.search(r'^requires-python = ">=(\d+\.\d+)"', pyproject, re.M)
    if not floor or floor.group(1) != '3.14':
        found.append('requires-python floor is not 3.14; move python-version with it')
    agents = read(files, 'AGENTS.md') or ''
    want = {CI: expected_gate_steps(agents), MUTANTS: ['uv sync --locked', MUTANTS_BLOCK]}
    if want[CI] is None:
        found.append('AGENTS.md: cannot read the profile setup/verify or the recipe gate')
        want[CI] = []
    for path, job in ((CI, 'gate'), (MUTANTS, 'mutants')):
        text = read(files, path)
        if text is None:
            found.append(f'{path} is missing')
            continue
        doc = yaml.safe_load(text)
        code = code_lines(text) + '\n' + yaml.safe_dump(doc)
        for pattern in BANNED:
            if re.search(pattern, code):
                found.append(f'{path}: banned pattern {pattern}')
        raw_uses = re.findall(r'^\s*(?:-\s*)?uses:\s*(\S+)', text, re.M)
        parsed_uses = [s['uses'] for j in (doc.get('jobs') or {}).values() for s in j.get('steps', []) if 'uses' in s]
        parsed_uses += [j['uses'] for j in (doc.get('jobs') or {}).values() if 'uses' in j]
        if sorted(raw_uses) != sorted(parsed_uses):
            found.append(f'{path}: uses lines {raw_uses} differ from parsed steps {parsed_uses}')
        for ref in raw_uses + parsed_uses:
            action, _, sha = ref.partition('@')
            if not re.fullmatch(r'[0-9a-f]{40}', sha):
                found.append(f'{path}: {ref} is not pinned to a full commit SHA')
            elif PINS.get(action) != sha:
                found.append(f'{path}: {ref} is not an allowed action at its recorded commit')
        if set(doc) != {'name', True, 'permissions', 'defaults', 'jobs'} and set(doc) != {'name', 'on', 'permissions', 'defaults', 'jobs'}:
            found.append(f'{path}: top-level keys {sorted(map(str, doc))} are not name/on/permissions/defaults/jobs')
        if doc.get('permissions') != {'contents': 'read'}:
            found.append(f'{path}: permissions are not exactly contents: read')
        if doc.get('defaults') != {'run': {'shell': 'bash'}}:
            found.append(f'{path}: defaults are not run.shell: bash')
        on = triggers(doc)
        want_on = ({'push': {'branches': ['**']}, 'pull_request': {'branches': ['develop', 'main']}}
                   if path == CI else {'workflow_dispatch': None})
        if on != want_on:
            found.append(f'{path}: triggers {on} are not {want_on}')
        jobs = doc.get('jobs') or {}
        if set(jobs) != {job}:
            found.append(f'{path}: jobs {sorted(jobs)} are not [{job}]')
            continue
        spec = jobs[job]
        if set(spec) - {'name', 'runs-on', 'timeout-minutes', 'steps'}:
            found.append(f'{path}: job {job} carries keys {sorted(set(spec) - {"name", "runs-on", "timeout-minutes", "steps"})}')
        if spec.get('name') != job or spec.get('runs-on') != 'ubuntu-24.04':
            found.append(f'{path}: job {job} must be named {job} and run on ubuntu-24.04')
        found += [f'{path}: {v}' for v in step_violations(job, spec.get('steps') or [], want[path])]
    return found


def agents_violations(files: dict[str, str]) -> list[str]:
    found = []
    agents = read(files, 'AGENTS.md') or ''
    for pattern in STALE_NO_CI:
        for match in re.finditer(pattern, agents):
            line = agents.count('\n', 0, match.start()) + 1
            found.append(f'AGENTS.md:{line}: stale no-CI claim ({match.group(0)})')
    ci_row = re.search(r'^\| ci \| (.*)\|$', agents, re.M)
    row = ci_row.group(1) if ci_row else ''
    for needle in ('.github/workflows/ci.yml', '`gate`', 'squash-local', 'head commit', 'red', 'missing',
                   'unfinished', 'gh run list --workflow ci.yml --commit', '-e push', 'latest attempt'):
        if needle not in row:
            found.append(f'AGENTS.md profile ci row does not say {needle!r}')
    packaging = re.search(r'^## Packaging\n(.*?)^## ', agents, re.M | re.S)
    if not packaging or '.github/workflows/ci.yml' not in packaging.group(1) or 'pull request' not in flat(packaging.group(1)):
        found.append('AGENTS.md § Packaging does not say the workflow runs the gate on push and pull request')
    rel = recipe(agents) or {}
    caveats = rel.get('caveats') or {}
    ci_caveat = flat(' '.join(v for k, v in caveats.items() if k.startswith('ci')))
    if 'mirrors' not in ci_caveat or 'before CI has run' not in ci_caveat:
        found.append('release caveats lack the CI-mirrors caveat (mirrors; before CI has run on that commit)')
    conventions = re.search(r'^### Conventions.*?\n(.*)', agents, re.M | re.S)
    text = flat(conventions.group(1)) if conventions else ''
    for needle in ('.github/workflows/ci.yml', 'same commit', 'python-version', 'commit SHA', HYGIENE):
        if needle not in text:
            found.append(f'AGENTS.md § Conventions does not bind the workflow ({needle!r})')
    return found


def job_steps(files: dict[str, str], job: str) -> list[str]:
    """The run steps of one job, in order, read from the workflow file under the root."""
    path = {'gate': CI, 'mutants': MUTANTS}[job]
    steps = yaml.safe_load(files[path])['jobs'][job]['steps']
    return [s['run'] for s in steps if 'run' in s]


SECTIONS = {'hygiene': hygiene_violations, 'workflow': workflow_violations, 'agents': agents_violations}

# Plants: (section, file, old, new, fragment the rejection must carry). `old` must occur exactly
# once in the real file, so a plant that stops applying is reported instead of passing silently.
PLANTS = [
    ('workflow', CI, '@3d3c42e5aac5ba805825da76410c181273ba90b1', '@v7', 'not pinned to a full commit SHA'),
    ('workflow', CI, '@c18668ad3cf93ea998bef934396af7bb5c839dc7', '@1c37ad07a6a961277cf70c0d37d6f313000f5885', 'recorded commit'),
    ('workflow', MUTANTS, '@3d3c42e5aac5ba805825da76410c181273ba90b1', '@main', 'not pinned to a full commit SHA'),
    ('workflow', CI, 'permissions:\n  contents: read', 'permissions:\n  contents: write', 'permissions are not exactly'),
    ('workflow', CI, '    timeout-minutes: 20\n', '    timeout-minutes: 20\n    permissions:\n      contents: write\n', 'carries keys'),
    ('workflow', CI, '  pull_request:\n', '  pull_request_target:\n', 'pull_request_target'),
    ('workflow', CI, "    branches: ['**']\n", "    branches: ['**']\n    paths: ['src/**']\n", 'triggers'),
    ('workflow', CI, "    branches: ['**']\n", '    branches: [develop]\n', 'triggers'),
    ('workflow', MUTANTS, 'on:\n  workflow_dispatch:\n', 'on:\n  workflow_dispatch:\n  push:\n', 'triggers'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '', 'run steps'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '      - run: uv run pytest -q || true\n', 'banned pattern'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '      - run: uv run pytest -q\n        continue-on-error: true\n', 'banned pattern'),
    ('workflow', CI, '      - run: uv run pytest -q\n', "      - run: uv run pytest -q\n        if: ${{ false }}\n", 'carries keys'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '      - run: uv run pytest -q\n        env:\n          PYTEST_ADDOPTS: --collect-only\n', 'carries keys'),
    ('workflow', CI, f'      - run: {HYGIENE_CMD}\n', f'      - run: {HYGIENE_CMD}\n      - run: echo "${{{{ secrets.PYPI }}}}"\n', 'banned pattern'),
    ('workflow', CI, f'      - run: {HYGIENE_CMD}\n', f'      - run: {HYGIENE_CMD}\n      - run: git push origin HEAD:main\n', 'banned pattern'),
    ('workflow', CI, '          enable-cache: false', '          enable-cache: true', 'setup-uv must set exactly'),
    ('workflow', CI, "          python-version: '3.14'", '          python-version: 3.14', 'setup-uv must set exactly'),
    ('workflow', CI, '          persist-credentials: false', '          persist-credentials: true', 'persist-credentials'),
    ('workflow', MUTANTS, "          test -n \"$ids\" || { echo 'no mutation cases found in tests/mutants.py' >&2; exit 1; }\n", '', 'run steps'),
    ('workflow', 'AGENTS.md', '    - uv build -o dist/\n', '    - uv build -o dist/\n    - uv run pytest -q -m unit\n', 'run steps'),
    ('hygiene', 'AGENTS.md', f'    - {HYGIENE_CMD}\n', '', 'exactly once'),
    ('hygiene', HYGIENE, "'tests/fixtures', ", '', 'FURNITURE'),
    ('hygiene', CI, f'      - run: {HYGIENE_CMD}\n', f'      - run: {HYGIENE_CMD}\n      - run: python3 -c "import tarfile"\n', 'second copy'),
    ('agents', 'AGENTS.md', '## Packaging\n', '## Packaging\n\n- There is no CI.\n', 'stale no-CI claim'),
    ('agents', 'AGENTS.md', 'unfinished', 'pending', "'unfinished'"),
    ('agents', 'AGENTS.md', ' -e push ', ' ', "'-e push'"),
    ('workflow', CI, '      - run: uv run pytest -q\n', "      - name: ${{ format('{0}', secrets) }}\n        run: uv run pytest -q\n", 'banned pattern'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '      - run: uv run pytest -q -k "${{ toJSON(secrets) }}"\n', 'banned pattern'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '      - run: uv run pytest -q -k "${{ github.head_ref }}"\n', 'banned pattern'),
    ('workflow', CI, '      - run: uv run pytest -q\n', '      - name: tests\n        run: uv run pytest -q\n', 'carries keys'),
]


def controls(files: dict[str, str]) -> list[str]:
    found = []
    for name, check in SECTIONS.items():
        if (clean := check(files)):
            found.append(f'controls: the unplanted files fail {name}: {clean}')
    for section, path, old, new, fragment in PLANTS:
        text = files.get(path, '')
        if text.count(old) != 1:
            found.append(f'controls: plant on {path} no longer applies ({old.strip()!r} occurs {text.count(old)} times)')
            continue
        planted = dict(files, **{path: text.replace(old, new)})
        try:
            result = SECTIONS[section](planted)
        except Exception as error:  # A plant that crashes the check is not a rejection.
            result = [f'crashed: {error!r}']
        if not any(fragment in line for line in result):
            found.append(f'controls: {section} accepted {path} planted {new.strip()!r} (wanted {fragment!r}; got {result})')
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--steps', metavar='JOB')
    parser.add_argument('sections', nargs='*', choices=[*SECTIONS, 'controls'])
    args = parser.parse_args()
    files = load(args.root)
    if args.steps:
        sys.stdout.write(''.join(step + '\0' for step in job_steps(files, args.steps)))
        return 0
    if not args.sections:
        parser.error('name at least one section')
    found = []
    for section in args.sections:
        found += controls(files) if section == 'controls' else SECTIONS[section](files)
    for line in found:
        print(f'static: {line}', file=sys.stderr)
    print(f'static: {", ".join(args.sections)}: {"FAILED" if found else "ok"} ({len(PLANTS)} plants)' if 'controls' in args.sections
          else f'static: {", ".join(args.sections)}: {"FAILED" if found else "ok"}')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
