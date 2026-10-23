# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The tool registry holds exactly the expected tools, and the server serves exactly those."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Sequence

import pytest

from harness import SITE, served_tools
from rcac_mcp.server import create_mcp_server
from rcac_mcp.tools import TOOL_REGISTRY


# Every tool change edits this set in the same commit as SERVER_INSTRUCTIONS and the README.
EXPECTED_TOOLS: frozenset[str] = frozenset({
    'average_wait',
    'cluster_context',
    'download_file',
    'jobcmd',
    'jobenv',
    'jobinfo',
    'jobscript',
    'list_directory',
    'myquota',
    'read_file',
    'run_command',
    'sacct',
    'sbatch',
    'scancel',
    'scontrol_show_job',
    'scontrol_show_node',
    'sfeatures',
    'showpartitions',
    'sinfo',
    'slist',
    'squeue',
    'storage_paths',
    'upload_file',
    'write_file',
})


def registry_violations(names: Sequence[str]) -> list[str]:
    """Return every way `names` differs from EXPECTED_TOOLS; an empty list means it matches."""
    violations: list[str] = []
    present = set(names)
    for name in sorted(EXPECTED_TOOLS - present):
        violations.append(f'missing tool: {name!r}')
    for name in sorted(present - EXPECTED_TOOLS):
        violations.append(f'unexpected tool: {name!r}')
    for name, count in sorted(Counter(names).items()):
        if count > 1:
            violations.append(f'duplicate tool: {name!r} registered {count} times')
    for name in sorted(present):
        if name.startswith('doc_'):
            violations.append(f'documentation tool registered: {name!r}')
    return violations


def module_absent(name: str) -> bool:
    """True when `name` cannot be found as a module; a missing parent package counts as absent."""
    try:
        return importlib.util.find_spec(name) is None
    except ModuleNotFoundError:
        return True


def non_bytecode_files(path: Path) -> list[str]:
    """Every file at or under `path` that is not compiled bytecode, relative to `path`'s parent."""
    files = [path] if not path.is_dir() else [f for f in path.rglob('*') if not f.is_dir()]
    return sorted(str(f.relative_to(path.parent)) for f in files if f.suffix != '.pyc')


def absence_violations(name: str) -> list[str]:
    """Return why `name` still imports, naming where it resolves and what that means; empty if absent.

    Bytecode alone is what a stale checkout leaves behind, and deleting it is the remedy. Any other
    file means the module was re-added, which is a regression to undo in the change that made it,
    not a leftover to delete.
    """
    if module_absent(name):
        return []
    spec = importlib.util.find_spec(name)
    locations = list(spec.submodule_search_locations or []) or [spec.origin]
    violations: list[str] = []
    for location in locations:
        path = Path(location)
        held = sorted(entry.name for entry in path.iterdir()) if path.is_dir() else [path.name]
        sources = non_bytecode_files(path)
        if sources:
            violations.append(
                f'{name} imports from {path}, which holds files that are not bytecode ({sources}): '
                f'the module has been re-added, and it must stay removed. These are not '
                f'stale-checkout leftovers; take them out of the change that re-adds them.'
            )
        else:
            violations.append(
                f'{name} still imports from {path} (holding {held}), and that is only bytecode. Git '
                f'leaves ignored files such as __pycache__/ and *.pyc behind when a checkout moves past '
                f'the removal; a directory holding only those imports as an empty namespace package, '
                f'and a bare .pyc imports as a module. Delete the leftover with `del {path}` and re-run.'
            )
    return violations


@pytest.mark.unit
def test_registry_matches_expected_tools() -> None:
    names = [tool.name for tool in TOOL_REGISTRY]
    assert names, 'TOOL_REGISTRY is empty'
    assert registry_violations(names) == []
    assert len(names) == len(EXPECTED_TOOLS)


@pytest.mark.unit
def test_served_tools_match_registry() -> None:
    names = sorted(served_tools(create_mcp_server('none', site=SITE)))
    assert names, 'the server serves no tools'
    assert registry_violations(names) == []
    assert set(names) == {tool.name for tool in TOOL_REGISTRY}


@pytest.mark.unit
@pytest.mark.parametrize('names, expected', [
    pytest.param(sorted(EXPECTED_TOOLS) + ['doc_search'], "documentation tool registered: 'doc_search'",
                 id='added-doc'),
    pytest.param(sorted(EXPECTED_TOOLS - {'sinfo'}), "missing tool: 'sinfo'",
                 id='missing'),
    pytest.param(sorted(EXPECTED_TOOLS) + ['sinfo'], "duplicate tool: 'sinfo'",
                 id='duplicate'),
])
def test_registry_check_catches_drift(names: list[str], expected: str) -> None:
    assert registry_violations(sorted(EXPECTED_TOOLS)) == []
    violations = registry_violations(names)
    assert any(v.startswith(expected) for v in violations), violations


@pytest.mark.unit
@pytest.mark.parametrize('name', [
    pytest.param('rcac_mcp.docs', id='rcac_mcp.docs'),
    pytest.param('rcac_mcp.tools.docs', id='rcac_mcp.tools.docs'),
])
def test_docs_modules_absent(name: str) -> None:
    assert module_absent(name), '\n'.join(absence_violations(name))
    with pytest.raises(ModuleNotFoundError) as excinfo:
        importlib.import_module(name)
    assert excinfo.value.name == name


@pytest.mark.unit
def test_absence_check_catches_namespace_leftover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A directory holding only __pycache__/ still imports as a namespace package.
    package = f'leftover_{uuid.uuid4().hex}'
    (tmp_path / package / 'sub' / '__pycache__').mkdir(parents=True)
    (tmp_path / package / '__init__.py').write_text('')
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        assert module_absent(f'{package}.missing')
        assert absence_violations(f'{package}.missing') == []
        assert not module_absent(f'{package}.sub')
        leftover = tmp_path / package / 'sub'
        [message] = absence_violations(f'{package}.sub')
        assert str(leftover) in message and "['__pycache__']" in message, message
        assert f'`del {leftover}`' in message, message
    finally:
        for key in [k for k in sys.modules if k == package or k.startswith(f'{package}.')]:
            del sys.modules[key]


def plant(root: Path, entries: Sequence[str]) -> None:
    """Create each entry under `root`: a trailing slash makes a directory, anything else an empty file."""
    for entry in entries:
        target = root / entry
        if entry.endswith('/'):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'')


@pytest.fixture
def scratch_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A uniquely named importable package on `sys.path`; its modules leave `sys.modules` afterwards."""
    package = f'scratch_{uuid.uuid4().hex}'
    plant(tmp_path, [f'{package}/__init__.py'])
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path / package
    for key in [k for k in sys.modules if k == package or k.startswith(f'{package}.')]:
        del sys.modules[key]


@pytest.mark.unit
@pytest.mark.parametrize('entries, located', [
    pytest.param(['sub/__pycache__/'], 'sub', id='empty-pycache'),
    pytest.param(['sub/__pycache__/mod.cpython-314.pyc'], 'sub', id='pycache-pyc'),
    pytest.param(['sub/inner/__pycache__/mod.cpython-314.pyc'], 'sub', id='nested-pyc'),
    pytest.param(['sub.pyc'], 'sub.pyc', id='bare-pyc'),
])
def test_absence_message_for_leftover_bytecode(scratch_package: Path, entries: list[str], located: str) -> None:
    # Bytecode alone is what a stale checkout leaves: the message explains that and gives the remedy.
    plant(scratch_package, entries)
    importlib.invalidate_caches()
    name = f'{scratch_package.name}.sub'
    assert not module_absent(name)
    [message] = absence_violations(name)
    leftover = scratch_package / located
    assert str(leftover) in message and 'only bytecode' in message, message
    assert f'`del {leftover}`' in message, message
    assert 're-added' not in message, message


@pytest.mark.unit
@pytest.mark.parametrize('entries, located, named', [
    pytest.param(['sub/__init__.py', 'sub/__pycache__/'], 'sub', '__init__.py', id='init'),
    pytest.param(['sub/mod.py', 'sub/__pycache__/mod.cpython-314.pyc'], 'sub', 'mod.py', id='module'),
    pytest.param(['sub/schema.sql'], 'sub', 'schema.sql', id='data'),
    pytest.param(['sub/inner/mod.py'], 'sub', 'mod.py', id='nested-module'),
    pytest.param(['sub.py'], 'sub.py', 'sub.py', id='module-file'),
])
def test_absence_message_for_readded_source(scratch_package: Path, entries: list[str], located: str,
                                            named: str) -> None:
    # Any file that is not bytecode means the module was re-added: never advise deleting it as a leftover.
    plant(scratch_package, entries)
    importlib.invalidate_caches()
    name = f'{scratch_package.name}.sub'
    assert not module_absent(name)
    [message] = absence_violations(name)
    assert str(scratch_package / located) in message and named in message, message
    assert 're-added' in message, message
    assert '`del' not in message and 'only bytecode' not in message, message
