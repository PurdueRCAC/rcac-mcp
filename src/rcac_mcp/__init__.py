# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""RCAC MCP Server: Enables agentic development with HPC clusters and storage services."""


# Type annotations
from __future__ import annotations
from typing import List, Final

# Standard libs
import sys
import os
from functools import partial
from importlib.metadata import version as get_version
from platform import python_version, python_implementation

# External libs
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface
from cmdkit.logging import Logger, level_by_name, logging_styles

# Internal libs
from rcac_mcp.server import create_mcp_server
from rcac_mcp import site as site_context
from rcac_mcp.site import DEFAULT_BUDGET
from rcac_mcp.startup import StartupFailure, answer_until_eof, connect_failure_message
from rcac_mcp.token import generate_token

# Public interface
__all__ = ['main', 'MCPServerApp', '__version__']
__version__ = get_version('rcac-mcp')
__website__ = 'https://github.com/purduercac/rcac-mcp'
__description__ = 'MCP Server for Purdue RCAC: HPC clusters and storage services for AI agents.'

# Global logger
log = Logger.default(name=__name__, level=level_by_name['INFO'], **logging_styles['default'])


def print_exception(exc: Exception, status: int) -> int:
    """Log `exc` and return `status`."""
    log.critical(str(exc))
    return status


def exit_reported(exc: Exception, status: int) -> int:
    """Return `status` for a failure whose message was already reported."""
    return status


# Default configuration values
DEFAULT_HOST: Final[str] = 'localhost'
DEFAULT_PORT: Final[int] = 8000
DEFAULT_TRANSPORT: Final[str] = 'stdio'
DEFAULT_AUTH: Final[str] = 'none'
DEFAULT_LIFETIME: Final[int] = 3600
DEFAULT_EXEC_MODE: Final[str] = 'ssh'


APP_NAME = 'rcac-mcp'
APP_VERSION = f'RCAC MCP Server v{__version__} ({python_implementation()} {python_version()})'
APP_USAGE = f"""\
Usage:
  {APP_NAME} [-h] [-v] [-t TRANSPORT] [-H HOST] [-p PORT] [-a AUTH] [-e EXEC_MODE] [--ssh-host HOST]
             [--instructions-budget CHARS] [--generate-token] [--lifetime SECONDS]

  {__description__}\
"""

APP_HELP = f"""\
{APP_USAGE}

  The RCAC MCP Server exposes Purdue Research Computing resources through the
  Model Context Protocol (MCP), enabling AI agents to interact with HPC clusters
  and storage services.

  Transports:
    stdio     Standard I/O (default) - for local MCP clients
    sse       Server-Sent Events over HTTP - for web-based clients
    http      Streamable HTTP - for production deployments

  Authentication:
    none      No authentication (default) - for local development
    jwt       JWT with symmetric key (HS256) - requires JWT_SECRET env var
    oidc      OIDC proxy - requires OIDC_* env vars

  Execution Modes:
    ssh       Execute commands via SSH (default for stdio, or when --ssh-host is set)
    local     Execute commands locally under bash (default for http with auth=none)
    delegate  Execute commands as authenticated user via sudo (http with auth)

Options:
  -t, --transport   TRANSPORT   Transport protocol: stdio, sse, http (default: {DEFAULT_TRANSPORT}).
  -H, --host        HOST        Bind address for HTTP/SSE (default: {DEFAULT_HOST}).
  -p, --port        PORT        Port number for HTTP/SSE (default: {DEFAULT_PORT}).
  -a, --auth        AUTH        Authentication mode: none, jwt, oidc (default: {DEFAULT_AUTH}).
  -e, --exec-mode   MODE        Execution mode: ssh, local, delegate (default: auto).
      --ssh-host    HOST        SSH host to connect to (required for ssh mode).
      --instructions-budget CHARS
                                Most characters the served instructions may take (default: {DEFAULT_BUDGET}).
      --generate-token          Generate a JWT token and exit (requires JWT_SECRET).
      --sub         SUBJECT     Subject identifier for token (username or user ID).
      --lifetime    SECONDS     Token lifetime in seconds (default: {DEFAULT_LIFETIME}).
  -v, --version                 Show version and exit.
  -h, --help                    Show this message and exit.

Environment Variables:
  JWT_SECRET          Shared secret for JWT signing (min 32 chars, required for jwt auth).
  OIDC_CONFIG_URL     OIDC provider discovery URL (required for oidc auth).
  OIDC_CLIENT_ID      OAuth client ID (required for oidc auth).
  OIDC_CLIENT_SECRET  OAuth client secret (required for oidc auth).
  MCP_BASE_URL        Public URL of this server (required for oidc auth).
  RCAC_SSH_HOST       Default SSH host (can be overridden with --ssh-host).
  RCAC_USER_MAP       Path to user mapping file for delegate mode.
  RCAC_INSTRUCTIONS_BUDGET  Default for --instructions-budget.

Examples:
  {APP_NAME} --ssh-host cluster.rcac.purdue.edu   # SSH to cluster (stdio)
  {APP_NAME} -t http --ssh-host cluster.edu       # HTTP server with SSH execution
  {APP_NAME} -t http -e local                     # Local execution over HTTP
  {APP_NAME} -t http -a jwt -e delegate           # Delegate to auth user\
"""


class MCPServerApp(Application):
    """RCAC MCP Server application."""

    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)
    interface.add_argument('-v', '--version', action='version', version=APP_VERSION)
    ALLOW_NOARGS = True  # Run application even when no arguments are given

    transport: str = DEFAULT_TRANSPORT
    interface.add_argument('-t', '--transport', default=transport,
                           choices=['stdio', 'sse', 'http'])

    host: str = DEFAULT_HOST
    interface.add_argument('-H', '--host', default=host)

    port: int = DEFAULT_PORT
    interface.add_argument('-p', '--port', type=int, default=port)

    auth: str = DEFAULT_AUTH
    interface.add_argument('-a', '--auth', default=auth,
                           choices=['none', 'jwt', 'oidc'])

    exec_mode: str | None = None
    interface.add_argument('-e', '--exec-mode', default=exec_mode,
                           choices=['ssh', 'local', 'delegate'])

    ssh_host: str | None = None
    interface.add_argument('--ssh-host', default=ssh_host)

    instructions_budget: int | None = None
    interface.add_argument('--instructions-budget', type=int, default=instructions_budget)

    generate_token_flag: bool = False
    interface.add_argument('--generate-token', action='store_true', dest='generate_token_flag')

    subject: str | None = None
    interface.add_argument('--sub', dest='subject', default=subject)

    lifetime: int = DEFAULT_LIFETIME
    interface.add_argument('--lifetime', type=int, default=lifetime)

    log_critical = log.critical
    log_exception = log.exception
    exceptions = {
        StartupFailure: partial(exit_reported, status=exit_status.runtime_error),
        ValueError: partial(print_exception, status=exit_status.bad_argument),
        RuntimeError: partial(print_exception, status=exit_status.runtime_error),
        Exception: partial(print_exception, status=exit_status.uncaught_exception),
    }

    def run(self) -> None:
        """Run the MCP server or generate token."""
        if self.generate_token_flag:
            secret = os.environ.get('JWT_SECRET')
            if not secret:
                raise ValueError('JWT_SECRET environment variable required for token generation')
            if len(secret) < 32:
                raise ValueError('JWT_SECRET must be at least 32 characters')
            print(generate_token(secret, self.lifetime, self.subject))
            return

        # Determine execution mode
        exec_mode = self._resolve_exec_mode()
        log.info(f'Execution mode: {exec_mode}')

        # Create executor and middleware based on mode
        executor = None
        middlewares = []

        if exec_mode == 'delegate':
            # Delegate mode uses per-request executors via middleware
            from rcac_mcp.middleware import AuthExecutorMiddleware
            middlewares.append(AuthExecutorMiddleware(self.auth))
            log.info('Delegate mode: executor will be created per-request based on auth')
        else:
            # SSH and local modes use a shared executor via middleware
            from rcac_mcp.middleware import SharedExecutorMiddleware
            executor = self._create_executor(exec_mode)
            middlewares.append(SharedExecutorMiddleware(executor))

            if exec_mode == 'ssh':
                log.info(f'SSH connection established to {executor.hostname}')

        try:
            budget = self._resolve_budget()
            site = self._read_site(exec_mode, executor)
            mcp = create_mcp_server(self.auth, middlewares=middlewares, site=site, budget=budget)
            if self.transport == 'stdio':
                mcp.run(transport='stdio')
            elif self.transport == 'sse':
                mcp.run(transport='sse', host=self.host, port=self.port)
            elif self.transport == 'http':
                mcp.run(transport='streamable-http', host=self.host, port=self.port)
        finally:
            # Clean up executor (if any)
            if executor:
                executor.close()

    def _resolve_exec_mode(self) -> str:
        """Determine execution mode from args or defaults."""
        if self.exec_mode:
            return self.exec_mode

        # Auto-select based on transport, auth, and ssh-host
        if self.transport == 'stdio':
            return 'ssh'
        elif self.ssh_host or os.environ.get('RCAC_SSH_HOST'):
            # If SSH host is specified, use ssh mode regardless of transport
            return 'ssh'
        elif self.auth == 'none':
            return 'local'
        else:
            return 'delegate'

    def _resolve_budget(self) -> int:
        """The instructions budget: the flag, else RCAC_INSTRUCTIONS_BUDGET, else the default."""
        budget = self.instructions_budget
        if budget is None:
            value = os.environ.get('RCAC_INSTRUCTIONS_BUDGET', '').strip()
            if not value:
                return DEFAULT_BUDGET
            try:
                budget = int(value)
            except ValueError:
                raise ValueError(f'RCAC_INSTRUCTIONS_BUDGET must be a whole number of characters, not {value!r}') from None
        if budget <= 0:
            raise ValueError(f'the instructions budget must be over 0, not {budget}')
        return budget

    def _read_site(self, exec_mode: str, executor):
        """Read /etc/agents.d once: the cluster's over the executor in ssh mode, else this host's.

        In delegate mode the server reads for other accounts, so it keeps only what others can read.
        """
        if exec_mode == 'ssh':
            return site_context.read_bounded(lambda: site_context.read_remote(executor), 'the cluster')
        for_others = exec_mode == 'delegate'
        return site_context.read_bounded(lambda: site_context.read_local(for_others=for_others), 'this host')

    def _create_executor(self, exec_mode: str):
        """Create executor based on mode."""
        from rcac_mcp.executor.ssh import SSHExecutor
        from rcac_mcp.executor.shell import LocalShellExecutor
        from rcac_mcp.executor.delegate import DelegatingExecutor, load_user_map

        if exec_mode == 'ssh':
            ssh_host = self.ssh_host or os.environ.get('RCAC_SSH_HOST')
            if not ssh_host:
                raise ValueError(
                    'SSH host required: use --ssh-host or set RCAC_SSH_HOST'
                )
            try:
                return SSHExecutor(ssh_host)
            except Exception as exc:
                message = connect_failure_message(ssh_host, exc)
                self._report_startup_failure(message)
                raise StartupFailure(message) from exc

        elif exec_mode == 'local':
            return LocalShellExecutor()

        else:
            raise ValueError(f'Unknown exec mode: {exec_mode}')

    def _report_startup_failure(self, message: str) -> None:
        """Report why the server cannot start: on stderr at once, then, on stdio, to every request.

        A stdio client that discards stderr still sees the message, as the error answering each
        request it sends, until it closes the stream.
        """
        log.critical(message)
        if self.transport == 'stdio':
            answer_until_eof(sys.stdin, sys.stdout, message)


def main(argv: List[str] | None = None) -> int:
    """Entry point for the rcac-mcp server."""
    return MCPServerApp.main(argv or sys.argv[1:])
