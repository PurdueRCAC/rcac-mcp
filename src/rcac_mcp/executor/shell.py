# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Local shell command executor."""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Optional, Type
from types import TracebackType

# Standard libs
import os
import shutil
import socket

# Internal libs
from rcac_mcp.executor import launch
from rcac_mcp.executor.base import CommandResult

# Public interface
__all__ = ['LocalShellExecutor']


class LocalShellExecutor:
    """
    Execute commands on this machine, as the server process's user. Development only.

    Every command runs under the pinned login bash (`executor/launch.py`), never the server's
    `$SHELL`, and starts in the server process's working directory, whatever a startup file `cd`s to.
    """

    _hostname: str

    def __init__(self) -> None:
        """Initialize local shell executor."""
        self._hostname = socket.gethostname()

    @property
    def hostname(self) -> str:
        """Return the local hostname."""
        return self._hostname

    def open(self) -> None:
        """No-op for local executor."""
        pass

    def close(self) -> None:
        """No-op for local executor."""
        pass

    def run(
        self,
        command: Sequence[str] | str,
        cwd: str | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> CommandResult:
        """
        Run a command locally under the pinned login bash (see `Executor.run`).

        Args:
            command: An argv (each element one literal argument) or a bash script.
            cwd: Working directory; a leading `~` or `~/` is this user's home.
            timeout: Seconds to wait; at the deadline the result carries `TIMEOUT_EXIT_CODE`.
            stdin: The exact bytes the command reads; None is an empty, closed stdin.

        Returns:
            CommandResult with stdout, stderr, exit_code, and hostname.
        """
        # The working directory goes into the program text, where no startup file can change it.
        prog, sentinel = launch.program(command, cwd, start=os.getcwd())
        out, err, code, note = launch.run_process(launch.bash_argv(prog), stdin, timeout)
        out, err, code = launch.separate(out, err, code, sentinel, note)
        return CommandResult(stdout=launch.decode(out), stderr=launch.decode(err), exit_code=code,
                             hostname=self._hostname)

    def put(self, local_path: str, remote_path: str) -> str:
        """
        Copy a file locally (local_path -> remote_path) and return the absolute destination.

        Only a leading `~` or `~/` expands, to this process's HOME; a directory destination
        receives the file under its own name.

        Args:
            local_path: Source path.
            remote_path: Destination path.
        """
        return os.path.abspath(shutil.copy2(launch.expand_local(local_path), launch.expand_local(remote_path)))

    def get(self, remote_path: str, local_path: str) -> str:
        """
        Copy a file locally (remote_path -> local_path) and return the absolute destination.

        Only a leading `~` or `~/` expands, to this process's HOME; a directory destination
        receives the file under its own name.

        Args:
            remote_path: Source path.
            local_path: Destination path.
        """
        return os.path.abspath(shutil.copy2(launch.expand_local(remote_path), launch.expand_local(local_path)))

    def __enter__(self) -> LocalShellExecutor:
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
