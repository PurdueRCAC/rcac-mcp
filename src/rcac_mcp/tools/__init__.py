# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""MCP tool definitions."""

# Type annotations
from __future__ import annotations
from typing import List, Callable, Any

# Standard libs
import functools
import inspect

# External libs
from fastmcp.tools import Tool

# Public interface
__all__ = ['TOOL_REGISTRY', 'mcp_tool']


# Registry of tool functions for us to add to server later
TOOL_REGISTRY: List[Tool] = []


def _text_violation(name: str, value: Any) -> str | None:
    """Why `value`, given for the parameter `name`, cannot be passed on as text; None when it can.

    A string (or a string element of a list) must encode as UTF-8: a lone surrogate has no bytes
    that stand for it, so no command, path or file can carry it exactly.
    """
    for text in value if isinstance(value, list) else [value]:
        if isinstance(text, str):
            try:
                text.encode('utf-8')
            except UnicodeEncodeError:
                return f'{name} is not valid UTF-8 text: it contains a lone surrogate'
    return None


def _literal_text(func: Callable[..., Any]) -> Callable[..., Any]:
    """`func`, refusing any argument that is not valid text with an error naming its parameter.

    The check runs before the tool's body, so a refused call reaches no executor. The wrapper keeps
    `func`'s name, docstring and signature, which FastMCP reads to build the served schema.
    """
    signature = inspect.signature(func)

    @functools.wraps(func)
    def checked(*args: Any, **kwargs: Any) -> Any:
        for name, value in signature.bind(*args, **kwargs).arguments.items():
            why = _text_violation(name, value)
            if why:
                raise ValueError(why)
        return func(*args, **kwargs)

    return checked


def mcp_tool(func: Callable[..., Any]) -> Tool:
    """Decorator to register MCP tool functions.

    The served description is the full docstring (`Args:`, `Returns:` and `Examples:` included):
    FastMCP 4 otherwise serves only its summary paragraph, and every model reads the description to
    choose a tool. FastMCP still copies each `Args:` line into its property's description.

    Every string argument, and every string element of a list argument, must be valid UTF-8 text: a
    lone surrogate is refused, naming the parameter, before the tool runs.
    """
    func = _literal_text(func)
    tool = Tool.from_function(func, description=inspect.getdoc(func))
    TOOL_REGISTRY.append(tool)
    return tool


# Import tool modules to trigger registration
# These imports must come after TOOL_REGISTRY and mcp_tool are defined
from rcac_mcp.tools import shell  # noqa: E402, F401
from rcac_mcp.tools import filesystem  # noqa: E402, F401
from rcac_mcp.tools import transfer  # noqa: E402, F401
from rcac_mcp.tools import rcac  # noqa: E402, F401
from rcac_mcp.tools import slurm  # noqa: E402, F401
from rcac_mcp.tools import context  # noqa: E402, F401
