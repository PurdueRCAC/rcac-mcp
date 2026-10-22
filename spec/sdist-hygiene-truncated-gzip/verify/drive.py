"""Drive the real sdist hygiene command over every plant and compare with what it must print.

    uv run python spec/sdist-hygiene-truncated-gzip/verify/drive.py [--script-rev REV] [--base REV]
                                                                  [--expect-red PLANT,...]

Run from the repository root. Each plant (plants.py) is written under a fresh temporary
directory, and the command is the recipe's and CI's: `uv run python <script> <plant dir>`, with
<script> the working tree's `bin/sdist_hygiene.py`, or that file at `--script-rev`. A plant marked
`same_as_base` is also run through the `--base` revision's script (default 5153e47, read with
`git show`), and the two runs must agree byte for byte.

Default: exit 0 when every plant matches, else exit 1 listing each mismatch.
`--expect-red P,...`: the negative control. Exit 0 only when each named plant mismatches, so a
drive that cannot see a known defect is red.

Footprint: one child at a time (uv, then python); under 100 MB.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plants  # noqa: E402


def show(rev: str) -> str:
    return subprocess.run(['git', 'show', f'{rev}:bin/sdist_hygiene.py'], capture_output=True, text=True,
                          check=True).stdout


def run(script: Path, target: Path) -> tuple[int, str, str]:
    done = subprocess.run(['uv', 'run', '-q', 'python', str(script), str(target)],
                          capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout, done.stderr


def mismatch(spec: dict, target: Path, got: tuple[int, str, str]) -> str | None:
    """Return why `got` breaks the plant's expectation, or None."""
    code, out, err = got
    if 'Traceback' in err or 'Traceback' in out:
        return 'a traceback was printed'
    names = sorted(p.name for p in target.glob('*.tar.gz'))
    if spec.get('clean'):
        want = f'sdist hygiene: {", ".join(names)} clean\n'
        if (code, out, err) != (0, want, ''):
            return f'want exit 0 and {want!r}; got exit {code}, stdout {out!r}, stderr {err!r}'
        return None
    if code != 1 or out:
        return f'want exit 1 and no stdout; got exit {code}, stdout {out!r}'
    lines = err.splitlines()
    want = spec['lines']
    if len(lines) != len(want) + 1:
        return f'want {len(want)} problem line(s) and the summary; got {lines!r}'
    for line, expected in zip(lines, want):
        name, _, rest = expected.partition(': ')
        prefix = f'{target / name}: '
        if rest == plants.U:
            if not re.fullmatch(re.escape(prefix) + r'cannot be read \(\S.*\)', line):
                return f'want {prefix}cannot be read (...); got {line!r}'
        elif line != prefix + rest:
            return f'want {prefix + rest!r}; got {line!r}'
    if lines[-1] != f'sdist hygiene: {len(want)} problem(s); see above':
        return f'want the summary counting {len(want)}; got {lines[-1]!r}'
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--script-rev', default='')
    parser.add_argument('--base', default='5153e47')
    parser.add_argument('--expect-red', default='')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='sdist-drive-') as scratch:
        base = Path(scratch) / 'base_sdist_hygiene.py'
        base.write_text(show(args.base))
        script = Path('bin/sdist_hygiene.py').resolve()
        if args.script_rev:
            script = Path(scratch) / 'rev_sdist_hygiene.py'
            script.write_text(show(args.script_rev))
        out = Path(scratch) / 'plants'
        specs = plants.write(out)
        red: dict[str, str] = {}
        for plant, spec in specs.items():
            target = out / plant
            got = run(script, target)
            why = mismatch(spec, target, got)
            if why is None and spec.get('same_as_base') and run(base, target) != got:
                why = f'differs from {args.base}'
            if why:
                red[plant] = why
    if args.expect_red:
        wanted = args.expect_red.split(',')
        unknown = [p for p in wanted if p not in specs]
        missed = [p for p in wanted if p in specs and p not in red]
        for plant in unknown:
            print(f'drive: no plant named {plant}', file=sys.stderr)
        for plant in missed:
            print(f'drive: {plant} matched, but the negative control needs it red', file=sys.stderr)
        if unknown or missed:
            return 1
        print(f'drive: negative control ok ({len(wanted)} plant(s) red as expected)')
        return 0
    for plant, why in red.items():
        print(f'drive: {plant}: {why}', file=sys.stderr)
    if red:
        print(f'drive: {len(red)} of {len(specs)} plant(s) red', file=sys.stderr)
        return 1
    print(f'drive: {len(specs)} plant(s) ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
