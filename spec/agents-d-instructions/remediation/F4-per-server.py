# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""F4 (review 1): each server keeps its own site.

`uv run python spec/agents-d-instructions/remediation/F4-per-server.py`, from the tree root.

- In one process, servers built from distinct site contexts (files, missing, unreadable, timed out,
  and two file sets), all built before any is asked, then asked in an interleaved order in both
  eras: each one's tool, resource and instructions are its own; a server dropped and collected
  leaves the others intact.
- The class's mutant, `site-holder-global` (the holder swapped for one module value), is killed by
  the suite, and so is the cycle's `context-rereads`, which shares its anchor.

Footprint: one Python process, then the mutation children; about 30 s, under 300 MB.
"""

from __future__ import annotations

import asyncio
import gc
import sys

from common import check, finish, mutants, site

from fastmcp import Client  # noqa: E402
from rcac_mcp.server import SERVER_INSTRUCTIONS, create_mcp_server  # noqa: E402

SITES = {
    'files-1': site.SiteContext(files=(site.SiteFile('a.md', 'One.'),)),
    'files-2': site.SiteContext(files=(site.SiteFile('a.md', 'Two.'), site.SiteFile('b.md', 'Two b.'))),
    'missing': site.SiteContext.missing('this host'),
    'unreadable': site.SiteContext.unreadable('the cluster', 'permission denied'),
    'timeout': site.SiteContext.timed_out('the cluster'),
}


async def ask(server, era: str) -> tuple[str, str, str]:
    async with Client(server, mode='legacy' if era == 'legacy' else 'auto') as client:
        result = await client.call_tool('cluster_context', {}, raise_on_error=False)
        contents = await client.read_resource('rcac://context')
        return (''.join(getattr(p, 'text', '') for p in result.content),
                ''.join(getattr(c, 'text', '') for c in contents), client.instructions or '')


async def run(era: str) -> None:
    servers = {name: create_mcp_server('none', site=context) for name, context in SITES.items()}
    order = [*SITES, *reversed(SITES), *sorted(SITES)]
    for name in order:
        tool, resource, instructions = await ask(servers[name], era)
        want = SITES[name].text
        check(tool == want and resource == want, f'{era} {name}: served {tool[:60]!r} / {resource[:60]!r}')
        check(instructions == site.compose(SERVER_INSTRUCTIONS, SITES[name]), f'{era} {name}: instructions differ')
    del servers['files-2']
    gc.collect()
    for name in servers:
        tool, resource, _ = await ask(servers[name], era)
        check(tool == resource == SITES[name].text, f'{era} {name}: changed after another server went')


def main() -> int:
    for era in ('legacy', 'modern'):
        asyncio.run(asyncio.wait_for(run(era), 60))
    mutants('site-holder-global', 'context-rereads')
    return finish('F4 per server')


if __name__ == '__main__':
    sys.exit(main())
