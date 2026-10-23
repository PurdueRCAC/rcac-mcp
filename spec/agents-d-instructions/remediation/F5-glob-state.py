# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""F5 (review 1): `READ_SITE`'s result does not depend on the login shell's glob state.

`uv run python spec/agents-d-instructions/remediation/F5-glob-state.py`, from the tree root.

The option set is evaluated, not listed: every `shopt` option whose name holds `glob` in each bash
found (`/bin/bash`, and any bash 4.1 or later on the usual paths), plus `noglob` (`set -f`) and
`GLOBIGNORE`. Each is turned on alone and all together, set in the bash before the script (any
bash) and, where the bash reads them from its environment (4.1 or later), exported as `BASHOPTS`
and `GLOBIGNORE`. On layouts that exercise them (dotfiles and `.md`, upper-case `.MD`, names with
pattern characters, no hidden match for `failglob`, none at all), `READ_SITE` under
bin/sandbox.sh, parsed, must equal `read_local`. The rows run and the bashes found are printed. Then
the class's mutant, `site-glob-state-inherited`.

Footprint: one Python process and one sandboxed bash child at a time, then one mutation child;
about 1 min, under 200 MB.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from common import check, finish, mutants, plant, remote, site

LAYOUTS = {
    'mixed': {'a.md': 'A.\n', 'b.md': 'B.\n', '.hidden.md': 'H.\n', '.md': 'Bare.\n', 'Z.MD': 'Upper.\n',
              'x[1].md': 'Brackets.\n', '@(a).md': 'Extglob-shaped.\n', 'AGENTS.md': 'Never.\n'},
    'no-hidden': {'agents.md': 'Lower.\n'},
    'empty': {},
}


def bashes() -> dict[str, tuple[int, int]]:
    found = {}
    for shell in ('/bin/bash', '/usr/local/bin/bash', '/opt/homebrew/bin/bash', '/usr/bin/bash'):
        if os.access(shell, os.X_OK) and os.path.realpath(shell) not in {os.path.realpath(s) for s in found}:
            out = subprocess.run([shell, '-c', 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'],
                                 capture_output=True, text=True, timeout=10).stdout.split()
            found[shell] = (int(out[0]), int(out[1]))
    return found


def glob_options(shell: str) -> list[str]:
    out = subprocess.run([shell, '--norc', '--posix', '-c', 'shopt'], capture_output=True, text=True, timeout=10)
    return sorted(line.split()[0] for line in out.stdout.splitlines() if 'glob' in line.split()[0])


def main() -> int:
    found = bashes()
    print(f'F5: bashes {found}', file=sys.stderr)
    rows = 0
    with tempfile.TemporaryDirectory(prefix='f5-') as name:
        roots = {label: plant(Path(name) / label, files) for label, files in LAYOUTS.items()}
        local = {label: site.read_local('this host', str(root)) for label, root in roots.items()}
        check([f.name for f in local['mixed'].files] == ['.hidden.md', '.md', '@(a).md', 'a.md', 'b.md', 'x[1].md'],
              f'the mixed layout reads {[f.name for f in local["mixed"].files]}')
        for shell, version in found.items():
            options = glob_options(shell)
            check({'dotglob', 'nullglob', 'failglob', 'nocaseglob', 'extglob'} <= set(options),
                  f'{shell}: glob options {options}')
            states = {o: f'shopt -s {o}\n' for o in options}
            states |= {'noglob': 'set -f\n', 'globignore': 'GLOBIGNORE=./a*:./.h*\n', 'globignore-dot': 'GLOBIGNORE=x\n',
                       'all': f'shopt -s {" ".join(options)}\nset -f\nGLOBIGNORE=./b*\n'}
            envs = {}
            if version >= (4, 1):
                envs = {o: (f'BASHOPTS={o}',) for o in options}
                envs |= {'globignore': ('GLOBIGNORE=./a*',), 'all': (f'BASHOPTS={":".join(options)}', 'GLOBIGNORE=./b*')}
            for label, root in roots.items():
                for state, prelude in states.items():
                    rows += 1
                    got = remote(root, prelude=prelude, shell=shell)
                    check(got == local[label], f'{shell} {label} set {state}: {[f.name for f in got.files]} {got.reason}')
                for state, env in envs.items():
                    rows += 1
                    got = remote(root, env=env, shell=shell)
                    check(got == local[label], f'{shell} {label} exported {state}: {[f.name for f in got.files]} {got.reason}')
    print(f'F5: {rows} rows', file=sys.stderr)
    check(rows > 0, 'no row ran')
    mutants('site-glob-state-inherited')
    return finish('F5 glob state')


if __name__ == '__main__':
    sys.exit(main())
