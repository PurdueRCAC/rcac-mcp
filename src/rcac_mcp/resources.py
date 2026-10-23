# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""MCP resources for cluster context and configuration.

Resources provide read-only access to data that can be used as context
for LLM interactions. The primary resource is cluster_context which
loads markdown files from /etc/agents.d/ on the remote cluster.
"""

# Type annotations
from __future__ import annotations
from typing import Optional

# Internal libs
from rcac_mcp.context import get_executor
from rcac_mcp.scripts import fixed, script_argv

# Public interface
__all__ = ['RESOURCE_REGISTRY', 'load_cluster_context', 'get_cached_context']


# Cache for cluster context (loaded once per connection)
_cluster_context_cache: dict[str, str] = {}

# The context files: a maxdepth-1 glob of /etc/agents.d, in `sort` order (§8 of AGENTS.md).
LIST_CONTEXT = fixed('find /etc/agents.d -maxdepth 1 -name "*.md" -type f 2>/dev/null | sort')


def load_cluster_context(executor) -> str:
    """
    Load context files from /etc/agents.d/*.md on the remote cluster.

    Files are concatenated in sorted order with headers indicating
    the source file. Empty result if directory doesn't exist or
    contains no .md files.

    Args:
        executor: The executor to use for loading context.

    Returns:
        Concatenated markdown content from all context files.
    """
    hostname = executor.hostname

    # Check cache first
    if hostname in _cluster_context_cache:
        return _cluster_context_cache[hostname]

    # Find all .md files in /etc/agents.d/
    find_result = executor.run(script_argv(LIST_CONTEXT, 'cluster_context'))

    if find_result.exit_code != 0 or not find_result.stdout.strip():
        # Directory doesn't exist or no files found
        _cluster_context_cache[hostname] = ''
        return ''

    files = find_result.stdout.strip().split('\n')
    context_parts = []

    for filepath in files:
        if not filepath:
            continue

        # Read file content
        cat_result = executor.run(['cat', '--', filepath])
        if cat_result.exit_code == 0 and cat_result.stdout.strip():
            # Extract filename for header
            filename = filepath.split('/')[-1]
            context_parts.append(f'<!-- Source: {filename} -->\n{cat_result.stdout.strip()}')

    context = '\n\n'.join(context_parts)
    _cluster_context_cache[hostname] = context
    return context


def get_cached_context(hostname: str) -> Optional[str]:
    """
    Get cached context for a hostname if available.

    Args:
        hostname: The cluster hostname.

    Returns:
        Cached context string, or None if not cached.
    """
    return _cluster_context_cache.get(hostname)


def clear_context_cache(hostname: Optional[str] = None) -> None:
    """
    Clear the context cache.

    Args:
        hostname: Specific hostname to clear, or None to clear all.
    """
    if hostname:
        _cluster_context_cache.pop(hostname, None)
    else:
        _cluster_context_cache.clear()


# Resource registry for server to register
# Each entry is (uri, name, description, handler_function)
RESOURCE_REGISTRY = []


def _cluster_context_resource() -> str:
    """
    Cluster-specific context and guidelines for AI agents.

    This resource contains markdown documentation loaded from
    /etc/agents.d/*.md on the connected cluster. It provides
    cluster-specific information such as:

    - Available software modules and recommended versions
    - Cluster-specific policies and best practices
    - Storage paths and quota information
    - Partition configurations and job limits
    - Any other guidance for AI agents working on this cluster

    Returns:
        Markdown content with cluster-specific context, or empty
        string if no context files are available.
    """
    executor = get_executor()
    return load_cluster_context(executor)


# Register the resource
RESOURCE_REGISTRY.append({
    'uri': 'rcac://context',
    'name': 'cluster_context',
    'description': 'Cluster-specific context and guidelines loaded from /etc/agents.d/*.md',
    'handler': _cluster_context_resource,
})


def _storage_paths_resource() -> str:
    """
    User's resolved storage paths.

    Returns the actual filesystem paths for all storage spaces the user
    has access to. This provides immediate context about where data can
    be read and written.

    Returns:
        Formatted text showing home, scratch, and depot paths with usage.
    """
    # Import here to avoid circular dependency
    from rcac_mcp.tools.rcac import storage_paths

    paths = storage_paths()

    lines = [
        'User Storage Paths:',
        '',
        f'Home: {paths.home.path}',
        f'  Usage: {paths.home.size} / {paths.home.limit} ({paths.home.usage_percent})',
        '',
        f'Scratch: {paths.scratch.path}',
        f'  Usage: {paths.scratch.size} / {paths.scratch.limit} ({paths.scratch.usage_percent})',
        '',
        f'Depot Spaces ({len(paths.depots)} available):',
    ]

    for depot in paths.depots:
        lines.append(f'  - {depot.path}')
        lines.append(f'    Usage: {depot.size} / {depot.limit} ({depot.usage_percent})')

    return '\n'.join(lines)


RESOURCE_REGISTRY.append({
    'uri': 'rcac://storage',
    'name': 'storage_paths',
    'description': "User's resolved storage paths for home, scratch, and depot spaces",
    'handler': _storage_paths_resource,
})
