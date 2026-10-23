# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The site's rules: `/etc/agents.d/*.md`, read once at startup, and the instructions composed from them.

The files are every `*.md` regular file directly in the directory (a `maxdepth 1` glob; a symlink is
not a regular file, and dotfiles count, as `find -name` matches them) except the assembled
`AGENTS.md`, which repeats the others; only that exact name is skipped. Each becomes a block under a
`<!-- Source: {name} -->` header, in the byte order of the names (`LC_ALL=C sort`). A file's text is
its content with trailing whitespace removed; a file with nothing else in it carries no rule and is
not served.

The read happens once, before the server is built: over the shared executor in `ssh` mode (the
cluster's files), in this process in `local` and `delegate` modes (the host's files, read as the
server and never as a user). In `delegate` mode the server reads for accounts other than its own, so
a directory or file that others cannot read is unreadable there, and no user receives text an
ordinary account could not read; `local` and `ssh` keep the file system's own answer. The read is
bounded: a read that has not finished within `READ_TIMEOUT` seconds is abandoned, and the server
starts with one line saying why no site rules are served.

Every length here is in UTF-16 code units (`units`), the budget's own unit: what is reserved, cut
or checked. A name is shown with each control character (C0, DEL, C1, U+2028, U+2029) as an escape
(`shown`), so no name can break a header, a reason or the note across lines.
"""

# Type annotations
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Final
from weakref import WeakKeyDictionary

# Standard libs
import base64
import binascii
import fnmatch
import os
import queue
import stat
import threading

# Internal libs
from rcac_mcp.executor.base import TIMEOUT_EXIT_CODE, Executor
from rcac_mcp.scripts import fixed, script_argv

# Public interface
__all__ = ['CONVENTION', 'AGENTS_D', 'EXCLUDED', 'READ_TIMEOUT', 'DEFAULT_BUDGET', 'DETAIL_LIMIT', 'NOT_FOR_OTHERS',
           'HEADING', 'READ_SITE', 'SiteFile', 'SiteContext', 'BudgetError', 'units', 'shown', 'read_local', 'parse_remote',
           'read_remote', 'read_bounded', 'omission_note', 'compose', 'minimum_budget', 'register', 'site_of']

# The convention's path, as every text names it. `AGENTS_D` is where the read looks and `READ_TIMEOUT`
# how long it may take, both read at call time: a test points them elsewhere, and no text changes.
CONVENTION: Final = '/etc/agents.d'
AGENTS_D = CONVENTION
READ_TIMEOUT = 10.0
EXCLUDED: Final = 'AGENTS.md'
DEFAULT_BUDGET: Final = 4096
HEADING: Final = f'## Site context ({CONVENTION})'

# The longest detail a reason carries, in UTF-16 code units; a longer one (a long file name) is
# shortened, so every reason fits the room `minimum_budget` keeps for it.
DETAIL_LIMIT: Final = 80

# The detail of a directory or file that others cannot read, in `delegate` mode.
NOT_FOR_OTHERS: Final = 'not readable by others'

# The remote read: every regular `*.md` entry directly in "$1" (dotfiles and a bare `.md` too, as
# `find -name` matches them), each name and content as one base64 word, so no name or content can
# forge a line, then a count the parser checks, so an answer cut short reads as unreadable rather
# than as fewer files. The reset in front of it covers its own list of names, not these four. The
# glob state a login profile can export (`BASHOPTS`, `SHELLOPTS`, `GLOBIGNORE`) is reset here too, so
# the match is the same whatever it set; an option this bash does not know is skipped.
READ_SITE = fixed(
    'unset -f base64 shopt tr test\n'
    'unset GLOBIGNORE\n'
    'set +f\n'
    'for o in dotglob nullglob failglob nocaseglob globstar extglob; do shopt -u "$o" 2>/dev/null; done\n'
    'd=$1\n'
    'if [ ! -e "$d" ]; then printf "missing\\n"; exit 0; fi\n'
    'if [ ! -d "$d" ]; then printf "notdir\\n"; exit 0; fi\n'
    'cd -- "$d" 2>/dev/null && [ -r . ] || { printf "unreadable\\n"; exit 0; }\n'
    'n=0\n'
    'for f in ./*.md ./.*.md ./.md; do\n'
    '  [ -f "$f" ] && [ ! -h "$f" ] || continue\n'
    '  n=$((n+1))\n'
    '  name=$(printf %s "${f#./}" | base64 | tr -d "\\n")\n'
    '  if body=$(base64 < "$f"); then\n'
    '    printf "file %s %s\\n" "$name" "$(printf %s "$body" | tr -d "\\n")"\n'
    '  else\n'
    '    printf "unreadable-file %s\\n" "$name"\n'
    '  fi\n'
    'done\n'
    'printf "end %s\\n" "$n"\n')


@dataclass(frozen=True)
class SiteFile:
    """One file's name and its text (the content, trailing whitespace removed)."""

    name: str
    text: str

    @property
    def block(self) -> str:
        """The file as the site context shows it: its header line, then its text."""
        return f'<!-- Source: {shown(self.name)} -->\n{self.text}'


@dataclass(frozen=True)
class SiteContext:
    """What the startup read found: the files, or one line saying why there are none.

    `failed` marks a read that could not finish (unreadable, timed out), whose line the tool and the
    resource return; a missing directory or one with no eligible file is not a failure, and they
    return `''`. Each line names where the read looked (`the cluster` or `this host`), never a host.
    """

    files: tuple[SiteFile, ...] = ()
    reason: str = ''
    failed: bool = False

    @property
    def text(self) -> str:
        """The full site text, as `cluster_context` and `rcac://context` return it."""
        if self.files:
            return '\n\n'.join(f.block for f in self.files)
        return self.reason if self.failed else ''

    @classmethod
    def found(cls, files: list[SiteFile], where: str) -> SiteContext:
        """The eligible files read, in byte order of their names; a blank file carries no rule."""
        files = sorted((f for f in files if f.text.strip()), key=lambda f: f.name.encode('utf-8'))
        if not files:
            return cls(reason=f'No site rules are served: {CONVENTION} on {where} holds no rules '
                              f'(no non-empty *.md file but {EXCLUDED}).')
        return cls(files=tuple(files))

    @classmethod
    def missing(cls, where: str) -> SiteContext:
        """The directory does not exist: not a failure."""
        return cls(reason=f'No site rules are served: {CONVENTION} does not exist on {where}.')

    @classmethod
    def unreadable(cls, where: str, detail: str) -> SiteContext:
        """The directory or one of its files could not be read; `detail` is shown and cut to `DETAIL_LIMIT` units."""
        return cls(reason=f'No site rules are served: {CONVENTION} on {where} could not be read ({_detail(detail)}).',
                   failed=True)

    @classmethod
    def timed_out(cls, where: str, timeout: float | None = None) -> SiteContext:
        """The read did not finish within `timeout` seconds (`READ_TIMEOUT` by default)."""
        timeout = READ_TIMEOUT if timeout is None else timeout
        return cls(reason=f'No site rules are served: reading {CONVENTION} on {where} did not finish '
                          f'within {timeout:g} s.', failed=True)


class BudgetError(ValueError):
    """The instructions budget cannot hold the base instructions and the site context's smallest form."""

    def __init__(self, budget: int, minimum: int) -> None:
        super().__init__(f'instructions budget {budget} is too small: the base instructions and the site '
                         f'context need {minimum} characters; set --instructions-budget or '
                         f'RCAC_INSTRUCTIONS_BUDGET to at least {minimum}')
        self.budget = budget
        self.minimum = minimum


def _eligible(name: str) -> bool:
    return fnmatch.fnmatchcase(name, '*.md') and name != EXCLUDED


def _decode(data: bytes) -> str:
    """A file's content as text: invalid UTF-8 becomes U+FFFD, trailing whitespace goes."""
    return data.decode('utf-8', 'replace').rstrip()


def _name(name: str) -> str:
    """A directory entry's name as text, by the remote read's rule: U+FFFD, never a lone surrogate."""
    return os.fsencode(name).decode('utf-8', 'replace')


def _breaks(char: str) -> bool:
    code = ord(char)
    return code < 0x20 or 0x7f <= code <= 0x9f or code in (0x2028, 0x2029)


def _escape(char: str) -> str:
    code = ord(char)
    return f'\\x{code:02x}' if code <= 0xff else f'\\u{code:04x}'


def shown(text: str) -> str:
    """`text` as the site context shows a name: each control character (C0, DEL, C1, U+2028, U+2029)
    becomes a `\\xNN` or `\\uNNNN` escape, so no name can break a served line."""
    return ''.join(_escape(c) if _breaks(c) else c for c in text)


def _cut(text: str, limit: int) -> str:
    """`text`, or its longest start that fits `limit` UTF-16 code units with `…` after it."""
    if units(text) <= limit:
        return text
    kept, room = [], limit - units('…')
    for char in text:
        room -= units(char)
        if room < 0:
            break
        kept.append(char)
    return ''.join(kept) + '…'


def _detail(text: str) -> str:
    return _cut(shown(text), DETAIL_LIMIT)


def _why(exc: OSError) -> str:
    return (exc.strerror or type(exc).__name__).lower()


def _others_may(mode: int, bits: int) -> bool:
    return mode & bits == bits


def read_local(where: str = 'this host', directory: str | None = None, *, for_others: bool = False) -> SiteContext:
    """Read the site files on this host, as this process (`directory` defaults to `AGENTS_D`).

    `for_others` is the read of a server that serves accounts other than its own (`delegate` mode):
    the directory must let others list and enter it, and each file must let others read it, or the
    read is unreadable, whatever this process itself may read.
    """
    directory = AGENTS_D if directory is None else directory
    try:
        entries = list(os.scandir(directory))
        if for_others and not _others_may(os.stat(directory).st_mode, stat.S_IROTH | stat.S_IXOTH):
            return SiteContext.unreadable(where, NOT_FOR_OTHERS)
    except FileNotFoundError:
        return SiteContext.missing(where)
    except OSError as exc:
        return SiteContext.unreadable(where, _why(exc))
    files = []
    for entry in entries:
        name = _name(entry.name)
        try:
            # Not followed through a symlink: a link, a directory or a FIFO is not a regular file,
            # and opening a FIFO would wait for a writer.
            if not (_eligible(name) and entry.is_file(follow_symlinks=False)):
                continue
            with open(entry.path, 'rb') as stream:
                if for_others and not _others_may(os.fstat(stream.fileno()).st_mode, stat.S_IROTH):
                    return SiteContext.unreadable(where, f'{name}: {NOT_FOR_OTHERS}')
                data = stream.read()
        except OSError as exc:
            return SiteContext.unreadable(where, f'{name}: {_why(exc)}')
        files.append(SiteFile(name, _decode(data)))
    return SiteContext.found(files, where)


def _word(text: str) -> bytes:
    return base64.b64decode(text.encode('ascii'), validate=True)


def parse_remote(stdout: str, where: str) -> SiteContext:
    """The site context from `READ_SITE`'s answer; anything malformed or short is unreadable.

    Eligibility and order are applied again here, so the answer decides only what was read.
    """
    lines = stdout.splitlines()
    if lines == ['missing']:
        return SiteContext.missing(where)
    if lines == ['unreadable']:
        return SiteContext.unreadable(where, 'permission denied')
    if lines == ['notdir']:
        return SiteContext.unreadable(where, 'not a directory')
    if not lines or not lines[-1].startswith('end '):
        return SiteContext.unreadable(where, 'the answer was incomplete')
    files = []
    try:
        count = int(lines[-1][4:])
        for line in lines[:-1]:
            kind, _, rest = line.partition(' ')
            if kind == 'file':
                encoded_name, _, encoded_body = rest.partition(' ')
                name = _word(encoded_name).decode('utf-8', 'replace')
                if _eligible(name):
                    files.append(SiteFile(name, _decode(_word(encoded_body))))
            elif kind == 'unreadable-file':
                name = _word(rest).decode('utf-8', 'replace')
                if _eligible(name):
                    return SiteContext.unreadable(where, f'{name}: permission denied')
            else:
                raise ValueError(line)
    except (ValueError, binascii.Error):
        return SiteContext.unreadable(where, 'the answer was malformed')
    if count != len(lines) - 1:
        return SiteContext.unreadable(where, 'the answer was incomplete')
    return SiteContext.found(files, where)


def read_remote(executor: Executor, where: str = 'the cluster', directory: str | None = None,
                timeout: float | None = None) -> SiteContext:
    """Read the site files through `executor` (the cluster's, in `ssh` mode), waiting `timeout` seconds.

    A timeout is a timed-out read; any other nonzero exit (the command never started, or the
    connection was lost) is an unreadable one.
    """
    directory = AGENTS_D if directory is None else directory
    timeout = READ_TIMEOUT if timeout is None else timeout
    result = executor.run(script_argv(READ_SITE, 'cluster_context', directory), timeout=timeout)
    if result.exit_code == TIMEOUT_EXIT_CODE:
        return SiteContext.timed_out(where, timeout)
    if result.exit_code != 0:
        return SiteContext.unreadable(where, f'the read exited {result.exit_code}')
    return parse_remote(result.stdout, where)


def read_bounded(read: Callable[[], SiteContext], where: str, timeout: float | None = None) -> SiteContext:
    """`read()`, or a timed-out context when it has not returned within `timeout` seconds.

    The read runs on a daemon thread, which is abandoned at the deadline: nothing waits for it, and
    it cannot hold the process open at exit. An exception it raises makes the context unreadable.
    An executor's own timeout is no such bound: over ssh, opening a session can wait past it.
    """
    timeout = READ_TIMEOUT if timeout is None else timeout
    box: queue.Queue[SiteContext] = queue.Queue(maxsize=1)

    def work() -> None:
        try:
            box.put(read())
        except Exception as exc:
            box.put(SiteContext.unreadable(where, type(exc).__name__))

    threading.Thread(target=work, name='cluster-mcp-site-read', daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        return SiteContext.timed_out(where, timeout)


def units(text: str) -> int:
    """`text`'s length in UTF-16 code units, the unit JavaScript clients count (never fewer than code points)."""
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def omission_note(names: list[str]) -> str:
    """The line naming every file left out of the instructions, and the tool that returns them."""
    return (f'Left out here for length: {", ".join(shown(n) for n in names)}. '
            f'Call the `cluster_context` tool for the full site context.')


def _head(base: str) -> str:
    return f'{base}\n\n{HEADING}\n\n'


def _render(base: str, included: list[SiteFile], omitted: list[str]) -> str:
    parts = [f.block for f in included]
    if omitted:
        parts.append(omission_note(omitted))
    return _head(base) + '\n\n'.join(parts)


def _reserve(where: str) -> int:
    """The longest one-line reason a read can end in, in units: the room a failed read needs."""
    detail = 'x' * DETAIL_LIMIT
    return max(units(r.reason) for r in (SiteContext.missing(where), SiteContext.found([], where),
                                          SiteContext.unreadable(where, detail), SiteContext.timed_out(where)))


def minimum_budget(base: str, site: SiteContext) -> int:
    """The smallest budget `compose` accepts for `site`.

    Never less than the base and the longest reason a read can end in, so a budget that serves a good
    read also serves a failed one: the server starts whatever the read finds. Without files, also
    the base with this site's own reason, so no path serves more than the budget; with files, also
    the smaller of the whole text and the base with the note naming every file.
    """
    floor = units(_head(base)) + max(_reserve('the cluster'), _reserve('this host'))
    if not site.files:
        return max(floor, units(_head(base) + site.reason))
    names = [f.name for f in site.files]
    return max(floor, min(units(_render(base, list(site.files), [])), units(_render(base, [], names))))


def compose(base: str, site: SiteContext, budget: int = DEFAULT_BUDGET) -> str:
    """The served instructions: `base`, then the site context, within `budget` UTF-16 code units.

    The base is never cut, and files go in whole, in order. When they do not all fit, a file goes in
    if it fits beside the note naming every file not yet in, so the final note, which names fewer,
    fits too; a later file that fits still goes in after one left out. A budget under
    `minimum_budget` raises a `BudgetError` naming the minimum.
    """
    minimum = minimum_budget(base, site)
    if budget < minimum:
        raise BudgetError(budget, minimum)
    if not site.files:
        return _head(base) + site.reason
    whole = _render(base, list(site.files), [])
    if units(whole) <= budget:
        return whole
    included: list[SiteFile] = []
    for f in site.files:
        trial = [*included, f]
        rest = [g.name for g in site.files if g not in trial]
        if units(_render(base, trial, rest)) <= budget:
            included.append(f)
    omitted = [f.name for f in site.files if f not in included]
    return _render(base, included, omitted)


# The site context of each server built, keyed by the server object itself: set once at construction,
# read by that server's tool and resource, never per user and never by hostname.
_SITES: WeakKeyDictionary[object, SiteContext] = WeakKeyDictionary()


def register(server: object, site: SiteContext) -> None:
    """Hold `site` as `server`'s site context, for as long as the server lives."""
    _SITES[server] = site


def site_of(server: object) -> SiteContext:
    """The site context `server` was built with."""
    return _SITES[server]
