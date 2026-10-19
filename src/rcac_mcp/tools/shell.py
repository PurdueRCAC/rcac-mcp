# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Shell command execution tools."""

# Type annotations
from __future__ import annotations
from typing import Optional

# Internal libs
from rcac_mcp.tools import mcp_tool
from rcac_mcp.context import get_executor
from rcac_mcp.executor import CommandResult


@mcp_tool
def run_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
) -> CommandResult:
    """
    Execute a shell command on the remote system.

    The command runs under bash, after the login initialisation (so `module` and the site's tools
    are available whatever the user's login shell). Nothing the initialisation prints is returned,
    with one exception: over ssh, a background job started by the remote login shell's own rc file
    (zsh's .zshenv, say) can write into the result while the command runs.

    Args:
        command: The shell command to execute, as a bash script.
        cwd: Working directory for command execution; a leading ~ or ~/ is the home of the user the command runs as.
        timeout: Maximum time in seconds to wait for completion.

    Returns:
        CommandResult containing stdout, stderr, exit_code, and hostname. On timeout, exit_code is
        -1001 and stdout and stderr hold what was captured before the deadline; -1002 means the
        command never started.

    Examples:
        run_command("ls -la")
        run_command("pwd", cwd="~/projects")
        run_command("sleep 5", timeout=10)
    """
    executor = get_executor()
    # An empty cwd was not given: the command starts where it would without one.
    return executor.run(command, cwd=cwd or None, timeout=timeout)
