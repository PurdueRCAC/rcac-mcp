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

A result is complete unless only holders remain: once the command's exit status is known, the output
is read to its end, however long it keeps arriving (over SSH the status comes ahead of output still
queued on the link). The read stops early only when nothing has arrived for `GRACE` seconds while
something left running (the command's own background job, or one a startup file started) still
holds the output open, and then the result says so (`HELD_NOTE`). Output still arriving at the
timeout ends the call there, as a timeout whose note names the command's status (`LATE_NOTE`).

`local` and `delegate` hand `bash_argv(program)` to `run_process`; `ssh` sends `ssh_command(program)`,
a fixed bootstrap whose only variable part is one base64 word, so the remote login shell (sh, bash,
zsh, ksh, csh or tcsh) parses no byte of the command.
"""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Final

# Standard libs
import base64
import logging
import math
import os
import secrets
import shlex
import signal
import subprocess
import time

# Internal libs
from rcac_mcp.executor.base import NOT_STARTED_EXIT_CODE, TIMEOUT_EXIT_CODE, UserPath

# Public interface
__all__ = ['BASH', 'BASH_ARGS', 'GRACE', 'MAX_ARGUMENT', 'FIXED_RESET', 'HELD_NOTE', 'LATE_NOTE', 'program',
           'separate', 'bash_argv', 'ssh_command', 'fixed_argv', 'check_timeout', 'run_process', 'decode',
           'expand_local', 'word']

log = logging.getLogger(__name__)

# The pinned interpreter. Absolute, so `sudo`'s secure_path cannot pick another; a login shell,
# because only a login bash gives the same module state whatever shell launched it.
BASH: Final = '/bin/bash'
BASH_ARGS: Final = ('-l',)

# Seconds with nothing arriving after which a call stops reading output still held open once the
# command has exited (whatever is left running, the command's or a startup file's, may hold it open);
# how long output still arriving after the exit may run past the deadline; and the bound on the
# drain after each of SIGTERM and SIGKILL at a timeout.
GRACE: Final = 1.0

# The options before `-c` of every non-login bash the launcher starts or a fixed script runs in.
# `--norc`: no startup file, even where bash reads ~/.bashrc for a shell it believes sshd or rshd
# started (SSH_CLIENT or SSH2_CLIENT set, or a socket on stdin). `--posix` (a stage or a fixed
# script): no BASH_ENV either.
NORC: Final = '--norc'
POSIX_STAGE: Final = (BASH, NORC, '--posix')

# Seconds between checks that the command has exited, while something still holds its output open.
POLL: Final = 0.05

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
# three times: on macOS a stat through /dev/fd now and then fails while the descriptors are intact
# (about one first check in 1,500 under a concurrent test run; the next check passes), whereas a
# taken descriptor fails every time.
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
                   'unset -f builtin command exec set printf trap cd export declare compgen read; unset IFS; '
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

# The first stage of a local or delegated launch: a POSIX-mode bash that reads no startup file
# (`POSIX_STAGE`), parks the streams and starts the login bash on the program in "$1". The login
# bash gets no positional parameters: a startup file could rewrite them.
STAGE: Final = f'{PARK} && exec {BASH} {" ".join(BASH_ARGS)} -c -- "$1"'

# The start line for the running user's home; any other start is an absolute path, quoted.
START_HOME: Final = f'command cd -- {HOME_WORD} || exec /usr/bin/false'

# The SSH exec string, ahead of one base64 word. It holds no `!`, backslash or newline, so every
# login shell parses it the same. `&& printf .` keeps the program's trailing newlines, which `$( )`
# would strip, and fails the launch when decoding fails instead of running an empty program.
BOOTSTRAP: Final = (f"exec {' '.join(POSIX_STAGE)} -c 'p=$(printf %s \"$1\" | base64 -d && printf .) && "
                    f"{PARK} && exec {BASH} {' '.join(BASH_ARGS)} -c -- \"${{p%.}}\"' cluster-mcp ")

# What a fixed script (a constant script a tool or the delegate transfer runs, every value a
# positional parameter) undoes before it runs: functions a profile exported over the programs fixed
# scripts run, and options it exported in SHELLOPTS (`noclobber` would refuse an overwrite,
# `pipefail` would change a pipeline's status). `unset` and `set` are POSIX special builtins, which no
# function can replace in a POSIX-mode bash.
FIXED_RESET: Final = 'unset -f cat cd chmod cp find mkdir printf sort; set +C +o pipefail\n'

NOT_STARTED_NOTE: Final = 'cluster-mcp: the command did not start (exit {code}); the server log has the details\n'
TAKEN_NOTE: Final = ('cluster-mcp: the command did not start: the login initialisation took a descriptor its '
                     'streams were parked on, so it was not run; the server log has the details\n')
REDIRECTED_NOTE: Final = ("cluster-mcp: the login initialisation redirected the command's {stream}; "
                          'it was not captured\n')
HELD_NOTE: Final = ('cluster-mcp: the command exited, but something left running held its output open; '
                    f'nothing arrived for {GRACE:g} s, so reading stopped there\n').encode()
LATE_NOTE: Final = ('cluster-mcp: the command exited {code}, but its output was still arriving at the timeout; '
                    'reading stopped there\n')


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


def program(command: Sequence[str] | str, cwd: str | None = None,
            start: str | None = None) -> tuple[str, bytes]:
    """The bash program for `command`, and the sentinel `separate` cuts its output at.

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
        lines.append(f'command cd -- {shlex.quote(start)} || exec /usr/bin/false')
    if cwd is not None and _checked(cwd, 'cwd'):
        lines.append(f'command cd -- {word(UserPath(cwd))} || exec /usr/bin/false')
    if isinstance(command, str):
        lines.append(SCRIPT.format(script=shlex.quote(_checked(command, 'command'))))
    elif isinstance(command, Sequence) and not isinstance(command, (bytes, bytearray)):
        if not command:
            raise ValueError('command is an empty argv')
        lines.append('exec ' + ' '.join(word(_checked(arg, f'argument {i}')) for i, arg in enumerate(command)))
    else:
        raise TypeError(f'command must be an argv list or a script str, not {type(command).__name__}')
    # The nonce is printed in two halves, so the whole of it never appears in the program text.
    nonce = secrets.token_hex(16)
    head, tail = nonce[:16], nonce[16:]
    text = '\n'.join([PROLOGUE, f'printf %s%s {head} {tail}; printf %s%s {head} {tail} >&2', *lines])
    return text, nonce.encode('ascii')


def _fits(argument: str, what: str) -> str:
    """`argument`, or a ValueError when it is over `MAX_ARGUMENT` with its terminating NUL."""
    size = len(argument.encode('utf-8')) + 1
    if size > MAX_ARGUMENT:
        raise ValueError(f'command is too long: {what} is {size} bytes with its terminating NUL, over the '
                         f'{MAX_ARGUMENT}-byte per-argument limit')
    return argument


def _after(data: bytes, sentinel: bytes) -> bytes | None:
    at = data.find(sentinel)
    return None if at < 0 else data[at + len(sentinel):]


def separate(out: bytes, err: bytes, code: int, sentinel: bytes, note: bytes = b'') -> tuple[bytes, bytes, int]:
    """The command's own (stdout, stderr, exit code), with everything before the sentinel dropped.

    Each stream is cut after the first occurrence of the sentinel. With no sentinel on either
    stream the command never started: a timeout stays an empty timeout; anything else becomes
    `NOT_STARTED_EXIT_CODE` with a fixed note on stderr (the dropped stderr goes to the server log),
    `TAKEN_NOTE` when the program refused to run because a parked descriptor was taken.
    A sentinel on one stream only means the login initialisation redirected the other: that stream
    is empty and a note naming it is appended to stderr. `note`, the wire's own word on how the read
    ended (`HELD_NOTE`, `LATE_NOTE`), always ends stderr.
    """
    if not sentinel:
        raise ValueError('sentinel is empty')
    kept_out, kept_err = _after(out, sentinel), _after(err, sentinel)
    if kept_out is None and kept_err is None:
        if code == TIMEOUT_EXIT_CODE:
            return b'', note, code
        log.warning('command did not start (exit %s); its stderr ended: %r', code, err[-4096:])
        if TAKEN_MARK in err:
            return b'', TAKEN_NOTE.encode() + note, NOT_STARTED_EXIT_CODE
        return b'', NOT_STARTED_NOTE.format(code=code).encode() + note, NOT_STARTED_EXIT_CODE
    notes = b''
    for stream, kept in (('stdout', kept_out), ('stderr', kept_err)):
        if kept is None:
            log.warning('the login initialisation redirected the command\'s %s (exit %s)', stream, code)
            notes += REDIRECTED_NOTE.format(stream=stream).encode()
    return kept_out or b'', (kept_err or b'') + notes + note, code


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


def check_timeout(timeout: float | None) -> float | None:
    """`timeout` as seconds, or a ValueError before anything starts: finite, over 0, at most 10⁷."""
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f'timeout must be a number of seconds, not {type(timeout).__name__}')
    if not (math.isfinite(timeout) and 0 < timeout <= MAX_TIMEOUT):
        raise ValueError(f'timeout must be a number of seconds over 0 and at most {MAX_TIMEOUT:g}, got {timeout!r}')
    return float(timeout)


def _close(proc: subprocess.Popen) -> None:
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def run_process(argv: list[str], stdin: bytes | None,
                timeout: float | None) -> tuple[bytes, bytes, int, bytes]:
    """Run `argv` in a new session, feeding `stdin` (empty when None); return (stdout, stderr, code, note).

    The child never inherits this process's stdin. Once the command has exited, its status is the
    code and its output is read to the end, however long it keeps arriving. The read stops early
    only once nothing has arrived for `GRACE` while something left running (a background job, the
    command's or a startup file's) still holds a pipe open: the pipes are closed, nothing is
    signalled, and the note is `HELD_NOTE`. At the deadline, a command still running gets
    `TIMEOUT_EXIT_CODE`: the whole process group gets SIGTERM, then SIGKILL, each followed by a
    bounded drain, and the output captured so far is kept; a process that left the group and still
    holds a pipe cannot hold the call past the drains, as the pipes are then closed. Output from a
    command that has exited still arriving `GRACE` past the deadline ends the read too: the code is
    `TIMEOUT_EXIT_CODE` and the note (`LATE_NOTE`) names the command's own status.
    """
    timeout = check_timeout(timeout)
    deadline = None if timeout is None else time.monotonic() + timeout
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    data: bytes | None = stdin or b''
    status: int | None = None
    exited = heard = 0.0
    size = 0
    while True:
        if status is None:
            limit = deadline
        else:
            limit = max(heard, exited) + GRACE
            limit = limit if deadline is None else min(limit, deadline + GRACE)
        left = None if limit is None else max(0.0, limit - time.monotonic())
        try:
            out, err = proc.communicate(data, timeout=POLL if left is None else min(POLL, left))
            return out, err, proc.returncode, b''
        except subprocess.TimeoutExpired as exc:
            last = exc
        # The input went with the first call; a retry continues it.
        data = None
        now = time.monotonic()
        got = len(last.stdout or b'') + len(last.stderr or b'')
        if got != size:
            size, heard = got, now
        if status is None:
            status = proc.poll()
            if status is not None:
                exited = now
            elif deadline is not None and now >= deadline:
                break
        elif now >= max(heard, exited) + GRACE:
            _close(proc)
            return last.stdout or b'', last.stderr or b'', status, HELD_NOTE
        elif deadline is not None and now >= deadline + GRACE:
            _close(proc)
            return last.stdout or b'', last.stderr or b'', TIMEOUT_EXIT_CODE, LATE_NOTE.format(code=status).encode()
    code = TIMEOUT_EXIT_CODE
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            # Gone already, or (macOS) EPERM for a group whose members are all zombies.
            pass
        try:
            out, err = proc.communicate(timeout=GRACE)
            return out, err, code, b''
        except subprocess.TimeoutExpired as exc:
            last = exc
    _close(proc)
    proc.wait()
    return last.stdout or b'', last.stderr or b'', code, b''


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
