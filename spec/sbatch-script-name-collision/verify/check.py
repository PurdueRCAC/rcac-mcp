"""Checks behind gate.sh for sbatch-script-name-collision. Run from the worktree root with `uv run python`.

    check.py targets LIST              print each id in LIST as a pytest node id under tests/
    check.py outcomes LIST JUNIT...    every id in LIST is in each report exactly once, and passed
    check.py static --base REV         P1: what the fix may touch and how the name is drawn
    check.py surface --base REV        P2: the one named surface delta, its length cap, and AGENTS.md's record

Offline and read-only: the base revision is read with `git show`/`git diff`, the package is imported
from this tree, and nothing is launched but git.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
SLURM = ROOT / 'src/rcac_mcp/tools/slurm.py'
FIXTURE = 'tests/fixtures/served_surface.json'

# The only source file the cycle changes, and the paths it must leave byte-identical.
CHANGED_SOURCE = {'src/rcac_mcp/tools/slurm.py'}
UNTOUCHED = ['src/rcac_mcp/executor', 'src/rcac_mcp/context.py', 'src/rcac_mcp/middleware.py',
             'src/rcac_mcp/server.py', 'bin/sandbox.sh']

# The distinguishing part comes from the operating system's entropy source and nothing else: no
# module whose values repeat across processes or hosts (a seeded generator, a counter, the clock,
# the process id, the host name).
FORBIDDEN_MODULES = {'random', 'uuid', 'itertools', 'socket', 'platform', 'time'}
FORBIDDEN_ATTRIBUTES = {'getpid', 'getppid', 'gethostname', 'uname', 'time_ns', 'monotonic_ns', 'perf_counter_ns'}

OLD_FORM = '~/jobs/<name>_<timestamp>.sh'
OLD_DOC_FORM = '~/jobs/<job_name>_<timestamp>.sh'
NEW_FORM = '~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh'
FROZEN = datetime(2026, 10, 23, 12, 0, 0, 1)
DESCRIPTION_CAP = 2000


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout


def read_ids(path: str) -> list[str]:
    lines = [line.strip() for line in Path(path).read_text().splitlines()]
    return [line for line in lines if line and not line.startswith('#')]


def node_id(case: ET.Element) -> str:
    # pytest writes classname "tests.test_x" and name "test_y[param]" for tests/test_x.py::test_y[param].
    module = case.get('classname', '').split('.')
    return '/'.join(module[1:-1] + [module[-1] + '.py']) + '::' + case.get('name', '')


def outcomes(ids_path: str, *reports: str) -> list[str]:
    found = []
    ids = read_ids(ids_path)
    for report in reports:
        seen: dict[str, list[ET.Element]] = {}
        for case in ET.parse(report).getroot().iter('testcase'):
            seen.setdefault(node_id(case), []).append(case)
        for test in ids:
            cases = seen.get(test, [])
            if len(cases) != 1:
                found.append(f'{report}: {test} ran {len(cases)} times, expected once')
                continue
            bad = sorted({child.tag for child in cases[0] if child.tag in ('failure', 'error', 'skipped')})
            if bad:
                found.append(f'{report}: {test}: {", ".join(bad)}')
    return found


def frozen_paths(names: list[str | None]) -> list[str]:
    """`_job_script_path` for each name, with the wall clock held inside one second."""
    sys.path.insert(0, str(ROOT / 'src'))
    import rcac_mcp.tools.slurm as slurm

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return FROZEN

    real = slurm.datetime
    slurm.datetime = Frozen
    try:
        return [str(slurm._job_script_path(name)) for name in names]
    finally:
        slurm.datetime = real


def static(base: str) -> list[str]:
    found = []
    changed = set(git('diff', '--name-only', base, '--', 'src').split())
    if not changed <= CHANGED_SOURCE:
        found.append(f'source outside tools/slurm.py changed: {sorted(changed - CHANGED_SOURCE)}')
    for path in UNTOUCHED:
        if git('diff', '--name-only', base, '--', path).strip():
            found.append(f'{path} changed; the fix is leaf-only')

    tree = ast.parse(SLURM.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or '').split('.')[0]}
        else:
            names = set()
        if names & FORBIDDEN_MODULES:
            found.append(f'slurm.py imports {sorted(names & FORBIDDEN_MODULES)}: the name must not depend on them')
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            found.append(f'slurm.py reads .{node.attr} (line {node.lineno}): the name must not depend on it')
    function = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_job_script_path'), None)
    if function is None:
        found.append('slurm.py has no _job_script_path')
    else:
        calls = [n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == 'token_hex' and isinstance(n.func.value, ast.Name) and n.func.value.id == 'secrets']
        if len(calls) != 1 or [ast.literal_eval(a) for a in calls[0].args] != [8]:
            found.append('_job_script_path does not draw its token with exactly one secrets.token_hex(8)')

    paths = frozen_paths(['sweep', 'sweep', None, None, 'a/b', 'a_b'])
    if len(set(paths)) != len(paths):
        found.append(f'two calls in one second share a path: {paths}')
    stems = ['sweep', 'sweep', 'job', 'job', 'a_b', 'a_b']
    for stem, path in zip(stems, paths):
        if not re.fullmatch(rf'~/jobs/{re.escape(stem)}_20261023_120000_[0-9a-f]{{16}}\.sh', path):
            found.append(f'{path!r} is not ~/jobs/{stem}_20261023_120000_<16 hex digits>.sh')

    agents = (ROOT / 'AGENTS.md').read_text()
    if OLD_FORM in agents:
        found.append(f'AGENTS.md still states the old form {OLD_FORM}')
    return found


def leaves(value: Any, where: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(value, dict):
        out: dict[tuple[str, ...], Any] = {}
        for key, item in value.items():
            out.update(leaves(item, (*where, str(key))))
        return out if value else {where: {}}
    if isinstance(value, list):
        out = {}
        for i, item in enumerate(value):
            out.update(leaves(item, (*where, str(i))))
        return out if value else {where: []}
    return {where: value}


def surface(base: str) -> list[str]:
    found = []
    old = leaves(json.loads(git('show', f'{base}:{FIXTURE}')))
    new = leaves(json.loads((ROOT / FIXTURE).read_text()))
    differ = sorted(k for k in old.keys() | new.keys() if old.get(k, ...) != new.get(k, ...))
    if differ != [('tools', 'sbatch', 'description')]:
        found.append(f'the fixture delta is not exactly tools.sbatch.description: {[".".join(k) for k in differ][:10]}')

    sys.path.insert(0, str(ROOT / 'src'))
    import rcac_mcp.tools.slurm as slurm
    doc = inspect.getdoc(slurm.sbatch.fn)
    if new.get(('tools', 'sbatch', 'description')) != doc:
        found.append('the fixture description is not the sbatch docstring as served')
    # This cycle's delta only, not a suite-wide rule: Claude Code cuts an MCP tool description at
    # 2,048 characters, and the sbatch description must stay clear of it (gate ruling, 2026-10-23).
    if doc is not None and len(doc) > DESCRIPTION_CAP:
        found.append(f'the sbatch description is {len(doc)} characters, over this cycle\'s cap of {DESCRIPTION_CAP}')
    if doc is None or NEW_FORM not in doc or OLD_DOC_FORM in doc:
        found.append(f'the sbatch docstring does not state {NEW_FORM} in place of {OLD_DOC_FORM}')

    agents = ' '.join((ROOT / 'AGENTS.md').read_text().split())
    records = re.findall(r'`sbatch-script-name-collision`.{0,200}?`sbatch\.description`', agents)
    if len(records) < 2:
        found.append('AGENTS.md does not record the sbatch.description delta for this cycle in both §9 and '
                     f'§ Testing ({len(records)} record(s) found)')
    return found


def main(argv: list[str]) -> int:
    command, *rest = argv
    if command == 'targets':
        print('\n'.join(f'tests/{test}' for test in read_ids(rest[0])))
        return 0
    if command == 'outcomes':
        found = outcomes(rest[0], *rest[1:])
    elif command in ('static', 'surface') and rest[:1] == ['--base']:
        found = (static if command == 'static' else surface)(rest[1])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for line in found:
        print(f'check {command}: {line}', file=sys.stderr)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
