# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""File transfer tools."""

# Type annotations
from __future__ import annotations

# Internal libs
from rcac_mcp.tools import mcp_tool
from rcac_mcp.context import get_executor


def _path(name: str, path: str) -> str:
    """`path`, or a ValueError naming the parameter when it is empty.

    An empty path names no file, but a copy tool may read it as the current directory (BSD `cp`
    does) and an SFTP server may resolve it to the login home, so it is refused before anything runs.
    """
    if not path:
        raise ValueError(f'{name} is empty: an empty path names no file')
    return path


@mcp_tool
def upload_file(local_path: str, remote_path: str) -> str:
    """
    Upload a file from the local machine to the remote system.

    Args:
        local_path: Path to the local file.
        remote_path: Destination path on the remote system (supports ~ expansion).

    Returns:
        The absolute path of the uploaded file on the remote system.

    Examples:
        upload_file("/tmp/data.csv", "~/data/input.csv")
        upload_file("./script.py", "/home/user/scripts/run.py")
    """
    # The executor reports the absolute path it wrote, computed on the side the file landed on.
    executor = get_executor()
    return executor.put(_path('local_path', local_path), _path('remote_path', remote_path))


@mcp_tool
def download_file(remote_path: str, local_path: str) -> str:
    """
    Download a file from the remote system to the local machine.

    Args:
        remote_path: Path to the file on the remote system (supports ~ expansion).
        local_path: Destination path on the local machine.

    Returns:
        The absolute path of the downloaded file on the local machine.

    Examples:
        download_file("~/results/output.csv", "/tmp/output.csv")
        download_file("/var/log/app.log", "./logs/app.log")
    """
    # The executor reports the absolute path it wrote, computed on the side the file landed on.
    executor = get_executor()
    return executor.get(_path('remote_path', remote_path), _path('local_path', local_path))
