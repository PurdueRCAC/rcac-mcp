# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Create MCP server instance."""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional, Final, Callable, Awaitable

# Standard libs
from pathlib import Path
import os

# External libs
from fastmcp import FastMCP
from mcp.types import Icon, Request
from starlette.responses import Response, FileResponse

# Internal libs
from rcac_mcp.auth import AUTH_MODES
from rcac_mcp.middleware import ToolErrorMiddleware
from rcac_mcp.tools import TOOL_REGISTRY
from rcac_mcp.resources import RESOURCE_REGISTRY
from rcac_mcp.site import DEFAULT_BUDGET, SiteContext, compose, register

# Public interface
__all__ = [
    'create_mcp_server'
]


# Base URL for constructing absolute URLs (icons, etc.)
MCP_BASE_URL = os.environ.get('MCP_BASE_URL', '').rstrip('/')


# Registry for custom routes handled by the server
CUSTOM_ROUTES: Dict[str, Callable[[Request], Awaitable[Response]]] = {}


def custom_route(route: str):
    """Decorator to register custom route handlers."""

    def decorator(func: Callable[[Request], Awaitable[Response]]) -> Callable[[Request], Awaitable[Response]]:
        CUSTOM_ROUTES[route] = func
        return func

    return decorator



# Path to static files directory
STATIC_DIR: Final[Path] = Path(__file__).parent / 'static'
ICON_PATH: Final[Path] = STATIC_DIR / 'purdue-favicon.ico'


ICON_URL = f'{MCP_BASE_URL}/static/purdue-favicon.ico' if MCP_BASE_URL else '/static/purdue-favicon.ico'


# Serve the icon via custom route
@custom_route("/static/purdue-favicon.ico")
async def serve_icon(request: Request) -> FileResponse:
    return FileResponse(ICON_PATH, media_type="image/x-icon")


SERVER_INSTRUCTIONS: Final[str] = """\
RCAC MCP Server: tools for working on an HPC cluster. Prefer a dedicated tool
below to run_command when one fits. The site context section after this one
carries the cluster's own rules; follow them.

Shell and files:
- run_command: Execute a shell command
- list_directory: List a directory
- read_file: Read a file
- write_file: Write a file
- upload_file: Copy a file to the cluster
- download_file: Copy a file from the cluster

Site and storage:
- cluster_context: The cluster's rules from /etc/agents.d, in full
- storage_paths: Resolve home, scratch and depot paths
- myquota: Storage spaces, usage and quotas

Jobs:
- sbatch: Submit a batch job (script path or content)
- squeue: View the job queue
- scancel: Cancel jobs
- sacct: Job accounting history
- jobinfo: Detailed job information (RCAC)
- jobcmd: The command a job was submitted with
- jobenv: A job's environment variables
- jobscript: A job's submission script
- scontrol_show_job: Slurm's view of a job

Cluster status:
- sinfo: Cluster and partition status
- showpartitions: Partitions and their status
- average_wait: Queue wait time statistics
- scontrol_show_node: Node details for diagnostics
- slist: Slurm accounts and usage (RCAC)
- sfeatures: Node features and constraints (RCAC)

Resources: rcac://context (the site context in full) and rcac://storage.\
"""


def create_mcp_server(
    auth_mode: Optional[str] = None,
    middlewares: Optional[List] = None,
    *,
    site: SiteContext,
    budget: int = DEFAULT_BUDGET,
) -> FastMCP:
    """
    Create and configure the MCP server.

    Args:
        auth_mode: Authentication mode ('none', 'jwt', 'oidc').
        middlewares: Optional list of FastMCP Middleware instances to add.
        site: The site context read at startup; the server never reads it again.
        budget: The most characters the composed instructions may take.

    Returns:
        Configured FastMCP server instance.
    """
    server = FastMCP(
        name='RCAC',
        instructions=compose(SERVER_INSTRUCTIONS, site, budget),
        auth=AUTH_MODES[auth_mode](),
        icons=[
            Icon(
                src=ICON_URL,
                mimeType="image/x-icon",
            ),
        ],
    )

    register(server, site)

    for route, impl in CUSTOM_ROUTES.items():
        server.custom_route(route, methods=['GET'])(impl)

    for tool in TOOL_REGISTRY:
        server.add_tool(tool)

    # Register resources using the @resource decorator approach
    for resource in RESOURCE_REGISTRY:
        server.resource(
            resource['uri'],
            name=resource['name'],
            description=resource['description'],
        )(resource['handler'])

    # The error boundary goes before the caller's middleware: FastMCP runs later middleware inside
    # earlier middleware, so the boundary sees whatever the executor middleware raises.
    server.add_middleware(ToolErrorMiddleware())

    # Add any provided middleware
    if middlewares:
        for middleware in middlewares:
            server.add_middleware(middleware)

    return server
