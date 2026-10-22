# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Filesystem tools for directory listing and file operations."""

# Type annotations
from __future__ import annotations
from typing import Final, List, Literal, Optional

# Standard libs
import base64
import posixpath
from dataclasses import dataclass

# Internal libs
from rcac_mcp.tools import mcp_tool
from rcac_mcp.context import get_executor
from rcac_mcp.executor import UserPath
from rcac_mcp.scripts import fixed, script_argv

# List a directory in one call: change into it, print its absolute path (logical, as `cd` reached
# it) and a NUL, then the long listing of `.`; any further arguments are `ls` options.
LIST: Final = fixed('cd -- "$1" && printf \'%s\\0\' "$PWD" && shift && exec ls -l --time-style=long-iso "$@" .')

# The whitespace a base64 `content` may carry (RFC 2045 line breaks: GNU `base64` wraps at 76
# columns and ends with a newline); every other byte outside the alphabet is an error.
BASE64_WHITESPACE: Final = {ord(c): None for c in ' \t\r\n'}


@dataclass
class FileInfo:
    """Information about a file or directory entry."""

    name: str
    path: str
    is_dir: bool
    size: int
    permissions: str
    owner: str
    group: str
    modified: str


@mcp_tool
def list_directory(
    path: str = '.',
    show_hidden: bool = False,
) -> List[FileInfo]:
    """
    List contents of a directory.

    Args:
        path: Directory path to list (supports ~ expansion). Defaults to current directory.
        show_hidden: Whether to include hidden files (starting with .).

    Returns:
        List of FileInfo objects with details about each entry.

    Examples:
        list_directory()
        list_directory("~/projects")
        list_directory("/tmp", show_hidden=True)
    """
    executor = get_executor()
    # An empty path was not given: the default, the current directory.
    path = path or '.'
    options = ['-a'] if show_hidden else []
    result = executor.run(script_argv(LIST, 'list_directory', UserPath(path), *options))

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to list directory: {result.stderr}')

    directory, _, listing = result.stdout.partition('\0')
    entries: List[FileInfo] = []

    for line in listing.strip().split('\n'):
        # Skip the "total" line and empty lines
        if not line or line.startswith('total'):
            continue

        # Parse ls -l output: perms links owner group size date time name
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue

        perms, _, owner, group, size_str, date, time, name = parts

        entries.append(FileInfo(
            name=name,
            path=f'{directory.rstrip("/")}/{name}',
            is_dir=perms.startswith('d'),
            size=int(size_str),
            permissions=perms,
            owner=owner,
            group=group,
            modified=f'{date} {time}',
        ))

    return entries


@mcp_tool
def read_file(
    path: str,
    encoding: str = 'utf-8',
    max_size: Optional[int] = None,
) -> str:
    """
    Read contents of a file.

    Args:
        path: Path to the file (supports ~ expansion).
        encoding: Character encoding (default: utf-8).
        max_size: Maximum bytes to read. If None, reads entire file.

    Returns:
        The file contents as a string.

    Examples:
        read_file("~/.bashrc")
        read_file("/etc/hostname")
        read_file("large_file.log", max_size=10000)
    """
    executor = get_executor()

    if max_size:
        # Use head to limit bytes read
        result = executor.run(['head', '-c', str(max_size), '--', UserPath(path)])
    else:
        result = executor.run(['cat', '--', UserPath(path)])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to read file: {result.stderr}')

    return result.stdout


def _content_bytes(content: str, encoding: str) -> bytes:
    """The exact bytes `content` stands for under `encoding`, or a ValueError naming the parameter."""
    if encoding == 'base64':
        try:
            return base64.b64decode(content.translate(BASE64_WHITESPACE), validate=True)
        except ValueError as exc:
            raise ValueError(f'content is not valid base64 (encoding="base64"): {exc}') from None
    if encoding != 'utf-8':
        raise ValueError(f'encoding must be "utf-8" or "base64", not {encoding!r}')
    try:
        return content.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('content is not valid UTF-8 text: it contains a lone surrogate') from None


@mcp_tool
def write_file(
    path: str,
    content: str,
    append: bool = False,
    create_dirs: bool = False,
    encoding: Literal['utf-8', 'base64'] = 'utf-8',
) -> None:
    """
    Write content to a file, byte for byte.

    The file receives exactly the bytes sent: no newline is added, and empty content creates an
    empty file (or empties one). Invalid content is refused before the file is touched.

    Args:
        path: Path to the file (supports ~ expansion).
        content: Content to write: text, or base64 when encoding is "base64".
        append: If True, append to file instead of overwriting.
        create_dirs: If True, create parent directories if they don't exist.
        encoding: How content is given: "utf-8" (default; the text is written as its UTF-8 bytes)
            or "base64" (decoded, and the decoded bytes written; use it for binary content). Under
            "base64", spaces, tabs and line breaks are ignored; any other character outside the
            base64 alphabet, or bad padding, is an error.

    Examples:
        write_file("~/output.txt", "Hello, World!")
        write_file("~/log.txt", "New entry\\n", append=True)
        write_file("~/new/path/file.txt", "content", create_dirs=True)
        write_file("~/data.bin", "AAEC/w==", encoding="base64")
    """
    # Validated and encoded before any executor call: nothing touches the target until it passes.
    data = _content_bytes(content, encoding)
    executor = get_executor()

    # The parent is computed here, never with `$(dirname …)`, which strips a trailing newline from a
    # directory name.
    parent = posixpath.dirname(path) if create_dirs else ''

    # The content goes through the executor's write, never in an argv.
    try:
        result = executor.write(path, data, append=append, parent=parent or None)
    except OSError as exc:
        raise RuntimeError(f'Failed to write file: {exc}') from None

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to write file (exit {result.exit_code}): {result.stderr}')
