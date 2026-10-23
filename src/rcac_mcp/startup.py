# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""What a server that cannot start tells its client: the reason, as the answer to every request.

A failed SSH connect at startup is reported three ways: once on stderr, which a client may discard;
on stdio, as a JSON-RPC error carrying the same message in answer to every request the client sends
until it closes the stream; and as a nonzero exit status. Every request is answered, not only the
first, because a client of protocol revision 2026-07-28 swallows the error to its `server/discover`
probe and retries with `initialize`; an answer to that retry is the one it shows.

This module needs no MCP stack: it speaks newline-delimited JSON-RPC directly, the stdio framing
both protocol eras share, so it behaves the same whichever era the client speaks.
"""

# Type annotations
from __future__ import annotations
from typing import Any, Final, Iterator, TextIO

# Standard libs
import json
import socket

# External libs
from paramiko.ssh_exception import AuthenticationException, NoValidConnectionsError, SSHException

# Public interface
__all__ = ['StartupFailure', 'answer_until_eof', 'connect_failure_message', 'refusal', 'INTERNAL_ERROR']


# JSON-RPC's internal-error code: standard, accurate, and read by no client as protocol-version
# evidence or as a transport failure of its own.
INTERNAL_ERROR: Final = -32603

# How far down an exception's cause and context chain the failure class is looked for.
CHAIN_DEPTH: Final = 8


class StartupFailure(RuntimeError):
    """The server could not start; the message has already been reported."""


def refusal(request_id: Any, message: str) -> str:
    """The JSON-RPC error line answering the request `request_id` with `message`."""
    error = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': INTERNAL_ERROR, 'message': message}}
    return json.dumps(error, separators=(',', ':')) + '\n'


def _request_id(line: str) -> tuple[bool, Any]:
    """Whether `line` is a JSON-RPC request, and its id when it is."""
    try:
        message = json.loads(line)
    except ValueError:
        return False, None
    if not isinstance(message, dict) or not isinstance(message.get('method'), str):
        return False, None
    request_id = message.get('id')
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        return False, None
    return True, request_id


def answer_until_eof(stdin: TextIO, stdout: TextIO, message: str) -> None:
    """Answer every request read from `stdin` with `message` as an error on `stdout`, until EOF.

    Input is newline-delimited JSON-RPC. A request (an object with a `method` and a string or integer
    `id`) is answered with an internal error carrying `message` and the request's own id; a
    notification, a response, a blank or unreadable line is skipped. Nothing else is written. A
    terminal on stdin is no client, so nothing is read from one; a closed stdout ends the answers.
    """
    if stdin.isatty():
        return
    while line := stdin.readline():
        is_request, request_id = _request_id(line)
        if not is_request:
            continue
        try:
            stdout.write(refusal(request_id, message))
            stdout.flush()
        except (BrokenPipeError, ValueError):
            return


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """`exc`, then the exceptions it was raised from or while handling, nearest first."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < CHAIN_DEPTH:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _refused(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionRefusedError):
        return True
    if isinstance(exc, NoValidConnectionsError):
        return bool(exc.errors) and all(isinstance(e, ConnectionRefusedError) for e in exc.errors.values())
    return False


def _cause_class(exc: BaseException) -> str | None:
    """The class of a connect failure, or None when it is none of the known ones."""
    for link in _chain(exc):
        if isinstance(link, AuthenticationException):
            return 'authentication failed'
        if isinstance(link, socket.gaierror):
            return 'unknown host or SSH alias'
        if _refused(link):
            return 'connection refused'
        if isinstance(link, TimeoutError):
            return 'timed out'
        # Paramiko's report when the key exchange has not finished by the connect timeout: the
        # negotiation stopped waiting while the transport was still alive.
        if type(link) is SSHException and str(link) == 'No existing session':
            return 'timed out'
    return None


def _detail(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return str(exc.strerror)
    return str(exc)


def connect_failure_message(host: str, exc: BaseException) -> str:
    """`cannot connect to <host>: <cause>`, naming the host as given and the class of failure.

    The class is one of `unknown host or SSH alias`, `connection refused`, `authentication failed`
    or `timed out`, followed by the exception's own text in parentheses when it says more; a failure
    of no known class is described by that text, or by the exception's type when it has none.
    """
    cause = _cause_class(exc)
    detail = _detail(exc).strip()
    if cause is None:
        return f'cannot connect to {host}: {detail or type(exc).__name__}'
    if detail and detail.lower().rstrip('.') != cause:
        return f'cannot connect to {host}: {cause} ({detail})'
    return f'cannot connect to {host}: {cause}'
