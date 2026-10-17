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
"""

from __future__ import annotations

import datetime
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hygiene_mutants  # noqa: E402
import static  # noqa: E402

HERE = Path(__file__).parent
RECORD = HERE.parent / 'EVIDENCE.md'
GATE = HERE / 'gate.sh'
WORKFLOWS = f'{static.CI} {static.MUTANTS}'


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


MODES = {'local': local}


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
