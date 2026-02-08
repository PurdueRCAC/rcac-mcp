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
from rcac_mcp.tools import TOOL_REGISTRY
from rcac_mcp.resources import RESOURCE_REGISTRY

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
RCAC MCP Server provides tools for interacting with Purdue's
Research Computing resources and HPC clusters.

IMPORTANT — Documentation Search:
Before advising users on storage policies, job submission, software
usage, or any RCAC-specific topic, use `doc_search` to check the
official RCAC documentation. This prevents outdated or incorrect advice.
After finding a relevant result, use `doc_load` to read the full page.

Storage Paths:
When users reference "scratch", "depot", or "home" storage:
- Home: /home/<user> or $HOME (25GB, private, for configs and small files)
- Scratch: /scratch/<cluster>/<user> or $CLUSTER_SCRATCH (high-performance,
  large but purged regularly - use for job I/O, not long-term storage).
  Run `findscratch` to get the exact path.
- Depot: /depot/<group> where <group> is the allocation name. Users may have
  access to multiple depot spaces. Use the `myquota` tool to discover all
  available depot paths - look for "depot" type entries in the output.
- Use the `storage_paths` tool to resolve all storage locations at once.

General Tools:
- run_command: Execute shell commands
- list_directory: List directory contents
- read_file: Read file contents
- write_file: Write content to files
- upload_file: Upload files to the remote system
- download_file: Download files from the remote system

RCAC-Specific Tools:
- storage_paths: Get resolved paths for home, scratch, and depot spaces
- myquota: Show storage spaces, usage, and quotas
- jobinfo: Get detailed job information (RCAC)
- jobcmd: Get the command submitted for a job
- jobenv: Get environment variables for a job
- jobscript: Get the full submission script for a job
- showpartitions: Show available partitions and status
- average_wait: Show queue wait time statistics

Slurm Job Management:
- sbatch: Submit a batch job (script path or content)
- squeue: View the job queue (default: your jobs)
- scancel: Cancel jobs by ID or filter
- sacct: Query job accounting history

Slurm Cluster Status:
- sinfo: Show cluster and partition status
- scontrol_show_job: Detailed job info from Slurm
- scontrol_show_node: Detailed node info for diagnostics
- slist: Show Slurm accounts and usage (RCAC)
- sfeatures: Show node features/constraints (RCAC)

Documentation Search:
- doc_search: Search RCAC documentation (user guides, software catalog,
  datasets, blog posts, workshops). Keep queries to 2-3 key terms, not
  full sentences. Use OR for synonyms ("conda OR anaconda"), quoted
  phrases for exact concepts ('"job array"'), and prefix wildcards
  for variants ("contai*"). Natural-language queries are auto-normalized
  but targeted queries produce better results.
- doc_load: Load the full content of a documentation page by its path
  (as shown in doc_search results). Use after identifying a relevant
  document to read it in full.

Resources:
- rcac://context: Cluster-specific context loaded from /etc/agents.d/*.md
- rcac://storage: User's resolved storage paths (home, scratch, depots)\
"""


def create_mcp_server(
    auth_mode: Optional[str] = None,
    middlewares: Optional[List] = None,
) -> FastMCP:
    """
    Create and configure the MCP server.

    Args:
        auth_mode: Authentication mode ('none', 'jwt', 'oidc').
        middlewares: Optional list of FastMCP Middleware instances to add.

    Returns:
        Configured FastMCP server instance.
    """
    server = FastMCP(
        name='RCAC',
        instructions=SERVER_INSTRUCTIONS,
        auth=AUTH_MODES[auth_mode](),
        icons=[
            Icon(
                src=ICON_URL,
                mimeType="image/x-icon",
            ),
        ],
    )

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

    # Add any provided middleware
    if middlewares:
        for middleware in middlewares:
            server.add_middleware(middleware)

    return server
