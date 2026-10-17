#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Fail when a built sdist ships repository furniture, or when there is no sdist to check.

Usage, from the repository root:

    uv run python bin/sdist_hygiene.py [DIST_DIR]       # DIST_DIR defaults to dist/

This file is the one definition of what an sdist must not contain. The release recipe's `gate`
(AGENTS.md § Factory) and the CI workflow both run it, so the two cannot drift apart.
`[tool.hatch.build.targets.sdist]` in pyproject.toml excludes the same directories; this check
backs that list up, because the list only covers the directories that exist today.

Every member of an sdist sits under one root directory named after the tarball
(`rcac_mcp-0.1.0/`). A member outside that root is reported too: its path cannot be read
relative to the root, so it cannot be cleared.

Exit status: 0 when every sdist found is clean; 1 when any sdist ships furniture, any member sits
outside the root, a tarball cannot be read, or there is no sdist. Each problem is one line on
stderr naming the tarball and the offending path.
"""

from __future__ import annotations

import sys
import tarfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

# Repository furniture, relative to the sdist root. `.security/` is the one that matters: shipping
# it would publish an inventory of unremediated weaknesses.
FURNITURE: tuple[str, ...] = ('.security', 'spec', 'issues', 'tests/fixtures', 'bin', '.agents')

SDIST_SUFFIX = '.tar.gz'


def member_violations(root: str, names: Iterable[str]) -> list[str]:
    """Return one message per member that is furniture or that sits outside `root`."""
    banned = [PurePosixPath(entry).parts for entry in FURNITURE]
    found: list[str] = []
    for name in names:
        parts = PurePosixPath(name).parts
        if not parts or parts[0] != root or '..' in parts:
            found.append(f'{name} (outside the sdist root {root}/)')
            continue
        relative = parts[1:]
        if any(relative[: len(entry)] == entry for entry in banned):
            found.append('/'.join(relative))
    return found


def sdist_violations(dist: Path) -> list[str]:
    """Return one message per problem across every sdist in `dist`, or the absence of any."""
    tarballs = sorted(dist.glob(f'*{SDIST_SUFFIX}'))
    if not tarballs:
        return [f'{dist}: no sdist (*{SDIST_SUFFIX}) to check']
    found: list[str] = []
    for tarball in tarballs:
        root = tarball.name.removesuffix(SDIST_SUFFIX)
        try:
            with tarfile.open(tarball) as archive:
                names = archive.getnames()
        except (OSError, tarfile.TarError) as error:
            found.append(f'{tarball}: cannot be read ({error})')
            continue
        found.extend(f'{tarball}: {path}' for path in member_violations(root, names))
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dist = Path(args[0] if args else 'dist')
    found = sdist_violations(dist)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(f'sdist hygiene: {len(found)} problem(s); see above', file=sys.stderr)
        return 1
    print(f'sdist hygiene: {", ".join(p.name for p in sorted(dist.glob("*" + SDIST_SUFFIX)))} clean')
    return 0


if __name__ == '__main__':
    sys.exit(main())
