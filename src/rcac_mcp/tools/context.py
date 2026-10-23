# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The site context tool: the rules read from /etc/agents.d at startup, in full."""

# Type annotations
from __future__ import annotations

# External libs
from fastmcp.server.dependencies import get_server

# Internal libs
from rcac_mcp.site import site_of
from rcac_mcp.tools import mcp_tool


@mcp_tool
def cluster_context() -> str:
    """
    Return the cluster's own rules for agents, from /etc/agents.d, in full.

    The server read the files once, when it started; this returns that text, whatever the
    instructions had room for, and runs nothing on the cluster. Each file follows a
    `<!-- Source: name.md -->` header. An empty string means the site publishes no rules; a line
    starting "No site rules are served" says why they could not be read.

    Returns:
        The site context as Markdown, an empty string, or the one-line reason none was read.

    Examples:
        >>> cluster_context()
        '<!-- Source: storage.md -->\\n...'
    """
    return site_of(get_server()).text
