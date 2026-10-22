# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""SSH-based command executor: Fabric for the connection, a raw Paramiko channel per command.

A file's content never travels on a command's stdin here: `write` sends it by SFTP into a private
directory beside the target, and a program then moves it into place.
"""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Any, Final, Optional, Type
from types import TracebackType

# Standard libs
import errno
import os
import posixpath
import secrets
import select
import stat
import time

# External libs
from fabric import Config, Connection
from paramiko import SFTPClient, Transport
from paramiko.common import cMSG_CHANNEL_REQUEST
from paramiko.message import Message
from paramiko.ssh_exception import SSHException

# Internal libs
from rcac_mcp.executor import launch
from rcac_mcp.executor.base import TIMEOUT_EXIT_CODE, CommandResult, UserPath

# Public interface
__all__ = ['SSHExecutor']


# Reconnection settings
MAX_RECONNECT_DELAY: float = 10.0  # Maximum delay between reconnect attempts
INITIAL_RECONNECT_DELAY: float = 0.5  # Initial delay for exponential backoff

# Seconds to wait for the server to open a session channel.
OPEN_TIMEOUT: Final = 30.0

# Default bound, in seconds, on each step of connecting: the TCP connect, the server's SSH banner and
# authentication. A whole number: Fabric truncates the connect timeout to an int, and 0 means none.
CONNECT_TIMEOUT: Final = 15

# How long one wait for output lasts: a select on the channel before its output ends, a short
# sleep once its output has ended or its exit status is in (the channel then reads as ready forever).
SELECT_WAIT: Final = 0.1
SLEEP_WAIT: Final = 0.02

# A read from the channel, per call.
CHUNK: Final = 65536

# Where a write stages its content: a directory of this name and a fresh token, mode 0700, beside
# the target (so the move into place is a rename within one filesystem), holding one file, mode 0600.
STAGE_PREFIX: Final = '.cluster-mcp-'
STAGED: Final = 'content'

# The programs that put a staged file in place, "$1" the staged file, "$2" the target, "$3" the mode
# the target keeps (its own, or `=rw`, which a new file's umask narrows, as `cat >` would leave it).
# An overwrite moves the file into place, atomically; an append adds its bytes to the target; a
# target that is not a regular file (a FIFO, a device) is written through, as `cat >` would.
PLACE: Final = 'chmod -- "$3" "$1" && mv -f -- "$1" "$2"'
PLACE_APPEND: Final = 'cat -- "$1" >>"$2"'
PLACE_THROUGH: Final = 'cat -- "$1" >"$2"'
PLACE_EXECUTABLE: Final = ' && chmod -- +x "$2"'
MAKE_PARENT: Final = 'mkdir -p -- "$1"'

# The errors that leave a directory closed to a new entry (no write permission), while a writable
# target in it can still be written through.
CLOSED: Final = (errno.EACCES, errno.EPERM)


def _drain(chan: Any, out: bytearray, err: bytearray) -> None:
    while chan.recv_ready():
        out += chan.recv(CHUNK)
    while chan.recv_stderr_ready():
        err += chan.recv_stderr(CHUNK)


def _signal(chan: Any, name: str) -> None:
    """Ask the server to deliver signal `name` to the command (RFC 4254 §6.9); best effort."""
    try:
        message = Message()
        message.add_byte(cMSG_CHANNEL_REQUEST)
        message.add_int(chan.remote_chanid)
        message.add_string('signal')
        message.add_boolean(False)
        message.add_string(name)
        chan.transport._send_user_message(message)
    except Exception:
        pass


def _ended(chan: Any) -> bool:
    """The channel has closed, or has delivered both its end of output and its exit status."""
    return chan.closed or (chan.eof_received and chan.exit_status_ready())


def _wait(chan: Any, left: float | None) -> None:
    """Wait for more from the channel, at most `left` seconds, without spinning.

    A select on the channel while output may still come; a short sleep once its output has ended or
    its exit status is in (the channel then reads as ready forever).
    """
    if chan.eof_received or chan.exit_status_ready():
        wait = SLEEP_WAIT if left is None else min(SLEEP_WAIT, left)
        time.sleep(wait)
    else:
        select.select([chan], [], [], SELECT_WAIT if left is None else min(SELECT_WAIT, left))


def _stopped(chan: Any, out: bytearray, err: bytearray) -> None:
    """After the TERM at a deadline: keep reading what the command prints as it stops, for `GRACE`."""
    end = time.monotonic() + launch.GRACE
    while True:
        _drain(chan, out, err)
        left = end - time.monotonic()
        if _ended(chan) or left <= 0:
            return
        _wait(chan, left)


def _collect(chan: Any, timeout: float | None, end: bytes | None = None) -> tuple[bytes, bytes, int, bytes]:
    """Read the channel's stdout and stderr until both have carried their end record; return them,
    the status and a note.

    Given the program's end nonce `end`, the output is complete once each stream has carried its end
    record, however long it takes to arrive: sshd sends the exit status as soon as the command exits,
    ahead of output still queued on a slow link, so the status is never a reason to stop. The read
    then stops and the channel is closed, so nothing left running holds the call. A channel that ends
    first (`_ended`) ends the read too, with everything it carried. At the deadline the code is
    `TIMEOUT_EXIT_CODE`: a command whose exit status has arrived is asked nothing and the note
    (`launch.LATE_NOTE`) names that status; otherwise the command is asked to TERM and what it prints
    as it stops is read for `GRACE`. The status beside complete output is the channel's if it has
    arrived, else -1, as Paramiko reports a signal death or a lost status; the record is the
    command's.
    """
    out, err = bytearray(), bytearray()
    deadline = None if timeout is None else time.monotonic() + timeout
    scanned = [0, 0]
    while True:
        _drain(chan, out, err)
        if end is not None:
            for i, buf in enumerate((out, err)):
                if scanned[i] >= 0:
                    found = launch.record(buf, end, scanned[i])
                    scanned[i] = -1 if found else max(0, len(buf) - len(end) - 5)
            if scanned == [-1, -1]:
                return bytes(out), bytes(err), chan.recv_exit_status() if chan.exit_status_ready() else -1, b''
        if _ended(chan):
            _drain(chan, out, err)
            return bytes(out), bytes(err), chan.recv_exit_status(), b''
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            if chan.exit_status_ready():
                note = launch.LATE_NOTE.format(code=chan.recv_exit_status()).encode()
                return bytes(out), bytes(err), TIMEOUT_EXIT_CODE, note
            _signal(chan, 'TERM')
            _stopped(chan, out, err)
            return bytes(out), bytes(err), TIMEOUT_EXIT_CODE, b''
        _wait(chan, None if deadline is None else deadline - now)


def _remote(path: str) -> str:
    """A command-side path for SFTP, which starts in the login home and expands nothing.

    `~` is `.` and `~/rest` is `rest`, with the leading slashes of `rest` collapsed so the path
    stays in the home; any other path is passed as given.
    """
    if path == '~':
        return '.'
    if path.startswith('~/'):
        return path[2:].lstrip('/') or '.'
    return path


def _bounded(connect_kwargs: dict) -> dict:
    """`connect_kwargs` for Fabric's `Connection`, with every step of connecting bounded by default.

    The connect timeout goes in as Fabric's config default, so an ssh_config `ConnectTimeout` and an
    explicit `connect_timeout` still win; the banner and auth timeouts join the connect arguments
    unless they are already set. A `timeout` already among the connect arguments is left alone.
    """
    kwargs = dict(connect_kwargs)
    given = kwargs.get('config')
    config = Config() if given is None else given.clone(into=None if isinstance(given, Config) else Config)
    explicit = dict(kwargs.get('connect_kwargs') or {})
    if 'timeout' not in explicit and 'timeout' not in config.connect_kwargs and config.timeouts.connect is None:
        config.timeouts.connect = CONNECT_TIMEOUT
    for key in ('banner_timeout', 'auth_timeout'):
        if key not in config.connect_kwargs:
            config.connect_kwargs[key] = CONNECT_TIMEOUT
        if explicit and key not in explicit:
            explicit[key] = CONNECT_TIMEOUT
    kwargs['config'] = config
    if explicit:
        kwargs['connect_kwargs'] = explicit
    return kwargs


def _is_dir(sftp: SFTPClient, path: str) -> bool:
    try:
        return stat.S_ISDIR(sftp.stat(path).st_mode or 0)
    except OSError:
        return False


def _staging(sftp: SFTPClient, path: str) -> tuple[str, str | None, bool]:
    """Where a write of SFTP path `path` lands, the mode it keeps, and whether it is written through.

    A symbolic link is followed to the file it names (the server resolves it), as a shell's `>`
    follows it. An existing regular file keeps its permission bits; a missing one gets a new file's
    (None). A target that exists and is not a regular file is written through; a directory is
    refused.
    """
    try:
        if stat.S_ISLNK(sftp.lstat(path).st_mode or 0):
            path = sftp.normalize(path)
    except FileNotFoundError:
        return path, None, False
    try:
        mode = sftp.stat(path).st_mode or 0
    except FileNotFoundError:
        return path, None, False
    if stat.S_ISDIR(mode):
        raise IsADirectoryError(errno.EISDIR, 'Is a directory', sftp.normalize(path))
    return path, f'{stat.S_IMODE(mode):o}', not stat.S_ISREG(mode)


def _stage(sftp: SFTPClient, near: str) -> tuple[str, str]:
    """Make a fresh private directory in `near`; return it and the path of the file to stage there.

    The directory is made mode 0700, so no other user can reach what it holds.
    """
    directory = posixpath.join(near, STAGE_PREFIX + secrets.token_hex(8))
    sftp.mkdir(directory, 0o700)
    staged = posixpath.join(directory, STAGED)
    return directory, staged


def _upload(sftp: SFTPClient, staged: str, data: bytes) -> None:
    """Create `staged` exclusively, make it mode 0600 before any byte is written, then write `data`."""
    with sftp.open(staged, 'wbx') as handle:
        sftp.chmod(staged, 0o600)
        handle.set_pipelined(True)
        handle.write(data)


def _discard(sftp: SFTPClient, directory: str, staged: str) -> None:
    """Remove a staging directory and what is left in it (nothing, once a move has taken the file)."""
    for remove, target in ((sftp.remove, staged), (sftp.rmdir, directory)):
        try:
            remove(target)
        except OSError:
            pass


class SSHExecutor:
    """
    Execute commands on a remote host via SSH.

    Uses Fabric's Connection which auto-loads ~/.ssh/config for host aliases,
    identity files, and proxy commands. Maintains a persistent connection with
    automatic reconnection on failure. Each step of connecting is bounded (`CONNECT_TIMEOUT` unless
    the SSH config or the caller sets it), so an unreachable host fails within a bound a client waits for.
    """

    _host: str
    _conn: Optional[Connection]
    _connect_kwargs: dict

    def __init__(self, host: str, **connect_kwargs) -> None:
        """
        Initialize SSH executor and establish connection.

        Args:
            host: Hostname or SSH config alias to connect to.
            **connect_kwargs: Additional arguments passed to Fabric Connection
                              (e.g., user, port, connect_timeout).
        """
        self._host = host
        self._conn = None
        self._connect_kwargs = connect_kwargs
        # Establish connection immediately
        self.open()

    @property
    def hostname(self) -> str:
        """Return the configured host."""
        return self._host

    def open(self) -> None:
        """Establish SSH connection (single attempt, no retry)."""
        if self._conn is not None and self._conn.is_connected:
            return
        self._conn = Connection(self._host, **_bounded(self._connect_kwargs))
        self._conn.open()

    def close(self) -> None:
        """Close the SSH connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> Connection:
        """
        Ensure connection is open, connecting with exponential backoff if necessary.

        This method lazily establishes the SSH connection on first use and
        automatically reconnects with retry logic if the connection is lost.
        """
        if self._conn is not None and self._conn.is_connected:
            return self._conn

        delay = INITIAL_RECONNECT_DELAY

        while delay <= MAX_RECONNECT_DELAY:
            try:
                self.open()
                return self._conn
            except (SSHException, OSError):
                time.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

        # Final attempt after max delay
        try:
            self.open()
            return self._conn
        except (SSHException, OSError) as exc:
            raise ConnectionError(
                f'Failed to connect to {self._host} after exponential backoff: {exc}'
            ) from exc

    def run(
        self,
        command: Sequence[str] | str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """
        Run a command on the remote host under the pinned login bash (see `Executor.run`).

        The program travels as the launcher's fixed bootstrap carrying one base64 word, so the
        remote login shell parses none of it. It starts in the login home (`$HOME`), whatever a
        startup file `cd`s to. Its stdin is empty: the end of it is sent at once, so a remote rc
        file that reads stdin reads nothing. The output is read up to the program's end records,
        however long it takes to arrive after the exit status (`_collect`).

        Args:
            command: An argv (each element one literal argument) or a bash script.
            cwd: Working directory; a leading `~` or `~/` is the remote user's home.
            timeout: Seconds to wait; at the deadline the result carries `TIMEOUT_EXIT_CODE`.

        Returns:
            CommandResult with stdout, stderr, exit_code, and hostname.

        Raises:
            ValueError: The exec string is over the per-argument limit, before connecting.
        """
        timeout = launch.check_timeout(timeout)
        prog, marks = launch.program(command, cwd, start='home')
        wire = launch.ssh_command(prog)
        transport = self._ensure_connected().client.get_transport()
        out, err, code, note = self._exec(transport, wire, timeout, marks.end)
        out, err, code = launch.separate(out, err, code, marks, note)
        return CommandResult(stdout=launch.decode(out), stderr=launch.decode(err), exit_code=code,
                             hostname=self._host)

    @staticmethod
    def _exec(transport: Transport, wire: str, timeout: float | None, end: bytes) -> tuple[bytes, bytes, int, bytes]:
        """Run the exec string `wire` on a fresh session channel and collect its output.

        The end of stdin goes at once: the channel carries no byte to the command, so nothing the
        remote login shell's rc files run can read anything from it.
        """
        chan = transport.open_session(timeout=OPEN_TIMEOUT)
        try:
            chan.exec_command(wire)
            chan.shutdown_write()
            return _collect(chan, timeout, end)
        finally:
            chan.close()

    def write(self, path: str, data: bytes, append: bool = False, parent: str | None = None,
              executable: bool = False) -> CommandResult:
        """
        Write `data` to the remote file `path`, exactly (see `Executor.write`).

        The bytes go by SFTP, never on a command's stdin, into a file of their own (mode 0600) in a
        private directory (mode 0700) made beside the target, on the target's filesystem; a fixed
        program then moves that file into place, a rename, so the target is replaced whole or not at
        all, and keeps its permission bits (a new file gets the mode `cat >` would give it). An
        append adds the staged bytes to the target. A symbolic link is followed to the file it
        names. A target that is not a regular file, or a regular file in a directory this user may
        not add to, is written through instead, from a staging directory in the login home. The
        staging directory is removed whatever happens.

        Raises:
            OSError: The content could not be staged (the target is a directory, its directory is
                missing or closed, a transfer failed); nothing was written.
        """
        if not path:
            # As a shell's `>` with an empty name.
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
        with self._ensure_connected().client.open_sftp() as sftp:
            # A parent that is already a directory needs no command, and no login shell's start.
            if parent is not None and not _is_dir(sftp, _remote(parent)):
                made = self.run(launch.fixed_argv(MAKE_PARENT, 'cluster-mcp-write', UserPath(parent)))
                if made.exit_code != 0:
                    return made
            target, mode, through = _staging(sftp, _remote(path))
            near = '.' if through else posixpath.dirname(target) or '.'
            try:
                directory, staged = _stage(sftp, near)
            except OSError as exc:
                if exc.errno not in CLOSED or mode is None or through:
                    raise
                # A writable file in a directory closed to new entries: written through, as before.
                through = True
                directory, staged = _stage(sftp, '.')
            try:
                _upload(sftp, staged, data)
                if append:
                    script = PLACE_APPEND
                elif through:
                    script = PLACE_THROUGH
                else:
                    script = PLACE
                if executable:
                    script += PLACE_EXECUTABLE
                return self.run(launch.fixed_argv(script, 'cluster-mcp-write', UserPath(staged), UserPath(target),
                                                  mode or '=rw'))
            finally:
                _discard(sftp, directory, staged)

    def put(self, local_path: str, remote_path: str) -> str:
        """
        Upload a file over SFTP and return the absolute remote path written, in the SFTP server's
        canonical form (OpenSSH resolves it through symlinks).

        `local_path` is on the server's side: only a leading `~` or `~/` expands, to this process's
        HOME. In `remote_path` a leading `~` or `~/` is the login home and nothing else expands; a
        remote directory receives the file under its own name.

        Args:
            local_path: Path to local file.
            remote_path: Destination path on remote system.
        """
        local = launch.expand_local(local_path)
        with self._ensure_connected().client.open_sftp() as sftp:
            remote = _remote(remote_path)
            if _is_dir(sftp, remote):
                remote = posixpath.join(remote, os.path.basename(local))
            sftp.put(local, remote)
            return sftp.normalize(remote)

    def get(self, remote_path: str, local_path: str) -> str:
        """
        Download a file over SFTP and return the absolute local path written.

        In `remote_path` a leading `~` or `~/` is the login home and nothing else expands.
        `local_path` is on the server's side and taken literally, but for a leading `~` or `~/`
        (this process's HOME); a local directory receives the file under its own name.

        Args:
            remote_path: Path to file on remote system.
            local_path: Destination path on local system.
        """
        local = launch.expand_local(local_path)
        with self._ensure_connected().client.open_sftp() as sftp:
            remote = _remote(remote_path)
            if os.path.isdir(local):
                local = os.path.join(local, posixpath.basename(remote))
            sftp.get(remote, local)
        return os.path.abspath(local)

    def __enter__(self) -> SSHExecutor:
        """Context manager entry - opens connection."""
        self.open()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Context manager exit - closes connection."""
        self.close()
