# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Shared parts of the review-1 class checkers (F1-F5). Run each checker from the root of the tree.

Each checker evaluates its class's property over planted directories, through both reads where a
read is involved, with an oracle of its own (a UTF-16 count by encoding, Unicode categories for
control characters, the file mode bits), then drives the real CLI through the frozen `verify/`
gate's shim and server helpers (imported, never edited), then runs the class's mutants.
"""

from __future__ import annotations

import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, 'tests')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'verify'))

from rcac_mcp import site  # noqa: E402
from rcac_mcp.scripts import script_argv  # noqa: E402

SANDBOX = Path('bin/sandbox.sh').resolve()
FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    if not ok:
        FAILURES.append(what)
        print(f'{Path(sys.argv[0]).stem}: {what}', file=sys.stderr)


def finish(name: str) -> int:
    print(f'{name}: {"FAILED, " + str(len(FAILURES)) + " check(s)" if FAILURES else "ok"}', file=sys.stderr)
    return 1 if FAILURES else 0


def u16(text: str) -> int:
    """UTF-16 code units, counted by encoding: independent of `site.units`."""
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def breaks(char: str) -> bool:
    """A control character or a line or paragraph separator, by Unicode category."""
    return unicodedata.category(char) in ('Cc', 'Zl', 'Zp')


def plant(root: Path, files: dict[str, str], modes: dict[str, int] | None = None) -> Path:
    root.mkdir(parents=True)
    root.chmod(0o755)
    for name, text in files.items():
        (root / name).write_text(text)
        (root / name).chmod(0o644)
    for name, mode in (modes or {}).items():
        (root / name).chmod(mode)
    return root


def unlock(root: Path) -> None:
    if root.is_dir():
        root.chmod(0o755)
        for entry in root.iterdir():
            if entry.is_file():
                entry.chmod(0o644)


def remote(root: Path, prelude: str = '', env: tuple[str, ...] = (), shell: str | None = None) -> site.SiteContext:
    """`READ_SITE` run for real under bin/sandbox.sh, parsed; `prelude` runs first in its bash."""
    argv = script_argv(site.READ_SITE, 'cluster_context', str(root))
    c = argv.index('-c')
    argv[c + 1] = prelude + argv[c + 1]
    if shell:
        argv[0] = shell
    done = subprocess.run([str(SANDBOX), 'env', *env, *argv], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    check(done.returncode == 0, f'READ_SITE exited {done.returncode}: {done.stderr[-200:]!r}')
    return site.parse_remote(done.stdout, 'this host')


def both(root: Path) -> dict[str, site.SiteContext]:
    """`root` read locally and remotely; the two must agree."""
    reads = {'local': site.read_local('this host', str(root)), 'remote': remote(root)}
    check(reads['local'] == reads['remote'], f'{root.name}: local and remote reads differ')
    return reads


def mutants(*cases: str) -> None:
    """Run `cases` with the runner's controls; every one must be killed."""
    done = subprocess.run([sys.executable, 'tests/mutants.py', '--self-check', *cases], capture_output=True,
                          text=True, timeout=1800)
    lines = done.stdout.splitlines() + done.stderr.splitlines()
    for case in cases:
        check(any(line.startswith(f'killed  {case}:') for line in lines), f'mutant {case} was not killed')
    check(done.returncode == 0, f'tests/mutants.py exited {done.returncode}: {done.stdout[-400:]}')
