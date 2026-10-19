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

A tarball is read in full before it is judged: every member header, every byte of member data,
the end of the archive and the compression trailer that checks them. Whatever stops that read,
the tarball is reported as `<tarball>: cannot be read (<reason>)` and the next one is checked; an
sdist the check could not read is never called clean.

Exit status: 0 when every sdist found is clean; 1 when any sdist ships furniture, any member sits
outside the root, a tarball cannot be read, or there is no sdist. Each problem is one line on
stderr naming the tarball and the offending path.
"""

from __future__ import annotations

import bz2
import lzma
import sys
import tarfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

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


def member_names(tarball: Path) -> list[str]:
    """Return the name of every member of `tarball`, raising when any part of it cannot be read.

    Listing the members is not a full read. `tarfile` treats a header it cannot parse, or no
    header at all, as the end of the archive, so the members after it go unlisted, and it never
    reads the compression trailer, so a damaged or truncated stream can list cleanly. Reading on
    from where the listing stopped to the end of the stream closes both: an end-of-archive block
    must be there, every byte from it on must be zero, and reaching the end makes the decompressor
    check its own length and checksum. `BZ2File` and `LZMAFile` skip a later stream they cannot
    decode, so those streams are also decoded one by one, strictly, by `check_streams`.
    """
    with tarfile.open(tarball) as archive:
        names = archive.getnames()
        stream = archive.fileobj
        stream.seek(archive.offset)
        tail = 0
        while block := stream.read(tarfile.RECORDSIZE):
            if block.count(0) != len(block):
                raise tarfile.ReadError(f'data after the end of the archive, at or after offset {archive.offset}')
            tail += len(block)
        if tail < tarfile.BLOCKSIZE:
            raise tarfile.ReadError(f'no end-of-archive block at offset {archive.offset}')
        strict = STRICT_STREAMS.get(type(stream))
    if strict:
        check_streams(tarball, strict)
    return names


# Compressed formats whose file readers pass over a damaged later stream, with a decoder for each.
STRICT_STREAMS = {bz2.BZ2File: bz2.BZ2Decompressor, lzma.LZMAFile: lzma.LZMADecompressor}


def check_streams(tarball: Path, decoder: Callable[[], Any]) -> None:
    """Decode every compressed stream in `tarball` in turn, raising unless each one ends whole.

    Each stream must reach its own end marker, and whatever follows it must start another stream.
    Output is decoded a record at a time and dropped, so memory stays bounded.
    """
    with tarball.open('rb') as raw:
        current, started, data = decoder(), False, raw.read(tarfile.RECORDSIZE)
        while data or not current.needs_input:
            current.decompress(data, tarfile.RECORDSIZE)
            started, data = True, b''
            if current.eof:
                current, started, data = decoder(), False, current.unused_data
            if not data and current.needs_input:
                data = raw.read(tarfile.RECORDSIZE)
        if started:
            raise EOFError('compressed stream ended before its end-of-stream marker')


def sdist_violations(dist: Path) -> list[str]:
    """Return one message per problem across every sdist in `dist`, or the absence of any."""
    tarballs = sorted(dist.glob(f'*{SDIST_SUFFIX}'))
    if not tarballs:
        return [f'{dist}: no sdist (*{SDIST_SUFFIX}) to check']
    found: list[str] = []
    for tarball in tarballs:
        root = tarball.name.removesuffix(SDIST_SUFFIX)
        try:
            names = member_names(tarball)
        # Every error is caught, not a list of types: the tar layer and each decompressor raise
        # their own, and one missed type would stop the run with a traceback. Failing closed only
        # needs the tarball reported.
        except Exception as error:
            reason = ' '.join(f'{type(error).__name__}: {error}'.split())
            found.append(f'{tarball}: cannot be read ({reason})')
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
