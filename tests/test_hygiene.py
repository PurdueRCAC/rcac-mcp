# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The suite's own safety rails: sandboxed CLI drives, the unit guard, the PATH shim, the harness fence.

No test may read or write the developer's real HOME, reach a real host or run a real `sudo`. Unit
tests are held to that at runtime by the guard in `conftest.py`; integration tests launch processes
on purpose, so `sandbox_violations` holds every launch in every test module to `bin/sandbox.sh`
statically. The tests below prove each rail is in place, so a broken one fails loudly instead of
letting every other test pass unguarded. One more rail keeps a stack move's blast radius known: no
test module but `harness.py` imports the stack, FastMCP, mcp (and its `mcp_types`) or the HTTP
client (`httpx2`, `httpcore2`, and the older `httpx`), by any route the check can read
(`stack_import_violations`).
"""

from __future__ import annotations

import ast
import asyncio
import multiprocessing
import os
import pty
import pwd
import re
import shutil
import socket
import subprocess
from contextvars import ContextVar
from pathlib import Path

import _posixsubprocess
import pytest

import harness
import rcac_mcp
from rcac_mcp.executor import launch

TESTS = Path(__file__).resolve().parent
SANDBOX = TESTS.parent / 'bin' / 'sandbox.sh'

# Calls that start a process, by module; `os.exec*` and `os.spawn*` are matched by prefix.
LAUNCHERS = {
    'subprocess': {'run', 'Popen', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput'},
    'os': {'system', 'popen', 'posix_spawn', 'posix_spawnp'},
    'asyncio': {'create_subprocess_exec', 'create_subprocess_shell'},
    'asyncio.subprocess': {'create_subprocess_exec', 'create_subprocess_shell'},
}
FORBIDDEN_PROGRAMS = ('sudo', 'ssh')


def _is_launcher(module: str, name: str) -> bool:
    return name in LAUNCHERS.get(module, ()) or (module == 'os' and name.startswith(('exec', 'spawn')))


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f'{base}.{node.attr}'
    return None


def _is_unit_mark(decorator: ast.expr) -> bool:
    name = _dotted(decorator)
    return name is not None and (name == 'mark.unit' or name.endswith('.mark.unit'))


def _starts_with_sandbox(argv: ast.expr) -> bool:
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return False
    first = argv.elts[0]
    if isinstance(first, ast.Name) and first.id == 'SANDBOX':
        return True
    return (isinstance(first, ast.Call) and isinstance(first.func, ast.Name) and first.func.id == 'str'
            and len(first.args) == 1 and isinstance(first.args[0], ast.Name) and first.args[0].id == 'SANDBOX')


def _forbidden_programs(argv: ast.expr) -> list[str]:
    if not isinstance(argv, (ast.List, ast.Tuple)):
        return []
    return [elt.value for elt in argv.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            and (elt.value in FORBIDDEN_PROGRAMS or elt.value.endswith(tuple(f'/{p}' for p in FORBIDDEN_PROGRAMS)))]


def sandbox_violations(source: str, filename: str) -> list[str]:
    """Return every process launch in `source` that does not go through `bin/sandbox.sh`.

    A launch is a `subprocess`, `os.system`/`os.popen`/`os.exec*`/`os.spawn*`/`os.posix_spawn*` or
    `asyncio.create_subprocess_*` call, whatever name the module was imported under. It passes only
    when its argv is a list or tuple literal starting with `SANDBOX` or `str(SANDBOX)` and names
    neither `sudo` nor `ssh`. A call through a bare name imported from one of those modules is
    always flagged. Calls lexically inside a function marked `unit` are exempt: the unit guard
    refuses them at runtime.
    """
    tree = ast.parse(source, filename)
    modules: dict[str, str] = {}
    bare: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    root = alias.name.split('.')[0]
                    modules[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if node.module in LAUNCHERS and (_is_launcher(node.module, alias.name)
                                                 or node.module == 'subprocess'):
                    bare[alias.asname or alias.name] = f'{node.module}.{alias.name}'
                elif node.module == 'asyncio' and alias.name == 'subprocess':
                    modules[alias.asname or alias.name] = 'asyncio.subprocess'

    violations: list[str] = []

    def check(node: ast.AST, exempt: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exempt = exempt or any(_is_unit_mark(d) for d in node.decorator_list)
        if isinstance(node, ast.Call) and not exempt:
            where = f'{filename}:{node.lineno}'
            func = node.func
            if isinstance(func, ast.Name) and func.id in bare:
                violations.append(f'{where}: {func.id}() is {bare[func.id]} called through a bare name')
            elif isinstance(func, ast.Attribute):
                owner = _dotted(func.value)
                module = None
                if owner is not None:
                    head, _, tail = owner.partition('.')
                    module = modules.get(head, None)
                    module = f'{module}.{tail}' if module and tail else module
                if module is not None and _is_launcher(module, func.attr):
                    argv = node.args[0] if node.args else next(
                        (k.value for k in node.keywords if k.arg == 'args'), None)
                    if argv is None or not _starts_with_sandbox(argv):
                        violations.append(f'{where}: {module}.{func.attr} does not run through bin/sandbox.sh')
                    for program in _forbidden_programs(argv) if argv is not None else []:
                        violations.append(f'{where}: {module}.{func.attr} launches {program!r}')
        for child in ast.iter_child_nodes(node):
            check(child, exempt)

    check(tree, False)
    return violations


@pytest.mark.unit
def test_cli_drives_are_sandboxed() -> None:
    files = sorted(TESTS.glob('*.py'))
    assert {'conftest.py', 'harness.py', 'mutants.py', 'test_cli.py'} <= {f.name for f in files}
    violations = [v for f in files for v in sandbox_violations(f.read_text(), f.name)]
    assert violations == [], '\n'.join(violations)


SANDBOXED = [
    pytest.param('import subprocess\nsubprocess.run([str(SANDBOX), "sh", "-c", "true"])\n', id='list-literal'),
    pytest.param('import subprocess\nsubprocess.run((SANDBOX, "sh"))\n', id='tuple-literal'),
    pytest.param('import subprocess as sp\nsp.Popen(args=[str(SANDBOX), "env"])\n', id='args-keyword'),
    pytest.param('import pytest, subprocess\n@pytest.mark.unit\ndef test_x():\n    subprocess.run(["true"])\n',
                 id='unit-exempt'),
]


@pytest.mark.unit
@pytest.mark.parametrize('source', SANDBOXED)
def test_sandbox_check_accepts_sandboxed_drives(source: str) -> None:
    assert sandbox_violations(source, 'f.py') == []


@pytest.mark.unit
@pytest.mark.parametrize(('source', 'fragment'), [
    pytest.param('import subprocess\nsubprocess.run(["rcac-mcp", "--help"])\n',
                 'subprocess.run does not run through', id='bare-cli'),
    pytest.param('import subprocess\nsubprocess.run(argv)\n',
                 'subprocess.run does not run through', id='computed-argv'),
    pytest.param('from subprocess import run\nrun([str(SANDBOX)])\n',
                 'subprocess.run called through a bare name', id='imported-run'),
    pytest.param('import subprocess as sp\nsp.check_output(["rcac-mcp"])\n',
                 'subprocess.check_output does not run through', id='aliased-module'),
    pytest.param('import os\nos.system("rcac-mcp --help")\n',
                 'os.system does not run through', id='os-system'),
    pytest.param('import os\nos.execvp("rcac-mcp", ["rcac-mcp"])\n',
                 'os.execvp does not run through', id='exec-family'),
    pytest.param('import asyncio\nasyncio.create_subprocess_exec("rcac-mcp")\n',
                 'asyncio.create_subprocess_exec does not run through', id='asyncio-subprocess'),
    pytest.param('import subprocess\nsubprocess.run([str(SANDBOX), "sudo", "-n", "true"])\n',
                 "launches 'sudo'", id='sudo-argv'),
    pytest.param('import subprocess\nsubprocess.run([str(SANDBOX), "/usr/bin/ssh", "host"])\n',
                 "launches '/usr/bin/ssh'", id='ssh-argv'),
    pytest.param('import pytest, subprocess\n@pytest.mark.integration\ndef test_x():\n    subprocess.run(["true"])\n',
                 'f.py:4: subprocess.run does not run through', id='integration-call'),
])
def test_sandbox_check_catches_unsandboxed(source: str, fragment: str) -> None:
    violations = sandbox_violations(source, 'f.py')
    assert any(fragment in v for v in violations), violations


# The stack, by every top-level import name it ships under: FastMCP, the MCP SDK and its protocol
# types, and the HTTP client (`httpx2`/`httpcore2` today; `httpx` stays listed so a stray import of
# the old client is caught too). Only the harness may import it, so a stack move rewrites the
# harness and the internals-bound modules AGENTS.md lists (which reach the stack through objects the
# harness or `rcac_mcp` hands them), and nothing else.
STACK = ('fastmcp', 'mcp', 'mcp_types', 'httpx', 'httpx2', 'httpcore2')
STACK_HOME = 'harness.py'


# The functions that import a module named by a string, whatever they are reached through:
# `pytest.importorskip` imports its module as surely as `import_module` does.
IMPORTERS = ('import_module', '__import__', 'importorskip')


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _imported_name(call: ast.Call, importers: set[str]) -> str | None:
    """The module a call to an importer names by a string literal, or None for any other call."""
    callee = _dotted(call.func)
    if callee is None or (callee.rsplit('.', 1)[-1] not in IMPORTERS and callee not in importers):
        return None
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    name = _literal(call.args[0] if call.args else keywords.get('name', keywords.get('modname')))
    if name and name.startswith('.'):
        # `import_module('.client', 'fastmcp')` resolves against its package argument.
        return _literal(call.args[1] if len(call.args) > 1 else keywords.get('package'))
    return name


def stack_import_violations(source: str, filename: str) -> list[str]:
    """Return every import of a `STACK` name in `source`; empty for the harness or a clean module.

    Reads, at any depth (so an import tucked inside a function is found too): `import` and
    `from … import`; and a call to `importlib.import_module`, `__import__` or
    `pytest.importorskip` whose module name is a string literal, positional, `name=` or
    `modname=`, reached by any dotted path ending in the importer's name
    (`importlib.import_module`, `builtins.__import__`, `pytest.importorskip`, a bare
    `import_module` or `importorskip`) or by the alias a `from importlib import import_module as …`
    or `from pytest import importorskip as …` binds. A relative name reads its literal package.

    What it cannot read: a module name that is not a literal (a variable, an f-string, a
    concatenation); an importer rebound by assignment or reached through `getattr`, a container or
    `sys.modules`; other loaders (`importlib.util`, `runpy`, `pkgutil`); and source run through
    `exec` or `eval`. Those stay with review.
    """
    if filename == STACK_HOME:
        return []
    tree = ast.parse(source, filename)
    importers = {alias.asname for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module in ('importlib', 'builtins', 'pytest')
                 for alias in node.names if alias.name in IMPORTERS and alias.asname}
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Call) and (name := _imported_name(node, importers)):
            found.append((node.lineno, name))
    return [f'{filename}:{line}: imports {name}; only {STACK_HOME} may, so re-export it there'
            for line, name in found if name.split('.')[0] in STACK]


@pytest.mark.unit
def test_only_the_harness_imports_the_stack() -> None:
    files = sorted(TESTS.glob('*.py'))
    assert STACK_HOME in {f.name for f in files}
    assert stack_import_violations((TESTS / STACK_HOME).read_text(), 'elsewhere.py'), 'the check sees no imports'
    violations = [v for f in files for v in stack_import_violations(f.read_text(), f.name)]
    assert violations == [], '\n'.join(violations)


@pytest.mark.unit
@pytest.mark.parametrize(('source', 'fragment'), [
    pytest.param('from fastmcp.server.auth.providers.jwt import JWTVerifier\n', 'f.py:1: imports fastmcp.',
                 id='from-fastmcp'),
    pytest.param('import mcp.types\n', 'imports mcp.types', id='import-mcp'),
    pytest.param('import json\nimport httpx as h\n', 'f.py:2: imports httpx', id='aliased-httpx'),
    pytest.param('def f():\n    from fastmcp import Client\n', 'f.py:2: imports fastmcp', id='nested'),
    pytest.param('import importlib\nimportlib.import_module("fastmcp.client")\n', 'imports fastmcp.client',
                 id='import-module'),
    pytest.param('__import__("httpx")\n', 'imports httpx', id='dunder-import'),
    pytest.param('from importlib import import_module\nimport_module("fastmcp")\n', 'f.py:2: imports fastmcp',
                 id='from-import-module'),
    pytest.param('from importlib import import_module as load\nload("mcp.types")\n', 'f.py:2: imports mcp.types',
                 id='aliased-import-module'),
    pytest.param('import importlib as il\nil.import_module("httpx")\n', 'f.py:2: imports httpx',
                 id='aliased-importlib'),
    pytest.param('import importlib\nimportlib.import_module(name="fastmcp.client")\n', 'imports fastmcp.client',
                 id='name-keyword'),
    pytest.param('import importlib\nimportlib.import_module(".client", "fastmcp")\n', 'imports fastmcp',
                 id='relative-with-package'),
    pytest.param('import builtins\nbuiltins.__import__("httpx")\n', 'imports httpx', id='builtins-dunder-import'),
])
def test_stack_import_check_catches_imports(source: str, fragment: str) -> None:
    for clean in ('from harness import JWTVerifier, call\nimport mcpx\nfrom rcac_mcp.server import create_mcp_server\n',
                  'from . import mcp\n',
                  'from importlib import import_module\nimport_module("rcac_mcp.docs")\nimport_module(name)\n'):
        assert stack_import_violations(clean, 'f.py') == [], clean
    assert stack_import_violations(source, STACK_HOME) == []
    violations = stack_import_violations(source, 'f.py')
    assert any(fragment in v for v in violations), violations


@pytest.mark.unit
@pytest.mark.parametrize(('source', 'fragment'), [
    pytest.param('from httpx2 import AsyncClient\n', 'f.py:1: imports httpx2', id='from-httpx2'),
    pytest.param('import json\nimport httpcore2\n', 'f.py:2: imports httpcore2', id='import-httpcore2'),
    pytest.param('import mcp_types\n', 'f.py:1: imports mcp_types', id='import-mcp-types'),
    pytest.param('import pytest\nfastmcp = pytest.importorskip("fastmcp")\n', 'f.py:2: imports fastmcp',
                 id='importorskip'),
    pytest.param('import pytest\npytest.importorskip(modname="httpx2")\n', 'f.py:2: imports httpx2',
                 id='importorskip-modname'),
    pytest.param('from pytest import importorskip as need\nneed("mcp_types")\n', 'f.py:2: imports mcp_types',
                 id='importorskip-alias'),
])
def test_stack_import_check_catches_new_stack_names(source: str, fragment: str) -> None:
    # The new stack's import names, and pytest.importorskip, are read like any other import.
    for clean in ('import httpx2x\nimport mcp_typesafe\n', 'import pytest\npytest.importorskip("yaml")\n',
                  'from pytest import importorskip as need\nneed(name)\n'):
        assert stack_import_violations(clean, 'f.py') == [], clean
    assert stack_import_violations(source, STACK_HOME) == []
    violations = stack_import_violations(source, 'f.py')
    assert any(fragment in v for v in violations), violations


def _guarded(entry_points: list[tuple[object, str]]) -> None:
    # Check every entry point is the guard's refuser before calling any: a broken guard must fail
    # here, not fork or exec the test process.
    unguarded = [name for owner, name in entry_points if not getattr(getattr(owner, name), 'unit_guard', False)]
    assert unguarded == [], f'not replaced by the unit guard: {unguarded}'


@pytest.mark.unit
def test_unit_guard_refuses_process_launch(guard) -> None:
    _guarded([(subprocess, 'Popen'), (os, 'system'), (os, 'popen')])
    with pytest.raises(OSError, match='unit guard'):
        subprocess.run(['true'], check=True)
    with pytest.raises(OSError, match='unit guard'):
        os.system('true')
    with pytest.raises(OSError, match='unit guard'):
        os.popen('true')
    assert [name for name, _ in guard.refused] == ['subprocess.Popen', 'os.system', 'os.popen']
    guard.refused.clear()


@pytest.mark.unit
def test_unit_guard_refuses_exec_family(guard) -> None:
    entry_points = [(os, 'execv'), (os, 'execvpe'), (os, 'spawnv'), (os, 'spawnlp'), (os, 'posix_spawn'),
                    (os, 'posix_spawnp'), (os, 'fork'), (os, 'forkpty'), (pty, 'fork'), (pty, 'spawn'),
                    (_posixsubprocess, 'fork_exec'), (multiprocessing.Process, 'start')]
    _guarded(entry_points)
    attempts = [
        lambda: os.execv('/usr/bin/true', ['true']),
        lambda: os.execvpe('true', ['true'], {}),
        lambda: os.spawnv(os.P_WAIT, '/usr/bin/true', ['true']),
        lambda: os.spawnlp(os.P_WAIT, 'true', 'true'),
        lambda: os.posix_spawn('/usr/bin/true', ['true'], {}),
        lambda: os.posix_spawnp('true', ['true'], {}),
        lambda: os.fork(),
        lambda: os.forkpty(),
        lambda: pty.fork(),
        lambda: pty.spawn(['true']),
        lambda: _posixsubprocess.fork_exec(),
        lambda: multiprocessing.Process(target=print).start(),
    ]
    for attempt in attempts:
        with pytest.raises(OSError, match='unit guard'):
            attempt()
    assert len(guard.refused) == len(attempts), guard.refused
    guard.refused.clear()


@pytest.mark.unit
def test_unit_guard_refuses_network(guard) -> None:
    _guarded([(socket.socket, 'connect'), (socket.socket, 'connect_ex'), (socket, 'create_connection'),
              (socket, 'getaddrinfo')])
    with pytest.raises(OSError, match='unit guard'):
        socket.create_connection(('example.org', 80))
    with pytest.raises(OSError, match='unit guard'):
        socket.getaddrinfo('example.org', 80)
    with socket.socket() as sock:
        with pytest.raises(OSError, match='unit guard'):
            sock.connect(('192.0.2.1', 80))
        with pytest.raises(OSError, match='unit guard'):
            sock.connect_ex(('192.0.2.1', 80))
    assert [name for name, _ in guard.refused] == ['socket.create_connection', 'socket.getaddrinfo',
                                                   'socket.connect', 'socket.connect_ex']
    guard.refused.clear()


@pytest.mark.unit
def test_unit_guard_redirects_home(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    assert os.path.expanduser('~') == str(home)
    assert Path.home() == home
    assert os.path.expanduser('~') != pwd.getpwuid(os.getuid()).pw_dir
    for var in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
        assert Path(os.environ[var]).is_relative_to(home), var
    for var in ('RCAC_SSH_HOST', 'RCAC_USER_MAP', 'JWT_SECRET', 'OIDC_CONFIG_URL', 'OIDC_CLIENT_ID',
                'OIDC_CLIENT_SECRET', 'MCP_BASE_URL'):
        assert var not in os.environ, var


@pytest.mark.unit
def test_path_shim_shadows_sudo_and_ssh(path_shim: Path) -> None:
    assert os.environ['PATH'].split(os.pathsep)[0] == str(path_shim)
    for program in ('sudo', 'ssh'):
        assert shutil.which(program) == str(path_shim / program), program
    # A login bash rebuilds PATH; the file BASH_ENV names puts the shim first again.
    bash_env = Path(os.environ['BASH_ENV'])
    assert bash_env.parent == path_shim
    assert bash_env.read_text().startswith(f'PATH={path_shim}:'), bash_env.read_text()


# Resolves each name inside the sandbox and runs it only when it resolves into the shim ($1).
SHIM_PROBE = """\
shim=$1
for name in sudo ssh; do
    path=$(command -v "$name") || { echo "$name: not found" >&2; exit 3; }
    case $path in
        "$shim"/*) ;;
        *) echo "$name resolves to $path, outside the shim" >&2; exit 4 ;;
    esac
    out=$("$path" -n true 2>&1)
    echo "$name|$path|$?|$out"
done
"""


@pytest.mark.integration
def test_path_shim_reaches_sandboxed_children(path_shim: Path) -> None:
    result = subprocess.run([str(SANDBOX), 'sh', '-c', SHIM_PROBE, 'sh', str(path_shim)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f'{name}|{path_shim / name}|126|refused by the test PATH shim' for name in ('sudo', 'ssh')], result.stdout


@pytest.mark.integration
def test_path_shim_survives_a_login_bash(path_shim: Path) -> None:
    # The pinned interpreter is a login bash, whose /etc/profile may rebuild PATH (macOS's
    # path_helper puts /usr/bin first); a command it launches must still resolve the shim.
    prog, sentinel = launch.program(['/bin/sh', '-c', SHIM_PROBE, 'sh', str(path_shim)])
    raw_out, raw_err, raw_code, note = launch.run_process([str(SANDBOX), *launch.bash_argv(prog)], None, 60)
    out, err, code = launch.separate(raw_out, raw_err, raw_code, sentinel, note)
    assert code == 0, err.decode(errors='replace')
    assert out.decode().splitlines() == [
        f'{name}|{path_shim / name}|126|refused by the test PATH shim' for name in ('sudo', 'ssh')], out


@pytest.mark.integration
@pytest.mark.parametrize(('argv', 'fragment'), [
    pytest.param(['/bin/sh', '-c', 'true'], 'does not run /bin/bash', id='not-bash'),
    pytest.param(['sudo', '-u', 'umapped', '/bin/bash', '-c', 'true'], 'does not start exactly', id='sudo-flags-dropped'),
    pytest.param(['sudo', '-n', '-H', '-u', 'root', '/bin/bash', '-c', 'true'], 'does not start exactly',
                 id='sudo-other-user'),
    pytest.param(['sudo', '-n', '-H', '-u', 'umapped', '/bin/sh', '-c', 'true'], 'does not run /bin/bash',
                 id='sudo-not-bash'),
])
def test_executes_refuses_what_it_does_not_stand_in_for(argv: list[str], fragment: str, executes) -> None:
    # Refused before anything launches: no process starts for an argv the fence does not accept.
    with pytest.raises(OSError, match=fragment):
        launch.run_process(argv, None, 5)
    assert executes.launched == [] and executes.refused == [argv]


@pytest.mark.integration
def test_executes_launches_only_inside_the_sandbox(executes, path_shim: Path) -> None:
    from rcac_mcp.executor.delegate import DelegatingExecutor
    from rcac_mcp.executor.shell import LocalShellExecutor

    local = LocalShellExecutor().run('printf %s "$HOME"', timeout=20)
    probe = 'printf "%s|%s|%s" "$HOME" "$USER" "$(command -v sudo)"'
    delegated = DelegatingExecutor('someone', {'someone': executes.user}).run(probe, timeout=20)
    assert len(executes.launched) == 2, executes.launched
    for argv in executes.launched:
        assert argv[:3] == [str(SANDBOX), 'env', '-i'], argv
        assert 'sudo' not in argv, f'a launch names sudo: {argv!r}'
    assert local.stdout == str(executes.server_home), local
    # The stand-in gives the delegated command the mapped user's home and name, and sudo inside it
    # still resolves to the shim.
    assert delegated.stdout == f'{executes.mapped_home}|{executes.user}|{path_shim / "sudo"}', delegated


@pytest.mark.integration
def test_executes_opens_the_fence_only_to_sandboxed_launches(executes) -> None:
    # Under `executes` the harness fence lets an in-process server call through...
    async def scenario() -> tuple[str, ...]:
        async with harness.in_memory(harness.bare_server()) as client:
            return await harness.tool_names(client)

    assert 'write_file' in harness.drive(scenario())
    # ...because nothing outside bin/sandbox.sh can launch behind it: an unsandboxed argv is refused
    # before it starts. (Called through a name, since the static check reads literal launches.)
    unsandboxed = ['/bin/sh', '-c', 'exit 0']
    launcher = subprocess.Popen
    with pytest.raises(OSError, match='does not start with'):
        launcher(unsandboxed)
    assert executes.refused == [unsandboxed] and executes.launched == []


@pytest.mark.unit
def test_harness_refuses_unfenced_server_calls(monkeypatch: pytest.MonkeyPatch, guard) -> None:
    def unfenced_popen(*args: object, **kwargs: object) -> None:
        raise AssertionError('the fence let a server call reach an unfenced Popen')

    monkeypatch.setattr(subprocess, 'Popen', unfenced_popen)

    async def scenario() -> list[str]:
        server = harness.bare_server()
        refusals = []
        for attempt in (lambda: harness.in_memory(server), lambda: harness.serving(server),
                        lambda: harness.http_client(object(), 'token')):
            try:
                attempt()
            except RuntimeError as exc:
                refusals.append(str(exc))
        try:
            await harness.call_http(object(), 'token', 'run_command', {'command': 'true'})
        except RuntimeError as exc:
            refusals.append(str(exc))
        return refusals

    refusals = harness.drive(scenario())
    assert len(refusals) == 4 and all('harness fence' in r for r in refusals), refusals
    assert guard.refused == []


@pytest.mark.unit
def test_harness_builds_servers_inside_the_running_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('JWT_SECRET', 'k' * 32)
    map_path = harness.user_map(tmp_path, 'alice ualice\n')
    builders = [lambda: harness.delegate_server(map_path), lambda: harness.shared_server(harness.RecordingExecutor('x')),
                harness.bare_server]
    for build in builders:
        with pytest.raises(RuntimeError, match='inside the coroutine handed to harness.drive'):
            build()

    async def scenario() -> list[str]:
        return [type(build()).__name__ for build in builders]

    assert harness.drive(scenario()) == ['FastMCP'] * 3

    # The loop copies the main context, as production's run() does, so whatever construction
    # sets in it reaches the request tasks there too.
    probe: ContextVar[str] = ContextVar('probe', default='unset')
    token = probe.set('main')
    try:
        async def read() -> str:
            await asyncio.sleep(0)
            return probe.get()
        assert harness.drive(read()) == 'main'
    finally:
        probe.reset(token)


# A feature-scoped spec id (`R#`, `P#`) in a comment, docstring or message of the package or the
# suite: those restart per feature and live in `spec/`, never in the code (AGENTS.md § Conventions).
SPEC_ID = re.compile(r'\b[RP][0-9]{1,2}\b')


def spec_id_violations(name: str, text: str) -> list[str]:
    """Every line of `text` that names a feature-scoped spec id."""
    return [f'{name}:{n}: {line.strip()[:120]}' for n, line in enumerate(text.splitlines(), 1) if SPEC_ID.search(line)]


@pytest.mark.unit
def test_no_spec_ids_in_the_package_or_the_suite() -> None:
    package = Path(rcac_mcp.__file__).resolve().parent
    files = sorted(package.rglob('*.py')) + sorted(Path(__file__).resolve().parent.glob('*.py'))
    assert files, 'no source files found to check'
    violations = [v for f in files for v in spec_id_violations(str(f), f.read_text())]
    assert violations == [], 'a spec id names a requirement or phase in the code:\n' + '\n'.join(violations)


@pytest.mark.unit
@pytest.mark.parametrize(('text', 'found'), [
    pytest.param('"""Differs from ' + 'R' + '9\'s report."""', True, id='requirement'),
    pytest.param('# Added in ' + 'P' + '3.', True, id='phase'),
    pytest.param('x = "R2D2 P100 RP1 HTTP2"', False, id='not-ids'),
])
def test_spec_id_check_catches_planted_ids(text: str, found: bool) -> None:
    assert bool(spec_id_violations('planted', text)) == found


# A row that needs a login shell, or a git that runs, runs it or, off CI, skips: `conftest.need_shell`
# and `conftest.need_git` are the only places that decide, and on CI (where the workflow installs
# every shell and the runner image carries git) they fail the row instead. Any other skip in the
# suite would let a row vanish from CI without failing.
SKIPPERS = {'skip', 'skipif', 'importorskip', 'xfail'}
SKIP_HELPERS = {'need_shell', 'need_git'}


def skip_violations(name: str, text: str) -> list[str]:
    """Every skip, conditional skip or expected failure in `text` outside the `SKIP_HELPERS`."""
    found = []
    tree = ast.parse(text, name)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in SKIP_HELPERS and name.endswith('conftest.py'):
            allowed |= {n.lineno for n in ast.walk(node) if hasattr(n, 'lineno')}
    # `pytest.skip.Exception` names the outcome without skipping anything.
    named = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        word = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
        if word in SKIPPERS and node.lineno not in allowed and id(node) not in named:
            found.append(f'{name}:{node.lineno}: {word}')
    return found


@pytest.mark.unit
def test_only_need_shell_skips() -> None:
    files = sorted(TESTS.glob('*.py'))
    assert any(f.name == 'conftest.py' for f in files), 'conftest.py not found'
    violations = [v for f in files for v in skip_violations(str(f), f.read_text())]
    assert violations == [], 'a test can skip on CI without failing:\n' + '\n'.join(violations)


@pytest.mark.unit
@pytest.mark.parametrize(('name', 'text', 'found'), [
    pytest.param('t.py', 'import pytest\npytest.skip("no zsh")\n', True, id='skip'),
    pytest.param('t.py', 'import pytest\n@pytest.mark.skipif(True, reason="x")\ndef test(): pass\n', True,
                 id='skipif'),
    pytest.param('t.py', 'from pytest import importorskip\nimportorskip("yaml")\n', True, id='importorskip'),
    pytest.param('t.py', 'def need_shell(s):\n    pytest.skip(s)\n', True, id='need-shell-elsewhere'),
    pytest.param('conftest.py', 'def need_shell(s):\n    pytest.skip(s)\n', False, id='need-shell'),
    pytest.param('t.py', 'def need_git():\n    pytest.skip("git")\n', True, id='need-git-elsewhere'),
    pytest.param('conftest.py', 'def need_git():\n    pytest.skip("git")\n', False, id='need-git'),
    pytest.param('conftest.py', 'def need_other():\n    pytest.skip("x")\n', True, id='other-helper'),
    pytest.param('t.py', 'import pytest\nraised = pytest.skip.Exception\n', False, id='outcome-type'),
])
def test_skip_check_catches_planted_skips(name: str, text: str, found: bool) -> None:
    assert bool(skip_violations(name, text)) == found


@pytest.mark.unit
@pytest.mark.parametrize(('ci', 'outcome'), [
    pytest.param('true', pytest.fail.Exception, id='ci'),
    pytest.param(None, pytest.skip.Exception, id='local'),
    pytest.param('false', pytest.skip.Exception, id='ci-false'),
])
def test_a_missing_shell_fails_on_ci_and_skips_elsewhere(ci: str | None, outcome: type[BaseException],
                                                         monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from conftest import need_shell
    if ci is None:
        monkeypatch.delenv('CI', raising=False)
    else:
        monkeypatch.setenv('CI', ci)
    missing = str(tmp_path / 'no-such-shell')
    # Caught by hand: under pytest.raises a skip that should have been a failure would skip this row.
    try:
        need_shell(missing)
    except BaseException as exc:
        raised: BaseException | None = exc
    else:
        raised = None
    assert type(raised) is outcome, f'CI={ci}: a missing shell gave {raised!r}, not {outcome.__name__}'
    assert missing in str(raised), raised
    need_shell('/bin/sh')
