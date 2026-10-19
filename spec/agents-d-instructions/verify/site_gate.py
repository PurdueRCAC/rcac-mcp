# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""P1's gate: the site reader and composer, checked against an oracle written here, not by the build.

Run from the root of the tree being built: `uv run python spec/agents-d-instructions/verify/site_gate.py`.
Exit 0 when every check holds; otherwise each failure is printed as `site_gate: …` and the exit is 1.

Footprint: one Python process, plus one short `bin/sandbox.sh` bash child at a time (the remote
read run for real, about a dozen in all), each awaited before the next; under 200 MB, no network,
nothing left running. Planted directories live under a `tempfile` directory removed on exit.

What it pins (PLAN.md § 2): `rcac_mcp.site` exposes `AGENTS_D`, `HEADING`, `READ_SITE`,
`SiteContext` (`files`, `reason`, `failed`, `text`, and the constructors `missing`, `unreadable`,
`timed_out`), `SiteFile` (`name`, `text`), `read_local(where, directory)`,
`read_remote(executor, where, directory, timeout)`, `parse_remote(stdout, where)`,
`read_bounded(read, where, timeout)`, `compose(base, site, budget)`, `minimum_budget(base, site)`,
`units(text)` and `BudgetError` (a `ValueError`).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rcac_mcp import site
from rcac_mcp.executor.base import TIMEOUT_EXIT_CODE, CommandResult
from rcac_mcp.scripts import FIXED_SCRIPTS, script_argv

SANDBOX = Path('bin/sandbox.sh').resolve()
FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    if not ok:
        FAILURES.append(what)
        print(f'site_gate: {what}', file=sys.stderr)


# The oracle -------------------------------------------------------------------------------------

def oracle_files(root: Path) -> list[tuple[str, str]]:
    """(name, text) of every eligible file, in byte order of the names: written from the GOAL and PLAN § 2.1.

    A name is its bytes as UTF-8 with U+FFFD for invalid bytes; text is the content decoded the same
    way with trailing whitespace removed (leading whitespace kept); a whitespace-only file is not served.
    """
    out = []
    for entry in os.scandir(root):
        mode = os.lstat(entry.path).st_mode
        name = os.fsencode(entry.name).decode('utf-8', 'replace')
        if not stat.S_ISREG(mode) or not name.endswith('.md') or name == 'AGENTS.md':
            continue
        text = Path(entry.path).read_bytes().decode('utf-8', 'replace').rstrip()
        if text.strip():
            out.append((name, text))
    return sorted(out, key=lambda pair: pair[0].encode('utf-8'))


def u16(text: str) -> int:
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def reasons(where: str, detail: str = 'x' * 80, timeout: str = '10') -> list[str]:
    """PLAN.md § 2.1's four one-line reasons, word for word."""
    return [f'No site rules are served: /etc/agents.d does not exist on {where}.',
            f'No site rules are served: /etc/agents.d on {where} holds no rules (no non-empty *.md file but AGENTS.md).',
            f'No site rules are served: /etc/agents.d on {where} could not be read ({detail}).',
            f'No site rules are served: reading /etc/agents.d on {where} did not finish within {timeout} s.']


def floor(base: str) -> int:
    """The base, the heading and the longest reason: no budget under it is accepted (PLAN.md § 2.2)."""
    head = f'{base}\n\n## Site context (/etc/agents.d)\n\n'
    return u16(head) + max(u16(r) for w in ('the cluster', 'this host') for r in reasons(w))


def oracle_compose(base: str, files: list[tuple[str, str]], budget: int) -> str | None:
    """The composition rule of PLAN.md § 2.2, re-implemented; None when the budget is refused."""
    if budget < floor(base):
        return None
    head = f'{base}\n\n## Site context (/etc/agents.d)\n\n'
    blocks = {name: f'<!-- Source: {name} -->\n{text}' for name, text in files}

    def render(shown: list[str], left: list[str]) -> str:
        parts = [blocks[n] for n in shown]
        if left:
            parts.append(f'Left out here for length: {", ".join(left)}. '
                         f'Call the `cluster_context` tool for the full site context.')
        return head + '\n\n'.join(parts)

    names = [n for n, _ in files]
    whole = render(names, [])
    if u16(whole) <= budget:
        return whole
    if u16(render([], names)) > budget:
        return None
    shown: list[str] = []
    for name in names:
        trial = [*shown, name]
        if u16(render(trial, [n for n in names if n not in trial])) <= budget:
            shown.append(name)
    return render(shown, [n for n in names if n not in shown])


# Planting ---------------------------------------------------------------------------------------

LAYOUT = {
    'a b.md': 'Alpha rules, with `ticks` and snake_case.\n', 'b.md': 'Bravo é rules 😀.\n',
    '.hidden.md': 'Hidden rules.\n', 'AGENTS.md': 'The assembled file: must never appear.\n',
    'blank.md': ' \n\n', 'notes.txt': 'Not markdown.\n', 'Upper.MD': 'Wrong case.\n',
    'Z.md': 'Capital Z sorts before lower case in byte order.\n', '10-first.md': 'Digits sort first.\n',
    '.md': 'A name that is only the extension.\n', 'code.md': '    indented first line kept\n\n\n',
    'end 0.md': 'A name shaped like the end line.\n',
    '-n.md': 'A name shaped like an option.\n',
}


def plant(root: Path, layout: dict[str, str], link: bool = True) -> Path:
    root.mkdir(parents=True)
    for name, text in layout.items():
        (root / name).write_text(text)
    if link:
        (root / 'link.md').symlink_to(root / 'b.md')
        (root / 'dir.md').mkdir()
        os.mkfifo(root / 'fifo.md')
    return root


def remote_read(root: Path) -> 'site.SiteContext':
    argv = script_argv(site.READ_SITE, 'cluster_context', str(root))
    done = subprocess.run([str(SANDBOX), *argv], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    check(done.returncode == 0, f'the remote read script exited {done.returncode}: {done.stderr[-300:]!r}')
    return site.parse_remote(done.stdout, 'this host')


class Recorder:
    """An executor that answers every run with a fixed result and records the argv and timeout."""

    hostname = 'gate.invalid'

    def __init__(self, stdout: str, code: int = 0) -> None:
        self.stdout, self.code, self.calls = stdout, code, []

    def run(self, command, cwd=None, timeout=None, stdin=None):
        self.calls.append((command, timeout))
        return CommandResult(self.stdout, '', self.code, self.hostname)


# Checks -----------------------------------------------------------------------------------------

def check_reads(tmp: Path) -> None:
    root = plant(tmp / 'layout', LAYOUT)
    want = oracle_files(root)
    check([n for n, _ in want] == ['-n.md', '.hidden.md', '.md', '10-first.md', 'Z.md', 'a b.md', 'b.md', 'code.md', 'end 0.md'], f'oracle drift: {[n for n, _ in want]}')
    for label, context in (('local', site.read_local('this host', str(root))), ('remote', remote_read(root))):
        got = [(f.name, f.text) for f in context.files]
        check(got == want, f'{label} read: files {[n for n, _ in got]} != {[n for n, _ in want]}, or text differs')
        check(not context.failed and context.reason == '', f'{label} read of a good directory has a reason')
        text = '\n\n'.join(f'<!-- Source: {n} -->\n{t}' for n, t in want)
        check(context.text == text, f'{label} read: text is not the headed files in order')
        check('assembled file' not in context.text, f'{label} read: AGENTS.md leaked into the text')
    # Only AGENTS.md exactly is skipped. Its own directory: macOS file systems fold case, so
    # `agents.md` beside `AGENTS.md` would be one file.
    lower = plant(tmp / 'lower', {'agents.md': 'Lower case.\n'}, link=False)
    for label, context in (('local', site.read_local('this host', str(lower))), ('remote', remote_read(lower))):
        check([f.name for f in context.files] == ['agents.md'], f'{label}: agents.md (lower case) was not served')
    check(site.READ_SITE in FIXED_SCRIPTS, 'READ_SITE is not a registered fixed script')
    check('/etc/agents.d' not in site.READ_SITE, 'the directory is script text, not a positional parameter')

    states = {'missing': tmp / 'nope'}
    (tmp / 'empty').mkdir()
    states['empty'] = tmp / 'empty'
    states['only-agents'] = plant(tmp / 'only', {'AGENTS.md': 'x\n', 'blank.md': '\n'}, link=False)
    states['unreadable-dir'] = plant(tmp / 'ud', {'a.md': 'a\n'}, link=False)
    states['unreadable-file'] = plant(tmp / 'uf', {'a.md': 'a\n', 'b.md': 'b\n'}, link=False)
    (tmp / 'regular').write_text('x\n')
    states['notdir'] = tmp / 'regular'
    root_user = os.geteuid() == 0
    if root_user:
        # Root reads a mode-0 file anyway: those two rows would fail for the wrong reason.
        print('site_gate: running as root, the unreadable rows are skipped', file=sys.stderr)
        del states['unreadable-dir'], states['unreadable-file']
    else:
        states['unreadable-dir'].chmod(0)
        (states['unreadable-file'] / 'b.md').chmod(0)
    try:
        for label, read in (('local', lambda p: site.read_local('this host', str(p))), ('remote', remote_read)):
            for state, path in states.items():
                context = read(path)
                failed = state.startswith('unreadable') or state == 'notdir'
                check(context.files == (), f'{label} {state}: files were served')
                check(context.reason.startswith('No site rules are served') and '\n' not in context.reason,
                      f'{label} {state}: the reason is not one line naming why: {context.reason!r}')
                check(context.failed == failed, f'{label} {state}: failed={context.failed}')
                check(context.text == (context.reason if failed else ''), f'{label} {state}: text {context.text!r}')
                if state == 'unreadable-file':
                    check('b.md' in context.reason, f'{label} {state}: the reason does not name the file')
                if state == 'notdir':
                    check('not a directory' in context.reason, f'{label} notdir: {context.reason!r}')
    finally:
        if not root_user:
            (tmp / 'ud').chmod(0o755)
            (tmp / 'uf' / 'b.md').chmod(0o644)
    if not root_user:
        long = plant(tmp / 'long', {'a.md': 'a\n', 'b' * 200 + '.md': 'b\n'}, link=False)
        (long / ('b' * 200 + '.md')).chmod(0)
        reason = site.read_local('this host', str(long)).reason
        (long / ('b' * 200 + '.md')).chmod(0o644)
        check(reason.startswith('No site rules are served') and u16(reason) <= max(u16(r) for r in reasons('this host')),
              f'an unreadable file with a long name gives a reason longer than the room kept for it: {u16(reason)}')
    if sys.platform.startswith('linux'):
        bad = tmp / 'bad'
        bad.mkdir()
        Path(os.fsdecode(bytes(bad) + b'/bad\xff.md')).write_text('x\n')
        for label, context in (('local', site.read_local('this host', str(bad))), ('remote', remote_read(bad))):
            names = [f.name for f in context.files]
            check(names == ['bad\ufffd.md'], f'{label}: a non-UTF-8 name reads as {names!r}')
    else:
        print('site_gate: not Linux, the non-UTF-8 name row is skipped (macOS refuses such names)', file=sys.stderr)


def check_remote_answers() -> None:
    for stdout, code, want in (('file Yi5tZA== Yg==\nend 2\n', 0, 'short'), ('file Yi5tZA== Yg==\n', 0, 'no end'),
                               ('', TIMEOUT_EXIT_CODE, 'timeout'), ('', 255, 'exit'), ('garbage\nend 0\n', 0, 'junk'),
                               ('file Yi5tZA== %%%\nend 1\n', 0, 'bad base64')):
        executor = Recorder(stdout, code)
        context = site.read_remote(executor, 'the cluster', '/etc/agents.d', timeout=7)
        check(context.failed and not context.files, f'remote answer {want!r} was not a failure: {context!r}')
        if want == 'timeout':
            check('did not finish' in context.reason, f'a timed-out read does not say so: {context.reason!r}')
        [(argv, timeout)] = executor.calls
        check(argv[-1] == '/etc/agents.d' and timeout == 7, f'remote read argv/timeout: {argv[-1]!r} {timeout!r}')


def check_bound() -> None:
    began = time.monotonic()
    context = site.read_bounded(lambda: time.sleep(5) or site.SiteContext(), 'the cluster', 0.5)
    took = time.monotonic() - began
    check(took < 0.8, f'a hung read held the caller {took:.2f} s against a 0.5 s bound')
    check(context.failed and 'did not finish' in context.reason, f'a hung read is not a timeout: {context!r}')
    context = site.read_bounded(lambda: 1 / 0, 'the cluster', 5)
    check(context.failed and context.files == (), 'a read that raised is not a failure')


def check_compose() -> None:
    base = 'Base, never cut: `cluster_context` 😀.'
    sites = [
        [('a.md', 'A' * 10), ('b.md', 'B' * 300), ('c.md', 'C' * 20)],
        [('a.md', 'A' * 300), ('b.md', 'B' * 10), ('c.md', 'C' * 10)],
        [('x.md', 'é😀' * 40)],
        [('a.md', 'A' * 400), ('b.md', 'B' * 400)],
    ]
    for files in sites:
        context = site.SiteContext(files=tuple(site.SiteFile(n, t) for n, t in files))
        for budget in range(0, 1300):
            want = oracle_compose(base, files, budget)
            try:
                got = site.compose(base, context, budget)
            except site.BudgetError as exc:
                got = None
                minimum = next(b for b in range(0, 5000) if oracle_compose(base, files, b) is not None)
                check(f'at least {minimum}' in str(exc), f'refusal at {budget} does not name {minimum}: {exc}')
                check(site.minimum_budget(base, context) == minimum, 'minimum_budget disagrees with the oracle')
            if got != want:
                check(False, f'compose({[n for n, _ in files]}, {budget}) differs from the oracle: '
                             f'{(got or "")[-80:]!r} vs {(want or "")[-80:]!r}')
                break
            if got is not None and u16(got) > budget:
                check(False, f'compose exceeded {budget}: {u16(got)}')
                break
    failures = (site.SiteContext.missing('the cluster'), site.SiteContext.found([], 'this host'),
                site.SiteContext.unreadable('the cluster', 'y' * 500), site.SiteContext.timed_out('the cluster'))
    for context in failures:
        text = site.compose(base, context, 4096)
        check(text == f'{base}\n\n{site.HEADING}\n\n{context.reason}', f'no-file composition: {text[-120:]!r}')
        check(context.reason in reasons('the cluster', 'y' * 79 + '…') + reasons('this host'),
              f'a reason is not one of the four PLAN states: {context.reason!r}')
        for files in sites:
            good = site.SiteContext(files=tuple(site.SiteFile(n, t) for n, t in files))
            budget = site.minimum_budget(base, good)
            try:
                check(u16(site.compose(base, context, budget)) <= budget, 'a failed read overflows a good budget')
            except site.BudgetError:
                check(False, f'a budget that serves a good read ({budget}) refuses a failed one: {context.reason!r}')
    check(site.units('😀é') == 3, 'units does not count UTF-16 code units')
    check(issubclass(site.BudgetError, ValueError), 'BudgetError is not a ValueError')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='site-gate-', ignore_cleanup_errors=True) as name:
        check_reads(Path(name))
        check_remote_answers()
        check_bound()
        check_compose()
    print(f'site_gate: {"FAILED, " + str(len(FAILURES)) + " check(s)" if FAILURES else "ok"}', file=sys.stderr)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
