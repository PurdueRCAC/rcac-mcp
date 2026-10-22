# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The launcher (`rcac_mcp.executor.launch`): one bash program per command, one wire per backend.

Every value a tool passes reaches the command as exactly one literal argument; a `UserPath`'s
leading `~`/`~/` resolves to the running user's home and nothing else expands; a `run_command`
script runs as given under bash, whatever shell launched it. Nothing the login initialisation reads
or prints reaches a result; a call is complete exactly when the program's end record has arrived on
both streams, which reports the command's status (a signal death as one, re-raised on the launch
itself), and nothing left running holds it past them; a timeout returns what was captured with
`TIMEOUT_EXIT_CODE`; and a program is refused when the bytes that carry it are over Linux's
per-argument limit.

`launch_violations`, `bootstrap_violations` and `separation_violations` are the pure checks; the
`unit` rows feed them rendered programs and planted negatives. The `integration` rows execute the
rendered program for real, always through `bin/sandbox.sh` under an `env -i` environment with a
fixture `HOME`: through `bash_argv` (the local and delegate launch) and through `ssh_command` as the
`-c` string of every login shell an SSH server may hand it to (sh, bash, zsh, ksh, csh, tcsh).
"""

from __future__ import annotations

import base64
import logging
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from rcac_mcp.executor import NOT_STARTED_EXIT_CODE, TIMEOUT_EXIT_CODE, UserPath
from rcac_mcp.executor import launch

from conftest import need_shell

TAKEN_MARK = b'cluster-mcp:parked-descriptor-taken'
TAKEN_NOTE = (b'cluster-mcp: the command did not start: the login initialisation took a descriptor its streams '
              b'were parked on, so it was not run; the server log has the details\n')

SANDBOX = Path(__file__).resolve().parent.parent / 'bin' / 'sandbox.sh'
SYSTEM_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'

# The texts the plan fixes, written out here so a change to any of them fails a check.
PROLOGUE = ('\\unalias -a\n'
            'unset -f builtin command exec set printf trap cd export declare compgen read kill ulimit exit; '
            'unset IFS; '
            'set +euxvTkP +o errtrace +o posix; trap - EXIT ERR DEBUG RETURN; '
            '[[ /dev/fd/57 -ef /dev/fd/47 && /dev/fd/58 -ef /dev/fd/48 && /dev/fd/59 -ef /dev/fd/49 ]] || '
            '[[ /dev/fd/57 -ef /dev/fd/47 && /dev/fd/58 -ef /dev/fd/48 && /dev/fd/59 -ef /dev/fd/49 ]] || '
            '[[ /dev/fd/57 -ef /dev/fd/47 && /dev/fd/58 -ef /dev/fd/48 && /dev/fd/59 -ef /dev/fd/49 ]] || '
            '{ printf %s cluster-mcp:parked-descriptor-taken >&49; '
            'printf %s cluster-mcp:parked-descriptor-taken >&59; exec /usr/bin/false; }; '
            'exec 0<&57 1>&58 2>&59 47<&- 48>&- 49>&- 57<&- 58>&- 59>&-')
SENTINEL_LINE = re.compile(r'printf %s%s ([0-9a-f]{16}) ([0-9a-f]{16}); printf %s%s \1 \2 >&2; '
                           r'exec 59>&2 2>/dev/null\n')
HOME_LINE = 'command cd -- "${HOME:?HOME is not set}" || exit\n'
# A start of 'home' resolves through $HOME; any other is an absolute path written into the program.
POSITIONAL_LINE = 'command cd -- "${1:?the start directory is not set}" || exit\n'
# The subshell that runs the command, so the program outlives it; then its status, its end record
# on both streams, the signal re-raised and the command's status kept.
SUBSHELL_OPEN = '(\n'
SUBSHELL_CLOSE = '\n) 2>&59 59>&-\n'
STATUS = ('cluster_mcp_s=$? cluster_mcp_k=\n'
          '(( cluster_mcp_s > 128 )) && cluster_mcp_k=$(kill -l "$cluster_mcp_s")\n'
          'case $cluster_mcp_k in HUP|INT|QUIT|ILL|TRAP|ABRT|BUS|FPE|KILL|USR1|SEGV|USR2|PIPE|ALRM|TERM|XCPU|XFSZ|'
          'VTALRM|PROF|SYS|RTMIN*|RTMAX*) cluster_mcp_r=s$((cluster_mcp_s - 128)) ;; '
          '*) cluster_mcp_k= cluster_mcp_r=x$cluster_mcp_s ;; esac\n')
END_LINE = re.compile(r'printf %s%s%s\. ([0-9a-f]{16}) ([0-9a-f]{16}) "\$cluster_mcp_r"; '
                      r'printf %s%s%s\. \1 \2 "\$cluster_mcp_r" >&59\n')
RERAISE = ('[[ -z $cluster_mcp_k ]] || { ulimit -c 0; trap - "$cluster_mcp_k"; kill -s "$cluster_mcp_k" "$$"; }\n'
           '(exit "$cluster_mcp_s")')


def start_line(start: str) -> str:
    return HOME_LINE if start == 'home' else f'command cd -- {shlex.quote(start)} || exit\n'


SCRIPT_PREFIX = ('while IFS= read -r cluster_mcp_fn; do export -f -- "$cluster_mcp_fn"; '
                 'done < <(compgen -A function); unset cluster_mcp_fn; BASH_ENV= ENV= exec /bin/bash --norc -c -- ')
STAGE_ARGV = ['/bin/bash', '--norc', '--posix', '-c',
              'exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 </dev/null >/dev/null 2>&1 && exec /bin/bash -l -c -- "$1"',
              'cluster-mcp']
BOOTSTRAP_PREFIX = ("exec /bin/bash --norc --posix -c 'p=$(printf %s \"$1\" | base64 -d && printf .) && "
                    "exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 </dev/null >/dev/null 2>&1 && "
                    "exec /bin/bash -l -c -- \"${p%.}\"' cluster-mcp ")
BASE64 = re.compile(r'[A-Za-z0-9+/]*={0,2}')

# A word's value where the running bash substitutes $HOME; no argument can contain it (NUL).
HOME = '\0HOME\0'
HOME_WORD = '"${HOME:?HOME is not set}"'
# What bash leaves alone outside quotes (the characters shlex.quote leaves bare).
INERT = re.compile(r'[\w@%+=:,./-]', re.ASCII)


def _read_word(text: str, at: int) -> tuple[str, set[str], int]:
    """The bash word starting at `at`: (its value, the unquoted characters bash would act on, end)."""
    value, unquoted = [], set()
    while at < len(text) and text[at] not in ' \n':
        if text.startswith(HOME_WORD, at):
            value.append(HOME)
            at += len(HOME_WORD)
        elif text[at] == "'":
            end = text.find("'", at + 1)
            end = len(text) if end < 0 else end
            value.append(text[at + 1:end])
            at = end + 1
        elif text.startswith('"\'"', at):
            value.append("'")
            at += 3
        else:
            if not INERT.match(text[at]):
                unquoted.add(text[at])
            value.append(text[at])
            at += 1
    return ''.join(value), unquoted, at


def _resolved(arg: str) -> str:
    """The value bash must hand the command for `arg`, with HOME standing for the running $HOME."""
    if isinstance(arg, UserPath):
        if arg == '~':
            return HOME
        if arg.startswith('~/'):
            rest = arg[2:].lstrip('/')
            return HOME + '/' + rest if rest else HOME
        if arg and not arg.startswith('/'):
            return './' + arg
    return str(arg)


def _word_violations(what: str, arg: str, value: str, unquoted: set[str], into: str) -> list[str]:
    want = _resolved(arg)
    found = []
    if unquoted:
        found.append(f'{what} {arg!r} is not quoted as one word: bash acts on {"".join(sorted(unquoted))!r}')
    if '~' in unquoted and arg.startswith('~') and not (arg == '~' or arg.startswith('~/')):
        found.append(f'{what} {arg!r} leaves ~name for bash to expand')
    if HOME in want and HOME not in value:
        found.append(f'{what} {arg!r} does not resolve through $HOME')
    if value != want:
        found.append(f'{what} {arg!r} reaches {into} as {value!r}, not {want!r}')
    return found


def launch_violations(command: list[str] | str, cwd: str | None, start: str | None, prog: str,
                      marks: launch.Marks) -> list[str]:
    """Every way `prog` does not run `command` exactly, under the plan's prologue, sentinel and record."""
    found: list[str] = []
    if marks.start.decode() in prog:
        found.append('the sentinel appears whole in the program text')
    if marks.end.decode() in prog:
        found.append('the end nonce appears whole in the program text')
    if not prog.startswith(PROLOGUE + '\n'):
        return found + ['the program does not open with the prologue']
    at = len(PROLOGUE) + 1
    line = SENTINEL_LINE.match(prog, at)
    if not line or (line[1] + line[2]).encode() != marks.start:
        return found + ['the program does not print the sentinel, in two halves, on both streams, then quiet its own stderr']
    at = line.end()
    if not prog.startswith(SUBSHELL_OPEN, at):
        return found + ['the command does not run in a subshell: the program would not outlive it']
    at += len(SUBSHELL_OPEN)
    if start is not None:
        line = start_line(start)
        if not prog.startswith(line, at):
            return found + [f'the program does not start in {"$HOME" if start == "home" else repr(start)}']
        at += len(line)
    if cwd:
        if not prog.startswith('command cd -- ', at):
            return found + [f'cwd {cwd!r} is not changed to with `command cd --`']
        value, unquoted, at = _read_word(prog, at + len('command cd -- '))
        found += _word_violations('cwd', UserPath(cwd), value, unquoted, 'cd')
        if not prog.startswith(' || exit\n', at):
            return found + [f'a failed cd to {cwd!r} does not stop the command']
        at += len(' || exit\n')
    close = prog.find(SUBSHELL_CLOSE, at)
    if close < 0:
        return found + ['the subshell does not end, with the caller\'s stderr back, before the end record']
    body, tail = prog[at:close], prog[close + len(SUBSHELL_CLOSE):]
    end = END_LINE.match(tail, len(STATUS)) if tail.startswith(STATUS) else None
    if end is None or (end[1] + end[2]).encode() != marks.end:
        found.append('the program does not print the end record, its nonce in two halves, on both streams')
    elif tail[end.end():] != RERAISE:
        found.append('the program does not re-raise a signal death and keep the command\'s status')
    if isinstance(command, str):
        if not body.startswith(SCRIPT_PREFIX):
            return found + ['the script does not run in a fresh non-login bash']
        value, unquoted, end_at = _read_word(body, len(SCRIPT_PREFIX))
        if unquoted or end_at != len(body):
            found.append(f'the script is not quoted as one word: bash acts on {body[len(SCRIPT_PREFIX):]!r}')
        elif value != command:
            found.append(f'the script reaches bash as {value!r}, not verbatim')
        return found
    if not body.startswith('exec '):
        return found + ['the argv is not exec-ed']
    words, at = [], len('exec ')
    while at < len(body):
        value, unquoted, at = _read_word(body, at)
        words.append((value, unquoted))
        at += 1
    if len(words) != len(command):
        found.append(f'the command has {len(words)} words for {len(command)} arguments: '
                     'an argument is not quoted as one word')
    for i, (arg, (value, unquoted)) in enumerate(zip(command, words)):
        found += _word_violations(f'argument {i}', arg, value, unquoted, 'the command')
    return found


def bootstrap_violations(wire: str, prog: str) -> list[str]:
    """Every way the SSH exec string is not the fixed bootstrap carrying `prog` as one base64 word."""
    found = [f'the exec string contains {name}' for char, name in (('!', "'!'"), ('\\', 'a backslash'),
                                                                   ('\n', 'a newline')) if char in wire]
    if not wire.startswith(BOOTSTRAP_PREFIX):
        return found + ['the exec string does not start with the fixed bootstrap']
    payload = wire[len(BOOTSTRAP_PREFIX):]
    if not BASE64.fullmatch(payload):
        return found + ['the payload is not base64: the login shell parses program text']
    if base64.b64decode(payload) != prog.encode():
        found.append('the payload decodes to a different program')
    return found


def _after(data: bytes, sentinel: bytes) -> bytes | None:
    at = data.find(sentinel)
    return None if at < 0 else data[at + len(sentinel):]


RECORD = re.compile(rb'([xs])([0-9]{1,3})\.')

UNFINISHED_NOTE = b"cluster-mcp: the launch ended before it reported the command's end, so its output may be incomplete\n"


def _before_record(kept: bytes, end: bytes) -> tuple[bytes, int | None]:
    """`kept` up to its first complete end record, and the record's status (None without one)."""
    at = 0
    while (found := kept.find(end, at)) >= 0:
        tail = RECORD.match(kept, found + len(end))
        if tail:
            return kept[:found], (-int(tail[2]) if tail[1] == b's' else int(tail[2]))
        at = found + 1
    return kept, None


def separation_violations(raw: tuple[bytes, bytes, int], marks: launch.Marks,
                          result: tuple[bytes, bytes, int]) -> list[str]:
    """Every way `result` is not the command's own output given the `raw` streams and exit code."""
    out, err, code = raw
    got_out, got_err, got_code = result
    kept_out, kept_err = _after(out, marks.start), _after(err, marks.start)
    found = []
    if kept_out is None and kept_err is None:
        if code == TIMEOUT_EXIT_CODE:
            if result != (b'', b'', TIMEOUT_EXIT_CODE):
                found.append(f'a timeout before the sentinel returned {result!r}, not empty output and the marker')
            return found
        note = f'cluster-mcp: the command did not start (exit {code}); the server log has the details\n'.encode()
        if TAKEN_MARK in err:
            note = TAKEN_NOTE
        if got_code != NOT_STARTED_EXIT_CODE:
            found.append(f'the command did not start, but the exit code is {got_code}, not {NOT_STARTED_EXIT_CODE}')
        if (got_out, got_err) != (b'', note):
            found.append(f'the command did not start, but the result carries {got_out!r}, {got_err!r}')
        return found
    wants, statuses, notes = [], [], b''
    for stream, kept in (('stdout', kept_out), ('stderr', kept_err)):
        if kept is None:
            wants.append(b'')
            notes += f"cluster-mcp: the login initialisation redirected the command's {stream}; it was not captured\n".encode()
            if stream.encode() not in got_err:
                found.append(f'the {stream} sentinel is missing and no note names it')
            continue
        body, status = _before_record(kept, marks.end)
        wants.append(body)
        statuses.append(status)
    want_code = code
    if code != TIMEOUT_EXIT_CODE:
        if None in statuses:
            notes += UNFINISHED_NOTE
            if UNFINISHED_NOTE not in got_err:
                found.append('a stream has no end record, and no note says the output may be incomplete')
        reported = [status for status in statuses if status is not None]
        want_code = reported[0] if reported else code
    want_out, want_err = wants[0], wants[1] + notes
    for stream, got, want, before in (('stdout', got_out, want_out, out), ('stderr', got_err, want_err, err)):
        if got != want:
            prefix = before[:before.find(marks.start)] if marks.start in before else b''
            if marks.start in got or (prefix and got.startswith(prefix)):
                found.append(f'{stream} kept output from before the sentinel: {got[:80]!r}')
            elif marks.end in got:
                found.append(f'{stream} kept the end record, or what followed it: {got[-80:]!r}')
            else:
                found.append(f'{stream} is {got[:80]!r}, not {want[:80]!r}')
    if got_code != want_code:
        found.append(f'the exit code is {got_code}, not the reported {want_code}')
    return found


# ---------------------------------------------------------------------------------------------
# Rendering (unit)

HOSTILE = ['My Data', "it's", 'say "hi"', '$HOME$(id)`id`', '*?[a]', 'line\nbreak', 'dir\n/x', 'ends\n\n',
           ';|&<>(){}!#\\', '-n', '--help', '+x', '--', '', '~', '~/x', '~//x', '~name/x', '=word', 'é ü 中\t']
USER_PATHS = [UserPath(p) for p in ('~', '~/', '~/a b', '~//etc/x', "~/it's", '~name/x', '-n', 'rel dir', '/abs path',
                                    '$HOME', '*', '~x', '~root/x', '', '/')]


@pytest.mark.unit
@pytest.mark.parametrize(('command', 'cwd', 'start'), [
    pytest.param(['printf', '%s\\0', *HOSTILE], None, None, id='hostile'),
    pytest.param(['ls', '-l', *USER_PATHS], None, None, id='user-paths'),
    pytest.param(['cat', '--', UserPath('~/x y')], '~/sub dir', 'home', id='start-home-and-cwd'),
    pytest.param(['cat', '--', UserPath('x y')], 'sub dir', "/srv/it's here", id='start-dir-and-cwd'),
    pytest.param('echo "$HOME"; exit 4', None, None, id='script'),
    pytest.param('-x\ncat <<EOF\nhello\nEOF\n\n', None, None, id='script-dash-heredoc'),
    pytest.param('', '/tmp', None, id='empty-script-with-cwd'),
])
def test_program_quotes_each_argument_as_one_word(command: list[str] | str, cwd: str | None, start: str | None) -> None:
    prog, marks = launch.program(command, cwd, start)
    assert re.fullmatch(rb'[0-9a-f]{32}', marks.start) and re.fullmatch(rb'[0-9a-f]{32}', marks.end), marks
    assert marks.start != marks.end, marks
    violations = launch_violations(command, cwd, start, prog, marks)
    assert violations == [], '\n'.join(violations)
    # Fresh nonces per call.
    again = launch.program(command, cwd, start)[1]
    assert again.start != marks.start and again.end != marks.end, (marks, again)


@pytest.mark.unit
@pytest.mark.parametrize('cwd', ['sub', 'sub dir', '-', '-P', '~', '~/', '~/a b', '~//etc', '~name', '/abs dir',
                                 '$HOME', 'a\nb', UserPath('rel')])
def test_program_renders_the_cwd(cwd: str) -> None:
    prog, marks = launch.program(['pwd'], cwd)
    violations = launch_violations(['pwd'], cwd, None, prog, marks)
    assert violations == [], '\n'.join(violations)


# A planted program's nonces.
S = b'0123456789abcdeffedcba9876543210'
E = b'fedcba98765432100123456789abcdef'
M = launch.Marks(S, E)


def _record_line(end: bytes = E) -> str:
    head, tail = end[:16].decode(), end[16:].decode()
    return f'printf %s%s%s. {head} {tail} "$cluster_mcp_r"; printf %s%s%s. {head} {tail} "$cluster_mcp_r" >&59\n'


def _planted(body: str, cwd_line: str = '', *, opening: str = SUBSHELL_OPEN, closing: str = SUBSHELL_CLOSE,
             ending: str | None = None) -> tuple[str, launch.Marks]:
    head, tail = S[:16].decode(), S[16:].decode()
    ending = STATUS + _record_line() + RERAISE if ending is None else ending
    return (f'{PROLOGUE}\nprintf %s%s {head} {tail}; printf %s%s {head} {tail} >&2; exec 59>&2 2>/dev/null\n'
            f'{opening}{cwd_line}{body}{closing}{ending}'), M


@pytest.mark.unit
@pytest.mark.parametrize(('command', 'cwd', 'start', 'program', 'fragment'), [
    pytest.param(['printf', 'a b'], None, None, _planted('exec printf a b'), 'not quoted as one word', id='split'),
    pytest.param(['echo', '$HOME'], None, None, _planted('exec echo $HOME'), 'not quoted as one word',
                 id='dollar-bare'),
    pytest.param(['cat', UserPath('~/x')], None, None, _planted('exec cat ~/x'), 'does not resolve through $HOME',
                 id='tilde-bare'),
    pytest.param(['cat', UserPath('~')], None, None, _planted('exec cat "$HOME"'), 'reaches the command as',
                 id='home-unguarded'),
    pytest.param(['cat', UserPath('~nobody/x')], None, None, _planted("exec cat ~nobody/'x'"),
                 'leaves ~name for bash to expand', id='tilde-name-bare'),
    pytest.param(['cat', UserPath('-n')], None, None, _planted("exec cat '-n'"), 'reaches the command as',
                 id='relative-without-dot'),
    pytest.param(['cat', UserPath('~//etc/x')], None, None, _planted(f"exec cat {HOME_WORD}/'/etc/x'"),
                 'reaches the command as', id='slashes-not-collapsed'),
    pytest.param(['pwd'], 'sub', None,
                 _planted('exec pwd', "command cd -- 'sub' || exit\n"), 'reaches cd as',
                 id='cwd-without-dot'),
    pytest.param(['pwd'], 'sub', None, _planted('exec pwd', "cd -- './sub' || exit\n"),
                 'command cd --', id='cwd-plain-cd'),
    pytest.param(['pwd'], 'sub', None, _planted('exec pwd', "command cd -- './sub'\n"), 'does not stop',
                 id='cwd-unchecked'),
    pytest.param(['pwd'], None, 'home', _planted('exec pwd'), 'does not start in $HOME', id='no-start-home'),
    pytest.param(['pwd'], None, '/srv/start', _planted('exec pwd', HOME_LINE), "does not start in '/srv/start'",
                 id='home-for-a-directory'),
    pytest.param(['pwd'], None, '/srv/start', _planted('exec pwd', POSITIONAL_LINE), "does not start in '/srv/start'",
                 id='positional-start'),
    pytest.param('echo hi', None, None, _planted(SCRIPT_PREFIX + 'echo hi'), 'not quoted as one word',
                 id='script-bare'),
    pytest.param('echo hi', None, None, _planted('echo hi'), 'fresh non-login bash', id='script-in-login-shell'),
    pytest.param('echo hi', None, None, _planted(SCRIPT_PREFIX + "'echo hi;'"), 'not verbatim', id='script-altered'),
    pytest.param(['pwd'], None, None, (_planted('exec pwd')[0].replace('trap - EXIT ERR DEBUG RETURN; ', '', 1), M),
                 'prologue', id='traps-kept'),
    pytest.param(['pwd'], None, None, ('exec pwd', M), 'prologue', id='no-prologue'),
    pytest.param(['printf', '0123456789abcdeffedcba9876543210'], None, None,
                 _planted('exec printf 0123456789abcdeffedcba9876543210'), 'sentinel appears whole', id='sentinel-whole'),
    pytest.param(['printf', E.decode()], None, None, _planted(f'exec printf {E.decode()}'), 'end nonce appears whole',
                 id='end-nonce-whole'),
    pytest.param(['pwd'], None, None, _planted('exec pwd', opening='', closing='\n'), 'does not run in a subshell',
                 id='exec-in-place'),
    pytest.param(['pwd'], None, None, _planted('exec pwd', ending=STATUS + RERAISE), 'does not print the end record',
                 id='no-record'),
    pytest.param(['pwd'], None, None,
                 _planted('exec pwd', ending=STATUS + _record_line().replace(' >&59', ' >&1') + RERAISE),
                 'does not print the end record', id='record-on-stdout-twice'),
    pytest.param(['pwd'], None, None, _planted('exec pwd', ending=STATUS + _record_line() + '(exit "$cluster_mcp_s")'),
                 'does not re-raise', id='signal-not-reraised'),
    pytest.param(['pwd'], None, None, _planted('exec pwd', closing='\n)\n'), 'does not end, with the caller',
                 id='subshell-stderr-quiet'),
])
def test_launch_check_catches_planted_programs(command: list[str] | str, cwd: str | None, start: str | None,
                                               program: tuple[str, launch.Marks], fragment: str) -> None:
    violations = launch_violations(command, cwd, start, *program)
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
@pytest.mark.parametrize(('call', 'error', 'fragment'), [
    pytest.param(lambda: launch.program(['printf', 'a\0b']), ValueError, 'argument 1 contains a NUL', id='nul'),
    pytest.param(lambda: launch.program(['printf', 'x', '\udc80']), ValueError, 'argument 2 is not valid UTF-8',
                 id='surrogate'),
    pytest.param(lambda: launch.program('echo \udc80'), ValueError, 'command is not valid UTF-8', id='script-surrogate'),
    pytest.param(lambda: launch.program('echo \0'), ValueError, 'command contains a NUL', id='script-nul'),
    pytest.param(lambda: launch.program(['pwd'], cwd='a\0b'), ValueError, 'cwd contains a NUL', id='cwd-nul'),
    pytest.param(lambda: launch.program(['pwd'], cwd='\udc80'), ValueError, 'cwd is not valid UTF-8',
                 id='cwd-surrogate'),
    pytest.param(lambda: launch.program([]), ValueError, 'empty argv', id='empty-argv'),
    pytest.param(lambda: launch.program(UserPath('~/x')), TypeError, 'not a UserPath', id='userpath-command'),
    pytest.param(lambda: launch.program(['scancel', 42]), TypeError, 'argument 1 must be a str, not int',
                 id='int-element'),
    pytest.param(lambda: launch.program(['cat', Path('/x')]), TypeError, 'argument 1 must be a str', id='path-element'),
    pytest.param(lambda: launch.program(b'ls'), TypeError, 'not bytes', id='bytes-command'),
    pytest.param(lambda: launch.program(None), TypeError, 'not NoneType', id='none-command'),
    pytest.param(lambda: launch.program(['pwd'], start='elsewhere'), ValueError, 'an absolute directory',
                 id='relative-start'),
    pytest.param(lambda: launch.program(['pwd'], start='/a\0b'), ValueError, 'start contains a NUL', id='start-nul'),
    pytest.param(lambda: launch.bash_argv(launch.program(['printf', 'x' * 128 * 1024])[0]), ValueError,
                 'per-argument limit', id='argv-too-long'),
    pytest.param(lambda: launch.ssh_command(launch.program('#' * 96 * 1024)[0]), ValueError, 'per-argument limit',
                 id='script-too-long-for-ssh'),
])
def test_program_refuses(call: Callable[[], Any], error: type[Exception], fragment: str) -> None:
    with pytest.raises(error, match=re.escape(fragment)):
        call()


# Linux's limit on one argv element, its terminating NUL included (MAX_ARG_STRLEN); macOS has none.
ARGUMENT_LIMIT = 131_072


@pytest.mark.unit
def test_local_launch_limit_is_the_per_argument_limit() -> None:
    # A local or delegated launch passes the program as one argv element of its own.
    largest = ARGUMENT_LIMIT - 1
    assert launch.bash_argv('x' * largest)[-1] == 'x' * largest
    try:
        launch.bash_argv('x' * (largest + 1))
    except ValueError as exc:
        assert f'{ARGUMENT_LIMIT}-byte per-argument limit' in str(exc), exc
    else:
        pytest.fail(f'a program of {largest + 1} bytes passed: with its NUL it is over the per-argument limit')


def _wire_size(n: int) -> int:
    """The bytes an n-byte program's SSH exec string takes as one argument: bootstrap, base64 word, NUL."""
    return len(launch.BOOTSTRAP.encode()) + 4 * math.ceil(n / 3) + 1


@pytest.mark.unit
def test_ssh_wire_limit_counts_the_bootstrap() -> None:
    # sshd hands the exec string to the login shell as the one argument after -c.
    largest = max(n for n in range(ARGUMENT_LIMIT) if _wire_size(n) <= ARGUMENT_LIMIT)
    assert _wire_size(largest + 1) > ARGUMENT_LIMIT
    wire = launch.ssh_command('x' * largest)
    assert len(wire.encode()) + 1 == _wire_size(largest) <= ARGUMENT_LIMIT, (len(wire), largest)
    try:
        launch.ssh_command('x' * (largest + 1))
    except ValueError as exc:
        assert f'{ARGUMENT_LIMIT}-byte per-argument limit' in str(exc), exc
    else:
        pytest.fail(f'a program of {largest + 1} bytes passed: its exec string is {_wire_size(largest + 1)} '
                    'bytes, over the per-argument limit')


# What a startup file can rewrite before the program runs, on bash 4 and later: the login bash's
# positional parameters (bound before its startup files run) and their kin.
POSITIONAL = re.compile(r'\$(?:[0-9@*#]|\{[0-9@*#]|\{?BASH_ARGV|\{?BASH_ARGC)')


@pytest.mark.unit
@pytest.mark.parametrize(('command', 'cwd', 'start'), [
    pytest.param(['true'], None, None, id='argv'),
    pytest.param(['true'], 'sub', 'home', id='argv-home-cwd'),
    pytest.param(['true'], '~/x', '/srv/start', id='argv-directory'),
    pytest.param('true', None, '/srv/start', id='script-directory'),
])
def test_program_reads_no_positional_parameter(command: list[str] | str, cwd: str | None, start: str | None) -> None:
    prog, _ = launch.program(command, cwd, start)
    found = POSITIONAL.findall(prog)
    assert found == [], f'the program reads a positional parameter a startup file can rewrite: {found} in {prog!r}'
    # The login bash is started with the program alone: nothing after it becomes "$1".
    assert launch.STAGE.endswith('-c -- "$1"'), f'the stage passes positional parameters to the login bash: {launch.STAGE!r}'
    assert launch.BOOTSTRAP.endswith('-c -- "${p%.}"\' cluster-mcp '), launch.BOOTSTRAP


@pytest.mark.unit
def test_bash_argv_is_the_pinned_stage() -> None:
    prog, _ = launch.program(['true'])
    assert launch.bash_argv(prog) == [*STAGE_ARGV, prog]
    assert (launch.BASH, launch.BASH_ARGS) == ('/bin/bash', ('-l',))


@pytest.mark.unit
@pytest.mark.parametrize('command', [
    pytest.param(['printf', '%s\\0', *HOSTILE], id='hostile'),
    pytest.param('echo "!x" \\\n "$(printf \'%s\' \\!)"\n\n', id='script-bang-backslash-newlines'),
    pytest.param('', id='empty-script'),
])
def test_bootstrap_is_inert_text(command: list[str] | str) -> None:
    prog, _ = launch.program(command)
    violations = bootstrap_violations(launch.ssh_command(prog), prog)
    assert violations == [], '\n'.join(violations)


@pytest.mark.unit
@pytest.mark.parametrize(('wire', 'fragment'), [
    pytest.param(BOOTSTRAP_PREFIX + "'echo hi'", 'not base64', id='raw-program'),
    pytest.param(BOOTSTRAP_PREFIX + base64.b64encode(b'echo hi').decode(), 'different program', id='other-program'),
    pytest.param(BOOTSTRAP_PREFIX.replace('&& printf .', '; printf .') + base64.b64encode(b'x').decode(),
                 'fixed bootstrap', id='decode-unchecked'),
    pytest.param("exec /bin/bash -c 'echo hi\n'", 'a newline', id='newline'),
    pytest.param("echo hi!", "'!'", id='bang'),
])
def test_bootstrap_check_catches_planted_wires(wire: str, fragment: str) -> None:
    violations = bootstrap_violations(wire, 'x')
    assert any(fragment in v for v in violations), violations


# ---------------------------------------------------------------------------------------------
# Separation (unit)

NOISE = b'profile\n\xff\xfe partial' + b'z' * 70_000
# A complete end record: the end nonce, then the status.
X0, X3, S15 = E + b'x0.', E + b'x3.', E + b's15.'
# Text shaped like a record, but with another nonce: a command's own output.
LOOKALIKE = b'0f' * 16 + b'x0.' + b'fedcba9876543210' + b'x1.'

SEPARATIONS = [
    pytest.param((NOISE + S + b'out\n' + X0, b'err-noise' + S + b'err\n' + X0, 0), id='chatty-prefix'),
    pytest.param((S + b'out ' + S + b'\n' + X3, S + S + X3, 3), id='command-prints-the-sentinel'),
    pytest.param((S + LOOKALIKE + b'\n' + X0, S + LOOKALIKE + X0, 0), id='command-prints-record-lookalikes'),
    pytest.param((S + X0, S + X0, 0), id='quiet-empty'),
    pytest.param((S + b'out\n' + X0 + b'late\n', S + X0 + b'late-err', 0), id='after-the-record-dropped'),
    pytest.param((S + b'out\n' + E + b'x7.', S + E + b'x7.', -1), id='status-from-the-record'),
    pytest.param((S + S15, S + S15, -1), id='signal-death'),
    pytest.param((S + b'part', S, TIMEOUT_EXIT_CODE), id='timeout-after-start'),
    pytest.param((S + b'part\n' + X0, S + b'perr', TIMEOUT_EXIT_CODE), id='timeout-with-one-record'),
    pytest.param((S + b'part', S + b'err', -9), id='launch-killed-before-its-record'),
    pytest.param((S + b'half' + E + b'x', S + E, 0), id='record-incomplete'),
    pytest.param((b'noise', b'startup error\n', 0), id='not-started'),
    pytest.param((b'', b'', 127), id='not-started-127'),
    pytest.param((b'noise', b'profile noise\n' + TAKEN_MARK + TAKEN_MARK, 1), id='parked-descriptor-taken'),
    pytest.param((b'noise', b'', TIMEOUT_EXIT_CODE), id='timeout-before-start'),
    pytest.param((b'noise', S + b'err\n' + X0, 0), id='stdout-redirected'),
    pytest.param((S + b'out\n' + X0, b'noise', 0), id='stderr-redirected'),
]


@pytest.mark.unit
@pytest.mark.parametrize('raw', SEPARATIONS)
def test_separate_keeps_only_the_commands_output(raw: tuple[bytes, bytes, int], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger='rcac_mcp.executor.launch'):
        result = launch.separate(*raw, M)
    violations = separation_violations(raw, M, result)
    assert violations == [], '\n'.join(violations)
    kept = [_after(stream, S) for stream in raw[:2]]
    started = any(k is not None for k in kept)
    redirected = started and None in kept
    unfinished = raw[2] != TIMEOUT_EXIT_CODE and any(k is not None and _before_record(k, E)[1] is None for k in kept)
    expect_warning = redirected or unfinished or (not started and raw[2] != TIMEOUT_EXIT_CODE)
    assert bool(caplog.records) == expect_warning, caplog.records


def _raw_streams(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    return out, err, code


def _last_cut(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    def cut(data: bytes) -> bytes:
        kept = data.rsplit(marks.start, 1)[-1]
        return kept[:kept.find(marks.end)] if marks.end in kept else kept
    return cut(out), cut(err), code


def _status_kept(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    o, e, c = launch.separate(out, err, code, marks)
    return o, e, code


def _note_dropped(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    o, e, c = launch.separate(out, err, code, marks)
    return o, e.replace(b"cluster-mcp: the login initialisation redirected the command's stdout; it was not captured\n",
                        b''), c


def _taken_unnoted(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    return launch.separate(out, err.replace(TAKEN_MARK, b''), code, marks)


def _timeout_as_not_started(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    return launch.separate(out, err, 0 if code == TIMEOUT_EXIT_CODE else code, marks)


def _record_kept(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    o, e, c = launch.separate(out, err, code, launch.Marks(marks.start, b'f' * 32))
    return o, e.replace(UNFINISHED_NOTE, b''), c


def _unfinished_unnoted(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    o, e, c = launch.separate(out, err, code, marks)
    return o, e.replace(UNFINISHED_NOTE, b''), c


def _timeout_completed(out: bytes, err: bytes, code: int, marks: launch.Marks) -> tuple[bytes, bytes, int]:
    o, e, c = launch.separate(out, err, 0 if code == TIMEOUT_EXIT_CODE else code, marks)
    return o, e.replace(UNFINISHED_NOTE, b''), c


@pytest.mark.unit
@pytest.mark.parametrize(('separate', 'raw', 'fragment'), [
    pytest.param(_raw_streams, (NOISE + S + b'out' + X0, S + X0, 0), 'kept output from before the sentinel',
                 id='raw-streams'),
    pytest.param(_last_cut, (S + b'a' + S + b'b' + X0, S + X0, 0), "stdout is b'b', not", id='last-occurrence'),
    pytest.param(_status_kept, (b'', b'', 0), 'did not start, but the exit code is 0', id='status-kept'),
    pytest.param(_status_kept, (S + b'out' + E + b'x7.', S + E + b'x7.', -1), 'not the reported 7',
                 id='record-status-ignored'),
    pytest.param(_note_dropped, (b'noise', S + b'err' + X0, 0), 'no note names it', id='note-dropped'),
    pytest.param(_taken_unnoted, (b'', TAKEN_MARK, 1), 'did not start, but the result carries', id='taken-unnoted'),
    pytest.param(_timeout_as_not_started, (b'', b'', TIMEOUT_EXIT_CODE), 'a timeout before the sentinel',
                 id='timeout-misread'),
    pytest.param(_record_kept, (S + b'out' + X0 + b'late', S + X0, 0), 'kept the end record', id='record-kept'),
    pytest.param(_unfinished_unnoted, (S + b'part', S + b'err', -9), 'no note says the output may be incomplete',
                 id='unfinished-unnoted'),
    pytest.param(_timeout_completed, (S + b'part' + X0, S + b'perr', TIMEOUT_EXIT_CODE), f'not the reported {TIMEOUT_EXIT_CODE}',
                 id='timeout-read-as-complete'),
])
def test_separation_check_catches_planted_results(separate: Callable[..., tuple[bytes, bytes, int]],
                                                  raw: tuple[bytes, bytes, int], fragment: str) -> None:
    violations = separation_violations(raw, M, separate(*raw, M))
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
@pytest.mark.parametrize(('data', 'at', 'want'), [
    pytest.param(b'out' + X0, 0, 3, id='record'),
    pytest.param(b'out' + E + b'x', 0, None, id='tail-not-arrived'),
    pytest.param(E[:-1] + b'x0.' + b'out', 0, None, id='nonce-cut-short'),
    pytest.param(E + b'y0.' + b'then' + X3, 0, 1, id='bad-tail-then-record'),
    pytest.param(LOOKALIKE, 0, None, id='lookalike'),
])
def test_record_is_the_nonce_then_a_status(data: bytes, at: int, want: int | None) -> None:
    found = launch.record(data, E, at)
    if want is None:
        assert found is None, found
    else:
        assert found is not None and data[found.start() - len(E):found.start()] == E, (data, found)


# ---------------------------------------------------------------------------------------------
# Timeouts, decoding and server-side paths (unit)

@pytest.mark.unit
@pytest.mark.parametrize('timeout', [1e12, math.nan, math.inf, 0, -1, 10_000_001])
def test_bad_timeouts_are_refused_before_anything_starts(timeout: float, guard) -> None:
    with pytest.raises(ValueError, match='timeout must be a number of seconds'):
        launch.run_process(['/bin/true'], None, timeout)
    assert guard.refused == []


@pytest.mark.unit
@pytest.mark.parametrize('timeout', ['5', True, b'1'])
def test_non_numeric_timeouts_are_refused(timeout: Any, guard) -> None:
    with pytest.raises(TypeError, match='timeout must be a number of seconds'):
        launch.run_process(['/bin/true'], None, timeout)
    assert guard.refused == []


# Above any pid a kernel hands out, so a stray signal cannot reach a real process.
STUB_PID = 2_147_483_000


class StubPopen:
    """A `Popen` stand-in: records how it was launched, answers with `returncode`, launches nothing.

    Its stdout holds `part`, in a pipe of its own. With `times_out` it has not exited and its pipes
    stay open until a signal has been sent to it; otherwise its pipes have ended and it exits once
    its stdin has. What is written to its stdin is read back with `fed`.
    """

    calls: list[dict[str, Any]]
    signals: list[tuple[str, int, int]]
    returncode: int | None
    times_out: bool = False
    made: list['StubPopen']

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.calls.append({'argv': argv, **kwargs})
        self.made.append(self)
        self.pid = STUB_PID
        self.returncode = None
        out_r, self._out_w = os.pipe()
        err_r, self._err_w = os.pipe()
        os.write(self._out_w, b'part')
        self.stdout, self.stderr = os.fdopen(out_r, 'rb'), os.fdopen(err_r, 'rb')
        self._in_r, in_w = os.pipe()
        self.stdin = os.fdopen(in_w, 'wb')
        if not self.times_out:
            self.hang_up()

    def hang_up(self) -> None:
        for fd in (self._out_w, self._err_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def fed(self) -> bytes:
        os.set_blocking(self._in_r, False)
        try:
            return os.read(self._in_r, 1 << 16)
        except BlockingIOError:
            return b''

    def poll(self) -> int | None:
        if self.times_out and not self.signals:
            return None
        # It reads its stdin to the end before it exits, as `cat` does.
        if not self.stdin.closed:
            return None
        self.returncode = type(self).returncode
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.poll()


def _stub(monkeypatch: pytest.MonkeyPatch, returncode: int | None, times_out: bool) -> tuple[type[StubPopen], list]:
    signals: list[tuple[str, int, int]] = []
    stub = type('Stub', (StubPopen,), {'calls': [], 'signals': signals, 'returncode': returncode,
                                       'times_out': times_out, 'made': []})
    monkeypatch.setattr(subprocess, 'Popen', stub)

    # The stub's pid is not a real process; no signal may leave the test, whichever call sends it.
    def record(kind: str) -> Callable[[int, int], None]:
        def send(pid: int, sig: int) -> None:
            signals.append((kind, pid, sig))
            for made in stub.made:
                made.hang_up()
        return send
    monkeypatch.setattr(os, 'killpg', record('killpg'))
    monkeypatch.setattr(os, 'kill', record('kill'))
    return stub, signals


@pytest.mark.unit
@pytest.mark.parametrize('times_out', [False, True], ids=['exited', 'seen-at-the-deadline'])
def test_a_sighup_death_is_minus_one_not_the_marker(monkeypatch: pytest.MonkeyPatch, times_out: bool) -> None:
    stub, signals = _stub(monkeypatch, -signal.SIGHUP, times_out)
    out, err, code, _ = launch.run_process(['/bin/true'], None, 0.5)
    if times_out:
        # Still running at the deadline: a timeout, never the death's -1.
        assert code == TIMEOUT_EXIT_CODE and out == b'part', (out, code)
    else:
        assert code == -1, f'a SIGHUP death reported {code}'
        assert code != TIMEOUT_EXIT_CODE and out == b'part'


@pytest.mark.unit
def test_run_process_never_hands_the_child_this_process_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    stub, _ = _stub(monkeypatch, 0, False)
    launch.run_process(['/bin/cat'], None, None)
    launch.run_process(['/bin/cat'], b'bytes', None)
    launched = [c for c in stub.calls if 'argv' in c]
    assert all(c['stdin'] == subprocess.PIPE and c['start_new_session'] for c in launched), launched
    assert [made.fed() for made in stub.made] == [b'', b'bytes'], 'the stdin given was not what the child read'
    assert all(made.stdin.closed for made in stub.made), 'the child\'s stdin was not closed: it never reads its end'


@pytest.mark.unit
def test_a_timeout_signals_the_whole_group_term_first(monkeypatch: pytest.MonkeyPatch) -> None:
    stub, signals = _stub(monkeypatch, None, True)
    out, err, code, _ = launch.run_process(['/bin/sleep', '9'], None, 0.5)
    assert (out, code) == (b'part', TIMEOUT_EXIT_CODE)
    assert signals[:1] == [('killpg', STUB_PID, signal.SIGTERM)], signals


@pytest.mark.unit
def test_decode_replaces_invalid_utf8() -> None:
    assert launch.decode(b'ok \xff\xfe \xe4\xb8\xad') == 'ok \ufffd\ufffd \u4e2d'


@pytest.mark.unit
@pytest.mark.parametrize(('path', 'want'), [
    ('~', '{home}'), ('~/x y', '{home}/x y'), ('~//etc/x', '{home}//etc/x'), ('~name/x', '~name/x'),
    ('rel', 'rel'), ('/abs', '/abs'), ('$HOME/x', '$HOME/x'),
])
def test_expand_local_resolves_only_a_leading_tilde(path: str, want: str) -> None:
    home = os.environ['HOME']
    got = launch.expand_local(path)
    assert got == want.format(home=home)
    if path.startswith('~/'):
        assert got.startswith(home + '/')


@pytest.mark.unit
@pytest.mark.parametrize('home', [None, ''], ids=['unset', 'empty'])
def test_expand_local_refuses_without_home(monkeypatch: pytest.MonkeyPatch, home: str | None) -> None:
    if home is None:
        monkeypatch.delenv('HOME')
    else:
        monkeypatch.setenv('HOME', home)
    with pytest.raises(ValueError, match=r"HOME is not set, so '~/etc/hosts'"):
        launch.expand_local('~/etc/hosts')


# ---------------------------------------------------------------------------------------------
# Execution (integration): every launch runs through bin/sandbox.sh under `env -i`.

SHELLS = ('/bin/sh', '/bin/bash', '/bin/zsh', '/bin/ksh', '/bin/csh', '/bin/tcsh')
WIRES = ('local',) + tuple(f'ssh-{Path(s).name}' for s in SHELLS)
SEPARATION_WIRES = ('local', 'ssh-sh', 'ssh-bash', 'ssh-zsh', 'ssh-tcsh')


def _sandboxed(argv: list[str], stdin: bytes | None = None, timeout: float = 30,
               end: bytes | None = None) -> tuple[bytes, bytes, int, bytes]:
    assert argv[0] == str(SANDBOX), argv
    return launch.run_process(argv, stdin, timeout, end)


def _environment(home: Path | str, *extra: str, path: str | None = None) -> list[str]:
    shim = Path(os.environ['BASH_ENV']).parent
    return [str(SANDBOX), 'env', '-i', f'HOME={home}', f'PATH={path or f"{shim}:{SYSTEM_PATH}"}',
            f'BASH_ENV={os.environ["BASH_ENV"]}', *extra]


def _wire_argv(wire: str, prog: str) -> list[str]:
    if wire == 'local':
        return launch.bash_argv(prog)
    shell = f'/bin/{wire.removeprefix("ssh-")}'
    need_shell(shell)
    return [shell, '-c', launch.ssh_command(prog)]


def run_on(wire: str, command: list[str] | str, home: Path | str, *, cwd: str | None = None, start: str | None = None,
           stdin: bytes | None = None, timeout: float = 30, env: tuple[str, ...] = (),
           path: str | None = None) -> tuple[bytes, bytes, int]:
    """Render `command`, launch it on `wire` with `home` as HOME, and return the separated result."""
    prog, marks = launch.program(command, cwd, start)
    out, err, code, note = _sandboxed(_environment(home, *env, path=path) + _wire_argv(wire, prog), stdin, timeout,
                                      marks.end)
    return launch.separate(out, err, code, marks, note)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = (tmp_path / 'home').resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.integration
@pytest.mark.parametrize('wire', WIRES)
def test_values_arrive_literally(wire: str, home: Path) -> None:
    out, err, code = run_on(wire, ['printf', '%s\\0', *HOSTILE], home)
    want = b''.join(v.encode() + b'\0' for v in HOSTILE)
    assert (out, err, code) == (want, b'', 0), f'{wire}: a value reaches the command as {out!r}, not {want!r}; {err!r}'


@pytest.mark.integration
@pytest.mark.parametrize('wire', WIRES)
def test_user_paths_resolve_only_a_leading_tilde(wire: str, home: Path) -> None:
    out, err, code = run_on(wire, ['printf', '%s\\0', *USER_PATHS], home)
    want = [_resolved(p).replace(HOME, str(home)) for p in USER_PATHS]
    got = out.decode().split('\0')[:-1]
    assert (got, err, code) == (want, b'', 0), f'{wire}: a value reaches the command as {got!r}, not {want!r}; {err!r}'


@pytest.mark.integration
@pytest.mark.parametrize('wire', ['local', 'ssh-sh', 'ssh-zsh', 'ssh-tcsh'])
def test_cwd_resolves_in_the_running_users_home(wire: str, home: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / 'cdpath'
    for base in (home, elsewhere):
        for name in ('sub', 'a b', '-', '~name'):
            (base / name).mkdir(parents=True, exist_ok=True)
    rows = [('sub', 'home', f'{home}/sub'), ('-', 'home', f'{home}/-'), ('~name', 'home', f'{home}/~name'),
            ('~', None, str(home)), ('~/a b', None, f'{home}/a b'), (str(elsewhere / 'sub'), None, f'{elsewhere}/sub')]
    got = [run_on(wire, ['pwd'], home, cwd=cwd, start=start, env=(f'CDPATH={elsewhere}',))
           for cwd, start, _ in rows]
    want = [((w + '\n').encode(), b'', 0) for _, _, w in rows]
    assert got == want, f'{wire}: cwd reaches cd as {got!r}, not {want!r}'
    missing = run_on(wire, ['pwd'], home, cwd='no such dir', start='home')
    assert missing[0] == b'' and missing[2] == 1, f'{wire}: a missing cwd ran the command: {missing!r}'


SCRIPTS = [
    ('X=1; cat <<EOF\nv=$X\nEOF', (b'v=1\n', 0)),
    ('-n; echo ran', (b'ran\n', 0)),
    ('', (b'', 0)),
    ('# only a comment', (b'', 0)),
    ('echo out; exit 4', (b'out\n', 4)),
    ('greet', (b'hello from a profile function\n', 0)),
    ('[ -n "$BASH_VERSION" ] && ! shopt -qo posix && [ "$CLUSTER_MCP_TEST_LOGIN" = 1 ] && [ a == a ] && echo bash',
     (b'bash\n', 0)),
]


@pytest.mark.integration
@pytest.mark.parametrize('wire', WIRES)
def test_scripts_run_under_bash_after_the_login_initialisation(wire: str, home: Path) -> None:
    (home / '.bash_profile').write_text('export CLUSTER_MCP_TEST_LOGIN=1\ngreet() { echo hello from a profile function; }\n')
    got = [(s, run_on(wire, s, home)) for s, _ in SCRIPTS]
    wrong = [(s, (out, code), want) for (s, (out, _, code)), (_, want) in zip(got, SCRIPTS) if (out, code) != want]
    assert wrong == [], f'{wire}: scripts did not run as given: {wrong!r}'


@pytest.mark.integration
@pytest.mark.parametrize('wire', WIRES)
def test_profile_cannot_read_stdin_or_shadow_cd(wire: str, home: Path) -> None:
    (home / 'sub').mkdir()
    (home / '.bash_profile').write_text('read -r line\ncd() { echo cd-function; builtin cd "$@"; }\n')
    sent = b'first line\nsecond line\n\0\xff no final newline'
    result = run_on(wire, ['cat'], home, cwd='sub', start='home', stdin=sent)
    assert result == (sent, b'', 0), f"{wire}: the profile read the caller's stdin, or its cd() printed: cat returned {result!r}, not the stdin sent"


@pytest.mark.integration
@pytest.mark.parametrize('wire', WIRES)
def test_bootstrap_delivers_the_program_exactly(wire: str, home: Path) -> None:
    # A raw program (no prologue): it takes stdout back itself and prints the -c text bash ran.
    prog = 'exec 1>&58\nprintf %s "$BASH_EXECUTION_STRING"\n\n\n'
    out, err, code, _ = _sandboxed(_environment(home) + _wire_argv(wire, prog))
    assert (out, code) == (prog.encode(), 0), f'{wire}: the login bash ran {out!r}, not the program sent {prog!r}'


@pytest.mark.integration
@pytest.mark.parametrize('wire', [w for w in WIRES if w != 'local'])
def test_bootstrap_fails_without_base64(wire: str, home: Path, tmp_path: Path) -> None:
    empty = tmp_path / 'empty-path'
    empty.mkdir()
    marker = home / 'ran'
    prog, _ = launch.program(f': > {shlex.quote(str(marker))}')
    out, err, code, _ = _sandboxed(_environment(home, path=str(empty)) + _wire_argv(wire, prog))
    assert code != 0, f'{wire}: the bootstrap exited 0 without base64'
    assert not marker.exists(), f'{wire}: the bootstrap ran a program without base64'


@pytest.mark.integration
@pytest.mark.parametrize('wire', ['local', 'ssh-sh', 'ssh-zsh'])
def test_an_empty_home_fails_a_tilde_path(wire: str, tmp_path: Path) -> None:
    target = (tmp_path / 'outside').resolve()
    target.write_text('read outside the home\n')
    out, err, code = run_on(wire, ['cat', UserPath('~' + str(target))], '')
    assert b'outside' not in out and code != 0, \
        f'{wire}: with HOME empty, ~{target} does not resolve through $HOME: it read {out!r} ({code})'
    assert b'HOME is not set' in err, err


# Timeouts: a raw argv through the sandbox, no login initialisation.

def _gone(pid: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _reap(pidfile: Path) -> None:
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


@pytest.mark.integration
def test_timeout_keeps_partial_output() -> None:
    start = time.monotonic()
    out, err, code, _ = _sandboxed([str(SANDBOX), '/bin/bash', '-c', 'echo part; echo perr >&2; sleep 8'], timeout=0.5)
    elapsed = time.monotonic() - start
    assert code == TIMEOUT_EXIT_CODE, f'a timeout returned {code}, not the marker'
    # The sandbox's shell may add its own line about the killed child after ours.
    assert out == b'part\n' and err.startswith(b'perr\n'), f'the timeout lost the output: {out!r}, {err!r}'
    assert elapsed < 3, f'a 0.5 s timeout took {elapsed:.1f} s'


@pytest.mark.integration
def test_timeout_kills_the_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / 'background.pid'
    try:
        out, err, code, _ = _sandboxed([str(SANDBOX), '/bin/bash', '-c', 'sleep 8 & echo $! > "$1"; echo part; sleep 8',
                                     'bash', str(pidfile)], timeout=0.5)
        assert code == TIMEOUT_EXIT_CODE, code
        assert _gone(int(pidfile.read_text()), 3), 'the background sleep is still running: the group was not killed'
    finally:
        _reap(pidfile)


# Every step of the timeout sequence shows: a command that traps SIGTERM prints as it stops, and is
# kept; one that ignores SIGTERM is ended by SIGKILL after the grace, with its group.
@pytest.mark.integration
def test_a_timeout_sends_term_first_and_keeps_what_follows() -> None:
    out, err, code, _ = _sandboxed([str(SANDBOX), '/bin/bash', '-c',
                                    "trap 'echo stopping; exit 1' TERM; echo part; sleep 20 & wait"], timeout=0.5)
    assert (out, code) == (b'part\nstopping\n', TIMEOUT_EXIT_CODE), \
        f'the timeout did not send SIGTERM first and keep what followed it: {out!r}, {err!r}, {code}'


@pytest.mark.integration
def test_a_timeout_escalates_to_sigkill(tmp_path: Path) -> None:
    pidfile = tmp_path / 'stubborn.pid'
    try:
        start = time.monotonic()
        out, err, code, _ = _sandboxed([str(SANDBOX), '/bin/bash', '-c',
                                        "trap '' TERM; sleep 20 & echo $! > \"$1\"; echo part; wait",
                                        'bash', str(pidfile)], timeout=0.5)
        elapsed = time.monotonic() - start
        bound = 0.5 + 2 * launch.GRACE + 2
        assert (out, code) == (b'part\n', TIMEOUT_EXIT_CODE), (out, err, code)
        assert elapsed < bound, (f'a command that ignores SIGTERM held the call for {elapsed:.1f} s '
                                 f'(bound {bound:.1f} s): no SIGKILL followed')
        assert _gone(int(pidfile.read_text()), 3), 'the TERM-ignoring job is still running: no SIGKILL followed'
    finally:
        _reap(pidfile)


ESCAPE = 'import os, sys, time\nos.setsid()\nopen(sys.argv[1], "w").write(str(os.getpid()))\ntime.sleep(20)\n'


@pytest.mark.integration
def test_timeout_is_bounded_when_a_process_leaves_the_group(tmp_path: Path) -> None:
    pidfile = tmp_path / 'escaped.pid'
    try:
        start = time.monotonic()
        out, err, code, _ = _sandboxed([str(SANDBOX), '/bin/bash', '-c', '"$1" -c "$2" "$3" & echo part; sleep 8',
                                     'bash', sys.executable, ESCAPE, str(pidfile)], timeout=0.5)
        elapsed = time.monotonic() - start
        assert (out, code) == (b'part\n', TIMEOUT_EXIT_CODE), (out, err, code)
        bound = 0.5 + 2 * launch.GRACE + 2
        assert elapsed < bound, f'an escaped child held the call for {elapsed:.1f} s (bound {bound:.1f} s)'
    finally:
        deadline = time.monotonic() + 3
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        _reap(pidfile)


@pytest.mark.integration
def test_a_raw_launch_is_complete_at_its_exit(tmp_path: Path) -> None:
    # With no end record to wait for, a launch that has exited is complete: everything it wrote is
    # already in the pipes, and what it left running neither holds the call nor turns it into a
    # timeout.
    pidfile = tmp_path / 'holder.pid'
    try:
        start = time.monotonic()
        out, err, code, note = _sandboxed([str(SANDBOX), '/bin/bash', '-c',
                                           'sleep 6 & echo $! > "$1"; (sleep 2; echo late) & echo done; exit 3',
                                           'bash', str(pidfile)], timeout=4)
        elapsed = time.monotonic() - start
        assert elapsed < 1.5, f'a background holder held an exited launch for {elapsed:.1f} s'
        assert (out, code, note) == (b'done\n', 3, b''), \
            f'an exited launch was reported as {(out, err, code, note)!r}, not its own 3 with its output'
    finally:
        _reap(pidfile)


# The end record: a call ends at it, whatever is left running, and it reports a signal death.

# A command that leaves a background job writing to both streams after it exits.
LEAVES_A_WRITER = ['/bin/sh', '-c', '(sleep 3; echo late; echo late-err >&2) & echo now']


@pytest.mark.integration
@pytest.mark.parametrize('wire', SEPARATION_WIRES)
def test_the_call_ends_at_its_records(wire: str, home: Path) -> None:
    start = time.monotonic()
    result = run_on(wire, LEAVES_A_WRITER, home, timeout=20)
    elapsed = time.monotonic() - start
    assert result == (b'now\n', b'', 0), f'{wire}: the result is {result!r}, not the command\'s own output'
    assert elapsed < 2, f'{wire}: a background writer held the call for {elapsed:.1f} s past the end records'


# Records a launch's own exit status, as its parent sees it (bin/sandbox.sh would report a signal
# death as 128+N): the launch is run under it, its streams passed through.
REPORTER = ('import subprocess, sys\n'
            'code = subprocess.run(sys.argv[2:]).returncode\n'
            'open(sys.argv[1], "w").write(str(code))\n')

STATUSES = {
    # (command, the result's exit code, the launch's own status)
    'hup': (['/bin/sh', '-c', 'kill -s HUP $$'], -1, -1),
    'term': (['/bin/sh', '-c', 'kill -s TERM $$'], -15, -15),
    'kill': (['/bin/sh', '-c', 'kill -s KILL $$'], -9, -9),
    'script-kill': ('echo before; kill -s TERM $$', -15, -15),
    'exit-3': (['/bin/sh', '-c', 'exit 3'], 3, 3),
    'exit-130': (['/bin/sh', '-c', 'exit 130'], -2, -2),
    'exit-200': (['/bin/sh', '-c', 'exit 200'], 200, 200),
    'exit-255': (['/bin/sh', '-c', 'exit 255'], 255, 255),
}


@pytest.mark.integration
@pytest.mark.parametrize('status', STATUSES)
@pytest.mark.parametrize('wire', ['local', 'ssh-sh', 'ssh-bash', 'ssh-zsh', 'ssh-tcsh'])
def test_a_signal_death_is_reported_and_reraised(wire: str, status: str, home: Path, tmp_path: Path) -> None:
    command, want, own = STATUSES[status]
    prog, marks = launch.program(command, None, 'home')
    seen = tmp_path / 'status'
    argv = _environment(home) + [sys.executable, '-I', '-c', REPORTER, str(seen)] + _wire_argv(wire, prog)
    out, err, code, note = _sandboxed(argv, None, 20, marks.end)
    result = launch.separate(out, err, code, marks, note)
    stdout = b'before\n' if status == 'script-kill' else b''
    assert result == (stdout, b'', want), f'{wire}-{status}: the result is {result!r}, not exit code {want}'
    assert seen.read_text() == str(own), \
        f'{wire}-{status}: the launch itself ended with {seen.read_text()}, not {own}: the signal was not re-raised'


# Startup separation: planted startup files that read and print, against a quiet home.

def _both(tag: str) -> str:
    return f"printf '{tag}-out partial'; printf '{tag}-err partial' >&2\n"


def chatty_home(home: Path) -> Path:
    """Plant a startup file of every kind that prints to both streams, reads stdin and lingers."""
    bash_env = home / '.bash_env'
    files = {
        '.bash_profile': _both('bash_profile') + (
            "printf '\\377\\376binary\\0'; printf '\\377binary' >&2\n"
            'exec 8>/dev/null 9>>/dev/null\n'
            'set -x\n'
            "trap 'echo exit-trap; echo exit-trap >&2' EXIT\n"
            "trap 'echo debug-trap' DEBUG\n"
            "trap 'echo return-trap' RETURN\n"
            'printf() { builtin printf "shadowed %s" "$@"; }\n'
            'cd() { echo cd-function; builtin cd "$@"; }\n'
            'read -r line\n'
            '(sleep 0.2; echo late-out; echo late-err >&2) &\n'
            f'export BASH_ENV={shlex.quote(str(bash_env))}\n'
            "shopt -s expand_aliases; alias printf='echo aliased'; alias cd='echo aliased'\n"),
        '.bash_env': _both('bash_env') + (
            'read -r line\n'
            '(sleep 0.2; echo bash-env-late) &\n'
            f'. {shlex.quote(os.environ["BASH_ENV"])}\n'),
        '.bash_login': _both('bash_login'), '.profile': _both('profile'), '.bashrc': _both('bashrc'),
        '.bash_logout': _both('bash_logout'),
        '.zshenv': "head -c 1048576 /dev/zero | tr '\\0' z; print -u2 zshenv; print -n zshenv-partial\n",
        '.zprofile': 'print zprofile; print -u2 zprofile\n',
        '.cshrc': "echo cshrc; sh -c 'printf cshrc >&2'\n",
        '.tcshrc': "echo tcshrc; sh -c 'printf tcshrc >&2'; printf 'tcshrc-partial'\n",
        '.login': 'echo login\n',
    }
    for name, text in files.items():
        (home / name).write_text(text)
    return bash_env


SEPARATION_CASES = {
    'argv': (['printf', '%s\\0', 'a b', 'c'], None),
    'exit': ('echo out; echo err >&2; exit 4', None),
    'kill': ('echo before; kill $$', None),
    'stdin': (['cat'], b'line one\nline two\n\0\xff'),
    'empty': ('', None),
    # Long enough that a profile's background writer, if it had the caller's streams, would print
    # before the command's end.
    'slow': ('sleep 1; echo slow', None),
}


@pytest.mark.integration
@pytest.mark.parametrize('case', SEPARATION_CASES)
@pytest.mark.parametrize('wire', SEPARATION_WIRES)
def test_startup_output_is_separated(wire: str, case: str, tmp_path: Path) -> None:
    command, stdin = SEPARATION_CASES[case]
    quiet, chatty = (tmp_path / 'quiet').resolve(), (tmp_path / 'chatty').resolve()
    quiet.mkdir()
    chatty.mkdir()
    bash_env = chatty_home(chatty)
    # The server's own environment reaches a local launch; an SSH login shell starts without it.
    env = (f'BASH_ENV={bash_env}',) if wire == 'local' else ()
    want = run_on(wire, command, quiet, stdin=stdin)
    got = run_on(wire, command, chatty, stdin=stdin, env=env)
    assert got == want, f'{wire}-{case}: the result differs from the quiet home: {got!r} != {want!r}'


# A profile function a script can reach only through the launcher's function export.
GREET = 'greet() { echo greeted; }\n'

HIJACKS = {
    # Aliases on every word of the prologue, live while bash reads the program's first line.
    'alias': ("shopt -s expand_aliases\n"
              "alias unalias='echo aliased' unset='echo aliased' set='echo aliased' trap='echo aliased' "
              "exec='echo aliased' printf='echo aliased' command='echo aliased'\n"),
    # A function over a word the program uses after the prologue.
    'read-function': 'read() { return 1; }\n',
}


@pytest.mark.integration
@pytest.mark.parametrize('hijack', HIJACKS)
@pytest.mark.parametrize('wire', ['local', 'ssh-bash'])
def test_profile_cannot_hijack_the_prologue(wire: str, hijack: str, tmp_path: Path) -> None:
    quiet, planted = (tmp_path / 'quiet').resolve(), (tmp_path / 'planted').resolve()
    quiet.mkdir()
    planted.mkdir()
    (quiet / '.bash_profile').write_text(GREET)
    (planted / '.bash_profile').write_text(GREET + HIJACKS[hijack])
    # `greet` comes from the profile, so it reaches the script only through the launcher's export.
    script = 'greet; echo err >&2; exit 4'
    want = run_on(wire, script, quiet)
    got = run_on(wire, script, planted)
    assert want == (b'greeted\n', b'err\n', 4), want
    assert got == want, f'{wire}-{hijack}: the result differs from the quiet home: {got!r} != {want!r}'


# Shell state a profile can leave behind, and a command whose result would show it if anything the
# launcher does after the login initialisation depended on it: (profile text, command, start, the
# quiet home's result). Both homes define `greet`, which a script reaches only through the
# launcher's function export.
STATES = {
    # Word splitting: the function export must not split on a profile's IFS.
    'ifs-colon': ('IFS=:\nml() { :; }\n', 'greet; echo err >&2; exit 4', None, (b'greeted\n', b'err\n', 4)),
    'ifs-empty': ('IFS=\nml() { :; }\n', 'greet; echo err >&2; exit 4', None, (b'greeted\n', b'err\n', 4)),
    # Pathname expansion: a function whose name is a pattern, with every glob option a profile can set.
    'glob-options': ("shopt -s failglob nullglob extglob dotglob nocaseglob; GLOBIGNORE='*'\n"
                     "mod?x() { :; }\n", 'greet; echo err >&2; exit 4', None, (b'greeted\n', b'err\n', 4)),
    # `set -k`: an argument shaped like an assignment would become one for the command's environment.
    'keyword': ('set -k\n', ['printf', '%s\\n', 'b=c', 'x'], None, (b'b=c\nx\n', b'', 0)),
    # `set -P`: the start directory would be named through its physical path. `pwd -L` prints the
    # `$PWD` the launcher leaves (what a fixed script such as `list_directory`'s reads); a bare `pwd`
    # is `-P` in GNU coreutils and `-L` in BSD, so it would name the physical path on Linux anyway.
    'physical': ('set -P\n', ['pwd', '-L'], 'home', None),
    # CDPATH: a relative cwd must stay relative to the start, silently.
    'cdpath': ('export CDPATH=/:/tmp\n', ['pwd', '-L'], 'home', None),
    # POSIXLY_CORRECT: the launcher's own lines run outside POSIX mode.
    'posixly-correct': ('export POSIXLY_CORRECT=1\n', 'greet; echo err >&2; exit 4', None, (b'greeted\n', b'err\n', 4)),
}


@pytest.mark.integration
@pytest.mark.parametrize('state', STATES)
@pytest.mark.parametrize('wire', ['local', 'ssh-bash', 'ssh-zsh'])
def test_profile_state_does_not_reach_the_launcher(wire: str, state: str, tmp_path: Path) -> None:
    text, command, start, known = STATES[state]
    results = []
    for name, profile in (('quiet', GREET), ('planted', GREET + text)):
        real = (tmp_path / f'{name}-real').resolve()
        real.mkdir()
        (real / 'sub').mkdir()
        (real / '.bash_profile').write_text(profile)
        # The home is reached through a symlink, so a physical start would name it differently.
        home = tmp_path / name
        home.symlink_to(real)
        result = run_on(wire, command, home, start=start, cwd='sub' if state == 'cdpath' else None)
        results.append(result)
    want, got = results
    if known is None:
        known = (f'{tmp_path / "quiet"}{"/sub" if state == "cdpath" else ""}\n'.encode(), b'', 0)
    assert want == known, f'{wire}-{state}: the quiet home returned {want!r}, not {known!r}'
    stated = (want[0].replace(b'/quiet', b'/planted'), want[1], want[2])
    assert got == stated, f'{wire}-{state}: the profile\'s state reached the launcher: {got!r} != {stated!r}'


# An sshd-like environment: SSH_CLIENT set (as sshd sets it), and each non-login bash reached by
# `exec` from the shell before it. A bash built to read ~/.bashrc for a shell sshd started (RHEL's
# and macOS's are) reads it there unless told not to: no stage, script or fixed script may.
BASHRC_MARKER = 'echo read >> "$HOME/bashrc-read"\n'
SSHD_ENV = ('SSH_CLIENT=127.0.0.1 50000 22', 'SSH_CONNECTION=127.0.0.1 50000 127.0.0.1 22')


@pytest.mark.integration
@pytest.mark.parametrize('form', ['argv', 'script', 'fixed-script'])
@pytest.mark.parametrize('wire', ['local', 'ssh-sh', 'ssh-bash', 'ssh-zsh'])
def test_no_stage_reads_a_startup_file_under_sshd(wire: str, form: str, home: Path) -> None:
    (home / '.bashrc').write_text(BASHRC_MARKER)
    commands = {
        'argv': (['printf', '%s', 'ran'], None),
        'script': ('printf %s ran', None),
        'fixed-script': (launch.write_argv('~/written'), b'ran'),
    }
    command, stdin = commands[form]
    result = run_on(wire, command, home, start='home', stdin=stdin, env=SSHD_ENV)
    want_out = b'' if form == 'fixed-script' else b'ran'
    reads = (home / 'bashrc-read').read_text().count('read') if (home / 'bashrc-read').exists() else 0
    # Only a bash login shell reads ~/.bashrc itself, before the bootstrap, as it would under sshd.
    own = 1 if wire == 'ssh-bash' else 0
    assert result == (want_out, b'', 0), f'{wire}-{form}: {result!r}'
    assert reads == own, f'{wire}-{form}: ~/.bashrc was read {reads} times, not {own}: a stage read a startup file'


@pytest.mark.integration
@pytest.mark.parametrize('wire', ['local', 'ssh-zsh'])
def test_a_profile_that_exits_is_not_started(wire: str, home: Path, caplog: pytest.LogCaptureFixture) -> None:
    (home / '.bash_profile').write_text('echo leaving >&2; exit 0\n')
    with caplog.at_level(logging.WARNING, logger='rcac_mcp.executor.launch'):
        result = run_on(wire, 'echo ran', home)
    note = b'cluster-mcp: the command did not start (exit 0); the server log has the details\n'
    assert result == (b'', note, NOT_STARTED_EXIT_CODE), f'{wire}: the command did not start, but the result is {result!r}'
    assert any('did not start (exit 0)' in r.getMessage() for r in caplog.records), caplog.records


@pytest.mark.integration
@pytest.mark.parametrize('wire', ['local', 'ssh-tcsh'])
def test_a_profile_that_hangs_is_a_timeout(wire: str, home: Path) -> None:
    (home / '.bash_profile').write_text('echo starting; sleep 8\n')
    result = run_on(wire, 'echo ran', home, timeout=1)
    assert result == (b'', b'', TIMEOUT_EXIT_CODE), f'{wire}: a hanging profile returned {result!r}'


REDIRECTED = "cluster-mcp: the login initialisation redirected the command's {}; it was not captured\n"


@pytest.mark.integration
@pytest.mark.parametrize(('redirect', 'want'), [
    pytest.param('exec >/dev/null', (b'', b'err\n' + REDIRECTED.format('stdout').encode(), 3), id='stdout'),
    pytest.param('exec 2>/dev/null', (b'out\n', REDIRECTED.format('stderr').encode(), 3), id='stderr'),
])
@pytest.mark.parametrize(('wire', 'rc'), [('ssh-zsh', '.zshenv'), ('ssh-bash', '.bashrc')])
def test_a_redirected_stream_is_reported(wire: str, rc: str, redirect: str, want: tuple[bytes, bytes, int],
                                         home: Path) -> None:
    # The remote login shell's own rc file sends one stream away before the bootstrap starts: zsh's
    # .zshenv, or the .bashrc bash reads for a shell sshd started (SSH_CLIENT set).
    (home / rc).write_text(redirect + '\n')
    result = run_on(wire, 'echo out; echo err >&2; exit 3', home, env=SSHD_ENV)
    assert result == want, f'{wire}: a redirected stream was lost without a note: {result!r}'


# A command whose result shows whether it ran: it leaves a file, copies its stdin to stdout, prints
# on stderr and exits 3.
RAN = 'touch "$HOME/ran"; cat; echo err >&2; exit 3'
# How the launch's stage parks each descriptor.
PARKED = {'57': '57<&0', '58': '58>&1', '59': '59>&2', '47': '47<&0', '48': '48>&1', '49': '49>&2'}


@pytest.mark.integration
@pytest.mark.parametrize('fd', PARKED)
def test_a_taken_parked_descriptor_runs_nothing(fd: str, home: Path) -> None:
    # The launch's own stage, with one parked descriptor naming another file: what a startup file
    # that takes it leaves behind, on any bash (one that keeps a profile's `exec 58>…` or not).
    assert launch.STAGE.count(PARKED[fd]) == 1, f'descriptor {fd} is not parked by the stage'
    stage = launch.STAGE.replace(PARKED[fd], f'{fd}<>"$2"')
    prog, marks = launch.program(RAN)
    argv = [*launch.POSIX_STAGE, '-c', stage, 'cluster-mcp', prog, str(home / 'elsewhere')]
    out, err, code, note = _sandboxed(_environment(home) + argv, b'out\n', end=marks.end)
    result = launch.separate(out, err, code, marks, note)
    assert result == (b'', TAKEN_NOTE, NOT_STARTED_EXIT_CODE), f'descriptor {fd} taken: the result is {result!r}'
    assert not (home / 'ran').exists(), f'descriptor {fd} taken: the command ran'


def _keeps_a_profile_takeover(tmp_path: Path) -> bool:
    """Whether this /bin/bash lets a login profile's `exec 58>file` replace an open descriptor 58."""
    probe = (tmp_path / 'takeover-probe').resolve()
    probe.mkdir()
    (probe / '.bash_profile').write_text('exec 58>"$HOME/kept"\n')
    stage = 'exec 58>/dev/null && exec /bin/bash -l -c "echo kept >&58"'
    subprocess.run([str(SANDBOX), 'env', '-i', f'HOME={probe}', f'PATH={SYSTEM_PATH}', '/bin/bash', '--norc',
                    '--posix', '-c', stage], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    return (probe / 'kept').exists() and (probe / 'kept').read_bytes() == b'kept\n'


@pytest.mark.integration
@pytest.mark.parametrize('taken', ['streams', 'stdin'])
@pytest.mark.parametrize('wire', ['local', 'ssh-bash'])
def test_a_profile_taking_a_parked_descriptor_is_not_run(wire: str, taken: str, home: Path, tmp_path: Path) -> None:
    profile = {'streams': 'exec 58>"$HOME/taken" 59>"$HOME/taken too"\n', 'stdin': 'exec 57<"$HOME/decoy"\n'}
    (home / 'decoy').write_bytes(b'not the stdin sent\n')
    (home / '.bash_profile').write_text(profile[taken])
    result = run_on(wire, RAN, home, stdin=b'out\n')
    if _keeps_a_profile_takeover(tmp_path):
        # The descriptor was taken: the program runs nothing and says why.
        assert result == (b'', TAKEN_NOTE, NOT_STARTED_EXIT_CODE), f'{wire}-{taken}: the result is {result!r}'
        assert not (home / 'ran').exists(), f'{wire}-{taken}: reported as not started, but the command ran'
    else:
        # This bash kept the parked descriptor: the command runs with the caller's streams, whole.
        assert result == (b'out\n', b'err\n', 3), f'{wire}-{taken}: the result is {result!r}'
        assert (home / 'ran').exists(), f'{wire}-{taken}: the command did not run'


@pytest.mark.integration
def test_a_command_printing_its_own_sentinel_keeps_it(home: Path) -> None:
    prog, marks = launch.program(['cat'])
    sent = marks.start + b' and again ' + marks.start
    out, err, code, note = _sandboxed(_environment(home) + launch.bash_argv(prog), sent, end=marks.end)
    assert launch.separate(out, err, code, marks, note) == (sent, b'', 0)


# Text shaped like an end record: a fixed nonce, another call's, a record with no nonce at all.
RECORD_LOOKALIKES = (b'0' * 32 + b'x0.' + b'f' * 32 + b's9.' + b'deadbeef' * 4 + b'x1.\n'
                     + b'cluster-mcp-end x0.\n' + b'x0.s1.\n')


@pytest.mark.integration
@pytest.mark.parametrize('wire', ['local', 'ssh-sh', 'ssh-zsh'])
def test_a_command_printing_record_lookalikes_keeps_them(wire: str, home: Path) -> None:
    script = 'cat; cat >&2 <<EOF\n' + RECORD_LOOKALIKES.decode() + 'EOF\nexit 4'
    result = run_on(wire, script, home, stdin=RECORD_LOOKALIKES)
    assert result == (RECORD_LOOKALIKES, RECORD_LOOKALIKES, 4), \
        f'{wire}: output shaped like an end record cut the result: {result!r}'
