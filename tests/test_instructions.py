# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The agent-facing instructions name exactly the registered tools and carry no documentation search."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pytest

from harness import SITE, served_instructions
from rcac_mcp.server import SERVER_INSTRUCTIONS, create_mcp_server
from rcac_mcp.site import HEADING, compose
from rcac_mcp.tools import TOOL_REGISTRY


REPO_ROOT = Path(__file__).resolve().parents[1]

# Backticked or snake_case terms in the instructions that are not tools: `findscratch` is a shell
# command on the cluster. Every entry must occur in the text and must not be a registered tool.
NON_TOOL_TERMS: frozenset[str] = frozenset({'findscratch'})

# Documentation-search guidance: the words doc, docs, documentation, any doc_ identifier, and
# "user guide(s)". Documentation search is served by a separate service, not by this server.
DOCS_GUIDANCE = re.compile(r'(?i)\bdoc(?:s|umentation)?\b|\bdoc_\w*|\buser[\s_-]?guides?\b')

# A tool list entry: "- name: description", lowercase snake_case followed by whitespace.
LISTED = re.compile(r'^- ([a-z_][a-z0-9_]*):(?=\s)', re.MULTILINE)
TICKED = re.compile(r'`([^`\s]+)`')
SNAKE = re.compile(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b')


def guidance_violations(text: str) -> list[str]:
    """Return every documentation-search phrase in `text`; an empty list means none."""
    return [f'documentation guidance: {m.group(0)!r}' for m in DOCS_GUIDANCE.finditer(text)]


def _occurs(term: str, text: str) -> bool:
    return re.search(rf'(?<![\w]){re.escape(term)}(?![\w])', text) is not None


def instruction_violations(text: str, names: Sequence[str],
                           allow: Iterable[str] = NON_TOOL_TERMS) -> list[str]:
    """Cross-check instruction text against registered tool names; an empty list means consistent."""
    violations: list[str] = []
    allow = frozenset(allow)
    registry = set(names)
    if not names:
        violations.append('tool registry is empty')
    if len(registry) != len(names):
        violations.append(f'tool registry has duplicate names: {sorted(names)!r}')
    listed = set(LISTED.findall(text))
    ticked = set(TICKED.findall(text))
    snake = set(SNAKE.findall(text))
    for name in sorted(registry - listed):
        violations.append(f'registered tool not listed: {name!r}')
    for name in sorted((listed | ticked | snake) - registry - allow):
        violations.append(f'unregistered name in instructions: {name!r}')
    for term in sorted(allow & registry):
        violations.append(f'allowlist names a registered tool: {term!r}')
    for term in sorted(allow):
        if not _occurs(term, text):
            violations.append(f'allowlist term absent from instructions: {term!r}')
    violations.extend(guidance_violations(text))
    return violations


def _registered() -> list[str]:
    return [tool.name for tool in TOOL_REGISTRY]


def _served_instructions() -> str:
    # The base part of what a client in the selected protocol era receives when it connects: the
    # site context after it is the site's text, which this check does not govern.
    served = served_instructions(create_mcp_server('none', site=SITE)) or ''
    assert served == compose(SERVER_INSTRUCTIONS, SITE), 'served instructions are not the composed text'
    return served.partition(f'\n\n{HEADING}\n\n')[0]


def _drop_sinfo(text: str) -> str:
    return re.sub(r'^- sinfo:.*\n', '', text, flags=re.MULTILINE)


@pytest.mark.unit
@pytest.mark.parametrize('source', [
    pytest.param(lambda: SERVER_INSTRUCTIONS, id='constant'),
    pytest.param(_served_instructions, id='served'),
])
def test_instructions_match_registry(source: Callable[[], str]) -> None:
    text = source()
    assert text, 'instructions are empty'
    assert instruction_violations(text, _registered()) == []


@pytest.mark.unit
def test_instructions_md_has_no_docs_guidance() -> None:
    text = (REPO_ROOT / 'INSTRUCTIONS.md').read_text()
    assert text.strip(), 'INSTRUCTIONS.md is empty'
    assert guidance_violations(text) == []


# Each case: (mutated text, extra allowlist terms, the violation the mutation must produce).
MUTATIONS = [
    pytest.param(SERVER_INSTRUCTIONS + '\n- doc_search: Search RCAC documentation\n', (),
                 "unregistered name in instructions: 'doc_search'", id='doc-entry'),
    pytest.param(SERVER_INSTRUCTIONS + '\n- job_magic: Run a job\n', (),
                 "unregistered name in instructions: 'job_magic'", id='unknown-entry'),
    pytest.param(_drop_sinfo(SERVER_INSTRUCTIONS), (),
                 "registered tool not listed: 'sinfo'", id='missing-entry'),
    pytest.param(SERVER_INSTRUCTIONS + '\nBefore answering, call `doc_search` first.\n', (),
                 "unregistered name in instructions: 'doc_search'", id='ticked-doc'),
    pytest.param(SERVER_INSTRUCTIONS + '\nThen call slurm_magic to finish.\n', (),
                 "unregistered name in instructions: 'slurm_magic'", id='unknown-snake'),
    pytest.param(SERVER_INSTRUCTIONS, ('myquota',),
                 "allowlist names a registered tool: 'myquota'", id='allowlist-launder'),
    pytest.param(SERVER_INSTRUCTIONS, ('qstat_legacy',),
                 "allowlist term absent from instructions: 'qstat_legacy'", id='allowlist-stale'),
    pytest.param(SERVER_INSTRUCTIONS + '\nBefore answering, search the RCAC user guides.\n', (),
                 "documentation guidance: 'user guides'", id='user-guides'),
    pytest.param(SERVER_INSTRUCTIONS + '\nWhen unsure, see the docs.\n', (),
                 "documentation guidance: 'docs'", id='docs-word'),
]


@pytest.mark.unit
@pytest.mark.parametrize('text, extra_allow, expected', MUTATIONS)
def test_cross_check_catches_mutation(text: str, extra_allow: tuple[str, ...], expected: str) -> None:
    names = _registered()
    assert text != SERVER_INSTRUCTIONS or extra_allow, 'the mutation changed nothing'
    assert instruction_violations(SERVER_INSTRUCTIONS, names) == []
    violations = instruction_violations(text, names, NON_TOOL_TERMS | set(extra_allow))
    assert expected in violations, violations
