# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Start the real `rcac-mcp` CLI with /etc/agents.d pointed at a planted directory, for serve_gate.py.

`serve_shim.py DIRECTORY LOG [--hang] -- ARGV…` sets `rcac_mcp.site.AGENTS_D` to DIRECTORY, appends
one line to LOG each time any site reader runs (`read_local`, `read_remote`, `parse_remote`) and
`list` each time anything lists DIRECTORY (`os.scandir`, `os.listdir`), and
runs `rcac_mcp.main(ARGV)`. `--hang` makes `read_local` sleep for 60 s first, a read that never
finishes. Nothing else is changed: the startup path, the budget and the server are the product's.
Only serve_gate.py launches it, always under bin/sandbox.sh.
"""

from __future__ import annotations

import os
import sys
import time

from rcac_mcp import site


def main(argv: list[str]) -> int:
    split = argv.index('--')
    directory, log, *flags = argv[:split]
    site.AGENTS_D = directory

    def logged(name: str, fn):
        def reader(*args, **kwargs):
            with open(log, 'a') as stream:
                stream.write(f'{name}\n')
            if name == 'read_local' and '--hang' in flags:
                time.sleep(60)
            return fn(*args, **kwargs)
        return reader

    for name in ('read_local', 'read_remote', 'parse_remote'):
        setattr(site, name, logged(name, getattr(site, name)))

    # Any listing of the planted directory, by any code path, is logged as `list`: a re-read that
    # bypasses the named readers still shows.
    def listing(fn):
        def wrapper(path='.', *args, **kwargs):
            if os.path.realpath(os.fspath(path)) == os.path.realpath(directory):
                with open(log, 'a') as stream:
                    stream.write('list\n')
            return fn(path, *args, **kwargs)
        return wrapper

    os.scandir = listing(os.scandir)
    os.listdir = listing(os.listdir)
    import rcac_mcp
    return rcac_mcp.main(argv[split + 1:])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
