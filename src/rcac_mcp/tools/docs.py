# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""RCAC documentation search and retrieval tools.

Provides MCP tools for searching and loading RCAC documentation from
the local FTS5-powered SQLite index. Agents use these tools to consult
authoritative documentation before advising users.
"""

# Type annotations
from __future__ import annotations
from typing import Optional, Set

# Standard libs
import os
import re

# Internal libs
from rcac_mcp.tools import mcp_tool
from rcac_mcp.docs.database import DocsDatabase, DEFAULT_DB_PATH

# Public interface
__all__: list[str] = []


# FTS5 operators that indicate the caller is already using query syntax
_FTS5_OPERATORS = re.compile(r'\bOR\b|\bAND\b|\bNOT\b|\bNEAR\b|["*]')

# Common English stopwords that add noise to search queries
_STOPWORDS: Set[str] = {
    'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'it', 'as', 'be', 'was', 'are',
    'been', 'do', 'does', 'did', 'has', 'have', 'had', 'this', 'that',
    'these', 'those', 'i', 'my', 'me', 'we', 'our', 'you', 'your',
    'how', 'what', 'when', 'where', 'which', 'who', 'can', 'will',
    'about', 'into', 'than', 'then', 'some', 'just', 'also',
}


def _normalize_query(query: str) -> str:
    """Normalize a natural-language query into forgiving FTS5 syntax.

    If the query already contains FTS5 operators (OR, AND, NOT, NEAR,
    quoted phrases, or prefix wildcards), it is returned as-is.

    Otherwise, stopwords are stripped and the remaining terms are joined
    with OR and given prefix wildcards so that a query like
    "ssh key config setup" becomes ``ssh* OR key* OR config* OR setup*``.
    This avoids the implicit-AND behavior that causes overly specific
    queries to return zero results.
    """
    if _FTS5_OPERATORS.search(query):
        return query

    terms = [
        t for t in query.split()
        if t.lower() not in _STOPWORDS and len(t) > 1
    ]

    if not terms:
        return query

    return ' OR '.join(f'{t}*' for t in terms)


def _get_db_path() -> str:
    """Resolve the documentation database path.

    Checks RCAC_DOCS_DB environment variable first, then falls back
    to the XDG-compliant default (~/.config/rcac-mcp/docs.db).
    """
    return os.environ.get('RCAC_DOCS_DB', DEFAULT_DB_PATH)


def _db_available() -> bool:
    """Check whether the documentation database exists on disk."""
    return os.path.isfile(_get_db_path())


_NO_DB_MESSAGE = (
    'Documentation index is not available. '
    'Build it with: rcac-mcp --index-docs --docs-path /path/to/RCAC-Docs'
)


@mcp_tool
def doc_search(query: str, category: Optional[str] = None) -> str:
    """Search RCAC documentation for relevant sections.

    Performs full-text search over indexed RCAC documentation (user guides,
    software catalog, datasets, blog posts, workshops) using SQLite FTS5
    with BM25 ranking. Use this tool to find relevant documentation before
    advising users on storage, jobs, software, or any RCAC-specific topic.

    Search strategy — keep queries short and focused:
    - Use 2-3 key terms, not full sentences: "scratch purge" not
      "how does the scratch purge policy work on the cluster"
    - Use OR for synonyms or related terms: "conda OR anaconda"
    - Use quoted phrases for exact concepts: '"job array"'
    - Use prefix matching for word variants: "contai*" matches
      container, containers, containerize, etc.

    Plain natural-language queries are automatically normalized (stopwords
    removed, terms joined with OR and prefix-matched) so they still work,
    but targeted queries will produce better-ranked results.

    Args:
        query: The search query string (FTS5 syntax supported).
        category: Optional category filter to narrow results. Matches as a
            prefix, so 'userguides' matches 'userguides/anvil', etc.
            Common categories: 'userguides', 'software', 'datasets',
            'blog', 'workshops'.

    Returns:
        Formatted search results with document path, title, section heading,
        and a text snippet showing the matching context. Results are ranked
        by relevance (BM25). Returns up to 20 results.

    Examples:
        doc_search("scratch purge")
        doc_search("conda OR anaconda", category="software")
        doc_search("GPU job submission", category="userguides")
    """
    if not _db_available():
        return _NO_DB_MESSAGE

    normalized = _normalize_query(query)
    with DocsDatabase(_get_db_path(), read_only=True) as db:
        results = db.search(normalized, category=category, limit=20)

    if not results:
        msg = f'No documentation found matching: {query}'
        if category:
            msg += f' (category: {category})'
        return msg

    lines = [f'Found {len(results)} result(s):\n']
    for i, result in enumerate(results, 1):
        heading = f' > {result.heading}' if result.heading else ''
        lines.append(f'{i}. [{result.title}]{heading}')
        lines.append(f'   Path: {result.path}')
        lines.append(f'   {result.snippet}')
        lines.append('')

    return '\n'.join(lines)


@mcp_tool
def doc_load(path: str) -> str:
    """Load the full content of an RCAC documentation page.

    Returns the complete rendered markdown of a documentation page
    identified by its relative path. Use this after doc_search to
    read the full content of a relevant document.

    Args:
        path: Relative path to the document (e.g., 'userguides/gautschi/storage.md').
            This is the path shown in doc_search results.

    Returns:
        Full rendered markdown content of the document, with all snippets
        and templates already resolved.

    Examples:
        doc_load("userguides/gautschi/storage.md")
        doc_load("software/apps_md/conda.md")
        doc_load("blog/posts/scratch-purge.md")
    """
    if not _db_available():
        return _NO_DB_MESSAGE

    with DocsDatabase(_get_db_path(), read_only=True) as db:
        content = db.load_document(path)

    if content is None:
        return f'Document not found: {path}'

    return content
