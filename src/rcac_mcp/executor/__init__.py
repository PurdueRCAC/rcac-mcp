# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Command execution abstraction layer."""

# Type annotations
from __future__ import annotations

# Internal libs
from rcac_mcp.executor.base import (Executor, CommandResult, UserPath, TIMEOUT_EXIT_CODE,
                                    NOT_STARTED_EXIT_CODE, CONNECTION_LOST_EXIT_CODE)

# Public interface
__all__ = ['Executor', 'CommandResult', 'UserPath', 'TIMEOUT_EXIT_CODE', 'NOT_STARTED_EXIT_CODE',
           'CONNECTION_LOST_EXIT_CODE']
