# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""One bash program per command, and the wire each backend hands it to.

Every command becomes one bash program run under the pinned interpreter, an explicit login bash, so
site initialisation (Lmod's `module`, the site's job tools on `PATH`) is the same whatever the
user's login shell or the server's `$SHELL`. An argv becomes `exec 'a' 'b' …` with every element
quoted as one word, so each value reaches the command as exactly one literal argument. A path marked
`UserPath` resolves a leading `~` or `~/` through `$HOME` inside the program, so it names the home of
the user the command runs as; nothing else in any value expands. A `run_command` script runs, quoted
as one word, in a fresh non-login bash that inherits the login environment and its functions.
Wherever a startup file `cd`s to, the program starts where the backend says: the running user's
home, or an absolute directory written into the program text. Nothing the launcher relies on after
the login initialisation rides on state a startup file can change: the login bash is given no
positional parameters (bash 4 and later bind them before the startup files run, which may rewrite
them); the prologue resets the shell state the program's own lines would read (`IFS`, `set -k`,
`set -P`, among others), and the function export splits and globs nothing; and every other bash the
launcher starts, the stages, a script's bash and a fixed script's, reads no startup file at all
(`--norc`: a bash built to read `~/.bashrc` for a shell sshd started does so for a non-login `-c`
shell otherwise). A fixed script also undoes the functions and shell options a profile can export
to it (`FIXED_RESET`).

Nothing the login initialisation reads or prints reaches a result. The launch parks the caller's
stdin, stdout and stderr on descriptors 57–59, with a copy on 47–49, and gives the login shell
`/dev/null` for all three; the program's first lines take them back and the next prints a per-call
sentinel on both streams. If a startup file took a parked descriptor, so that it no longer names
the same file as its copy, the program runs nothing and the result says so (`TAKEN_NOTE`).
`separate` drops each stream up to and including that sentinel, which also removes whatever a remote
login shell printed before the program could park anything. The one exception: over SSH, a
background job started by the remote login shell's own rc file, which runs before anything is
parked, can write into a result while the command runs.

A result is complete exactly when the command's end has been reported on both streams. The program
runs the command as a child (a subshell that `exec`s it), never in its own place, and once the
command has exited prints a per-call end record on stdout and on stderr: a second nonce, never whole
in the program text, then the command's status (`x<N>` for an exit status, `s<N>` for a signal
death), then a full stop. A backend stops reading a stream at its record, however slowly the output
before it arrives, and closes the stream there, so nothing a startup file or the command left
running can cut a result short or hold a call past its records. bash reports a signal death as
128+N; the program reads such a status of a terminating signal N as that signal, reports it so, and
re-raises it on itself, so the launch dies of the signal its command died of. Only a timeout bounds
a call whose records do not come.

`local` and `delegate` hand `bash_argv(program)` to `run_process`; `ssh` sends `ssh_command(program)`,
a fixed bootstrap whose only variable part is one base64 word, so the remote login shell (sh, bash,
zsh, ksh, csh or tcsh) parses no byte of the command.
"""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Final, NamedTuple

# Standard libs
import base64
import logging
import math
import os
import re
import secrets
import selectors
import shlex
import signal
import subprocess
import time

# Internal libs
from rcac_mcp.executor.base import NOT_STARTED_EXIT_CODE, TIMEOUT_EXIT_CODE, UserPath

# Public interface
__all__ = ['BASH', 'BASH_ARGS', 'GRACE', 'MAX_ARGUMENT', 'FIXED_RESET', 'LATE_NOTE', 'UNFINISHED_NOTE', 'Marks',
           'program', 'record', 'separate', 'bash_argv', 'ssh_command', 'fixed_argv', 'write_argv', 'check_timeout',
           'run_process', 'decode', 'expand_local', 'word']

log = logging.getLogger(__name__)

# The pinned interpreter. Absolute, so `sudo`'s secure_path cannot pick another; a login shell,
# because only a login bash gives the same module state whatever shell launched it.
BASH: Final = '/bin/bash'
BASH_ARGS: Final = ('-l',)

# The bound on the drain after each of SIGTERM and SIGKILL at a timeout, and on the wait for a
# launch to exit once it has reported its command's end (it exits at once).
GRACE: Final = 1.0

# The options before `-c` of every non-login bash the launcher starts or a fixed script runs in.
# `--norc`: no startup file, even where bash reads ~/.bashrc for a shell it believes sshd or rshd
# started (SSH_CLIENT or SSH2_CLIENT set, or a socket on stdin). `--posix` (a stage or a fixed
# script): no BASH_ENV either.
NORC: Final = '--norc'
POSIX_STAGE: Final = (BASH, NORC, '--posix')

# Seconds between checks that a local launch has exited, while its output has not ended.
POLL: Final = 0.05

# A read from a pipe, per call.
CHUNK: Final = 65536

# Linux's limit on one argv element, its terminating NUL included (MAX_ARG_STRLEN). A local or
# delegated launch passes the program as one element; the SSH wire passes the bootstrap and the
# base64 program as the one argument after the remote login shell's `-c`.
MAX_ARGUMENT: Final = 131_072

# The longest timeout accepted, in seconds.
MAX_TIMEOUT: Final = 10_000_000.0

# A UserPath's leading `~`, resolved by the running bash; an unset or empty HOME fails the command.
HOME_WORD: Final = '"${HOME:?HOME is not set}"'

# The launch's parking step: the caller's streams move to 57–59 (high, so a startup file's own
# `exec 8>…` cannot take them), with a second copy on 47–49 that the program checks them against,
# and the login shell gets /dev/null for all three.
PARK: Final = 'exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 </dev/null >/dev/null 2>&1'

# What the program prints, in place of its sentinel, when the login initialisation took a parked
# descriptor (`TAKEN_NOTE` replaces it in the result).
TAKEN_MARK: Final = b'cluster-mcp:parked-descriptor-taken'

# True when each parked descriptor still names the same file as its copy. The prologue asks up to
# three times. macOS's /dev/fd shares one node per descriptor number among all processes, so a stat
# of /dev/fd/57 while another process stats its own 57 can fail with EBADF; every launch parks on the
# same numbers, so concurrent launches collide now and then. Such a failure only ever reads as "not
# the same", never the reverse, and the next ask passes; a taken descriptor fails every time.
# (Linux reads /proc/self/fd, which is per process.)
PARKED_SAME: Final = '[[ /dev/fd/57 -ef /dev/fd/47 && /dev/fd/58 -ef /dev/fd/48 && /dev/fd/59 -ef /dev/fd/49 ]]'

# The program's first two lines: undo what a profile can leave behind (aliases, functions shadowing
# the words used here, the field separator, shell options: `-k` would turn an argument shaped like an
# assignment into one, `-P` would name the start directory by its physical path; traps), then take
# the caller's streams back. Anything those commands print still goes to /dev/null. Bash reads a
# whole line before running any of it, so the alias reset is a line of its own (quoted, so no alias
# reaches it): an alias on any later word of a shared line would already have been expanded. A
# startup file that took a parked descriptor (bash 5 lets a profile's `exec 58>file` replace it; the
# 3.2 measured leaves an open descriptor above 9 as it was) leaves it naming another file than its
# copy: the program then runs nothing, leaves `TAKEN_MARK` on whichever stderr is still the
# caller's, and exits, so the command is reported as not started, which it was not, rather than run
# with its streams lost or its stdin replaced.
PROLOGUE: Final = ('\\unalias -a\n'
                   'unset -f builtin command exec set printf trap cd export declare compgen read kill ulimit exit; '
                   'unset IFS; '
                   'set +euxvTkP +o errtrace +o posix; trap - EXIT ERR DEBUG RETURN; '
                   f'{PARKED_SAME} || {PARKED_SAME} || {PARKED_SAME} || '
                   f'{{ printf %s {TAKEN_MARK.decode()} >&49; '
                   f'printf %s {TAKEN_MARK.decode()} >&59; exec /usr/bin/false; }}; '
                   'exec 0<&57 1>&58 2>&59 47<&- 48>&- 49>&- 57<&- 58>&- 59>&-')

# A script runs in a fresh non-login bash: it inherits the login environment and every function
# (`module`), exported one name per line, so no field separator or glob option touches the list; its
# `exit` never runs ~/.bash_logout, its `$$` is its own, no startup file runs again, and an empty or
# comment-only script is valid.
SCRIPT: Final = ('while IFS= read -r cluster_mcp_fn; do export -f -- "$cluster_mcp_fn"; '
                 'done < <(compgen -A function); unset cluster_mcp_fn; '
                 f'BASH_ENV= ENV= exec {BASH} {NORC} -c -- {{script}}')

# After the start sentinel, the program keeps the caller's stderr on 59 and gives its own lines
# /dev/null: bash reports a child killed by a signal on its own stderr ("line 4: 123 Hangup …"),
# which is not the command's output. The command's subshell gets the caller's stderr back.
QUIET: Final = 'exec 59>&2 2>/dev/null'

# The command runs in a subshell, which changes to its start and `exec`s the command, so the
# program outlives it to report its end. A failed `cd`, or a `~` with HOME unset, ends only the
# subshell, with its status (`exit` in a subshell never runs ~/.bash_logout).
SUBSHELL: Final = ('(', ') 2>&59 59>&-')
CD_LINE: Final = 'command cd -- {word} || exit'

# The signals whose default action ends a process (a stop, a continue or an ignored signal is none
# of them, and a few platform-specific ones are left out), by the names `kill -l` gives a status of
# 128+N. A command status of 128+N for one of these is reported, and re-raised, as that signal.
TERMINATING: Final = ('HUP', 'INT', 'QUIT', 'ILL', 'TRAP', 'ABRT', 'BUS', 'FPE', 'KILL', 'USR1', 'SEGV', 'USR2',
                      'PIPE', 'ALRM', 'TERM', 'XCPU', 'XFSZ', 'VTALRM', 'PROF', 'SYS', 'RTMIN*', 'RTMAX*')

# The program's last lines: the command's status and what it means; its end record on both streams
# (`record_line`); the signal re-raised (no core file, no trap); and, if the shell is still alive,
# the command's own status as the program's.
STATUS: Final = ('cluster_mcp_s=$? cluster_mcp_k=\n'
                 '(( cluster_mcp_s > 128 )) && cluster_mcp_k=$(kill -l "$cluster_mcp_s")\n'
                 f'case $cluster_mcp_k in {"|".join(TERMINATING)}) cluster_mcp_r=s$((cluster_mcp_s - 128)) ;; '
                 '*) cluster_mcp_k= cluster_mcp_r=x$cluster_mcp_s ;; esac')
RERAISE: Final = ('[[ -z $cluster_mcp_k ]] || { ulimit -c 0; trap - "$cluster_mcp_k"; kill -s "$cluster_mcp_k" "$$"; }\n'
                  '(exit "$cluster_mcp_s")')

# What follows the end nonce in a record: the kind and the number, then a full stop.
RECORD_TAIL: Final = re.compile(rb'([xs])([0-9]{1,3})\.')

# The first stage of a local or delegated launch: a POSIX-mode bash that reads no startup file
# (`POSIX_STAGE`), parks the streams and starts the login bash on the program in "$1". The login
# bash gets no positional parameters: a startup file could rewrite them.
STAGE: Final = f'{PARK} && exec {BASH} {" ".join(BASH_ARGS)} -c -- "$1"'

# The start line for the running user's home; any other start is an absolute path, quoted.
START_HOME: Final = CD_LINE.format(word=HOME_WORD)

# The SSH exec string, ahead of one base64 word. It holds no `!`, backslash or newline, so every
# login shell parses it the same. `&& printf .` keeps the program's trailing newlines, which `$( )`
# would strip, and fails the launch when decoding fails instead of running an empty program.
BOOTSTRAP: Final = (f"exec {' '.join(POSIX_STAGE)} -c 'p=$(printf %s \"$1\" | base64 -d && printf .) && "
                    f"{PARK} && exec {BASH} {' '.join(BASH_ARGS)} -c -- \"${{p%.}}\"' cluster-mcp ")

# What a fixed script (a constant script a tool or a backend runs, every value a positional
# parameter) undoes before it runs: functions a profile exported over the programs fixed scripts
# run, and options it exported in SHELLOPTS (`noclobber` would refuse an overwrite, `pipefail` would
# change a pipeline's status). `unset` and `set` are POSIX special builtins, which no function can
# replace in a POSIX-mode bash.
FIXED_RESET: Final = 'unset -f cat cd chmod cp find mkdir mv printf sort; set +C +o pipefail\n'

# Write stdin to "$1" exactly, overwriting or appending, after creating the directory "$2" when
# asked; then, when asked, make it executable. `--` comes before a mode: BSD chmod stops reading
# options at the mode, so a `--` after it would be taken as a file. The directory is the caller's,
# never `$(dirname …)`, which strips a trailing newline from a directory name.
WRITE_MKDIR: Final = 'mkdir -p -- "$2" && '
WRITE_OVER: Final = 'cat >"$1"'
WRITE_APPEND: Final = 'cat >>"$1"'
WRITE_EXECUTABLE: Final = ' && chmod -- +x "$1"'

NOT_STARTED_NOTE: Final = 'cluster-mcp: the command did not start (exit {code}); the server log has the details\n'
TAKEN_NOTE: Final = ('cluster-mcp: the command did not start: the login initialisation took a descriptor its '
                     'streams were parked on, so it was not run; the server log has the details\n')
REDIRECTED_NOTE: Final = ("cluster-mcp: the login initialisation redirected the command's {stream}; "
                          'it was not captured\n')
UNFINISHED_NOTE: Final = ("cluster-mcp: the launch ended before it reported the command's end, so its output "
                          'may be incomplete\n').encode()
LATE_NOTE: Final = ('cluster-mcp: the command exited {code}, but not all of its output had arrived at the '
                    'timeout\n')


class Marks(NamedTuple):
    """The per-call nonces a program prints: `start` before the command, `end` in its end record."""

    start: bytes
    end: bytes


def _checked(value: object, what: str) -> str:
    """`value` as text a command can carry, or a TypeError/ValueError naming `what`."""
    if not isinstance(value, str):
        raise TypeError(f'{what} must be a str, not {type(value).__name__}')
    if '\0' in value:
        raise ValueError(f'{what} contains a NUL character')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(f'{what} is not valid UTF-8 text') from None
    return value


def word(arg: str) -> str:
    """One argument as one bash word that expands to exactly one literal value.

    A `UserPath` equal to `~`, or starting `~/`, resolves through `$HOME` (leading slashes after
    `~/` collapse, so the path stays in the home); any other relative `UserPath` gains `./`, so it
    can never start with `-` or be looked up through `CDPATH`. Everything else is quoted literally.
    """
    if isinstance(arg, UserPath):
        if arg == '~':
            return HOME_WORD
        if arg.startswith('~/'):
            rest = arg[2:].lstrip('/')
            return f'{HOME_WORD}/{shlex.quote(rest)}' if rest else HOME_WORD
        if arg and not arg.startswith('/'):
            return shlex.quote('./' + arg)
    return shlex.quote(str(arg))


def _halves(nonce: str) -> str:
    """A nonce as the two words a `printf %s%s` joins, so the whole of it never appears in the text."""
    return f'{nonce[:16]} {nonce[16:]}'


def record_line(end: str) -> str:
    """The line that prints the end record for the end nonce `end` on both streams."""
    return (f'printf %s%s%s. {_halves(end)} "$cluster_mcp_r"; '
            f'printf %s%s%s. {_halves(end)} "$cluster_mcp_r" >&59')


def program(command: Sequence[str] | str, cwd: str | None = None,
            start: str | None = None) -> tuple[str, Marks]:
    """The bash program for `command`, and the nonces `separate` reads its output by.

    `command` is an argv (a sequence of `str`, each element one literal argument) or a script
    (a `str`, which only `run_command` passes). `cwd` is path-like: a leading `~`/`~/` is the
    running user's home. `start` is where the program first changes to, after the login
    initialisation and whatever it `cd`s to: `'home'` (`$HOME`, so a relative path means the running
    user's home) or an absolute directory, written into the program as one quoted word (the local
    backend's working directory); None stays put.
    """
    if isinstance(command, UserPath):
        raise TypeError('command must be an argv list or a script str, not a UserPath')
    lines = []
    if start == 'home':
        lines.append(START_HOME)
    elif start is not None:
        if not _checked(start, 'start').startswith('/'):
            raise ValueError(f"start must be 'home' or an absolute directory, not {start!r}")
        lines.append(CD_LINE.format(word=shlex.quote(start)))
    if cwd is not None and _checked(cwd, 'cwd'):
        lines.append(CD_LINE.format(word=word(UserPath(cwd))))
    if isinstance(command, str):
        lines.append(SCRIPT.format(script=shlex.quote(_checked(command, 'command'))))
    elif isinstance(command, Sequence) and not isinstance(command, (bytes, bytearray)):
        if not command:
            raise ValueError('command is an empty argv')
        lines.append('exec ' + ' '.join(word(_checked(arg, f'argument {i}')) for i, arg in enumerate(command)))
    else:
        raise TypeError(f'command must be an argv list or a script str, not {type(command).__name__}')
    # Each nonce is printed in two halves, so the whole of it never appears in the program text.
    start_nonce, end_nonce = secrets.token_hex(16), secrets.token_hex(16)
    text = '\n'.join([PROLOGUE,
                      f'printf %s%s {_halves(start_nonce)}; printf %s%s {_halves(start_nonce)} >&2; {QUIET}',
                      SUBSHELL[0], *lines, SUBSHELL[1], STATUS, record_line(end_nonce), RERAISE])
    return text, Marks(start_nonce.encode('ascii'), end_nonce.encode('ascii'))


def _fits(argument: str, what: str) -> str:
    """`argument`, or a ValueError when it is over `MAX_ARGUMENT` with its terminating NUL."""
    size = len(argument.encode('utf-8')) + 1
    if size > MAX_ARGUMENT:
        raise ValueError(f'command is too long: {what} is {size} bytes with its terminating NUL, over the '
                         f'{MAX_ARGUMENT}-byte per-argument limit')
    return argument


def record(data: bytes | bytearray, end: bytes, at: int = 0) -> re.Match[bytes] | None:
    """The first complete end record in `data` from `at` on: its tail (status and full stop) matched."""
    while (found := data.find(end, at)) >= 0:
        tail = RECORD_TAIL.match(data, found + len(end))
        if tail:
            return tail
        at = found + 1
    return None


def _status(tail: re.Match[bytes]) -> int:
    """A record's status as an exit code: `x<N>` is N, `s<N>` (a signal death) is -N."""
    kind, number = tail[1], int(tail[2])
    return -number if kind == b's' else number


def _after(data: bytes, sentinel: bytes) -> bytes | None:
    at = data.find(sentinel)
    return None if at < 0 else data[at + len(sentinel):]


def separate(out: bytes, err: bytes, code: int, marks: Marks, note: bytes = b'') -> tuple[bytes, bytes, int]:
    """The command's own (stdout, stderr, exit code): what each stream carries between its two marks.

    Each stream is cut after the first occurrence of the start sentinel and before the end record
    that follows it, and the exit code is the record's. With no start sentinel on either stream the
    command never started: a timeout stays an empty timeout; anything else becomes
    `NOT_STARTED_EXIT_CODE` with a fixed note on stderr (the dropped stderr goes to the server log),
    `TAKEN_NOTE` when the program refused to run because a parked descriptor was taken. A start
    sentinel on one stream only means the login initialisation redirected the other: that stream is
    empty and a note naming it is appended to stderr. A timeout (`code` is `TIMEOUT_EXIT_CODE`) stays
    a timeout whatever arrived, with the output read so far. A call that ended without a record (the
    launch died before its command's end) keeps `code`, the launch's own status, and says so
    (`UNFINISHED_NOTE`). `note`, the wire's own word on how the read ended (`LATE_NOTE`), always
    ends stderr.
    """
    if not marks.start or not marks.end:
        raise ValueError('a nonce is empty')
    kept_out, kept_err = _after(out, marks.start), _after(err, marks.start)
    if kept_out is None and kept_err is None:
        if code == TIMEOUT_EXIT_CODE:
            return b'', note, code
        log.warning('command did not start (exit %s); its stderr ended: %r', code, err[-4096:])
        if TAKEN_MARK in err:
            return b'', TAKEN_NOTE.encode() + note, NOT_STARTED_EXIT_CODE
        return b'', NOT_STARTED_NOTE.format(code=code).encode() + note, NOT_STARTED_EXIT_CODE
    streams: list[bytes] = []
    statuses: list[int | None] = []
    notes = b''
    for stream, kept in (('stdout', kept_out), ('stderr', kept_err)):
        if kept is None:
            log.warning('the login initialisation redirected the command\'s %s (exit %s)', stream, code)
            notes += REDIRECTED_NOTE.format(stream=stream).encode()
            streams.append(b'')
            continue
        tail = record(kept, marks.end)
        if tail is None:
            streams.append(kept)
            statuses.append(None)
        else:
            streams.append(kept[:tail.start() - len(marks.end)])
            statuses.append(_status(tail))
    if code != TIMEOUT_EXIT_CODE:
        if None in statuses:
            log.warning('the launch ended before it reported the command\'s end (exit %s)', code)
            notes += UNFINISHED_NOTE
        reported = [status for status in statuses if status is not None]
        if reported:
            code = reported[0]
    return streams[0], streams[1] + notes + note, code


def bash_argv(prog: str) -> list[str]:
    """The argv that runs `prog` under the pinned interpreter (local and delegate); `prog` is last.

    A ValueError names the per-argument limit when `prog` is over it.
    """
    _fits(prog, 'its program')
    return [*POSIX_STAGE, '-c', STAGE, 'cluster-mcp', prog]


def ssh_command(prog: str) -> str:
    """The exec string an SSH backend sends: the fixed bootstrap and `prog` as one base64 word.

    A ValueError names the per-argument limit when the exec string is over it.
    """
    wire = BOOTSTRAP + base64.b64encode(prog.encode('utf-8')).decode('ascii')
    return _fits(wire, 'its SSH exec string')


def fixed_argv(script: str, name: str, *values: str) -> list[str]:
    """The argv that runs the constant `script` as `name`, with `values` as its positional parameters.

    A POSIX-mode bash that reads no startup file runs `FIXED_RESET`, then the script; no value is
    ever part of the script text.
    """
    return [*POSIX_STAGE, '-c', FIXED_RESET + script, name, *values]


def write_argv(path: str, append: bool = False, parent: str | None = None, executable: bool = False) -> list[str]:
    """The fixed-script argv that writes its stdin to `path` exactly (local and delegate).

    `path`, and `parent` (a directory to create first), are command-side paths: a leading `~` or
    `~/` is the running user's home.
    """
    script = (WRITE_MKDIR if parent else '') + (WRITE_APPEND if append else WRITE_OVER)
    script += WRITE_EXECUTABLE if executable else ''
    values = [UserPath(path)] + ([UserPath(parent)] if parent else [])
    return fixed_argv(script, 'cluster-mcp-write', *values)


def check_timeout(timeout: float | None) -> float | None:
    """`timeout` as seconds, or a ValueError before anything starts: finite, over 0, at most 10⁷."""
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f'timeout must be a number of seconds, not {type(timeout).__name__}')
    if not (math.isfinite(timeout) and 0 < timeout <= MAX_TIMEOUT):
        raise ValueError(f'timeout must be a number of seconds over 0 and at most {MAX_TIMEOUT:g}, got {timeout!r}')
    return float(timeout)


class _Pipes:
    """A launch's three pipes: stdin fed from `data`, stdout and stderr read without blocking.

    A stream is done at its end of file or, given the end nonce, once its end record has arrived.
    """

    def __init__(self, proc: subprocess.Popen, data: bytes, end: bytes | None) -> None:
        self.proc, self.end = proc, end
        self.bufs = {proc.stdout: bytearray(), proc.stderr: bytearray()}
        self.scanned = {proc.stdout: 0, proc.stderr: 0}
        self.done = {proc.stdout: False, proc.stderr: False}
        self.recorded = False
        self.selector = selectors.DefaultSelector()
        for stream in self.bufs:
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ)
        self.data, self.sent = memoryview(data), 0
        if data:
            os.set_blocking(proc.stdin.fileno(), False)
            self.selector.register(proc.stdin, selectors.EVENT_WRITE)
        else:
            self._close_stdin()

    @property
    def out(self) -> bytes:
        return bytes(self.bufs[self.proc.stdout])

    @property
    def err(self) -> bytes:
        return bytes(self.bufs[self.proc.stderr])

    def complete(self) -> bool:
        return all(self.done.values())

    def eof(self) -> bool:
        return not any(key.fileobj in self.bufs for key in self.selector.get_map().values())

    def _close_stdin(self) -> None:
        stdin = self.proc.stdin
        if stdin is None or stdin.closed:
            return
        if stdin in [key.fileobj for key in self.selector.get_map().values()]:
            self.selector.unregister(stdin)
        try:
            stdin.close()
        except OSError:
            pass

    def _done(self, stream: object) -> None:
        self.done[stream] = True
        if stream in [key.fileobj for key in self.selector.get_map().values()]:
            self.selector.unregister(stream)

    def _read(self, stream: object) -> None:
        """Read what `stream` holds now, until it would block or ends; note its end record."""
        buf = self.bufs[stream]
        while True:
            try:
                chunk = os.read(stream.fileno(), CHUNK)
            except BlockingIOError:
                break
            if not chunk:
                self._done(stream)
                return
            buf += chunk
        if self.end is not None and not self.done[stream]:
            if record(buf, self.end, self.scanned[stream]) is not None:
                self.recorded = True
                self._done(stream)
            else:
                self.scanned[stream] = max(0, len(buf) - len(self.end) - 5)

    def _write(self) -> None:
        try:
            self.sent += os.write(self.proc.stdin.fileno(), self.data[self.sent:self.sent + CHUNK])
        except BlockingIOError:
            return
        except OSError:
            # The command closed its stdin, or exited: the rest has no reader.
            self.sent = len(self.data)
        if self.sent >= len(self.data):
            self._close_stdin()

    def wait(self, timeout: float) -> None:
        """Wait at most `timeout` seconds for a pipe to be ready, and serve every one that is."""
        if not self.selector.get_map():
            time.sleep(timeout)
            return
        for key, _ in self.selector.select(timeout):
            if key.fileobj is self.proc.stdin:
                self._write()
            elif not self.done[key.fileobj]:
                self._read(key.fileobj)

    def drain(self) -> None:
        """Read everything the pipes hold now, without waiting: all that an exited launch wrote."""
        self._close_stdin()
        for stream in self.bufs:
            if not self.done[stream]:
                self._read(stream)

    def read_until(self, until: float) -> None:
        """Read until both streams have ended, or until the monotonic time `until`."""
        self._close_stdin()
        while not self.eof() and (left := until - time.monotonic()) > 0:
            self.wait(left)

    def close(self) -> None:
        self._close_stdin()
        self.selector.close()
        for stream in self.bufs:
            stream.close()


def _reaped(proc: subprocess.Popen) -> int:
    """The status of a launch that has reported its command's end, which it exits right after; -1 if
    it has not exited within `GRACE` (the record, not this, is the command's status)."""
    try:
        return proc.wait(timeout=GRACE)
    except subprocess.TimeoutExpired:
        return -1


def run_process(argv: list[str], stdin: bytes | None, timeout: float | None,
                end: bytes | None = None) -> tuple[bytes, bytes, int, bytes]:
    """Run `argv` in a new session, feeding `stdin` (empty when None); return (stdout, stderr, code, note).

    The child never inherits this process's stdin. Given the program's end nonce `end`, the call is
    complete once each stream has carried its end record (or ended), however long that takes: the
    pipes are then closed, so nothing left running holds the call, nothing is signalled, and the code
    is the launch's own status. A launch that exits first is complete at its exit, with everything
    it wrote, since an exited process has nothing more to write; one that closes its output early
    is waited for. At the deadline, a call still incomplete gets `TIMEOUT_EXIT_CODE`: the whole
    process group gets SIGTERM, then SIGKILL, each followed by a bounded drain, and the output
    captured so far is kept; a process that left the group and still holds a pipe cannot hold the
    call past the drains, as the pipes are then closed. The note is always empty here (the SSH wire
    has its own).
    """
    timeout = check_timeout(timeout)
    deadline = None if timeout is None else time.monotonic() + timeout
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    pipes = _Pipes(proc, stdin or b'', end)
    try:
        while True:
            if pipes.complete() and pipes.recorded:
                return pipes.out, pipes.err, _reaped(proc), b''
            if proc.poll() is not None:
                pipes.drain()
                return pipes.out, pipes.err, proc.returncode, b''
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            pipes.wait(POLL if deadline is None else min(POLL, deadline - now))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                # Gone already, or (macOS) EPERM for a group whose members are all zombies.
                pass
            pipes.read_until(time.monotonic() + GRACE)
            if pipes.eof():
                break
        return pipes.out, pipes.err, TIMEOUT_EXIT_CODE, b''
    finally:
        pipes.close()
        if proc.returncode is None:
            try:
                proc.wait(timeout=GRACE)
            except subprocess.TimeoutExpired:
                pass


def decode(data: bytes) -> str:
    """Captured output as text, decoded once at the end; invalid UTF-8 becomes U+FFFD."""
    return data.decode('utf-8', 'replace')


def expand_local(path: str) -> str:
    """A path on the server's side: only a leading `~` or `~/` expands, to this process's HOME."""
    if path == '~' or path.startswith('~/'):
        home = os.environ.get('HOME')
        if not home:
            raise ValueError(f'HOME is not set, so {path!r} cannot be resolved')
        return home + path[1:]
    return path
