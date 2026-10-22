# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The tool registry holds exactly the expected tools, and the server serves exactly those."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Sequence

import pytest

from conftest import need_git
from harness import served_tools
from rcac_mcp.server import create_mcp_server
from rcac_mcp.tools import TOOL_REGISTRY


# Every tool change edits this set in the same commit as SERVER_INSTRUCTIONS and the README.
EXPECTED_TOOLS: frozenset[str] = frozenset({
    'average_wait',
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


SANDBOX = Path(__file__).resolve().parents[1] / 'bin' / 'sandbox.sh'

# The program asked what git knows; a test points it elsewhere to take git away.
GIT = 'git'

# What git knows of a file: in its index, neither in its index nor ignored, or ignored.
GIT_CLASSES = ('tracked', 'untracked', 'ignored')


def leaf_entries(path: Path) -> list[Path]:
    """Every file or symlink at or under `path`, and every directory there that holds nothing.

    A symlink is a leaf, never followed: git records the link, not what it points to.
    """
    if path.is_symlink() or not path.is_dir():
        return [path]
    return [entry for entry in [path, *sorted(path.rglob('*'))]
            if entry.is_symlink() or not entry.is_dir() or not any(entry.iterdir())]


def git_env() -> dict[str, str]:
    """The environment git runs in: this one without any variable that picks a repository for it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') or k == 'GIT_CEILING_DIRECTORIES'}
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    return env


def ask_git(program: str, where: Path, args: Sequence[str], stdin: str = '') -> subprocess.CompletedProcess[str] | None:
    """Run git in `where` through bin/sandbox.sh; None when it cannot be started or does not finish.

    Repository-selecting variables (`GIT_DIR`, `GIT_INDEX_FILE` and the rest, as a git hook sets
    them) are dropped so git answers for `where`; `GIT_CEILING_DIRECTORIES` is kept. The sandbox's
    HOME and the dropped system config leave the repository's own ignore rules as the only ones.
    """
    try:
        return subprocess.run([str(SANDBOX), program, '-C', str(where), *args], input=stdin, env=git_env(),
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def git_classes(path: Path) -> dict[str, list[Path]] | None:
    """Sort every leaf entry at or under `path` by what git knows of it; None when git cannot say.

    Git cannot say when no process may start (the unit guard), when it is not on PATH, when `path`
    is not inside a git work tree, when any call fails, or when a file is in none of git's lists (a
    repository nested inside, which git lists as one directory, or a name stored differently on
    disk and in the index). None means the caller explains by suffix instead; this never raises.
    """
    if getattr(subprocess.Popen, 'unit_guard', False) or not SANDBOX.is_file():
        return None
    program = shutil.which(GIT)
    if program is None:
        return None
    where = path if path.is_dir() else path.parent
    found = ask_git(program, where, ['rev-parse', '--show-toplevel'])
    if found is None or found.returncode != 0 or not found.stdout.strip():
        return None
    try:
        entries = leaf_entries(path)
        top = Path(found.stdout.strip()).resolve()
        base = path.resolve()
        relative = {(base / entry.relative_to(path)).relative_to(top).as_posix(): entry for entry in entries}
        spec = f':(literal){base.relative_to(top).as_posix()}'
    except (OSError, ValueError):
        return None
    listings: dict[str, set[str]] = {}
    for kind, flags in (('tracked', ['--cached']), ('untracked', ['--others', '--exclude-standard']),
                        ('ignored', ['--others', '--ignored', '--exclude-standard'])):
        listed = ask_git(program, top, ['ls-files', '-z', *flags, '--', spec])
        if listed is None or listed.returncode != 0:
            return None
        listings[kind] = set(listed.stdout.split('\0')) & relative.keys()
    # Git lists files only: a directory that holds nothing is ignored or untracked by its rules.
    empty = [r for r in relative if relative[r].is_dir() and not relative[r].is_symlink()]
    if empty:
        checked = ask_git(program, top, ['check-ignore', '-z', '--stdin'], ''.join(f'{r}\0' for r in empty))
        if checked is None or checked.returncode not in (0, 1):
            return None
        ignored = set(checked.stdout.split('\0'))
        listings['ignored'] |= {r for r in empty if r in ignored}
        listings['untracked'] |= {r for r in empty if r not in ignored}
    if set().union(*listings.values()) != relative.keys() or sum(map(len, listings.values())) != len(relative):
        return None
    return {kind: [relative[r] for r in sorted(names)] for kind, names in listings.items()}


def covering(path: Path, chosen: Sequence[Path], entries: Sequence[Path]) -> list[Path]:
    """The fewest paths whose removal takes exactly `chosen` of `entries`, all at or under `path`.

    A directory stands for everything under it only when all of that is chosen, so a covering path
    never reaches an entry outside `chosen`.
    """
    picked = set(chosen)

    def all_chosen(directory: Path) -> bool:
        return all(e in picked for e in entries if e == directory or directory in e.parents)

    cover: set[Path] = set()
    for entry in chosen:
        lineage = [a for a in reversed(entry.parents) if a == path or path in a.parents] + [entry]
        cover.add(next(a for a in lineage if all_chosen(a)))
    return sorted(cover)


def suffix_explanation(name: str, path: Path) -> str:
    """Explain an importable `path` by file suffix alone: what is said where git cannot answer."""
    held = sorted(entry.name for entry in path.iterdir()) if path.is_dir() else [path.name]
    sources = non_bytecode_files(path)
    if sources:
        return (
            f'{name} imports from {path}, which holds files that are not bytecode ({sources}): '
            f'the module has been re-added, and it must stay removed. These are not '
            f'stale-checkout leftovers; take them out of the change that re-adds them.'
        )
    return (
        f'{name} still imports from {path} (holding {held}), and that is only bytecode. Git '
        f'leaves ignored files such as __pycache__/ and *.pyc behind when a checkout moves past '
        f'the removal; a directory holding only those imports as an empty namespace package, '
        f'and a bare .pyc imports as a module. Delete the leftover with `del {path}` and re-run.'
    )


def git_explanation(name: str, path: Path, classes: dict[str, list[Path]]) -> str:
    """Explain an importable `path` by what git knows of each file there, one line per class present."""
    entries = [e for files in classes.values() for e in files]

    def shown(files: list[Path]) -> list[str]:
        return [str(f.relative_to(path.parent)) + ('/' if f.is_dir() else '') for f in files]

    def removal(files: list[Path]) -> str:
        return 'del ' + ' '.join(shlex.quote(str(p)) for p in covering(path, files, entries))

    lines = [f'{name} imports from {path}. What git knows of the files there:']
    if files := classes['tracked']:
        lines.append(f'- tracked {shown(files)}: the module has been re-added, and it must stay removed. '
                     f'Take these files out of the change that re-adds them; they are not leftovers.')
    if files := classes['untracked']:
        lines.append(f'- untracked {shown(files)}: git neither tracks nor ignores them. Move out '
                     f'anything you mean to keep, delete the rest with '
                     f'`{removal(files)}`, and re-run.')
    if files := classes['ignored']:
        lines.append(f'- ignored {shown(files)}: leftovers of an older checkout, which git leaves '
                     f'behind when a checkout moves past the removal; a directory holding only '
                     f'those imports as an empty namespace package, and a bare .pyc as a module. '
                     f'Delete them with `{removal(files)}` and re-run.')
    if not classes['tracked'] and classes['untracked'] and classes['ignored']:
        lines.append(f'Git tracks nothing there, so `del {shlex.quote(str(path))}` takes it all, and the '
                     f'directory that would otherwise stay behind and still import.')
    return '\n'.join(lines)


def absence_violations(name: str) -> list[str]:
    """Return why `name` still imports, naming where it resolves and what that means; empty if absent.

    Each location is explained by what git knows of its files: a tracked file means the module was
    re-added, a regression to undo in the change that made it; an ignored or untracked file is a
    leftover to delete. Where git cannot answer, the explanation falls back to file suffixes.
    """
    if module_absent(name):
        return []
    spec = importlib.util.find_spec(name)
    locations = list(spec.submodule_search_locations or []) or [spec.origin]
    violations: list[str] = []
    for location in locations:
        path = Path(location)
        classes = git_classes(path)
        violations.append(suffix_explanation(name, path) if classes is None
                          else git_explanation(name, path, classes))
    return violations


@pytest.mark.unit
def test_registry_matches_expected_tools() -> None:
    names = [tool.name for tool in TOOL_REGISTRY]
    assert names, 'TOOL_REGISTRY is empty'
    assert registry_violations(names) == []
    assert len(names) == len(EXPECTED_TOOLS)


@pytest.mark.unit
def test_served_tools_match_registry() -> None:
    names = sorted(served_tools(create_mcp_server('none')))
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


@pytest.mark.integration
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


def git_in(root: Path, *args: str) -> None:
    """Run git in `root` through bin/sandbox.sh, failing the test if it fails."""
    subprocess.run([str(SANDBOX), 'git', '-C', str(root), *args], env=git_env(), check=True, capture_output=True)


@pytest.fixture
def scratch_repo(scratch_package: Path) -> Path:
    """`scratch_package` in a fresh git work tree that ignores bytecode and swap files."""
    need_git()
    git_in(scratch_package.parent, 'init', '-q')
    (scratch_package.parent / '.gitignore').write_text('__pycache__/\n*.py[cod]\n*.swp\n')
    return scratch_package


def explain(package: Path, entries: list[str], tracked: list[str]) -> str:
    """Plant `entries` in `package`, add `tracked` to git's index, and return the one explanation."""
    plant(package, entries)
    if tracked:
        git_in(package.parent, 'add', '--', *[f'{package.name}/{t}' for t in tracked])
    importlib.invalidate_caches()
    name = f'{package.name}.sub'
    assert not module_absent(name)
    [message] = absence_violations(name)
    return message


def class_lines(message: str) -> dict[str, str]:
    """The explanation's line for each class it names."""
    return {line.split()[1]: line for line in message.splitlines() if line.startswith('- ')}


def removals(line: str) -> list[str]:
    """The paths a line's `del` remedy names."""
    _, _, rest = line.partition('`del ')
    return shlex.split(rest.partition('`')[0]) if rest else []


@pytest.mark.integration
@pytest.mark.parametrize('entries, located, named', [
    pytest.param(['sub/__pycache__/'], 'sub', ['sub/__pycache__/'], id='empty-pycache'),
    pytest.param(['sub/__pycache__/mod.cpython-314.pyc'], 'sub', ['sub/__pycache__/mod.cpython-314.pyc'],
                 id='pycache-pyc'),
    pytest.param(['sub/inner/__pycache__/mod.cpython-314.pyc'], 'sub',
                 ['sub/inner/__pycache__/mod.cpython-314.pyc'], id='nested-pyc'),
    pytest.param(['sub.pyc'], 'sub.pyc', ['sub.pyc'], id='bare-pyc'),
    pytest.param(['sub/.mod.py.swp'], 'sub', ['sub/.mod.py.swp'], id='ignored-other'),
])
def test_absence_message_for_ignored_files(scratch_repo: Path, entries: list[str], located: str,
                                           named: list[str]) -> None:
    # Ignored files are what an older checkout leaves: delete them, and nothing was re-added.
    message = explain(scratch_repo, entries, [])
    lines = class_lines(message)
    assert set(lines) == {'ignored'}, message
    assert str(lines['ignored'].split(':')[0]) == f'- ignored {named}', message
    assert removals(lines['ignored']) == [str(scratch_repo / located)], message
    assert 're-added' not in message, message


@pytest.mark.integration
@pytest.mark.parametrize('entries, located, named', [
    pytest.param(['sub/.DS_Store'], 'sub', ['sub/.DS_Store'], id='ds-store'),
    # Python writes its temp file in __pycache__/, which ignores it; outside, the same name is untracked.
    pytest.param(['sub/mod.cpython-314.pyc.4242'], 'sub', ['sub/mod.cpython-314.pyc.4242'], id='pyc-temp'),
    pytest.param(['sub/mod.py'], 'sub', ['sub/mod.py'], id='uncommitted-source'),
    pytest.param(['sub/'], 'sub', ['sub/'], id='empty-directory'),
    pytest.param(['sub.py'], 'sub.py', ['sub.py'], id='uncommitted-module-file'),
])
def test_absence_message_for_untracked_files(scratch_repo: Path, entries: list[str], located: str,
                                             named: list[str]) -> None:
    # Untracked files belong to no commit: they get their own remedy, and nothing was re-added.
    message = explain(scratch_repo, entries, [])
    lines = class_lines(message)
    assert set(lines) == {'untracked'}, message
    assert lines['untracked'].split(':')[0] == f'- untracked {named}', message
    assert removals(lines['untracked']) == [str(scratch_repo / located)], message
    assert 're-added' not in message, message


@pytest.mark.integration
@pytest.mark.parametrize('entries, named', [
    pytest.param(['sub/__init__.py'], ['sub/__init__.py'], id='init'),
    pytest.param(['sub/mod.py'], ['sub/mod.py'], id='module'),
    pytest.param(['sub/schema.sql'], ['sub/schema.sql'], id='data'),
    pytest.param(['sub/inner/mod.py'], ['sub/inner/mod.py'], id='nested-module'),
    pytest.param(['sub.py'], ['sub.py'], id='module-file'),
])
def test_absence_message_for_tracked_files(scratch_repo: Path, entries: list[str], named: list[str]) -> None:
    # A tracked file means the module was re-added: say so, and never advise deleting it.
    message = explain(scratch_repo, entries, entries)
    lines = class_lines(message)
    assert set(lines) == {'tracked'}, message
    assert lines['tracked'].split(':')[0] == f'- tracked {named}', message
    assert 're-added' in lines['tracked'], message
    assert '`del' not in message, message


@pytest.mark.integration
@pytest.mark.parametrize('entries, tracked, expected', [
    pytest.param(['sub/mod.py', 'sub/.DS_Store', 'sub/__pycache__/mod.cpython-314.pyc'], ['sub/mod.py'],
                 {'tracked': [], 'untracked': ['sub/.DS_Store'], 'ignored': ['sub/__pycache__']},
                 id='all-three'),
    pytest.param(['sub/mod.py', 'sub/__pycache__/mod.cpython-314.pyc'], ['sub/mod.py'],
                 {'tracked': [], 'ignored': ['sub/__pycache__']}, id='tracked-and-ignored'),
    pytest.param(['sub/.DS_Store', 'sub/__pycache__/mod.cpython-314.pyc'], [],
                 {'untracked': ['sub/.DS_Store'], 'ignored': ['sub/__pycache__']}, id='untracked-and-ignored'),
    pytest.param(['sub/inner/mod.py', 'sub/inner/.DS_Store', 'sub/other/__pycache__/x.pyc'], ['sub/inner/mod.py'],
                 {'tracked': [], 'untracked': ['sub/inner/.DS_Store'], 'ignored': ['sub/other']},
                 id='nested'),
])
def test_absence_message_for_mixed_files(scratch_repo: Path, entries: list[str], tracked: list[str],
                                         expected: dict[str, list[str]]) -> None:
    # Each class present gets its own line and remedy; a removal never reaches a tracked file.
    message = explain(scratch_repo, entries, tracked)
    lines = class_lines(message)
    assert set(lines) == set(expected), message
    for kind, removed in expected.items():
        assert removals(lines[kind]) == [str(scratch_repo / r) for r in removed], message
    assert ('re-added' in message) == bool(tracked), message
    whole = f'`del {scratch_repo / "sub"}`'
    assert (whole in message.splitlines()[-1]) == (not tracked and len(expected) > 1), message
    for path in (Path(p) for line in lines.values() for p in removals(line)):
        assert not any((scratch_repo / t) == path or path in (scratch_repo / t).parents for t in tracked), message


SUFFIX_READDED = ('{name} imports from {path}, which holds files that are not bytecode ({sources}): the module '
                  'has been re-added, and it must stay removed. These are not stale-checkout leftovers; take '
                  'them out of the change that re-adds them.')
SUFFIX_BYTECODE = ('{name} still imports from {path} (holding {held}), and that is only bytecode. Git leaves '
                   'ignored files such as __pycache__/ and *.pyc behind when a checkout moves past the removal; '
                   'a directory holding only those imports as an empty namespace package, and a bare .pyc '
                   'imports as a module. Delete the leftover with `del {path}` and re-run.')


# Planted shapes and the suffix rule's message for each, word for word, with {name} and {path} open.
FALLBACK_ROWS = [
    pytest.param(['sub/.DS_Store', 'sub/__pycache__/mod.cpython-314.pyc'], 'sub',
                 SUFFIX_READDED.format(name='{name}', path='{path}', sources=['sub/.DS_Store']), id='mixed'),
    pytest.param(['sub/__pycache__/mod.cpython-314.pyc'], 'sub',
                 SUFFIX_BYTECODE.format(name='{name}', path='{path}', held=['__pycache__']), id='bytecode'),
    pytest.param(['sub/__pycache__/'], 'sub',
                 SUFFIX_BYTECODE.format(name='{name}', path='{path}', held=['__pycache__']), id='empty-pycache'),
    pytest.param(['sub.pyc'], 'sub.pyc',
                 SUFFIX_BYTECODE.format(name='{name}', path='{path}', held=['sub.pyc']), id='bare-pyc'),
    pytest.param(['sub.py'], 'sub.py',
                 SUFFIX_READDED.format(name='{name}', path='{path}', sources=['sub.py']), id='module-file'),
]


def fallback_message(package: Path, entries: list[str], located: str, expected: str) -> None:
    """Plant `entries` and assert the one explanation is `expected`, filled in."""
    plant(package, entries)
    importlib.invalidate_caches()
    name = f'{package.name}.sub'
    assert not module_absent(name)
    assert absence_violations(name) == [expected.format(name=name, path=package / located)]


def fake_git(tmp_path: Path, body: str) -> str:
    """A `git` stand-in under `tmp_path` running `body`; returns its path."""
    fake = tmp_path / 'fake-bin' / 'git'
    plant(tmp_path, ['fake-bin/git'])
    fake.write_text(f'#!/bin/sh\n{body}\n')
    fake.chmod(0o755)
    return str(fake)


@pytest.mark.integration
@pytest.mark.parametrize('condition', ['no-git', 'git-fails', 'git-answers-elsewhere', 'outside-work-tree'])
@pytest.mark.parametrize('entries, located, expected', FALLBACK_ROWS)
def test_absence_message_falls_back_without_git(scratch_package: Path, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path, condition: str, entries: list[str],
                                                located: str, expected: str) -> None:
    # Where git cannot answer, the explanation is the suffix rule's, word for word, and nothing raises.
    if condition == 'no-git':
        monkeypatch.setattr(sys.modules[__name__], 'GIT', f'git-missing-{uuid.uuid4().hex}')
    elif condition == 'git-fails':
        monkeypatch.setattr(sys.modules[__name__], 'GIT', fake_git(tmp_path, 'exit 3'))
    elif condition == 'git-answers-elsewhere':
        monkeypatch.setattr(sys.modules[__name__], 'GIT', fake_git(tmp_path, 'echo /nonexistent-top'))
    else:
        monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    fallback_message(scratch_package, entries, located, expected)


# Git asks check-ignore only about a directory that holds nothing, so only that shape reaches it.
MIDWAY_ROWS = [pytest.param('ls-files', *row.values, id=f'{row.id}-ls-files') for row in FALLBACK_ROWS] + [
    pytest.param('check-ignore', *row.values, id=f'{row.id}-check-ignore') for row in FALLBACK_ROWS
    if row.id == 'empty-pycache'] + [
    pytest.param('--others --exclude-standard', *row.values, id=f'{row.id}-omits-untracked') for row in FALLBACK_ROWS
    if row.id == 'mixed']


@pytest.mark.integration
@pytest.mark.parametrize('failing, entries, located, expected', MIDWAY_ROWS)
def test_absence_message_falls_back_when_git_fails_midway(scratch_repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                          tmp_path: Path, failing: str, entries: list[str],
                                                          located: str, expected: str) -> None:
    # Git finds the work tree, then one later call fails or leaves a file out: no partial answer is
    # taken for a whole one.
    real = shutil.which('git')
    answer = 'exit 0' if failing.startswith('--') else 'echo fatal >&2; exit 128'
    body = f'case " $* " in *" {failing} "*) {answer} ;; esac\nexec {shlex.quote(str(real))} "$@"'
    monkeypatch.setattr(sys.modules[__name__], 'GIT', fake_git(tmp_path, body))
    fallback_message(scratch_repo, entries, located, expected)


@pytest.mark.unit
@pytest.mark.parametrize('ci, outcome', [
    pytest.param('true', pytest.fail.Exception, id='ci'),
    pytest.param(None, pytest.skip.Exception, id='local'),
    pytest.param('false', pytest.skip.Exception, id='ci-false'),
])
def test_a_missing_git_fails_on_ci_and_skips_elsewhere(ci: str | None, outcome: type[BaseException],
                                                        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if ci is None:
        monkeypatch.delenv('CI', raising=False)
    else:
        monkeypatch.setenv('CI', ci)
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    # Caught as BaseException: a skip that escaped a narrower `raises` would skip this test, not fail it.
    with pytest.raises(BaseException) as raised:
        need_git()
    assert raised.type is outcome and 'git is not on PATH' in str(raised.value), raised
    plant(tmp_path, ['present/git'])
    (tmp_path / 'present' / 'git').chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path / 'present'))
    need_git()


@pytest.mark.integration
@pytest.mark.parametrize('variables', [
    pytest.param(['GIT_DIR'], id='git-dir'),
    pytest.param(['GIT_WORK_TREE'], id='work-tree'),
    pytest.param(['GIT_INDEX_FILE'], id='index-file'),
    pytest.param(['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'], id='all'),
])
def test_absence_message_ignores_git_hook_environment(scratch_repo: Path, tmp_path_factory: pytest.TempPathFactory,
                                                      monkeypatch: pytest.MonkeyPatch, variables: list[str]) -> None:
    # A git hook exports the repository it runs for; the explanation still answers for the module's own.
    decoy = tmp_path_factory.mktemp('decoy')
    git_in(decoy, 'init', '-q')
    hook = {'GIT_DIR': scratch_repo.parent / '.git', 'GIT_WORK_TREE': decoy,
            'GIT_INDEX_FILE': decoy / '.git' / 'index'}
    for variable in variables:
        monkeypatch.setenv(variable, str(hook[variable]))
    message = explain(scratch_repo, ['sub/mod.py'], ['sub/mod.py'])
    assert set(class_lines(message)) == {'tracked'}, message


@pytest.mark.integration
@pytest.mark.parametrize('shape', ['tracked-symlink', 'nested-repository', 'unreadable-directory'])
def test_absence_message_for_odd_entries(scratch_repo: Path, tmp_path: Path, shape: str) -> None:
    # A symlink is a file to git; a repository inside, or a directory nothing can read, is not git's to answer.
    sub = scratch_repo / 'sub'
    name = f'{scratch_repo.name}.sub'
    if shape == 'tracked-symlink':
        plant(tmp_path, ['target/mod.py', f'{scratch_repo.name}/sub/'])
        (sub / 'link').symlink_to(tmp_path / 'target', target_is_directory=True)
        git_in(scratch_repo.parent, 'add', '--', f'{scratch_repo.name}/sub/link')
    elif shape == 'nested-repository':
        plant(sub, ['vendor/mod.py'])
        git_in(sub / 'vendor', 'init', '-q')
    else:
        plant(sub, ['__pycache__/mod.cpython-314.pyc', 'locked/'])
        (sub / 'locked').chmod(0)
    importlib.invalidate_caches()
    try:
        assert not module_absent(name)
        [message] = absence_violations(name)
    finally:
        if shape == 'unreadable-directory':
            (sub / 'locked').chmod(0o755)
    if shape == 'tracked-symlink':
        assert set(class_lines(message)) == {'tracked'}, message
    else:
        assert message == suffix_explanation(name, sub), message
