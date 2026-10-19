# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""An in-process SSH server on 127.0.0.1 that executes what it is sent, as sshd would. Not collected.

It is the `ssh` leg of the executor contracts without a real host. Each exec request runs as
`<login shell> -c <exec string>` (the shell is the server's parameter: `/bin/bash`, `/bin/zsh`,
`/bin/sh`, `/bin/tcsh`), started in the user's home with an sshd-like minimal environment (`env -i`
with HOME, USER, LOGNAME, SHELL, SSH_CLIENT and SSH_CONNECTION, and a PATH whose first entry is the
test PATH shim, plus the shim's BASH_ENV so a login bash keeps it first), always inside
`bin/sandbox.sh`. With SSH_CLIENT set, a bash built to read `~/.bashrc` for a shell sshd started (as
RHEL's and macOS's are) does so for every non-login `bash -c` that does not refuse it, as under sshd. The channel's stdin is
pumped to the process until the client sends EOF; stdout, stderr and the exit status come back. The
exit status goes as soon as the process exits, as sshd sends it, so a background process that
still holds the output open delays only the end of output.
When the client closes the channel first (a timeout), the server does not kill the process, as
OpenSSH does not: as sshd does, it closes the process's output pipes, then its stdin, so a later
write meets a broken pipe; and `close()` kills whatever is still running, so a test never leaves a
process behind.

`ThrottledSSH` is the same server on a slow link, as sshd behaves on one: it reads the command's
output as fast as the client's window allows, sends stdout and stderr over one link at a fixed rate
in the order it read them, and sends the exit status as soon as the process exits, ahead of
whatever output is still queued.

An SFTP subsystem serves the same filesystem, starting in the home. It refuses any path outside
`jail` (the test's own temporary tree), so a transfer that resolves somewhere else fails loudly
instead of touching the real filesystem. Authentication is one generated client key; the client
side is isolated from the developer's SSH setup (`executor()` builds an `SSHExecutor` with no key
lookup, no agent and an empty SSH config).
"""

from __future__ import annotations

import os
import select
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import paramiko
from fabric import Config

from rcac_mcp.executor.ssh import SSHExecutor

SANDBOX = Path(__file__).resolve().parent.parent / 'bin' / 'sandbox.sh'
LOOPBACK = '127.0.0.1'
USER = 'loopback'
SECURE_PATH = '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'

# What sshd tells the session about the connection; a bash that reads ~/.bashrc for a shell sshd
# started keys on SSH_CLIENT (or SSH2_CLIENT).
SSH_CLIENT = f'{LOOPBACK} 50000 22'
SSH_CONNECTION = f'{LOOPBACK} 50000 {LOOPBACK} 22'

# sshd runs the exec string from the user's home; the sandbox starts elsewhere, so step there first
# and then exec the login shell exactly as sshd would, on exactly the string the client sent.
ENTER_HOME = 'cd "$HOME" && exec "$0" -c "$1"'

# The shortest time between an exec request and its exit status: Paramiko confirms the request
# after the server's check returns, and a channel closed before that confirmation reaches the client
# fails the exec.
CONFIRM = 0.1

_KEYS: dict[str, paramiko.PKey] = {}


def _key(name: str) -> paramiko.PKey:
    if name not in _KEYS:
        _KEYS[name] = paramiko.ECDSAKey.generate()
    return _KEYS[name]


class _Handle(paramiko.SFTPHandle):
    def stat(self) -> Any:
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


class _SFTP(paramiko.SFTPServerInterface):
    """A filesystem view starting in `home` that refuses every path outside `jail`."""

    def __init__(self, server: Any, home: str, jail: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(server, *args, **kwargs)
        self.home = home
        self.jail = jail

    def _real(self, path: str) -> str | None:
        real = os.path.normpath(path if os.path.isabs(path) else os.path.join(self.home, path))
        return real if real == self.jail or real.startswith(self.jail + os.sep) else None

    def canonicalize(self, path: str) -> str:
        return self._real(path) or path

    def _stat(self, path: str, how: Any) -> Any:
        real = self._real(path)
        if real is None:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            return paramiko.SFTPAttributes.from_stat(how(real))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path: str) -> Any:
        return self._stat(path, os.stat)

    def lstat(self, path: str) -> Any:
        return self._stat(path, os.lstat)

    def open(self, path: str, flags: int, attr: Any) -> Any:
        real = self._real(path)
        if real is None:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            fd = os.open(real, flags, 0o644)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        if flags & os.O_WRONLY:
            mode = 'ab' if flags & os.O_APPEND else 'wb'
        elif flags & os.O_RDWR:
            mode = 'a+b' if flags & os.O_APPEND else 'r+b'
        else:
            mode = 'rb'
        handle = _Handle(flags)
        handle.filename = real
        handle.readfile = handle.writefile = os.fdopen(fd, mode)
        return handle

    def chattr(self, path: str, attr: Any) -> int:
        return paramiko.SFTP_OK


class _Server(paramiko.ServerInterface):
    def __init__(self, owner: LoopbackSSH) -> None:
        self.owner = owner

    def get_allowed_auths(self, username: str) -> str:
        return 'publickey'

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        same = key.get_base64() == _key('client').get_base64()
        return paramiko.AUTH_SUCCESSFUL if same else paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        self.owner.received.append(command.decode())
        threading.Thread(target=self.owner.execute, args=(channel, command.decode()), daemon=True).start()
        return True


# How long the feeder waits, after the client's end of stdin, to tell a closed channel from an EOF.
CLOSING = 0.2


def _pump(source: Any, send: Any, stop: threading.Event) -> None:
    # Once the channel is gone the read end closes, so the process meets a broken pipe on its next
    # write, as it would under sshd.
    try:
        while not stop.is_set():
            if not select.select([source], [], [], 0.02)[0]:
                continue
            data = os.read(source.fileno(), 65536)
            if not data:
                break
            send(data)
    except (OSError, EOFError, paramiko.SSHException):
        pass
    finally:
        source.close()


def _feed(channel: paramiko.Channel, sink: Any, stop: threading.Event, pumps: list[threading.Thread]) -> None:
    try:
        while data := channel.recv(65536):
            sink.write(data)
            sink.flush()
    except (OSError, EOFError, paramiko.SSHException):
        pass
    finally:
        # A client that closes the channel sends its EOF just before. When the channel closes, the
        # output pipes go first, as sshd closes them, so whatever runs once stdin ends cannot write.
        deadline = time.monotonic() + CLOSING
        while not channel.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        if channel.closed:
            stop.set()
            for pump in pumps:
                pump.join()
        try:
            sink.close()
        except OSError:
            pass


class LoopbackSSH:
    """The server, serving `home` with `shell` as the login shell; a context manager."""

    def __init__(self, home: Path, shell: str, jail: Path) -> None:
        self.home = str(Path(home).resolve())
        self.shell = shell
        self.jail = str(Path(jail).resolve())
        self.received: list[str] = []
        self.processes: list[subprocess.Popen[bytes]] = []
        self._transports: list[paramiko.Transport] = []
        self._listener = socket.socket()
        self._listener.bind((LOOPBACK, 0))
        self._listener.listen(8)
        self.port = self._listener.getsockname()[1]
        self._accepting = threading.Thread(target=self._accept, daemon=True)
        self._accepting.start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            transport = paramiko.Transport(conn)
            self._transports.append(transport)
            transport.add_server_key(_key('host'))
            transport.set_subsystem_handler('sftp', paramiko.SFTPServer, _SFTP, self.home, self.jail)
            transport.start_server(server=_Server(self))

    def environment(self) -> list[str]:
        shim = Path(os.environ['BASH_ENV']).parent
        return [f'HOME={self.home}', f'USER={USER}', f'LOGNAME={USER}', f'SHELL={self.shell}',
                f'SSH_CLIENT={SSH_CLIENT}', f'SSH_CONNECTION={SSH_CONNECTION}',
                f'PATH={shim}:{SECURE_PATH}', f'BASH_ENV={os.environ["BASH_ENV"]}']

    def execute(self, channel: paramiko.Channel, wire: str) -> None:
        """Run `wire` as sshd would, relaying the channel; never kill the process on a closed channel."""
        started = time.monotonic()
        proc = self._launch(wire)
        stop = threading.Event()
        out = threading.Thread(target=_pump, args=(proc.stdout, channel.sendall, stop), daemon=True)
        err = threading.Thread(target=_pump, args=(proc.stderr, channel.sendall_stderr, stop), daemon=True)
        feeder = threading.Thread(target=_feed, args=(channel, proc.stdin, stop, [out, err]), daemon=True)
        for thread in (feeder, out, err):
            thread.start()
        code = proc.wait()
        time.sleep(max(0.0, CONFIRM - (time.monotonic() - started)))
        try:
            # As sshd does, the exit status goes when the process exits, while anything it left
            # running may still hold its output open. A process killed by a signal has no exit status
            # to send (sshd sends exit-signal, which Paramiko's server side does not offer); the
            # client then reports the status as lost.
            if code >= 0:
                channel.send_exit_status(code)
        except (OSError, EOFError, paramiko.SSHException):
            pass
        out.join()
        err.join()
        try:
            channel.shutdown_write()
            channel.close()
        except (OSError, EOFError, paramiko.SSHException):
            pass

    def _launch(self, wire: str) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen([str(SANDBOX), 'env', '-i', *self.environment(), '/bin/sh', '-c', ENTER_HOME,
                                 self.shell, wire],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        self.processes.append(proc)
        return proc

    def executor(self) -> SSHExecutor:
        """An `SSHExecutor` for this server, isolated from the developer's SSH setup."""
        return SSHExecutor(LOOPBACK, port=self.port, user=USER,
                           config=Config(overrides={'load_ssh_configs': False}, ssh_config=paramiko.SSHConfig()),
                           connect_kwargs={'pkey': _key('client'), 'look_for_keys': False, 'allow_agent': False})

    def close(self) -> None:
        """Stop serving, and kill every process group an exec started that is still running."""
        self._listener.close()
        for proc in self.processes:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
        for transport in self._transports:
            transport.close()

    def __enter__(self) -> LoopbackSSH:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# The client's window (Paramiko's default): how much output sshd reads ahead of what has been sent.
WINDOW = 2 * 1024 * 1024

# The largest piece sent at once on the slow link.
PIECE = 8192


class _Link:
    """One slow link carrying both output streams, in the order they were read, at `rate` bytes/s."""

    def __init__(self, channel: paramiko.Channel, rate: int, window: int) -> None:
        self.channel, self.rate, self.window = channel, rate, window
        self.queue: deque[tuple[bool, bytes]] = deque()
        self.queued = 0
        self.ended = 0
        self.cond = threading.Condition()

    def read(self, source: Any, is_err: bool, stop: threading.Event) -> None:
        """Read `source` as fast as the window allows; at its end (or `stop`), close it."""
        try:
            while not stop.is_set():
                with self.cond:
                    if self.queued >= self.window:
                        self.cond.wait(0.05)
                        continue
                if not select.select([source], [], [], 0.02)[0]:
                    continue
                data = os.read(source.fileno(), 65536)
                if not data:
                    break
                with self.cond:
                    self.queue.append((is_err, data))
                    self.queued += len(data)
                    self.cond.notify_all()
        except OSError:
            pass
        finally:
            source.close()
            with self.cond:
                self.ended += 1
                self.cond.notify_all()

    def send(self) -> None:
        """Send what was read, a piece at a time, at the link's rate, until both streams have ended."""
        while True:
            with self.cond:
                while not self.queue and self.ended < 2:
                    self.cond.wait()
                if not self.queue:
                    return
                is_err, data = self.queue.popleft()
            send = self.channel.sendall_stderr if is_err else self.channel.sendall
            for at in range(0, len(data), PIECE):
                piece = data[at:at + PIECE]
                try:
                    send(piece)
                except (OSError, EOFError, paramiko.SSHException):
                    return
                time.sleep(len(piece) / self.rate)
            with self.cond:
                self.queued -= len(data)
                self.cond.notify_all()


class ThrottledSSH(LoopbackSSH):
    """The loopback server on a link of `rate` bytes a second, with sshd's read-ahead of `window`."""

    def __init__(self, home: Path, shell: str, jail: Path, rate: int, window: int = WINDOW) -> None:
        self.rate, self.window = rate, window
        super().__init__(home, shell, jail)

    def execute(self, channel: paramiko.Channel, wire: str) -> None:
        """Run `wire` as sshd would on the slow link: the exit status goes ahead of queued output."""
        started = time.monotonic()
        proc = self._launch(wire)
        stop = threading.Event()
        link = _Link(channel, self.rate, self.window)
        readers = [threading.Thread(target=link.read, args=(proc.stdout, False, stop), daemon=True),
                   threading.Thread(target=link.read, args=(proc.stderr, True, stop), daemon=True)]
        sender = threading.Thread(target=link.send, daemon=True)
        feeder = threading.Thread(target=_feed, args=(channel, proc.stdin, stop, readers), daemon=True)
        for thread in (feeder, *readers, sender):
            thread.start()
        code = proc.wait()
        time.sleep(max(0.0, CONFIRM - (time.monotonic() - started)))
        try:
            if code >= 0:
                channel.send_exit_status(code)
        except (OSError, EOFError, paramiko.SSHException):
            pass
        sender.join()
        try:
            channel.shutdown_write()
            channel.close()
        except (OSError, EOFError, paramiko.SSHException):
            pass
