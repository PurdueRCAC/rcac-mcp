# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""SSH-based command executor: Fabric for the connection, a raw Paramiko channel per command."""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Any, Callable, Final, Optional, Type
from types import TracebackType

# Standard libs
import os
import posixpath
import select
import stat
import threading
import time

# External libs
from fabric import Config, Connection
from paramiko import Channel, SFTPClient, Transport
from paramiko.common import cMSG_CHANNEL_REQUEST
from paramiko.message import Message
from paramiko.ssh_exception import SSHException

# Internal libs
from rcac_mcp.executor import launch
from rcac_mcp.executor.base import TIMEOUT_EXIT_CODE, CommandResult

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

# Seconds a call that holds the caller's stdin waits, with nothing arriving, for the program to
# start. Its bytes go only once the program has started, so a remote rc file that reads stdin would
# otherwise hold the call for ever; the call then ends as a timeout, having sent nothing.
START_TIMEOUT: Final = 30.0


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


def _collect(chan: Any, timeout: float | None, sentinel: bytes = b'',
             started: Callable[[], None] | None = None) -> tuple[bytes, bytes, int, bytes]:
    """Read the channel's stdout and stderr to the end; return them, the exit status and a note.

    The output is done when the channel has ended (`_ended`). sshd sends the exit status as soon as
    the command exits, while its output may still be queued behind it on a slow link, so the read
    goes on while output arrives. Only once nothing has arrived for `launch.GRACE` after the exit
    status does it stop, with `launch.HELD_NOTE`: what still holds the output open (a background job
    of the command's, or one a startup file left running) is no longer the command. At the deadline,
    a command with no exit status yet is asked to TERM, what it prints as it stops is read for
    `GRACE`, and the code is `TIMEOUT_EXIT_CODE`; output still arriving after an exit status at
    `GRACE` past the deadline ends the read too, as a timeout whose note (`launch.LATE_NOTE`) names
    that status. A signal death or a lost status stays -1, as Paramiko reports it.

    `started`, if given, is called once, when `sentinel` first arrives on either stream: the program
    is running and its startup files are done. Until then the call waits for it at most
    `START_TIMEOUT` with nothing arriving, then ends as a timeout.
    """
    out, err = bytearray(), bytearray()
    now = time.monotonic()
    deadline = None if timeout is None else now + timeout
    heard = now
    exited = None
    while True:
        size = len(out) + len(err)
        _drain(chan, out, err)
        now = time.monotonic()
        if len(out) + len(err) != size:
            heard = now
        if started is not None and (sentinel in out or sentinel in err):
            started()
            started = None
        if _ended(chan):
            _drain(chan, out, err)
            return bytes(out), bytes(err), chan.recv_exit_status(), b''
        if chan.exit_status_ready():
            status = chan.recv_exit_status()
            exited = now if exited is None else exited
            quiet = max(heard, exited)
            if now >= quiet + launch.GRACE:
                return bytes(out), bytes(err), status, launch.HELD_NOTE
            if deadline is not None and now >= deadline + launch.GRACE:
                return bytes(out), bytes(err), TIMEOUT_EXIT_CODE, launch.LATE_NOTE.format(code=status).encode()
            left = quiet + launch.GRACE - now
            left = left if deadline is None else min(left, deadline + launch.GRACE - now)
        else:
            limit = deadline
            if started is not None:
                limit = heard + START_TIMEOUT if limit is None else min(limit, heard + START_TIMEOUT)
            if limit is not None and now >= limit:
                _signal(chan, 'TERM')
                _stopped(chan, out, err)
                return bytes(out), bytes(err), TIMEOUT_EXIT_CODE, b''
            left = None if limit is None else limit - now
        _wait(chan, left)


def _feed(chan: Channel, stdin: bytes | None) -> None:
    """Send `stdin` to the command, then its end; a channel closed under it ends the feed."""
    try:
        if stdin:
            chan.sendall(stdin)
        chan.shutdown_write()
    except (OSError, EOFError, SSHException):
        pass


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
        stdin: bytes | None = None,
    ) -> CommandResult:
        """
        Run a command on the remote host under the pinned login bash (see `Executor.run`).

        The program travels as the launcher's fixed bootstrap carrying one base64 word, so the
        remote login shell parses none of it. It starts in the login home (`$HOME`), whatever a
        startup file `cd`s to. The caller's stdin is sent only once the program has started (its
        sentinel has arrived), so no startup file reads it; a program that never starts gets none,
        and a call holding stdin waits for the start at most `START_TIMEOUT` with nothing arriving.
        The output is read to its end, however long it takes to arrive after the exit status
        (`_collect`).

        Args:
            command: An argv (each element one literal argument) or a bash script.
            cwd: Working directory; a leading `~` or `~/` is the remote user's home.
            timeout: Seconds to wait; at the deadline the result carries `TIMEOUT_EXIT_CODE`.
            stdin: The exact bytes the command reads; None is an empty, closed stdin.

        Returns:
            CommandResult with stdout, stderr, exit_code, and hostname.

        Raises:
            ValueError: The exec string is over the per-argument limit, before connecting.
        """
        timeout = launch.check_timeout(timeout)
        prog, sentinel = launch.program(command, cwd, start='home')
        wire = launch.ssh_command(prog)
        transport = self._ensure_connected().client.get_transport()
        out, err, code, note = self._exec(transport, wire, stdin, timeout, sentinel)
        out, err, code = launch.separate(out, err, code, sentinel, note)
        return CommandResult(stdout=launch.decode(out), stderr=launch.decode(err), exit_code=code,
                             hostname=self._host)

    @staticmethod
    def _exec(transport: Transport, wire: str, stdin: bytes | None, timeout: float | None,
              sentinel: bytes) -> tuple[bytes, bytes, int, bytes]:
        """Run the exec string `wire` on a fresh session channel and collect its output.

        The remote login shell's rc files run on the channel's own stdin before the program can park
        it, so the caller's bytes go, from a thread, only once the program has printed `sentinel`.
        With nothing to send, the end of stdin goes at once: it carries no byte of the caller's.
        """
        chan = transport.open_session(timeout=OPEN_TIMEOUT)
        try:
            chan.exec_command(wire)
            if not stdin:
                _feed(chan, None)
                return _collect(chan, timeout)
            feeder = threading.Thread(target=_feed, args=(chan, stdin), daemon=True)
            return _collect(chan, timeout, sentinel, feeder.start)
        finally:
            chan.close()

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
