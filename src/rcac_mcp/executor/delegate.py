# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Delegating executor that runs commands as another user via sudo."""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Dict, Final, Optional, Type
from types import TracebackType

# Standard libs
import socket

# Internal libs
from rcac_mcp.executor import launch
from rcac_mcp.executor.base import CommandResult, UserPath

# Public interface
__all__ = ['DelegatingExecutor', 'load_user_map']

# The delegation prefix: non-interactive (there is no TTY to prompt on, so a missing sudoers rule is
# an error, not a hang) and with the target user's own HOME; the local user follows.
SUDO: Final = ('sudo', '-n', '-H', '-u')

# A transfer, run as the mapped user: copy "$1" to "$2" (into it, under the source's name, when it
# is a directory) and print the absolute path written, against $PWD and without resolving symlinks.
# POSIX mode, so the bash that runs it reads no BASH_ENV a profile exported.
TRANSFER: Final = ('src=$1 dst=$2\n'
                   'if [ -d "$dst" ]; then dst=${dst%/}/${src##*/}; fi\n'
                   'cp -- "$src" "$dst" || exit\n'
                   'case $dst in ./*) dst=${dst#./} ;; esac\n'
                   'case $dst in /*) ;; *) dst=${PWD%/}/$dst ;; esac\n'
                   'printf %s "$dst"\n')


def load_user_map(filepath: str) -> Dict[str, str]:
    """
    Load user mapping from a plain text file.

    File format: one mapping per line, "<id|user|email> <local-user>"

    Args:
        filepath: Path to the user mapping file.

    Returns:
        Dictionary mapping identity claims to local usernames.
    """
    user_map: Dict[str, str] = {}
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            parts = line.split(None, 1)  # Split on first whitespace
            if len(parts) != 2:
                raise ValueError(
                    f'Invalid user map format at line {line_num}: expected "<id> <local-user>"'
                )
            identity, local_user = parts
            user_map[identity] = local_user
    return user_map


class DelegatingExecutor:
    """
    Execute commands as another user via `sudo -n -H -u <local user>`.

    Used when running the MCP server as root (or a sudo-capable service account) and delegating
    commands to the authenticated user based on their JWT sub or OIDC email claim. Every command
    runs under the pinned login bash in the mapped user's own `$HOME`, and transfers run as that
    user too: the server process touches no file on a caller's behalf.
    """

    _user_map: Dict[str, str]
    _identity: str
    _local_user: str
    _hostname: str

    def __init__(self, identity: str, user_map: Dict[str, str]) -> None:
        """
        Initialize delegating executor.

        Args:
            identity: The identity claim (sub or email) from authentication.
            user_map: Dictionary mapping identity claims to local usernames.

        Raises:
            KeyError: If identity is not found in user_map.
        """
        if identity not in user_map:
            raise KeyError(f'Identity {identity!r} not found in user map')

        self._identity = identity
        self._user_map = user_map
        self._local_user = user_map[identity]
        self._hostname = socket.gethostname()

    @property
    def hostname(self) -> str:
        """Return the local hostname."""
        return self._hostname

    @property
    def local_user(self) -> str:
        """Return the local user commands are delegated to."""
        return self._local_user

    def open(self) -> None:
        """No-op for delegating executor."""
        pass

    def close(self) -> None:
        """No-op for delegating executor."""
        pass

    def run(
        self,
        command: Sequence[str] | str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """
        Run a command as the delegated user, under the pinned login bash (see `Executor.run`).

        The command starts in the mapped user's `$HOME`, whatever a startup file `cd`s to, so `~`
        and relative paths are that user's, never the server process's.

        Args:
            command: An argv (each element one literal argument) or a bash script.
            cwd: Working directory; a leading `~` or `~/` is the mapped user's home.
            timeout: Seconds to wait; at the deadline the result carries `TIMEOUT_EXIT_CODE`.

        Returns:
            CommandResult with stdout, stderr, exit_code, and hostname.
        """
        return self._run(command, cwd, timeout, None)

    def _run(self, command: Sequence[str] | str, cwd: str | None, timeout: float | None,
             stdin: bytes | None) -> CommandResult:
        prog, marks = launch.program(command, cwd, start='home')
        argv = [*SUDO, self._local_user, *launch.bash_argv(prog)]
        out, err, code, note = launch.run_process(argv, stdin, timeout, marks.end)
        out, err, code = launch.separate(out, err, code, marks, note)
        return CommandResult(stdout=launch.decode(out), stderr=launch.decode(err), exit_code=code,
                             hostname=self._hostname)

    def write(self, path: str, data: bytes, append: bool = False, parent: str | None = None,
              executable: bool = False) -> CommandResult:
        """
        Write `data` to `path` as the delegated user, exactly (see `Executor.write`).

        The bytes reach a fixed script's stdin, which the launch keeps parked while the login shell
        initialises, so no startup file can read them; the script, run as the mapped user, writes
        them to the file in place. `~` and relative paths are the mapped user's.
        """
        return self._run(launch.write_argv(path, append, parent, executable), None, None, data)

    def _copy(self, source: str, destination: str) -> str:
        """Copy `source` to `destination` as the mapped user; return the absolute path written."""
        result = self.run(launch.fixed_argv(TRANSFER, 'cluster-mcp-transfer', UserPath(source), UserPath(destination)))
        if result.exit_code != 0:
            raise RuntimeError(f'copy as {self._local_user} failed (exit {result.exit_code}): {result.stderr}')
        return result.stdout

    def put(self, local_path: str, remote_path: str) -> str:
        """
        Copy a file as the delegated user and return the absolute path written.

        Both paths are on this host and both are the mapped user's: `~` is that user's home, a
        relative path starts there, and the copy has exactly that user's permissions.

        Args:
            local_path: Source path.
            remote_path: Destination path.

        Raises:
            RuntimeError: The copy failed; the message carries its stderr.
        """
        return self._copy(local_path, remote_path)

    def get(self, remote_path: str, local_path: str) -> str:
        """
        Copy a file as the delegated user and return the absolute path written.

        Both paths are on this host and both are the mapped user's: `~` is that user's home, a
        relative path starts there, and the copy has exactly that user's permissions.

        Args:
            remote_path: Source path.
            local_path: Destination path.

        Raises:
            RuntimeError: The copy failed; the message carries its stderr.
        """
        return self._copy(remote_path, local_path)

    def __enter__(self) -> DelegatingExecutor:
        """Context manager entry."""
        self.open()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Context manager exit."""
        self.close()
