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
__all__ = ['Executor', 'CommandResult', 'UserPath', 'TIMEOUT_EXIT_CODE', 'NOT_STARTED_EXIT_CODE',
           'CONNECTION_LOST_EXIT_CODE']

# The exit code of a command stopped at its timeout. Below -1000, so no completed command (0..255)
# and no signal death (-1..-64) can report it (AGENTS.md § Invariants §3).
TIMEOUT_EXIT_CODE: Final = -1001

# The exit code of a command that never began: the login initialisation ended (or the launch
# failed) before the command's first line ran. Below -1000 for the same reason.
NOT_STARTED_EXIT_CODE: Final = -1002

# The exit code of a command whose connection was lost before both of its end records arrived: the
# command may have run, and may have finished, so it is never reported as not started. Below -1000
# for the same reason.
CONNECTION_LOST_EXIT_CODE: Final = -1003


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
    reads a command's stdin or a file's content, or changes where a command starts; a result holds
    all of the command's output however slowly it arrives, and nothing left running holds a call
    past the command's end; and nothing the login initialisation prints reaches a result, with one
    exception: over `ssh`, a background job started by the remote login shell's own rc file (zsh's
    `.zshenv`, say), which runs before anything can be parked, can write into a result while the
    command runs (`issues/startup-background-jobs.md`).
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
    ) -> CommandResult:
        """
        Run one command under the pinned login bash and return its result.

        A sequence is an argv: each element reaches the program as exactly one literal argument,
        never interpreted by a shell, and an element marked `UserPath` resolves a leading `~` or
        `~/` to the home of the user the command runs as (nothing else in any element expands). A
        `str` is a bash script, run as given; only `run_command` passes one. The login
        initialisation runs first; none of its startup files moves where the command starts, and
        nothing they print reaches the result, but for the one exception the class docstring
        states. A command starts in the running user's home (`ssh`, `delegate`) or in the server
        process's working directory (`local`). Its stdin is empty and closed; a command never reads
        the server's own stdin, and a file's content reaches a command only through `write`.

        Args:
            command: An argv (a sequence of `str`) or a bash script (a `str`).
            cwd: Working directory. A leading `~` or `~/` is the running user's home; nothing else
                expands, and a relative path is taken from where the command starts.
            timeout: Seconds to wait, over 0 and at most 10⁷; None waits for the command to finish.

        Returns:
            CommandResult with stdout, stderr, exit_code, and hostname. The result is complete
            exactly when the command's end has been reported on both streams: the launcher runs the
            command as a child and, once it exits, prints a per-call end record on stdout and on
            stderr, which the backend reads up to however slowly the output arrives (over `ssh` the
            exit status comes ahead of output still queued on the link, and decides nothing). The
            call then returns at once: nothing the command or a startup file left running holds it,
            and nothing printed after the record is kept. When the end has not been reported by
            `timeout`, the command is stopped (unless it has exited) and the result carries what it
            printed before the deadline and as it stopped, and `exit_code == TIMEOUT_EXIT_CODE`
            (−1001); over `ssh`, a command whose exit status had arrived is named in a note on
            stderr. No backend raises on a timeout. A command that never began (the login
            initialisation ended first, or took a descriptor the command's streams were parked on,
            so the program ran nothing) returns `NOT_STARTED_EXIT_CODE` (−1002); one whose start is
            announced late, behind a chatty rc file on a slow link, has begun. Over `ssh`, a
            connection lost before both end records arrived returns `CONNECTION_LOST_EXIT_CODE`
            (−1003), whatever arrived, with the note `cluster-mcp: the connection was lost; the
            command may have run` ending stderr: the command may have run, so it is never reported
            as not started. A command killed by
            signal N reports −N in every mode, and so does one that exits 128+N for a terminating
            signal N, which bash reports alike; the launch then dies of that signal itself.

        Raises:
            TypeError, ValueError: The command, cwd or timeout cannot be run as given (a non-`str`
                element, NUL, text that is not valid UTF-8, an empty argv, a program over the
                per-argument limit of the bytes that carry it, a timeout out of range), before
                anything starts.
        """
        ...

    def write(
        self,
        path: str,
        data: bytes,
        append: bool = False,
        parent: str | None = None,
        executable: bool = False,
    ) -> CommandResult:
        """
        Write exactly `data` to the file `path` on the side commands run on, as the running user.

        `path` and `parent` follow `UserPath`'s rule: a leading `~` or `~/` is the running user's
        home, nothing else expands, and a relative path is taken from where a command starts. No
        startup file reads the bytes: in `local` and `delegate` they reach a fixed script on the
        stdin the launch keeps parked while the login shell initialises, and the file is written
        in place; over `ssh` they never ride a command's stdin, but go by SFTP to a private
        temporary file (mode 0600, in a fresh directory of mode 0700) beside the target, every
        write's reply and the close checked; a fixed program then checks that file's size and
        checksum against the bytes sent and moves it into place, so the target is replaced whole
        and keeps its permission bits but setuid and setgid, as `cat >` leaves them (`ssh.write`
        states the cases written through instead). A failed or short transfer, or a mismatch,
        leaves the target untouched. Over `ssh`, too, a write refuses what `cat >` refuses: an
        existing target the user may not write, and a symbolic link whose target's directory does
        not exist (the link stays). A symbolic link is followed to the file it names. The temporary
        file is removed on every path.

        Args:
            path: The file to write.
            data: The exact bytes the file holds afterwards (or gains, with `append`).
            append: Add `data` to the end of the file instead of replacing it; a missing file is
                created.
            parent: A directory to create first, with its parents (`mkdir -p`), or None.
            executable: Make the file executable afterwards (`chmod +x`).

        Returns:
            The CommandResult of the write: exit code 0 when it is done; otherwise its stderr says
            why (over `ssh`, a staged file that is not what was sent, or a target the user may not
            write, leaves the target untouched).

        Raises:
            OSError: Over `ssh`, the content could not be staged (the target is a directory, a
                symbolic link whose target's directory does not exist, a path through a loop of
                links (`ELOOP`), or its directory is missing or closed to the user, or the transfer
                failed or fell short, with the reason, such as `No space left on device`); nothing
                was written.
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
