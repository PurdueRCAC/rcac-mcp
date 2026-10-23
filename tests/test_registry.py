# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The tool registry holds exactly the expected tools, and the server serves exactly those."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import pwd
import shlex
import shutil
import socket
import subprocess
import sys
import unicodedata
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


def leaf_entries(path: Path) -> list[Path]:
    """Every file or symlink at or under `path`, and every directory there that holds nothing.

    A symlink is a leaf, never followed: git records the link, not what it points to.
    """
    if path.is_symlink() or not path.is_dir():
        return [path]
    return [entry for entry in [path, *sorted(path.rglob('*'))]
            if entry.is_symlink() or not entry.is_dir() or not any(entry.iterdir())]


# What bin/sandbox.sh points into its throwaway directory. Git finds the developer's global config,
# the files it includes and every `~` in a config value through them, so each is handed back.
HOME_VARIABLES = ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME')

# What a git hook exports to point git at its own repository, index or object store (the list
# `git rev-parse --local-env-vars` prints, less the configuration it carries), and what changes how
# git reads the pathspec given here. The configuration variables (`GIT_CONFIG_*`) are the
# developer's, and stay.
REDIRECTING = frozenset({
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_IMPLICIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE',
    'GIT_NO_REPLACE_OBJECTS', 'GIT_REPLACE_REF_BASE', 'GIT_PREFIX', 'GIT_INTERNAL_SUPER_PREFIX',
    'GIT_LITERAL_PATHSPECS', 'GIT_GLOB_PATHSPECS', 'GIT_NOGLOB_PATHSPECS', 'GIT_ICASE_PATHSPECS',
})

# Nothing the developer's configuration names may run while git is asked: a file-system monitor is
# a hook command or a daemon git starts, and hooks run when git writes its index.
READ_ONLY = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null')


def git_env() -> dict[str, str]:
    """The environment git runs in: this one without what points git at another repository.

    `GIT_OPTIONAL_LOCKS=0` keeps git from refreshing, and so writing, the index it reads.
    """
    env = {k: v for k, v in os.environ.items() if k not in REDIRECTING}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    return env


def developer_home() -> list[str]:
    """`env` arguments giving git this process's own HOME and XDG directories inside bin/sandbox.sh."""
    return [*(a for k in HOME_VARIABLES if k not in os.environ for a in ('-u', k)),
            *(f'{k}={os.environ[k]}' for k in HOME_VARIABLES if k in os.environ)]


def ask_git(program: str, where: Path, args: Sequence[str], stdin: str = '') -> subprocess.CompletedProcess[str] | None:
    """Ask the developer's own git, read-only, in `where`; None when it cannot be started or does not finish.

    It runs through bin/sandbox.sh with the developer's HOME and XDG directories handed back, so it
    reads exactly the configuration their `git status` reads: global and system config, includes,
    `GIT_CONFIG_*` and every excludes file. Only what points it at another repository is dropped
    (`git_env`), and nothing that configuration names can run (`READ_ONLY`).
    """
    try:
        return subprocess.run([str(SANDBOX), 'env', *developer_home(), program, *READ_ONLY, '-C', str(where), *args],
                              input=stdin, env=git_env(), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def comparable(name: str) -> str:
    """`name` in the form git's listings are matched in: composed (NFC) on macOS, its own bytes elsewhere.

    macOS file systems take either Unicode form of a name as the same name, and git there prints it
    composed or as stored on disk according to `core.precomposeUnicode`; composing both sides
    matches whichever it prints. Elsewhere two forms are two names, and git prints them as stored.
    """
    return unicodedata.normalize('NFC', name) if sys.platform == 'darwin' else name


def special(entry: Path) -> bool:
    """True when `entry` is neither a file, a directory nor a symlink: a FIFO, a socket or a device."""
    return not entry.is_symlink() and not entry.is_dir() and not entry.is_file()


def git_classes(path: Path) -> dict[str, list[Path]] | None:
    """Sort every leaf entry at or under `path` by what git knows of it, as the developer's git status does.

    Returns the classes, `tracked`, `untracked` and `ignored`; under `unknown` the special files
    there (`special`), which git lists in no class; and under `nested` the repositories
    nested there, each of which git lists as one entry in its class and does not look into. None
    when git cannot be asked or does not answer: no process may start (the unit guard),
    bin/sandbox.sh or git is missing, `path` is not inside a git work tree, a call fails, or a file
    is in none of git's lists or in more than one. The caller explains by suffix instead; this
    never raises.
    """
    if getattr(subprocess.Popen, 'unit_guard', False) or not SANDBOX.is_file():
        return None
    program = shutil.which(GIT)
    if program is None:
        return None
    # Git is asked from the parent about `path` by name, so a symlinked location is the link git
    # records, and every name is relative to the parent, as git prints it.
    where = path.absolute().parent
    found = ask_git(program, where, ['rev-parse', '--show-toplevel'])
    if found is None or found.returncode != 0 or not found.stdout.strip():
        return None
    try:
        # A top the parent does not lie under is no answer for it.
        where.resolve().relative_to(Path(found.stdout.strip()).resolve())
        relative = {comparable(entry.relative_to(path.parent).as_posix()): entry for entry in leaf_entries(path)}
    except (OSError, ValueError, RuntimeError):
        return None
    listings: dict[str, list[str]] = {}
    for kind, flags in (('tracked', ['--cached']), ('untracked', ['--others', '--exclude-standard']),
                        ('ignored', ['--others', '--ignored', '--exclude-standard'])):
        listed = ask_git(program, where, ['ls-files', '-z', *flags, '--', f':(literal){path.name}'])
        if listed is None or listed.returncode != 0:
            return None
        listings[kind] = [comparable(n).rstrip('/') for n in listed.stdout.split('\0') if n]
    # A repository nested inside is listed as one entry, its directory, and none of its files: that
    # directory is the leaf, in the class git lists it under.
    named = {n for names in listings.values() for n in names}
    nested: dict[str, Path] = {}
    for r, entry in relative.items():
        for n in named:
            top = path.parent.joinpath(*entry.relative_to(path.parent).parts[:n.count('/') + 1])
            if r.startswith(f'{n}/') and os.path.lexists(top / '.git'):
                nested[n] = top
    relative = {r: e for r, e in relative.items() if not any(r.startswith(f'{n}/') for n in nested)} | nested
    # Git lists files only: a directory that holds nothing is ignored or untracked by its rules,
    # unless git lists it already, as it lists a gitlink whose submodule is not checked out.
    empty = [r for r, entry in relative.items()
             if r not in nested and r not in named and entry.is_dir() and not entry.is_symlink()]
    if empty:
        checked = ask_git(program, where, ['check-ignore', '-z', '--stdin'], ''.join(f'{r}\0' for r in empty))
        if checked is None or checked.returncode not in (0, 1):
            return None
        ignored = set(checked.stdout.split('\0'))
        listings['ignored'] += [r for r in empty if r in ignored]
        listings['untracked'] += [r for r in empty if r not in ignored]
    placed = Counter(n for names in listings.values() for n in set(names))
    # Git records no FIFO, socket or device and lists one nowhere: it is unknown to git.
    unknown = [r for r, entry in relative.items() if r not in nested and special(entry) and placed[r] == 0]
    if any(placed[r] != 1 for r in relative if r not in unknown):
        return None
    classes = {kind: [relative[r] for r in sorted(set(names) & relative.keys())] for kind, names in listings.items()}
    return classes | {'unknown': [relative[r] for r in sorted(unknown)], 'nested': [nested[n] for n in sorted(nested)]}


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
    entries = [e for kind in ('tracked', 'untracked', 'ignored', 'unknown') for e in classes[kind]]

    def shown(files: list[Path]) -> list[str]:
        return [str(f.relative_to(path.parent)) + ('/' if f.is_dir() and not f.is_symlink() else '')
                for f in files]

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
    if files := classes['unknown']:
        lines.append(f'- unknown {shown(files)}: unknown to git, which records no FIFO, socket or device and '
                     f'lists none. Once nothing uses them, delete them with `{removal(files)}` and re-run.')
    if not classes['tracked'] and sum(bool(classes[k]) for k in ('untracked', 'ignored', 'unknown')) > 1:
        lines.append(f'Git tracks nothing there, so `del {shlex.quote(str(path))}` takes it all, and the '
                     f'directory that would otherwise stay behind and still import.')
    if nested := classes['nested']:
        lines.append(f'Each of {shown(nested)} is a git repository nested there, which git lists as one entry '
                     f'and does not look into; move it out first if you mean to keep it.')
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
        violations.append(suffix_explanation(name, path) if classes is None else git_explanation(name, path, classes))
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
def scratch_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch configuration for every git the test asks, returned as its HOME.

    HOME is an empty scratch directory, the system config an empty scratch file, and no
    `GIT_CONFIG_*` variable is set, so the developer's own configuration never reaches a control; a
    control that wants one plants it.
    """
    home = tmp_path_factory.mktemp('home')
    for variable in [k for k in os.environ if k.startswith('GIT_CONFIG') or k == 'XDG_CONFIG_HOME']:
        monkeypatch.delenv(variable)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', str(write(home / '.system-gitconfig', '')))
    return home


@pytest.fixture
def scratch_package(tmp_path: Path, scratch_home: Path, monkeypatch: pytest.MonkeyPatch):
    """A uniquely named importable package on `sys.path`; its modules leave `sys.modules` afterwards.

    Git asked about it reads `scratch_home`'s configuration.
    """
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
def scratch_repo(scratch_package: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`scratch_package` in a fresh git work tree that ignores bytecode and swap files.

    Every configuration file git reads there is a scratch one, so the developer's are never read;
    the one exception is the defaults a git distribution builds in (scope `unknown`, as Apple's
    git reads them from its own installation).
    """
    need_git()
    top = scratch_package.parent
    git_in(top, 'init', '-q')
    (top / '.gitignore').write_text('__pycache__/\n*.py[cod]\n*.swp\n')
    shown = ask_git(GIT, top, ['config', '--list', '--show-scope', '--show-origin', '-z'])
    assert shown is not None and shown.returncode == 0, shown
    fields = shown.stdout.split('\0')
    read = {(scope, (top / origin.removeprefix('file:')).resolve())
            for scope, origin in zip(fields[0::3], fields[1::3]) if origin.startswith('file:')}
    scratch = tmp_path_factory.getbasetemp().resolve()
    assert ('local', (top / '.git' / 'config').resolve()) in read, read
    assert all(scratch in f.parents for scope, f in read if scope != 'unknown'), read
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    assert not any(real_home in f.parents and scratch not in f.parents for _, f in read), read
    return scratch_package


def status_classes(top: Path) -> dict[str, str]:
    """What the developer's `git status --ignored` shows in `top`: each path it lists, and its class.

    Run as the developer runs it, outside any hook, with this process's HOME and XDG directories:
    what a hook exports to point git at its own repository or index (`REDIRECTING`) is dropped.
    """
    unset = [a for k in ('HOME', 'XDG_CONFIG_HOME') if k not in os.environ for a in ('-u', k)]
    home = [*unset, *(f'{k}={os.environ[k]}' for k in ('HOME', 'XDG_CONFIG_HOME') if k in os.environ)]
    outside_hook = {k: os.environ[k] for k in os.environ.keys() - REDIRECTING}
    shown = subprocess.run([str(SANDBOX), 'env', *home, 'git', '-C', str(top), 'status', '--porcelain', '-z',
                            '--ignored', '--untracked-files=all'], env=outside_hook, capture_output=True, text=True,
                           check=True)
    return {comparable(entry[3:]): {'??': 'untracked', '!!': 'ignored'}.get(entry[:2], 'tracked')
            for entry in shown.stdout.split('\0') if entry}


def assert_classes_are_status(message: str, package: Path) -> None:
    """Every file the explanation names is in the class the developer's `git status --ignored` gives it.

    A tracked file git status does not list is unchanged, and an unknown one it never lists; a path
    it lists with a trailing slash stands for everything under it. Names are relative to `package`,
    as the explanation shows them.
    """
    status = status_classes(package.parent)
    for kind, line in class_lines(message).items():
        for shown in ast.literal_eval(line.split(':')[0].split(' ', 2)[2]):
            # Git status never lists a directory that holds nothing.
            if shown.endswith('/') and not any((package / shown).iterdir()):
                continue
            name = comparable(f'{package.name}/{shown}'.rstrip('/'))
            listed = [c for p, c in status.items() if p.rstrip('/') == name or (p.endswith('/') and name.startswith(p))]
            want = [] if kind == 'unknown' or (kind == 'tracked' and not listed) else [kind]
            assert listed == want, (name, kind, status, message)


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
    assert_classes_are_status(message, scratch_repo)


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
    assert_classes_are_status(message, scratch_repo)


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
    assert_classes_are_status(message, scratch_repo)


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
    assert_classes_are_status(message, scratch_repo)


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


FATAL = 'echo fatal >&2; exit 128'

# Git asks check-ignore only about a directory that holds nothing, so only that shape reaches it. A
# listing that leaves a file out, or names one a second time, is no answer either.
MIDWAY_ROWS = [pytest.param('ls-files', FATAL, *row.values, id=f'{row.id}-ls-files') for row in FALLBACK_ROWS] + [
    pytest.param('check-ignore', FATAL, *row.values, id=f'{row.id}-check-ignore') for row in FALLBACK_ROWS
    if row.id == 'empty-pycache'] + [
    pytest.param('--others --exclude-standard', 'exit 0', *row.values, id=f'{row.id}-omits-untracked')
    for row in FALLBACK_ROWS if row.id == 'mixed'] + [
    pytest.param('--ignored', '"$real" "$@"; printf \'sub/.DS_Store\\000\'; exit', *row.values,
                 id=f'{row.id}-lists-twice') for row in FALLBACK_ROWS if row.id == 'mixed']


@pytest.mark.integration
@pytest.mark.parametrize('failing, answer, entries, located, expected', MIDWAY_ROWS)
def test_absence_message_falls_back_when_git_fails_midway(scratch_repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                          tmp_path: Path, failing: str, answer: str,
                                                          entries: list[str], located: str, expected: str) -> None:
    # Git finds the work tree, then one later call fails, leaves a file out or lists one twice: no
    # partial answer is taken for a whole one.
    real = shlex.quote(str(shutil.which('git')))
    body = f'real={real}\ncase " $* " in *" {failing} "*) {answer} ;; esac\nexec "$real" "$@"'
    monkeypatch.setattr(sys.modules[__name__], 'GIT', fake_git(tmp_path, body))
    fallback_message(scratch_repo, entries, located, expected)


# What bin/sandbox.sh needs on PATH to start; a PATH of these alone holds no git.
SANDBOX_NEEDS = ('dirname', 'mktemp', 'mkdir', 'rm')


@pytest.mark.integration
@pytest.mark.parametrize('ci, git, outcome', [
    pytest.param('true', None, pytest.fail.Exception, id='ci'),
    pytest.param(None, None, pytest.skip.Exception, id='local'),
    pytest.param('false', None, pytest.skip.Exception, id='ci-false'),
    pytest.param('true', 'echo git version 0', None, id='ci-present'),
    pytest.param(None, 'echo git version 0', None, id='local-present'),
    pytest.param('true', 'echo no developer tools >&2; exit 1', pytest.fail.Exception, id='ci-broken'),
    pytest.param(None, 'echo no developer tools >&2; exit 1', pytest.skip.Exception, id='local-broken'),
])
def test_a_missing_git_fails_on_ci_and_skips_elsewhere(ci: str | None, git: str | None,
                                                        outcome: type[BaseException] | None,
                                                        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # need_git never skips on CI: it returns when git on PATH runs, and fails when there is none or
    # it does not run (`git --version` exits nonzero, as macOS's /usr/bin/git does without the
    # Command Line Tools). Every outcome is caught, a skip included, so this test cannot skip
    # itself: a need_git that skips where it must not fails it.
    if ci is None:
        monkeypatch.delenv('CI', raising=False)
    else:
        monkeypatch.setenv('CI', ci)
    farm = tmp_path / 'farm'
    farm.mkdir()
    for program in SANDBOX_NEEDS:
        found = shutil.which(program)
        assert found is not None, program
        (farm / program).symlink_to(found)
    directories = [str(farm)]
    if git is not None:
        directories.insert(0, str(Path(fake_git(tmp_path, git)).parent))
    monkeypatch.setenv('PATH', os.pathsep.join(directories))
    try:
        need_git()
    except BaseException as raised:
        got: BaseException | None = raised
    else:
        got = None
    assert (type(got) if got is not None else None) is outcome, got
    reason = 'git is not on PATH' if git is None else 'does not run here'
    assert got is None or reason in str(got), got


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
    # The oracle answers for the module's repository too, as the developer's git status outside the hook.
    assert_classes_are_status(message, scratch_repo)


# `café.txt` with its accent as a combining mark: how macOS has stored the name, and git may print it composed.
DECOMPOSED = 'cafe\u0301.txt'


@pytest.mark.integration
@pytest.mark.parametrize('shape, expected', [
    pytest.param('tracked-symlink', 'tracked', id='tracked-symlink'),
    pytest.param('tracked-symlink-location', 'tracked', id='tracked-symlink-location'),
    pytest.param('untracked-symlink-location', 'untracked', id='untracked-symlink-location'),
    pytest.param('decomposed-name', 'untracked', id='decomposed-name'),
    pytest.param('decomposed-name-unprecomposed', 'untracked', id='decomposed-name-unprecomposed'),
    pytest.param('tracked-decomposed-name', 'tracked', id='tracked-decomposed-name'),
    pytest.param('unreadable-directory', 'fallback', id='unreadable-directory'),
    pytest.param('above-ceiling', 'fallback', id='above-ceiling'),
])
def test_absence_message_for_odd_entries(scratch_repo: Path, tmp_path_factory: pytest.TempPathFactory,
                                         monkeypatch: pytest.MonkeyPatch, shape: str, expected: str) -> None:
    # A symlink is a file to git, the location itself included, and a name matches in whichever
    # Unicode form git prints it. A directory nothing can read, or a work tree above
    # GIT_CEILING_DIRECTORIES, falls back.
    sub = scratch_repo / 'sub'
    top = scratch_repo.parent
    name = f'{scratch_repo.name}.sub'
    if shape == 'tracked-symlink':
        plant(top, ['target/mod.py', f'{scratch_repo.name}/sub/'])
        (sub / 'link').symlink_to(top / 'target', target_is_directory=True)
        git_in(top, 'add', '--', f'{scratch_repo.name}/sub/link')
    elif shape.endswith('symlink-location'):
        target = tmp_path_factory.mktemp('target')
        plant(target, ['__pycache__/mod.cpython-314.pyc'])
        sub.symlink_to(target, target_is_directory=True)
        if expected == 'tracked':
            git_in(top, 'add', '--', f'{scratch_repo.name}/sub')
    elif shape.endswith('decomposed-name') or shape.endswith('unprecomposed'):
        plant(sub, [DECOMPOSED])
        if shape.endswith('unprecomposed'):
            git_in(top, 'config', 'core.precomposeUnicode', 'false')
        if expected == 'tracked':
            git_in(top, 'add', '--', f'{scratch_repo.name}/sub/{DECOMPOSED}')
    elif shape == 'unreadable-directory':
        plant(sub, ['__pycache__/mod.cpython-314.pyc', 'locked/'])
        (sub / 'locked').chmod(0)
    else:
        plant(sub, ['mod.py'])
        git_in(top, 'add', '--', f'{scratch_repo.name}/sub/mod.py')
        monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(top))
    importlib.invalidate_caches()
    try:
        assert not module_absent(name)
        [message] = absence_violations(name)
    finally:
        if shape == 'unreadable-directory':
            (sub / 'locked').chmod(0o755)
    if expected == 'fallback':
        assert message == suffix_explanation(name, sub), message
    else:
        lines = class_lines(message)
        assert set(lines) == {expected}, message
        assert removals(lines[expected]) == ([str(sub)] if expected == 'untracked' else []), message
        assert ('`del' in message) == (expected == 'untracked'), message
        if shape.endswith('symlink-location'):
            assert lines[expected].split(':')[0] == f"- {expected} ['sub']", message


def write(path: Path, text: str) -> Path:
    """Write `text` to `path`, making its directories; returns `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.integration
@pytest.mark.parametrize('setting, expected', [
    pytest.param('default-ignore', 'ignored', id='default-ignore'),
    pytest.param('xdg-ignore', 'ignored', id='xdg-ignore'),
    pytest.param('gitconfig', 'ignored', id='gitconfig'),
    pytest.param('xdg-config', 'ignored', id='xdg-config'),
    pytest.param('gitconfig-over-xdg-config', 'ignored', id='gitconfig-over-xdg-config'),
    pytest.param('git-config-global', 'ignored', id='git-config-global'),
    pytest.param('git-config-global-empty', 'untracked', id='git-config-global-empty'),
    pytest.param('include', 'ignored', id='include'),
    pytest.param('include-if-gitdir', 'ignored', id='include-if-gitdir'),
    pytest.param('repository-own', 'untracked', id='repository-own'),
    pytest.param('repository-own-home', 'ignored', id='repository-own-home'),
    pytest.param('git-config-count', 'ignored', id='git-config-count'),
    pytest.param('system-config', 'ignored', id='system-config'),
    pytest.param('no-system', 'untracked', id='no-system'),
])
def test_absence_message_follows_developer_excludes(scratch_repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                    setting: str, expected: str) -> None:
    # The developer's own git decides what is ignored, by every configuration it reads: global and
    # system files, the files they include, `~` against the developer's HOME, `GIT_CONFIG_*`, and a
    # repository's own excludes file, which wins. Each row is checked against `git status --ignored`.
    home = Path(os.environ['HOME'])
    top = scratch_repo.parent
    excludes = write(home / 'excludes', '.DS_Store\n')
    nothing = write(home / 'nothing', '')
    if setting == 'default-ignore':
        write(home / '.config' / 'git' / 'ignore', '.DS_Store\n')
    elif setting == 'xdg-ignore':
        write(home / '.config' / 'git' / 'ignore', '')
        write(home / 'xdg' / 'git' / 'ignore', '.DS_Store\n')
        monkeypatch.setenv('XDG_CONFIG_HOME', str(home / 'xdg'))
    elif setting == 'gitconfig':
        write(home / '.gitconfig', '[core]\n\texcludesFile = ~/excludes\n')
    elif setting == 'xdg-config':
        write(home / '.config' / 'git' / 'config', f'[core]\n\texcludesFile = {excludes}\n')
    elif setting == 'gitconfig-over-xdg-config':
        write(home / '.config' / 'git' / 'config', f'[core]\n\texcludesFile = {nothing}\n')
        write(home / '.gitconfig', '[core]\n\texcludesFile = ~/excludes\n')
    elif setting == 'git-config-global':
        write(home / '.gitconfig', f'[core]\n\texcludesFile = {nothing}\n')
        chosen = write(home / 'chosen', '[core]\n\texcludesFile = ~/excludes\n')
        monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(chosen))
    elif setting == 'git-config-global-empty':
        write(home / '.gitconfig', '[core]\n\texcludesFile = ~/excludes\n')
        monkeypatch.setenv('GIT_CONFIG_GLOBAL', '')
    elif setting == 'include':
        write(home / '.gitconfig', '[include]\n\tpath = ~/.gitconfig.local\n')
        write(home / '.gitconfig.local', '[core]\n\texcludesFile = ~/excludes\n')
    elif setting == 'include-if-gitdir':
        # `gitdir:~/` matches only a repository under the developer's HOME, so HOME is the top here.
        monkeypatch.setenv('HOME', str(top))
        write(top / '.gitconfig', '[includeIf "gitdir:~/"]\n\tpath = ~/conditional\n')
        write(top / 'conditional', '[core]\n\texcludesFile = ~/excludes\n')
        write(top / 'excludes', '.DS_Store\n')
    elif setting == 'repository-own':
        write(home / '.config' / 'git' / 'ignore', '.DS_Store\n')
        git_in(top, 'config', 'core.excludesFile', str(nothing))
    elif setting == 'repository-own-home':
        git_in(top, 'config', 'core.excludesFile', '~/excludes')
    elif setting == 'git-config-count':
        monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
        monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.excludesFile')
        monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(excludes))
    else:
        write(Path(os.environ['GIT_CONFIG_SYSTEM']), f'[core]\n\texcludesFile = {excludes}\n')
        if setting == 'no-system':
            monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    message = explain(scratch_repo, ['sub/.DS_Store'], [])
    lines = class_lines(message)
    assert set(lines) == {expected}, message
    assert lines[expected].split(':')[0] == f"- {expected} ['sub/.DS_Store']", message
    assert removals(lines[expected]) == [str(scratch_repo / 'sub')], message
    assert_classes_are_status(message, scratch_repo)


@pytest.mark.integration
@pytest.mark.parametrize('shape, expected', [
    pytest.param('untracked', {'untracked': ['sub/vendor/']}, id='untracked'),
    pytest.param('untracked-bytecode', {'untracked': ['sub/vendor/']}, id='untracked-bytecode'),
    pytest.param('ignored', {'ignored': ['sub/vendor/']}, id='ignored'),
    pytest.param('beside-tracked', {'tracked': ['sub/mod.py'], 'untracked': ['sub/vendor/']}, id='beside-tracked'),
    pytest.param('gitlink', {'tracked': ['sub/vendor/']}, id='gitlink'),
])
def test_absence_message_for_nested_repositories(scratch_repo: Path, shape: str,
                                                  expected: dict[str, list[str]]) -> None:
    # Git lists a repository nested inside as one entry and never looks into it: the explanation
    # names that entry in git's class for it, says why, and says "re-added" only when git tracks it.
    top = scratch_repo.parent
    sub = scratch_repo / 'sub'
    plant(sub, ['vendor/x.pyc' if shape == 'untracked-bytecode' else 'vendor/mod.py'])
    git_in(sub / 'vendor', 'init', '-q')
    if shape == 'ignored':
        write(top / '.gitignore', '__pycache__/\n*.py[cod]\nvendor/\n')
    elif shape == 'beside-tracked':
        plant(sub, ['mod.py'])
        git_in(top, 'add', '--', f'{scratch_repo.name}/sub/mod.py')
    elif shape == 'gitlink':
        git_in(sub / 'vendor', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q',
               '--allow-empty', '-m', 'vendor')
        git_in(top, 'add', '--', f'{scratch_repo.name}/sub/vendor')
    importlib.invalidate_caches()
    name = f'{scratch_repo.name}.sub'
    assert not module_absent(name)
    [message] = absence_violations(name)
    lines = class_lines(message)
    assert {kind: ast.literal_eval(line.split(':')[0].split(' ', 2)[2]) for kind, line in lines.items()} == expected, \
        message
    assert ('re-added' in message) == ('tracked' in expected), message
    assert "Each of ['sub/vendor/'] is a git repository nested there" in message.splitlines()[-1], message
    assert_classes_are_status(message, scratch_repo)


IDENTITY = ('-c', 'user.name=t', '-c', 'user.email=t@example.invalid')


@pytest.mark.integration
@pytest.mark.parametrize('shape', ['deinitialised', 'not-fetched'])
def test_absence_message_for_unfetched_submodules(scratch_repo: Path, tmp_path_factory: pytest.TempPathFactory,
                                                  shape: str) -> None:
    # A submodule git tracks but has not checked out is an empty directory git lists as tracked, as a
    # deinitialised submodule or a checkout that fetches no submodules leaves it: re-added, no `del`.
    top = scratch_repo.parent
    vendor = scratch_repo / 'sub' / 'vendor'
    if shape == 'deinitialised':
        upstream = tmp_path_factory.mktemp('upstream')
        plant(upstream, ['mod.py'])
        git_in(upstream, 'init', '-q')
        git_in(upstream, 'add', '--', 'mod.py')
        git_in(upstream, *IDENTITY, 'commit', '-q', '-m', 'upstream')
        git_in(top, *IDENTITY, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', '--', str(upstream),
               f'{scratch_repo.name}/sub/vendor')
        git_in(top, *IDENTITY, 'commit', '-q', '-m', 'submodule')
        git_in(top, 'submodule', 'deinit', '-q', '-f', '--', f'{scratch_repo.name}/sub/vendor')
    else:
        plant(vendor, ['mod.py'])
        git_in(vendor, 'init', '-q')
        git_in(vendor, *IDENTITY, 'commit', '-q', '--allow-empty', '-m', 'vendor')
        git_in(top, 'add', '--', f'{scratch_repo.name}/sub/vendor')
        shutil.rmtree(vendor)
        vendor.mkdir()
    assert vendor.is_dir() and not any(vendor.iterdir())
    importlib.invalidate_caches()
    name = f'{scratch_repo.name}.sub'
    assert not module_absent(name)
    [message] = absence_violations(name)
    lines = class_lines(message)
    assert set(lines) == {'tracked'}, message
    assert lines['tracked'].split(':')[0] == "- tracked ['sub/vendor/']", message
    assert 're-added' in message and '`del' not in message, message
    assert_classes_are_status(message, scratch_repo)


def make_special(path: Path, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a FIFO or a Unix socket at `path`, bound by its name alone so a long directory still fits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == 'fifo':
        os.mkfifo(path)
        return
    with monkeypatch.context() as here:
        here.chdir(path.parent)
        with socket.socket(socket.AF_UNIX) as bound:
            bound.bind(path.name)


@pytest.mark.integration
@pytest.mark.parametrize('special, entries, tracked, expected', [
    pytest.param('sub/fifo', [], [], {'unknown': ['sub/fifo']}, id='fifo'),
    pytest.param('sub/sock', [], [], {'unknown': ['sub/sock']}, id='socket'),
    pytest.param('sub/stale.pyc', [], [], {'unknown': ['sub/stale.pyc']}, id='fifo-named-ignored'),
    pytest.param('sub/fifo', ['sub/__pycache__/mod.cpython-314.pyc'], [],
                 {'ignored': ['sub/__pycache__/mod.cpython-314.pyc'], 'unknown': ['sub/fifo']}, id='fifo-beside-ignored'),
    pytest.param('sub/sock', ['sub/.DS_Store'], [], {'untracked': ['sub/.DS_Store'], 'unknown': ['sub/sock']},
                 id='socket-beside-untracked'),
    pytest.param('sub/fifo', ['sub/mod.py'], ['sub/mod.py'], {'tracked': ['sub/mod.py'], 'unknown': ['sub/fifo']},
                 id='fifo-beside-tracked'),
])
def test_absence_message_for_special_files(scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, special: str,
                                           entries: list[str], tracked: list[str],
                                           expected: dict[str, list[str]]) -> None:
    # Git records no FIFO or socket and lists one nowhere, whatever its name: the explanation names
    # it as unknown to git, in its own line, and says "re-added" only when git tracks a file there.
    make_special(scratch_repo / special, 'socket' if special.endswith('sock') else 'fifo', monkeypatch)
    message = explain(scratch_repo, entries, tracked)
    lines = class_lines(message)
    assert {kind: ast.literal_eval(line.split(':')[0].split(' ', 2)[2]) for kind, line in lines.items()} == expected, \
        message
    assert ('re-added' in message) == bool(tracked), message
    # Alone, it is all there is, so its removal is the location's; beside others, only its own.
    assert removals(lines['unknown']) == [str(scratch_repo / ('sub' if len(expected) == 1 else special))], message
    for kind in set(expected) - {'tracked', 'unknown'}:
        assert str(scratch_repo / special) not in removals(lines[kind]), message
    assert_classes_are_status(message, scratch_repo)


@pytest.mark.integration
@pytest.mark.parametrize('where', ['repository', 'global', 'environment'])
def test_asking_git_runs_nothing_the_configuration_names(scratch_repo: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    # The developer's configuration may name a file-system monitor or hooks; asking git what a file
    # is runs none of them, whichever config names them, `GIT_CONFIG_*` included.
    ran = tmp_path / 'ran'
    program = write(tmp_path / 'hooks' / 'run', f'#!/bin/sh\necho "$0" >> {shlex.quote(str(ran))}\nexit 1\n')
    program.chmod(0o755)
    for hook in ('post-index-change', 'reference-transaction', 'post-checkout'):
        (tmp_path / 'hooks' / hook).symlink_to(program)
    settings = {'core.fsmonitor': str(program), 'core.hooksPath': str(tmp_path / 'hooks'),
                'core.untrackedCache': 'true'}
    if where == 'repository':
        for key, value in settings.items():
            git_in(scratch_repo.parent, 'config', key, value)
    elif where == 'environment':
        monkeypatch.setenv('GIT_CONFIG_COUNT', str(len(settings)))
        for i, (key, value) in enumerate(settings.items()):
            monkeypatch.setenv(f'GIT_CONFIG_KEY_{i}', key)
            monkeypatch.setenv(f'GIT_CONFIG_VALUE_{i}', value)
    else:
        write(Path(os.environ['HOME']) / '.gitconfig',
              ''.join(f'[{k.split(".")[0]}]\n\t{k.split(".")[1]} = {v}\n' for k, v in settings.items()))
    message = explain(scratch_repo, ['sub/.DS_Store', 'sub/__pycache__/'], [])
    assert set(class_lines(message)) == {'untracked', 'ignored'}, message
    assert not ran.exists(), ran.read_text()


@pytest.mark.integration
def test_git_env_drops_what_points_git_elsewhere(scratch_home: Path) -> None:
    # The variables git itself clears when it moves into another repository are dropped, all but the
    # configuration they carry, which is the developer's; that configuration stays. Git is asked
    # outside any repository with a scratch configuration, so nothing of the developer's is read.
    need_git()
    shown = ask_git(GIT, scratch_home, ['rev-parse', '--local-env-vars'])
    assert shown is not None and shown.returncode == 0, shown
    local = set(shown.stdout.split())
    assert 'GIT_DIR' in local and 'GIT_INDEX_FILE' in local, local
    assert {v for v in local if not v.startswith('GIT_CONFIG')} <= REDIRECTING, local - REDIRECTING
    assert not any(v.startswith('GIT_CONFIG') or v == 'GIT_CEILING_DIRECTORIES' for v in REDIRECTING)
