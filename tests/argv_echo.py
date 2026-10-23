# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Argv-echo stand-ins for cluster programs, installed in a test home. Not collected.

`install(home, *programs)` writes one `/bin/sh` stand-in per program into `home/bin` and makes a
login bash in that home put `home/bin` first on PATH (a line appended to `home/.bash_profile`), so a
launch through any backend whose `HOME` is `home` (the `executes` homes, the loopback SSH server's)
runs the stand-in instead of a real program. Each call records the exact arguments it received, one
file per call under `home/.argv-echo`, and `sbatch` answers like the real one. `calls(home)` reads
them back in call order, so a test sees each argument exactly as the program got it, byte for byte.
"""

from __future__ import annotations

import stat
from pathlib import Path

# The job id the `sbatch` stand-in reports.
JOB_ID = '4242'

# Record the argument count, then each argument, NUL-terminated, in a file numbered by call order
# (with the real programs, whatever functions a test profile exported over them).
STAND_IN = f"""#!/bin/sh
unset -f basename ls mkdir printf tr wc
dir="$HOME/.argv-echo"
mkdir -p "$dir" || exit 90
n=$(ls "$dir" | wc -l | tr -d ' ')
name=$(basename "$0")
printf '%s\\0' "$#" "$@" >"$dir/$(printf '%06d' "$n")-$name" || exit 91
case $name in
    sbatch) echo 'Submitted batch job {JOB_ID}' ;;
esac
exit 0
"""

PROFILE_LINE = 'PATH="$HOME/bin:$PATH"; export PATH\n'


def install(home: Path, *programs: str) -> None:
    """Put an argv-echo stand-in for each of `programs` first on a login bash's PATH in `home`."""
    bin_dir = home / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    for program in programs:
        path = bin_dir / program
        path.write_text(STAND_IN)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(home / '.bash_profile', 'a') as profile:
        profile.write(PROFILE_LINE)


def calls(home: Path) -> list[tuple[str, list[str]]]:
    """Every stand-in call made in `home`, in order: (program, the arguments it received)."""
    found = []
    for record in sorted((home / '.argv-echo').glob('*')) if (home / '.argv-echo').is_dir() else []:
        program = record.name.split('-', 1)[1]
        count, *args = record.read_bytes().split(b'\0')[:-1]
        if len(args) != int(count):
            raise ValueError(f'{record}: {count!r} arguments announced, {len(args)} recorded')
        found.append((program, [a.decode('utf-8', 'surrogateescape') for a in args]))
    return found
