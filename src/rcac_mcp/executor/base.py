# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Base executor protocol and result types."""

# Type annotations
from __future__ import annotations
from collections.abc import Sequence
from typing import Final, Protocol, runtime_checkable

# Standard libs
from dataclasses import dataclass

# Public interface
__all__ = ['Executor', 'CommandResult', 'UserPath', 'TIMEOUT_EXIT_CODE', 'NOT_STARTED_EXIT_CODE']

# The exit code of a command stopped at its timeout. Below -1000, so no completed command (0..255)
# and no signal death (-1..-64) can report it (AGENTS.md § Invariants §3).
TIMEOUT_EXIT_CODE: Final = -1001

# The exit code of a command that never began: the login initialisation ended (or the launch
# failed) before the command's first line ran. Below -1000 for the same reason.
NOT_STARTED_EXIT_CODE: Final = -1002


class UserPath(str):
    """A path argument. A leading `~` or `~/` names the home of the user the command runs as.

    Nothing else in it expands: `~name`, `$VAR` and glob characters are passed literally, and a
    relative path is taken from the working directory, never read as an option.
    """


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int
    hostname: str


@runtime_checkable
class Executor(Protocol):
    """Protocol for command execution backends.

    Every backend runs every command under the same pinned interpreter: an explicit login bash
    (`/bin/bash -l`), whatever the user's login shell or the server's `$SHELL`, so site
    initialisation (`module`, the site's tools on `PATH`) is the same in every mode. No startup file
    reads the caller's stdin or changes where a command starts, a result holds all of the command's
    output however slowly it arrives, and nothing the login initialisation prints reaches a result,
    with one exception: over
    `ssh`, a background job started by the remote login shell's own rc file (zsh's `.zshenv`, say),
    which runs before anything can be parked, can write into a result while the command runs
    (`issues/startup-background-jobs.md`).
    """

    @property
    def hostname(self) -> str:
        """Return the hostname where commands are executed."""
        ...

    def run(
        self,
        command: Sequence[str] | str,
        cwd: str | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> CommandResult:
        """
        Run one command under the pinned login bash and return its result.

        A sequence is an argv: each element reaches the program as exactly one literal argument,
        never interpreted by a shell, and an element marked `UserPath` resolves a leading `~` or
        `~/` to the home of the user the command runs as (nothing else in any element expands). A
        `str` is a bash script, run as given; only `run_command` passes one. The login
        initialisation runs first; none of its startup files reads `stdin` or moves where the
        command starts, and nothing they print reaches the result, but for the one exception the
        class docstring states. A command starts in the running user's home (`ssh`, `delegate`) or
        in the server process's working directory (`local`).

        Args:
            command: An argv (a sequence of `str`) or a bash script (a `str`).
            cwd: Working directory. A leading `~` or `~/` is the running user's home; nothing else
                expands, and a relative path is taken from where the command starts.
            timeout: Seconds to wait, over 0 and at most 10⁷; None waits for the command to finish.
            stdin: The exact bytes the command reads on its stdin. None gives it an empty, closed
                stdin; a command never reads the server's own stdin. Over `ssh` the bytes go only
                once the command has started, so if the remote login shell's rc file reads stdin,
                a call that sends stdin times out and never writes partial content (−1001, empty
                output; the command never runs). Such a call waits for the start at most
                `ssh.START_TIMEOUT` (30 s) with nothing arriving, or `timeout` if that comes first,
                so the timeout comes even when the caller set none.

        Returns:
            CommandResult with stdout, stderr, exit_code, and hostname. The output is read to its
            end, however long it keeps arriving after the command's exit status (over `ssh` the
            status comes ahead of output still queued on the link). Only when nothing has arrived
            for `launch.GRACE` (1 s) while something left running (by the command or a startup
            file) holds the output open does the call stop reading, without waiting for or
            signalling it, and stderr then ends with a note saying so. When the command exceeds
            `timeout` it is stopped and the result carries what it printed before the deadline and
            as it stopped, and `exit_code == TIMEOUT_EXIT_CODE` (−1001); output still arriving
            `launch.GRACE` past the deadline from a command that has exited ends the call there too,
            as −1001, with a note naming the command's own status. No backend raises on a timeout.
            A command that never began (the login initialisation ended first, or took a
            descriptor the command's streams were parked on, so the program ran nothing) returns
            `NOT_STARTED_EXIT_CODE` (−1002); one whose start is announced late, behind a chatty rc
            file on a slow link, has begun. A command killed by a signal reports −N (or 128+N from
            a shell).

        Raises:
            TypeError, ValueError: The command, cwd or timeout cannot be run as given (a non-`str`
                element, NUL, text that is not valid UTF-8, an empty argv, a program over the
                per-argument limit of the bytes that carry it, a timeout out of range), before
                anything starts.
        """
        ...

    def put(self, local_path: str, remote_path: str) -> str:
        """
        Copy a file from the server's side to the side commands run on.

        A leading `~` or `~/` in either path is the home of the user who touches that file: on the
        command side the user commands run as, on the server's side the server process's user (in
        `delegate` mode both sides are the mapped user, on one host). A directory destination
        receives the file under its own name.

        Args:
            local_path: Path to the file on the server's side.
            remote_path: Destination path on the command side.

        Returns:
            The absolute path the file was written to, as that side names it (symlinks unresolved);
            over SFTP, the server's canonical form of it, which OpenSSH resolves through symlinks.
        """
        ...

    def get(self, remote_path: str, local_path: str) -> str:
        """
        Copy a file from the side commands run on to the server's side.

        Paths follow the same `~` rule as `put`; a directory destination receives the file under
        its own name.

        Args:
            remote_path: Path to the file on the command side.
            local_path: Destination path on the server's side.

        Returns:
            The absolute path the file was written to, as that side names it (symlinks unresolved).
        """
        ...

    def open(self) -> None:
        """Open the connection (if applicable)."""
        ...

    def close(self) -> None:
        """Close the connection and release resources."""
        ...

    def __enter__(self) -> Executor:
        """Context manager entry."""
        ...

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        ...
