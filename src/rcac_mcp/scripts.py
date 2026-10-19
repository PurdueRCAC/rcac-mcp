# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Fixed bash scripts: a redirection or a two-step operation without a command string.

When one executor call needs more than a single program (a redirection, `cd` before `ls`), the tool
hands the executor `[/bin/bash, --norc, --posix, -c, FIXED_RESET + SCRIPT, NAME, *values]`
(`launch.fixed_argv`): `SCRIPT` is a module-level constant, and every caller-supplied value reaches
it only as a positional parameter (`"$1"`, `"$2"`, ...), never as script text, so no value is ever
parsed as shell. The bash that runs it reads no startup file (`--norc`, and POSIX mode: no
`BASH_ENV`), and `launch.FIXED_RESET` first undoes the functions and shell options a login profile
can export to it.

Every such script is registered in `FIXED_SCRIPTS` when it is defined. The tool tests require the
element after `-c` in any shell argv a tool hands the executor to be a member behind the reset
(`script_of`), so a script with a value formatted into it fails like a command string does.
"""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Final

# Internal libs
from rcac_mcp.executor.launch import FIXED_RESET, fixed_argv

# Public interface
__all__ = ['FIXED_SCRIPTS', 'fixed', 'script_argv', 'script_of']

# Every fixed script a tool or resource may hand the executor.
FIXED_SCRIPTS: Final[set[str]] = set()


def fixed(script: str) -> str:
    """Register `script` as a fixed script and return it; call it only on a module-level constant."""
    FIXED_SCRIPTS.add(script)
    return script


def script_argv(script: str, name: str, *values: str) -> list[str]:
    """The argv that runs the fixed `script` as `name`, with `values` as its positional parameters."""
    return fixed_argv(script, name, *values)


def script_of(argv: Sequence[str] | str) -> str | None:
    """The registered fixed script `argv` runs, or None when it runs none (or an unregistered one)."""
    if isinstance(argv, str) or '-c' not in argv[:-1]:
        return None
    text = argv[list(argv).index('-c') + 1]
    script = text[len(FIXED_RESET):] if text.startswith(FIXED_RESET) else None
    return script if script in FIXED_SCRIPTS else None
