"""Check spec/ci-workflow/EVIDENCE.md against what the gates actually run.

    uv run --with pyyaml python spec/ci-workflow/verify/evidence.py MODE...

The record is prose and pasted output; this script is what makes it checkable. Every expectation
is read from the gate's own sources (gate.sh, the snapshot's ci.yml, hygiene_mutants.PLANTS), never
restated here, so a record that drifts from the gate goes red. The gate also re-runs every check
the record describes; the record is the dated trace of one run, not a substitute for it.

Modes:
  local   `## Lint`: each linter named at the version gate.sh pins, each lint command exactly as
          gate.sh runs it on both workflow files, with zero-finding output (actionlint prints
          nothing, zizmor prints its no-findings line) and `exit 0`.
          `## Local reds`: for each red replay gate.sh's P3 runs, its verdict line (the plant,
          the step it expects, the text it expects), a FAILED line at that step's index in ci.yml,
          an `ok` line for every earlier step, and a log line naming the text; then one `killed`
          line per hygiene_mutants plant, with its test ids, and no other killed or FAILED line.
          Each section carries a `Recorded: YYYY-MM-DD` line no later than today.
  green   `## Green run` (`- run:`, `- commit:`): GitHub's record of that run is completed/success,
          event push, branch ci/ci-workflow, workflow file ci.yml, at the recorded commit, in
          attempt 1 (or each later attempt listed as `- re-run N: <reason>`), with a job `gate`
          whose `Run <cmd>` step succeeded for every run step of ci.yml's gate job. The recorded
          commit is an ancestor of HEAD, and HEAD differs from it only under spec/ci-workflow/.
  red     `## Red run` (`- run:`, `- plant:`, `- failing step:`, `- log:`, `- removed:`): GitHub's
          record of that run is completed/failure, event push, branch ci/ci-workflow-red, workflow
          file ci.yml, at the plant, in attempt 1 (or each later attempt listed). In its job `gate`
          every step before the first failed one succeeded, every run step of ci.yml before the
          hygiene step among them, and the first failed step is the hygiene step, the one gate.sh's
          agents-furniture replay expects red. Its failed log names that replay's planted path,
          and the recorded log line is in it. The plant adds exactly that one file and has exactly
          one parent, the green run's commit. The throwaway branch answers 404, and the plant is
          in neither develop nor main.
  ledger  Every issues/<name>.md that PLAN.md or TECH.md names exists and is a ROADMAP.md seed.
  landing The profile ci row's read for HEAD: at least one push run of ci.yml, every one, in its
          latest attempt, completed/success.
"""

from __future__ import annotations

import datetime
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hygiene_mutants  # noqa: E402
import static  # noqa: E402

HERE = Path(__file__).parent
RECORD = HERE.parent / 'EVIDENCE.md'
GATE = HERE / 'gate.sh'
WORKFLOWS = f'{static.CI} {static.MUTANTS}'
GH_REPO = 'purduercac/rcac-mcp'
BRANCH = 'ci/ci-workflow'
RED_BRANCH = 'ci/ci-workflow-red'
RED_PLANT = 'agents-furniture'
SPEC = 'spec/ci-workflow/'


def sections(text: str) -> dict[str, list[str]]:
    """Map each `## ` heading to its lines; a heading that occurs twice maps to None."""
    found: dict[str, list[str] | None] = {}
    current = None
    for line in text.splitlines():
        if line.startswith('## '):
            current = line[3:].strip()
            found[current] = None if current in found else []
        elif current is not None and found[current] is not None:
            found[current].append(line)
    return found


def recorded(name: str, lines: list[str], bad: list[str]) -> None:
    dates = [m.group(1) for line in lines if (m := re.fullmatch(r'Recorded: (\d{4}-\d{2}-\d{2})', line.strip()))]
    if len(dates) != 1:
        bad.append(f'{name}: needs exactly one "Recorded: YYYY-MM-DD" line, found {len(dates)}')
        return
    try:
        day = datetime.date.fromisoformat(dates[0])
    except ValueError:
        bad.append(f'{name}: {dates[0]} is not a date')
        return
    if day > datetime.date.today():
        bad.append(f'{name}: recorded on {day}, which is in the future')


def lint_commands(gate: str) -> list[tuple[str, str]]:
    """(tool, the command gate.sh runs, with the workflow files substituted for "$@")."""
    body = re.search(r'^lint\(\) \{\n(.*?)^\}', gate, re.M | re.S)
    if not body:
        sys.exit('evidence: gate.sh has no lint() function')
    found = []
    for line in body.group(1).splitlines():
        line = line.strip()
        if line.startswith('#') or 'uvx' not in line:
            continue
        command = line.split(' || ')[0].removeprefix('GH_TOKEN=$token ').replace('"$@"', WORKFLOWS)
        tool = 'zizmor' if 'zizmor' in command else 'actionlint' if 'actionlint' in command else None
        if tool is None:
            sys.exit(f'evidence: unrecognised lint command in gate.sh: {line}')
        found.append((tool, command))
    if sorted(t for t, _ in found) != ['actionlint', 'zizmor']:
        sys.exit(f'evidence: gate.sh lint() should run actionlint and zizmor once each: {found}')
    return found


def pinned_versions(gate: str) -> dict[str, str]:
    pins = {'actionlint': r'actionlint-py==(\d+\.\d+\.\d+)\.\d+', 'shellcheck': r'shellcheck-py==(\d+\.\d+\.\d+)\.\d+',
            'zizmor': r'zizmor@(\d+\.\d+\.\d+)'}
    versions = {}
    for tool, pattern in pins.items():
        matches = set(re.findall(pattern, gate))
        if len(matches) != 1:
            sys.exit(f'evidence: gate.sh pins {tool} {len(matches)} ways: {sorted(matches)}')
        versions[tool] = matches.pop()
    return versions


def output_after(lines: list[str], command: str) -> list[str] | None:
    """The lines a `$ ` transcript line ending in COMMAND printed, up to the next `$ ` line or fence."""
    for index, line in enumerate(lines):
        if line.startswith('$ ') and line.endswith(command):
            out = []
            for following in lines[index + 1:]:
                if following.startswith('$ ') or following.startswith('```'):
                    break
                out.append(following)
            return out
    return None


def check_lint(lines: list[str], gate: str, bad: list[str]) -> None:
    text = '\n'.join(lines)
    for tool, version in pinned_versions(gate).items():
        if f'{tool} {version}' not in text:
            bad.append(f'Lint: does not name {tool} {version}, the version gate.sh pins')
    for tool, command in lint_commands(gate):
        out = output_after(lines, command)
        if out is None:
            bad.append(f'Lint: no transcript of `{command}`')
        elif tool == 'actionlint' and out != ['exit 0']:
            bad.append(f'Lint: actionlint should print nothing and exit 0, the record shows {out}')
        elif tool == 'zizmor' and (out[-1:] != ['exit 0'] or not any(o.startswith('No findings to report.') for o in out)):
            bad.append(f'Lint: zizmor should report no findings and exit 0, the record shows {out}')


def red_replays(gate: str) -> list[tuple[str, str, str]]:
    """(plant, expected step, expected text) for each red gate-job replay that gate.sh's p3 runs."""
    body = re.search(r'^p3\(\) \{\n(.*?)^\}', gate, re.M | re.S)
    if not body:
        sys.exit('evidence: gate.sh has no p3() function')
    found = []
    for line in body.group(1).splitlines():
        if 'replay.py' not in line:
            continue
        words = shlex.split(line)
        opts = {w: words[i + 1] for i, w in enumerate(words[:-1]) if w.startswith('--')}
        if '--job' in opts or not {'--plant', '--expect-red', '--expect-log'} <= set(opts):
            sys.exit(f'evidence: unexpected replay in gate.sh p3: {line.strip()}')
        found.append((opts['--plant'], opts['--expect-red'], opts['--expect-log']))
    if not found:
        sys.exit('evidence: gate.sh p3 runs no red replay')
    return found


def check_reds(lines: list[str], gate: str, bad: list[str]) -> None:
    steps = static.job_steps(static.load(Path.cwd()), 'gate')
    head = r'replay\[gate @ [0-9a-f]{10} from (?:worktree|HEAD) \+ %s\]'
    for plant, step, text in red_replays(gate):
        tag = head % re.escape(plant)
        index = next((i for i, s in enumerate(steps) if step in s), None)
        if index is None:
            bad.append(f'Local reds: no ci.yml gate step contains {step!r}')
            continue
        first = steps[index].splitlines()[0]
        verdict = re.compile(f'{tag}: red as expected at {re.escape(repr(first))}, naming {re.escape(repr(text))}')
        if sum(bool(verdict.fullmatch(line)) for line in lines) != 1:
            bad.append(f'Local reds: {plant}: needs one verdict line, red at {first!r} naming {text!r}')
        failed = re.compile(f'{tag} step {index + 1}/{len(steps)} FAILED \\(exit [1-9]\\d*\\): {re.escape(first)}')
        if sum(bool(failed.fullmatch(line)) for line in lines) != 1:
            bad.append(f'Local reds: {plant}: needs one FAILED line for step {index + 1}/{len(steps)}')
        for number, earlier in enumerate(steps[:index], 1):
            ok = re.compile(f'{tag} step {number}/{len(steps)} ok: {re.escape(earlier.splitlines()[0])}')
            if not any(ok.fullmatch(line) for line in lines):
                bad.append(f'Local reds: {plant}: step {number}/{len(steps)} is not shown ok before the red')
        if not any(text in line and not line.startswith('replay[') for line in lines):
            bad.append(f'Local reds: {plant}: no log line names {text!r}')
    plants = re.findall(r'\+ ([\w-]+)\]', '\n'.join(line for line in lines if line.startswith('replay[')))
    stray = sorted(set(plants) - {p for p, *_ in red_replays(gate)})
    if stray:
        bad.append(f'Local reds: replay lines for plants gate.sh does not run: {stray}')


def check_mutants(lines: list[str], bad: list[str]) -> None:
    killed = [line for line in lines if line.startswith('hygiene-mutants: killed ')]
    want = [f'hygiene-mutants: killed {name} ({", ".join(tests)})' for name, _, _, tests in hygiene_mutants.PLANTS]
    if sorted(killed) != sorted(want):
        bad.append(f'Local reds: the killed lines differ from hygiene_mutants.PLANTS; '
                   f'missing {sorted(set(want) - set(killed))}, extra {sorted(set(killed) - set(want))}, '
                   f'{len(killed)} recorded for {len(want)} plants')
    if any(line.startswith('hygiene-mutants: FAILED') for line in lines):
        bad.append('Local reds: the record shows a surviving hygiene mutant')


def local(record: dict[str, list[str] | None], gate: str, bad: list[str]) -> None:
    for name in ('Lint', 'Local reds'):
        if name not in record:
            bad.append(f'no "## {name}" section')
        elif record[name] is None:
            bad.append(f'"## {name}" occurs more than once')
    if bad:
        return
    recorded('Lint', record['Lint'], bad)
    recorded('Local reds', record['Local reds'], bad)
    check_lint(record['Lint'], gate, bad)
    check_reds(record['Local reds'], gate, bad)
    check_mutants(record['Local reds'], bad)


def run(*cmd: str) -> str:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f'evidence: {" ".join(cmd)} failed (exit {done.returncode}): {done.stderr.strip()}')
    return done.stdout


def gh_json(*args: str):
    return json.loads(run('gh', *args))


def fields(lines: list[str]) -> dict[str, list[str]]:
    """Each `- key: value` line of a section, by key (a key may repeat)."""
    found: dict[str, list[str]] = {}
    for line in lines:
        if m := re.fullmatch(r'- ([\w -]+?): (.+)', line.strip()):
            found.setdefault(m.group(1), []).append(m.group(2).strip().strip('`'))
    return found


def one(name: str, record: dict[str, list[str]], key: str, pattern: str, bad: list[str]) -> str | None:
    values = record.get(key, [])
    if len(values) != 1 or not re.fullmatch(pattern, values[0]):
        bad.append(f'{name}: needs exactly one "- {key}:" line matching {pattern}, found {values}')
        return None
    return values[0]


def attempts(name: str, view: dict, kv: dict[str, list[str]], bad: list[str]) -> None:
    attempt = view.get('attempt')
    reruns = {int(m.group(1)) for key in kv if (m := re.fullmatch(r're-run (\d+)', key))}
    if not isinstance(attempt, int) or attempt < 1 or reruns != set(range(2, attempt + 1)):
        bad.append(f'{name}: attempt {attempt!r}, re-runs listed {sorted(reruns)}; each attempt after 1 needs a "- re-run N:" line')


def run_record(name: str, run_id: str, want: dict[str, str], bad: list[str]) -> dict:
    """GitHub's record of one run, checked against WANT and for the ci.yml workflow file."""
    view = gh_json('run', 'view', run_id, '-R', GH_REPO, '--json',
                   'attempt,status,conclusion,event,headBranch,headSha,jobs')
    # `gh run view` has no workflow-path field; the REST record of the same run carries it.
    path = gh_json('api', f'repos/{GH_REPO}/actions/runs/{run_id}')['path']
    for key, value in want.items():
        if view.get(key) != value:
            bad.append(f'{name}: run {run_id} has {key} {view.get(key)!r}, want {value!r}')
    if path != static.CI:
        bad.append(f'{name}: run {run_id} ran {path!r}, want {static.CI!r}')
    return view


def green(record: dict[str, list[str] | None], gate: str, bad: list[str]) -> None:
    lines = record.get('Green run', [])
    if lines is None or 'Green run' not in record:
        bad.append('needs exactly one "## Green run" section')
        return
    recorded('Green run', lines, bad)
    kv = fields(lines)
    run_id = one('Green run', kv, 'run', r'\d+', bad)
    commit = one('Green run', kv, 'commit', r'[0-9a-f]{40}', bad)
    if run_id is None or commit is None:
        return
    view = run_record('Green run', run_id, {'status': 'completed', 'conclusion': 'success', 'event': 'push',
                                            'headBranch': BRANCH, 'headSha': commit}, bad)
    attempts('Green run', view, kv, bad)
    jobs = [j for j in view.get('jobs', []) if j.get('name') == 'gate']
    if len(jobs) != 1 or jobs[0].get('conclusion') != 'success':
        bad.append(f'Green run: needs one job "gate" with conclusion success, found {[(j.get("name"), j.get("conclusion")) for j in jobs]}')
        return
    steps = {s['name']: s.get('conclusion') for s in jobs[0].get('steps', [])}
    expected = [f'Run {cmd.splitlines()[0]}' for cmd in static.job_steps(static.load(Path.cwd()), 'gate')]
    if not expected:
        bad.append('Green run: ci.yml has no gate run steps to look for')
    for name in expected:
        if steps.get(name) != 'success':
            bad.append(f'Green run: step {name!r} is {steps.get(name)!r}, want success')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD']).returncode != 0:
        bad.append(f'Green run: {commit} is not an ancestor of HEAD')
        return
    moved = [p for p in run('git', 'diff', '--name-only', commit, 'HEAD').splitlines() if not p.startswith(SPEC)]
    if moved:
        bad.append(f'Green run: HEAD differs from {commit[:10]} outside {SPEC}: {moved}')


def red(record: dict[str, list[str] | None], gate: str, bad: list[str]) -> None:
    lines = record.get('Red run', [])
    if lines is None or 'Red run' not in record:
        bad.append('needs exactly one "## Red run" section')
        return
    green_lines = record.get('Green run') or []
    recorded('Red run', lines, bad)
    kv = fields(lines)
    run_id = one('Red run', kv, 'run', r'\d+', bad)
    plant = one('Red run', kv, 'plant', r'[0-9a-f]{40}', bad)
    failing = one('Red run', kv, 'failing step', r'Run .+', bad)
    logged = one('Red run', kv, 'log', r'.+', bad)
    one('Red run', kv, 'removed', r'\d{4}-\d{2}-\d{2}\b.*', bad)
    parent = one('Red run (from Green run)', fields(green_lines), 'commit', r'[0-9a-f]{40}', bad)
    if None in (run_id, plant, failing, logged, parent):
        return
    _, step, text = next((r for r in red_replays(gate) if r[0] == RED_PLANT), (None, None, None))
    steps = static.job_steps(static.load(Path.cwd()), 'gate')
    index = next((i for i, s in enumerate(steps) if step and step in s), None)
    if index is None:
        bad.append(f'Red run: gate.sh runs no {RED_PLANT} replay whose step is in ci.yml\'s gate job')
        return
    hygiene = f'Run {steps[index].splitlines()[0]}'
    if failing != hygiene:
        bad.append(f'Red run: records failing step {failing!r}, the plant targets {hygiene!r}')
    view = run_record('Red run', run_id, {'status': 'completed', 'conclusion': 'failure', 'event': 'push',
                                          'headBranch': RED_BRANCH, 'headSha': plant}, bad)
    attempts('Red run', view, kv, bad)
    jobs = [j for j in view.get('jobs', []) if j.get('name') == 'gate']
    if len(jobs) != 1 or jobs[0].get('conclusion') != 'failure':
        bad.append(f'Red run: needs one job "gate" with conclusion failure, found {[(j.get("name"), j.get("conclusion")) for j in jobs]}')
        return
    ordered = sorted(jobs[0].get('steps', []), key=lambda s: s['number'])
    first = next((i for i, s in enumerate(ordered) if s.get('conclusion') == 'failure'), None)
    if first is None:
        bad.append('Red run: no step of job gate failed')
        return
    if ordered[first]['name'] != hygiene:
        bad.append(f'Red run: the first failed step is {ordered[first]["name"]!r}, want {hygiene!r}')
    before = {s['name']: s.get('conclusion') for s in ordered[:first]}
    for s in ordered[:first]:
        if s.get('conclusion') != 'success':
            bad.append(f'Red run: step {s["name"]!r}, before the failure, is {s.get("conclusion")!r}')
    for cmd in steps[:index]:
        name = f'Run {cmd.splitlines()[0]}'
        if before.get(name) != 'success':
            bad.append(f'Red run: step {name!r} is not shown success before the hygiene step')
    log = run('gh', 'run', 'view', run_id, '-R', GH_REPO, '--log-failed').splitlines()
    under = [line for line in log if line.split('\t')[1:2] == [hygiene]]
    if not any(text in line for line in under):
        bad.append(f'Red run: the failed log names no {text!r} under {hygiene!r}')
    if not any(logged in line for line in under):
        bad.append(f'Red run: the recorded log line {logged!r} is not in the failed log under {hygiene!r}')
    commit = gh_json('api', f'repos/{GH_REPO}/commits/{plant}')
    files = [(f.get('filename'), f.get('status')) for f in commit.get('files', [])]
    if files != [(text, 'added')]:
        bad.append(f'Red run: the plant changes {files}, want only {text!r} added')
    parents = [p.get('sha') for p in commit.get('parents', [])]
    if parents != [parent]:
        bad.append(f'Red run: the plant\'s parents are {parents}, want the green run\'s commit {parent}')
    gone = subprocess.run(['gh', 'api', f'repos/{GH_REPO}/branches/{RED_BRANCH}'], capture_output=True, text=True)
    if gone.returncode == 0 or 'HTTP 404' not in gone.stderr:
        bad.append(f'Red run: {RED_BRANCH} does not answer 404 (exit {gone.returncode}): {gone.stderr.strip() or "it exists"}')
    for base in ('develop', 'main'):
        ahead = gh_json('api', f'repos/{GH_REPO}/compare/{base}...{plant}').get('ahead_by')
        if not isinstance(ahead, int) or ahead < 1:
            bad.append(f'Red run: the plant is {ahead!r} ahead of {base}; it must be in neither develop nor main')


def ledger(record: dict[str, list[str] | None], gate: str, bad: list[str]) -> None:
    spec = HERE.parent
    named = sorted({m for doc in ('PLAN.md', 'TECH.md')
                    for m in re.findall(r'issues/[a-z0-9][a-z0-9-]*\.md', (spec / doc).read_text())})
    if not named:
        bad.append('ledger: PLAN.md and TECH.md name no issues/<name>.md; the walk found nothing to check')
    roadmap = Path('ROADMAP.md').read_text()
    for path in named:
        if not Path(path).is_file():
            bad.append(f'ledger: {path} is named but does not exist')
        if f'**Seed:** [`{path}`]({path})' not in roadmap:
            bad.append(f'ledger: {path} has no ROADMAP.md seed entry')


def landing(record: dict[str, list[str] | None], gate: str, bad: list[str]) -> None:
    head = run('git', 'rev-parse', 'HEAD').strip()
    runs = gh_json('run', 'list', '-R', GH_REPO, '--workflow', 'ci.yml', '--commit', head, '-e', 'push',
                   '--json', 'databaseId,attempt,status,conclusion')
    if not runs:
        bad.append(f'no push run of ci.yml for HEAD {head[:10]}')
    for r in runs:
        if (r.get('status'), r.get('conclusion')) != ('completed', 'success'):
            bad.append(f'run {r.get("databaseId")} attempt {r.get("attempt")} is {r.get("status")}/{r.get("conclusion")}, not completed/success')


MODES = {'local': local, 'green': green, 'red': red, 'ledger': ledger, 'landing': landing}


def main() -> int:
    modes = sys.argv[1:]
    unknown = [m for m in modes if m not in MODES]
    if not modes or unknown:
        sys.exit(f'usage: evidence.py {{{",".join(MODES)}}}...; unknown: {unknown}')
    if not RECORD.is_file():
        print(f'evidence: {RECORD} does not exist', file=sys.stderr)
        return 1
    record = sections(RECORD.read_text())
    gate = GATE.read_text()
    status = 0
    for mode in modes:
        bad: list[str] = []
        MODES[mode](record, gate, bad)
        for line in bad:
            print(f'evidence[{mode}]: {line}', file=sys.stderr)
        print(f'evidence[{mode}]: ' + (f'{len(bad)} problem(s)' if bad else 'the record agrees with the gate'))
        status |= bool(bad)
    return status


if __name__ == '__main__':
    sys.exit(main())
