# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""Planted violations: each contract test must go red against a copy of the package that breaks it.

Usage, from the repository root:

    uv run python tests/mutants.py [--self-check] CASE_ID...

Each `Case` copies the imported `rcac_mcp` package (never `src/` itself), applies exact source
edits that break one contract, and runs the named target tests against the copy in a child pytest
under `bin/sandbox.sh`. A case is caught only when every target fails (a JUnit `<failure>`, not an
`<error>` or a skip) and the failure carries the case's `why`: the fragment of the violation
message that names the broken contract. A mutant that merely breaks an import fails for the wrong
reason and counts as surviving.

This module is not collected: a child takes seconds, so the phase gates run it and its report is
recorded with the change. `test_mutations.py` keeps the fast parts in the suite: every anchor still
applies, and `mutation_violations` rejects each way a child can fail to prove a kill.

`--self-check` also runs the runner's own controls: a docstring-only edit must be reported as
surviving, and a child that does not import the mutated copy must be reported as a wrong origin.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import rcac_mcp

TESTS = Path(__file__).resolve().parent
SANDBOX = TESTS.parent / 'bin' / 'sandbox.sh'

# Exit status of the child when the mutated copy is not what it imports.
WRONG_ORIGIN = 97

# The child steps back into the repository (the sandbox starts elsewhere) so pytest reads
# pyproject.toml, refuses to run unless `rcac_mcp` resolves under the mutated copy, then runs the
# targets with a JUnit report.
CHILD = """\
cd "$CLUSTER_MCP_REPO" || exit 96
py=$1
root=$2
junit=$3
shift 3
"$py" -c 'import os, sys, rcac_mcp; sys.exit(0 if os.path.realpath(rcac_mcp.__file__).startswith(os.path.realpath(sys.argv[1]) + os.sep) else 97)' "$root" || exit 97
exec "$py" -m pytest -q -rA -p no:cacheprovider --junitxml="$junit" "$@"
"""


@dataclass(frozen=True)
class Case:
    """One planted violation.

    `file` is relative to the package directory; `edits` are exact `(old, new)` replacements, each
    `old` occurring once; `targets` are `tests/`-relative test ids that must fail; `why` is the
    fragment each target's failure must carry.
    """

    id: str
    file: str
    edits: tuple[tuple[str, str], ...]
    targets: tuple[str, ...]
    why: str


PROCESS_GLOBAL = '''class _ProcessGlobal:
    value = None

    def get(self):
        return _ProcessGlobal.value

    def set(self, value):
        _ProcessGlobal.value = value


executor_var = _ProcessGlobal()'''

CONTEXT_VAR = "executor_var: ContextVar[Optional[Executor]] = ContextVar('executor', default=None)"

CLAIM_ORDER = "for claim in ('sub', 'email', 'preferred_username'):"

SUDO_PREFIX = "SUDO: Final = ('sudo', '-n', '-H', '-u')"

DELEGATE_PROGRAM = "        prog, sentinel = launch.program(command, cwd, start='home')\n"

TIMEOUT_ROWS = ('test_executors.py::test_timeout_returns_the_captured_output[local]',
                'test_executors.py::test_timeout_returns_the_captured_output[delegate]',
                'test_ssh_exec.py::test_timeout_returns_the_captured_output[bash]')

EXPLICIT_MODE = '        if self.exec_mode:\n'

BOUNDARY_ROWS = tuple(f'test_protocol.py::test_middleware_refusal_is_a_tool_error_result[{row}]'
                      for row in ('permission-memory', 'permission-http', 'runtime-memory', 'mcp-error-memory'))

OIDC_ROWS = tuple(f'test_auth_modes.py::test_oidc_requires_all_four[{variable}-{how}]'
                  for variable in ('OIDC_CONFIG_URL', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'MCP_BASE_URL')
                  for how in ('unset', 'empty'))

CASES: tuple[Case, ...] = (
    # Per-request isolation: the executor lives in a ContextVar and is built per call.
    Case('isolation-module-global', 'context.py',
         ((CONTEXT_VAR, PROCESS_GLOBAL),),
         ('test_isolation.py::test_concurrent_delegate_calls_stay_isolated',
          'test_isolation.py::test_concurrent_shared_servers_stay_isolated'),
         'ran as'),
    Case('isolation-executor-cached', 'middleware.py',
         (('        executor = DelegatingExecutor(identity, self._user_map)\n',
           "        executor = getattr(self, '_cached', None) or DelegatingExecutor(identity, self._user_map)\n"
           '        self._cached = executor\n'),),
         ('test_isolation.py::test_concurrent_delegate_calls_stay_isolated',),
         'ran as'),
    # Each executor middleware sets the executor on every call; set once, the later calls of the
    # same server find none.
    Case('shared-middleware-sets-first-call-only', 'middleware.py',
         (('        set_executor(self._executor)\n        return await call_next(context)\n',
           "        if not getattr(self, '_set_once', False):\n"
           '            set_executor(self._executor)\n'
           '            self._set_once = True\n'
           '        return await call_next(context)\n'),),
         ('test_isolation.py::test_sequential_shared_calls_each_get_the_executor',),
         'ran 0 times'),
    Case('auth-middleware-sets-first-call-only', 'middleware.py',
         (('        executor = DelegatingExecutor(identity, self._user_map)\n        set_executor(executor)\n',
           '        executor = DelegatingExecutor(identity, self._user_map)\n'
           "        if not getattr(self, '_set_once', False):\n"
           '            set_executor(executor)\n'
           '            self._set_once = True\n'),),
         ('test_isolation.py::test_sequential_delegate_calls_each_get_their_executor',),
         'ran 0 times'),
    # The tool surface: tools reach the cluster only through get_executor(), never an executor of
    # their own.
    Case('tool-builds-local-executor', 'tools/slurm.py',
         (("    executor = get_executor()\n\n    argv = ['sinfo']\n",
           "    executor = __import__('rcac_mcp.executor.shell', fromlist=['LocalShellExecutor']).LocalShellExecutor()\n\n"
           "    argv = ['sinfo']\n"),),
         ('test_tools.py::test_tool_reaches_only_the_installed_executor[sinfo]',),
         'launched outside the executor'),
    Case('tool-builds-ssh-executor', 'tools/rcac.py',
         (("    executor = get_executor()\n    result = executor.run(['myquota'])",
           "    executor = __import__('rcac_mcp.executor.ssh', fromlist=['SSHExecutor']).SSHExecutor('cluster.invalid')\n"
           "    result = executor.run(['myquota'])"),),
         ('test_tools.py::test_tool_reaches_only_the_installed_executor[myquota]',),
         'launched outside the executor'),
    # No executor set: get_executor() raises, with no fallback, and nothing set at construction or
    # by another request reaches this one.
    Case('no-executor-module-global', 'context.py',
         ((CONTEXT_VAR, PROCESS_GLOBAL),),
         ('test_no_executor.py::test_no_executor_after_earlier_request',
          'test_no_executor.py::test_no_executor_while_other_request_in_flight',
          'test_no_executor.py::test_delegate_without_token_after_authenticated_request',
          'test_no_executor.py::test_delegate_without_token_while_authenticated_request_in_flight'),
         'not an error'),
    Case('no-executor-default-fallback', 'context.py',
         (("        raise RuntimeError('No executor configured. Set executor via set_executor() first.')",
           '        from rcac_mcp.executor.shell import LocalShellExecutor\n        return LocalShellExecutor()'),),
         ('test_no_executor.py::test_tool_without_executor_middleware_fails[run_command]',
          'test_no_executor.py::test_no_executor_after_earlier_request'),
         'not an error'),
    Case('no-token-fallback-identity', 'middleware.py',
         (("            # No auth - this shouldn't happen in delegate mode\n"
           '            # but let the request proceed (will fail at executor access)\n'
           '            return await call_next(context)',
           '            set_executor(DelegatingExecutor(next(iter(self._user_map)), self._user_map))\n'
           '            return await call_next(context)'),),
         ('test_no_executor.py::test_delegate_without_token_fails',),
         'not an error'),
    Case('auth-middleware-sets-at-construction', 'middleware.py',
         (('        self._user_map = load_user_map(user_map_path)\n',
           '        self._user_map = load_user_map(user_map_path)\n'
           '        set_executor(DelegatingExecutor(next(iter(self._user_map)), self._user_map))\n'),),
         ('test_no_executor.py::test_delegate_without_token_fails',
          'test_no_executor.py::test_delegate_without_token_after_authenticated_request',
          'test_no_executor.py::test_delegate_without_token_while_authenticated_request_in_flight'),
         'not an error'),
    Case('shared-middleware-sets-at-construction', 'middleware.py',
         (('        super().__init__()\n        self._executor = executor\n',
           '        super().__init__()\n        self._executor = executor\n        set_executor(executor)\n'),),
         ('test_no_executor.py::test_no_executor_after_earlier_request',
          'test_no_executor.py::test_no_executor_while_other_request_in_flight'),
         'not an error'),
    # A tool that falls back to a local executor fails on the missing file with a local error, not
    # the refusal; only the stated reason tells the two apart.
    Case('transfer-local-fallback', 'tools/transfer.py',
         (("    executor = get_executor()\n    return executor.get(_path('remote_path', remote_path), "
           "_path('local_path', local_path))",
           "    try:\n        executor = get_executor()\n    except RuntimeError:\n"
           "        from rcac_mcp.executor.shell import LocalShellExecutor\n"
           "        executor = LocalShellExecutor()\n    return executor.get(_path('remote_path', remote_path), "
           "_path('local_path', local_path))"),),
         ('test_no_executor.py::test_tool_without_executor_middleware_fails[download_file]',),
         'error lacks reason'),
    # Identity claims: sub, then email, then preferred_username, the first non-empty one wins, and
    # a token with none of them is refused rather than given an identity.
    Case('precedence-reordered', 'middleware.py',
         ((CLAIM_ORDER, "for claim in ('email', 'sub', 'preferred_username'):"),),
         ('test_identity.py::test_identity_claim_precedence[all-three]',),
         'ran as'),
    Case('precedence-username-before-email', 'middleware.py',
         ((CLAIM_ORDER, "for claim in ('sub', 'preferred_username', 'email'):"),),
         ('test_identity.py::test_identity_claim_precedence[sub-absent]',),
         'ran as'),
    Case('precedence-username-dropped', 'middleware.py',
         ((CLAIM_ORDER, "for claim in ('sub', 'email'):"),),
         ('test_identity.py::test_identity_claim_precedence[email-empty]',
          'test_identity.py::test_identity_claim_precedence[username-only]'),
         'error result'),
    Case('precedence-empty-claim-accepted', 'middleware.py',
         (('if claim in claims and claims[claim]:', 'if claim in claims:'),),
         ('test_identity.py::test_identity_claim_precedence[sub-empty]',),
         'error result'),
    Case('no-claim-fallback-identity', 'middleware.py',
         (('                return str(claims[claim])\n        return None\n',
           '                return str(claims[claim])\n        return next(iter(self._user_map))\n'),),
         ('test_identity.py::test_token_without_identity_fails[no-claims]',
          'test_identity.py::test_token_without_identity_fails[all-empty]'),
         'not an error'),
    # The user map: an unmapped identity is refused by the middleware and by the executor, never
    # given a default user; a malformed line fails naming its line; comments load as nothing.
    Case('unmapped-gets-default-user', 'middleware.py',
         (('        if identity not in self._user_map:\n            raise PermissionError(',
           '        if identity not in self._user_map:\n            identity = next(iter(self._user_map))\n'
           '        if False:\n            raise PermissionError('),),
         ('test_identity.py::test_unmapped_identity_is_not_authorized',
          'test_identity.py::test_first_claim_unmapped_does_not_fall_through'),
         'not an error'),
    Case('executor-unmapped-default', 'executor/delegate.py',
         (("        if identity not in user_map:\n            raise KeyError(f'Identity {identity!r} not found in user map')\n",
           ''),
          ('        self._local_user = user_map[identity]', "        self._local_user = user_map.get(identity, 'nobody')")),
         ('test_identity.py::test_executor_rejects_unmapped_identity',),
         'DID NOT RAISE'),
    Case('map-malformed-line-skipped', 'executor/delegate.py',
         (('            if len(parts) != 2:\n                raise ValueError(',
           '            if len(parts) != 2:\n                continue\n                raise ValueError('),),
         ('test_identity.py::test_user_map_rejects_line_without_local_user',),
         'DID NOT RAISE'),
    Case('map-line-number-dropped', 'executor/delegate.py',
         (('''f'Invalid user map format at line {line_num}: expected "<id> <local-user>"\'''',
           '''f'Invalid user map format: expected "<id> <local-user>"\''''),),
         ('test_identity.py::test_user_map_rejects_line_without_local_user',),
         'does not name line 5'),
    # Numbering by entries, not file lines, names the wrong line once comments or blanks precede it.
    Case('map-line-counts-entries', 'executor/delegate.py',
         (('Invalid user map format at line {line_num}:', 'Invalid user map format at line {len(user_map) + 1}:'),),
         ('test_identity.py::test_user_map_rejects_line_without_local_user',),
         'does not name line 5'),
    Case('map-comments-loaded', 'executor/delegate.py',
         (("if not line or line.startswith('#'):", 'if not line:'),),
         ('test_identity.py::test_user_map_skips_comments_and_blanks',),
         'map differs'),
    # The delegated launch: exactly sudo -n -H -u <user>, then the launcher's stage and one program
    # that runs the script as one word, with the cwd quoted as one word.
    Case('sudo-n-dropped', 'executor/delegate.py',
         ((SUDO_PREFIX, "SUDO: Final = ('sudo', '-H', '-u')"),),
         ('test_executors.py::test_sudo_argv[no-cwd]',),
         'argv differs'),
    Case('sudo-H-dropped', 'executor/delegate.py',
         ((SUDO_PREFIX, "SUDO: Final = ('sudo', '-n', '-u')"),),
         ('test_executors.py::test_sudo_argv[no-cwd]',),
         'argv differs'),
    Case('cwd-unquoted', 'executor/launch.py',
         (("lines.append(f'command cd -- {word(UserPath(cwd))} || exec /usr/bin/false')",
           "lines.append(f'command cd -- {cwd} || exec /usr/bin/false')"),),
         ('test_executors.py::test_sudo_argv[cwd-spaces]', 'test_executors.py::test_sudo_argv[cwd-metacharacters]'),
         'argv differs'),
    Case('command-quoted', 'executor/launch.py',
         (("lines.append(SCRIPT.format(script=shlex.quote(_checked(command, 'command'))))",
           "lines.append(SCRIPT.format(script=shlex.quote(shlex.quote(_checked(command, 'command')))))"),),
         ('test_executors.py::test_sudo_argv[no-cwd]',),
         'argv differs'),
    # The interpreter is the constant /bin/bash in every backend, never the server's $SHELL.
    Case('interpreter-from-shell-env', 'executor/launch.py',
         (("    return [*POSIX_STAGE, '-c', STAGE, 'cluster-mcp', prog]",
           "    return [os.environ.get('SHELL', BASH), NORC, '--posix', '-c', STAGE, 'cluster-mcp', prog]"),),
         ('test_executors.py::test_interpreter_is_pinned[local]', 'test_executors.py::test_interpreter_is_pinned[delegate]'),
         'argv differs'),
    # Delegate mode: the program starts in the mapped user's $HOME, `~` resolves there inside the
    # program, and transfers run as the mapped user through the same launch.
    Case('delegate-tilde-server-home', 'executor/delegate.py',
         ((DELEGATE_PROGRAM,
           "        prog, sentinel = launch.program(command, __import__('os').path.expanduser(cwd) if cwd else cwd,"
           " start='home')\n"),),
         ('test_executors.py::test_delegate_cwd_resolves_in_the_mapped_home[tilde]',
          'test_executors.py::test_delegate_cwd_resolves_in_the_mapped_home[tilde-sub-dir]',
          'test_literal_args.py::test_literal_argument[delegate-run_command-cwd]'),
         'not the mapped home'),
    Case('delegate-server-cwd', 'executor/delegate.py',
         ((DELEGATE_PROGRAM, '        prog, sentinel = launch.program(command, cwd)\n'),),
         ('test_executors.py::test_delegate_starts_in_the_mapped_home',),
         'not the mapped home'),
    Case('transfer-as-server', 'executor/delegate.py',
         (('        return self._copy(local_path, remote_path)\n',
           "        return __import__('shutil').copy2(launch.expand_local(local_path), launch.expand_local(remote_path))\n"),),
         ('test_executors.py::test_delegate_transfers_land_in_the_mapped_home[put]',),
         'not the mapped home'),
    # One timeout contract: every backend returns the captured output and a marker no completed or
    # signal-killed command can produce, and the ssh backend never raises.
    Case('timeout-sentinel-minus-one', 'executor/base.py',
         (('TIMEOUT_EXIT_CODE: Final = -1001\n', 'TIMEOUT_EXIT_CODE: Final = -1\n'),),
         TIMEOUT_ROWS,
         'indistinguishable'),
    Case('ssh-timeout-raises', 'executor/ssh.py',
         (("                return bytes(out), bytes(err), TIMEOUT_EXIT_CODE, b''\n",
           "                raise TimeoutError(f'timed out after {timeout} s')\n"),),
         ('test_ssh_exec.py::test_timeout_returns_the_captured_output[bash]',),
         'raised'),
    # The ssh wire: the program travels behind the bootstrap, stdin ends with EOF, the output loop
    # waits rather than spins, and the login shell's startup output is cut away.
    Case('ssh-bootstrap-dropped', 'executor/ssh.py',
         (('        wire = launch.ssh_command(prog)\n', '        wire = prog\n'),),
         ('test_ssh_exec.py::test_bash_runs_whatever_the_login_shell[zsh]',),
         'pinned interpreter'),
    Case('ssh-stdin-not-closed', 'executor/ssh.py',
         (('        chan.shutdown_write()\n', '        pass\n'),),
         ('test_ssh_exec.py::test_stdin_arrives_exactly_and_closes[bash]',),
         'EOF'),
    Case('ssh-drain-spins', 'executor/ssh.py',
         (('        time.sleep(wait)\n', '        pass\n'),),
         ('test_ssh_exec.py::test_drain_waits_instead_of_spinning',),
         'busy-waited'),
    Case('backend-separation-skipped', 'executor/ssh.py',
         (('        out, err, code = launch.separate(out, err, code, sentinel, note)\n', ''),),
         ('test_ssh_exec.py::test_startup_output_is_separated[bash]',),
         'differs from the quiet home'),
    # No startup file moves where an ssh command starts, reads the caller's stdin, or holds the call;
    # a timeout reads what the command prints as it stops; the one separation exception is stated.
    Case('ssh-start-not-home', 'executor/ssh.py',
         (("        prog, sentinel = launch.program(command, cwd, start='home')\n",
           '        prog, sentinel = launch.program(command, cwd)\n'),),
         ('test_ssh_exec.py::test_a_startup_cd_does_not_move_the_start[bash-profile]',
          'test_ssh_exec.py::test_a_startup_cd_does_not_move_the_start[zshenv]'),
         'a startup cd moved'),
    Case('ssh-stdin-before-start', 'executor/ssh.py',
         (('            return _collect(chan, timeout, sentinel, feeder.start)\n',
           '            feeder.start()\n            return _collect(chan, timeout)\n'),),
         ('test_ssh_exec.py::test_a_remote_rc_never_reads_the_callers_stdin',),
         "consumed the caller's stdin"),
    Case('ssh-drain-waits-for-holders', 'executor/ssh.py',
         (('            exited = now if exited is None else exited\n', '            exited = now\n'),),
         ('test_ssh_exec.py::test_a_startup_background_job_does_not_hold_the_call[bash-profile]',
          'test_ssh_exec.py::test_a_startup_background_job_does_not_hold_the_call[zshenv]'),
         'held the call'),
    Case('ssh-timeout-no-drain', 'executor/ssh.py',
         (('                _stopped(chan, out, err)\n', ''),),
         ('test_ssh_exec.py::test_a_timeout_drains_what_follows_the_term',),
         'lost what followed the TERM'),
    # Over ssh the exit status comes ahead of output still queued on a slow link: the read goes on
    # while output arrives, stops only after the grace with nothing arriving and says so, ends at the
    # timeout when output keeps arriving, and a call holding stdin waits for the start only so long.
    Case('ssh-returns-at-exit-status', 'executor/ssh.py',
         (('            status = chan.recv_exit_status()\n',
           "            return bytes(out), bytes(err), chan.recv_exit_status(), b''\n"),),
         ('test_ssh_exec.py::test_a_slow_link_returns_the_whole_file[256KiBps]',
          'test_ssh_exec.py::test_output_after_the_exit_status_is_read_to_the_end'),
         'was cut'),
    Case('ssh-idle-grace-zero', 'executor/ssh.py',
         (('            if now >= quiet + launch.GRACE:\n', '            if now >= quiet:\n'),),
         ('test_ssh_exec.py::test_a_slow_link_returns_the_whole_file[256KiBps]',
          'test_ssh_exec.py::test_output_after_the_exit_status_is_read_to_the_end'),
         'was cut'),
    Case('ssh-fixed-grace-from-exit', 'executor/ssh.py',
         (('            quiet = max(heard, exited)\n', '            quiet = exited\n'),),
         ('test_ssh_exec.py::test_a_slow_link_returns_the_whole_file[256KiBps]',
          'test_ssh_exec.py::test_a_slow_link_returns_the_whole_file[1MiBps]',
          'test_ssh_exec.py::test_a_slow_link_returns_both_streams_whole',
          'test_ssh_exec.py::test_a_late_sentinel_still_counts_as_started',
          'test_ssh_exec.py::test_output_after_the_exit_status_is_read_to_the_end'),
         'was cut'),
    Case('ssh-held-stop-silent', 'executor/ssh.py',
         (('                return bytes(out), bytes(err), status, launch.HELD_NOTE\n',
           "                return bytes(out), bytes(err), status, b''\n"),),
         ('test_ssh_exec.py::test_a_startup_background_job_does_not_hold_the_call[bash-profile]',
          'test_ssh_exec.py::test_a_startup_background_job_does_not_hold_the_call[zshenv]'),
         'plus the held-output note'),
    Case('ssh-holder-past-the-timeout', 'executor/ssh.py',
         (('            if deadline is not None and now >= deadline + launch.GRACE:\n'
           '                return bytes(out), bytes(err), TIMEOUT_EXIT_CODE, launch.LATE_NOTE.format(code=status).encode()\n',
           ''),
          ('            left = left if deadline is None else min(left, deadline + launch.GRACE - now)\n', '')),
         ('test_ssh_exec.py::test_a_holder_that_keeps_writing_ends_at_the_timeout[/bin/bash]',),
         'output still arriving'),
    Case('ssh-late-stop-silent', 'executor/ssh.py',
         (('TIMEOUT_EXIT_CODE, launch.LATE_NOTE.format(code=status).encode()\n', "TIMEOUT_EXIT_CODE, b''\n"),),
         ('test_ssh_exec.py::test_a_holder_that_keeps_writing_ends_at_the_timeout[/bin/zsh]',),
         'the stop at the timeout is silent'),
    Case('ssh-stdin-wait-unbounded', 'executor/ssh.py',
         (('                limit = heard + START_TIMEOUT if limit is None else min(limit, heard + START_TIMEOUT)\n',
           '                pass\n'),),
         ('test_ssh_exec.py::test_a_call_holding_stdin_is_bounded[run]',
          'test_ssh_exec.py::test_a_call_holding_stdin_is_bounded[write_file]',
          'test_ssh_exec.py::test_a_call_holding_stdin_is_bounded[sbatch]'),
         'has no bound'),
    Case('stdin-rule-unstated', 'executor/base.py',
         (("                stdin; a command never reads the server's own stdin. Over `ssh` the bytes go only\n"
           "                once the command has started, so if the remote login shell's rc file reads stdin,\n"
           "                a call that sends stdin times out and never writes partial content (−1001, empty\n"
           "                output; the command never runs). Such a call waits for the start at most\n",
           "                stdin; a command never reads the server's own stdin. Such a call waits for the start at most\n"),),
         ('test_ssh_exec.py::test_stdin_texts_state_the_rc_timeout',),
         'does not state the rc timeout'),
    Case('separation-exception-unstated', 'tools/shell.py',
         (("    are available whatever the user's login shell). Nothing the initialisation prints is returned,\n"
           "    with one exception: over ssh, a background job started by the remote login shell's own rc file\n"
           "    (zsh's .zshenv, say) can write into the result while the command runs.\n",
           "    are available whatever the user's login shell); nothing the initialisation prints is returned.\n"),),
         ('test_ssh_exec.py::test_separation_texts_state_the_remote_rc_exception',),
         'does not state the exception'),
    # SFTP: `~//x` stays in the login home, and a local name is taken literally.
    Case('sftp-tilde-root', 'executor/ssh.py',
         (("        return path[2:].lstrip('/') or '.'\n", "        return path[2:] or '.'\n"),),
         ('test_ssh_exec.py::test_transfers_land_where_named[put-tilde-double-slash]',
          'test_ssh_exec.py::test_transfers_land_where_named[get-tilde-double-slash]'),
         'did not land at'),
    Case('sftp-path-formatted', 'executor/ssh.py',
         (('            sftp.get(remote, local)\n', '            self._conn.get(remote_path, local_path)\n'),),
         ('test_ssh_exec.py::test_transfers_land_where_named[get-brace-local-name]',),
         'did not land at'),
    # The Executor protocol: every backend carries all eight members; a renamed one drops out.
    Case('local-put-renamed', 'executor/shell.py',
         (('    def put(self, local_path: str, remote_path: str) -> str:',
           '    def put_file(self, local_path: str, remote_path: str) -> str:'),),
         ('test_executors.py::test_backend_satisfies_executor_protocol[local]',),
         'does not satisfy Executor'),
    Case('delegate-hostname-renamed', 'executor/delegate.py',
         (('    def hostname(self) -> str:', '    def host_name(self) -> str:'),),
         ('test_executors.py::test_backend_satisfies_executor_protocol[delegate]',),
         'does not satisfy Executor'),
    Case('ssh-close-renamed', 'executor/ssh.py',
         (('    def close(self) -> None:\n        """Close the SSH connection."""',
           '    def disconnect(self) -> None:\n        """Close the SSH connection."""'),),
         ('test_executors.py::test_backend_satisfies_executor_protocol[ssh]',),
         'does not satisfy Executor'),
    # The execution-mode ladder: explicit -e, then stdio, then an SSH host from the flag or the
    # environment, then auth none, then delegate; delegate alone gets the auth middleware.
    Case('ladder-rungs-1-2-swapped', '__init__.py',
         (("        if self.exec_mode:\n            return self.exec_mode\n\n"
           "        # Auto-select based on transport, auth, and ssh-host\n"
           "        if self.transport == 'stdio':\n            return 'ssh'\n",
           "        if self.transport == 'stdio':\n            return 'ssh'\n"
           "        if self.exec_mode:\n            return self.exec_mode\n\n"
           "        # Auto-select based on transport, auth, and ssh-host\n"
           "        if False:\n            return 'ssh'\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-local-over-stdio]',),
         'mode:'),
    Case('ladder-rungs-3-4-swapped', '__init__.py',
         (("        elif self.ssh_host or os.environ.get('RCAC_SSH_HOST'):\n"
           '            # If SSH host is specified, use ssh mode regardless of transport\n'
           "            return 'ssh'\n"
           "        elif self.auth == 'none':\n            return 'local'\n",
           "        elif self.auth == 'none':\n            return 'local'\n"
           "        elif self.ssh_host or os.environ.get('RCAC_SSH_HOST'):\n"
           "            return 'ssh'\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[host-flag-over-auth-none]',
          'test_exec_mode.py::test_exec_mode_ladder[env-host-over-auth-none]'),
         'mode:'),
    # Each value of an explicit -e wins on its own; ignoring one lets the later rungs decide.
    Case('ladder-explicit-local-ignored', '__init__.py',
         ((EXPLICIT_MODE, "        if self.exec_mode and self.exec_mode != 'local':\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-local-over-stdio]',
          'test_exec_mode.py::test_exec_mode_ladder[explicit-local-over-host]',
          'test_exec_mode.py::test_exec_mode_ladder[explicit-local-over-auth]'),
         'mode:'),
    Case('ladder-explicit-ssh-ignored', '__init__.py',
         ((EXPLICIT_MODE, "        if self.exec_mode and self.exec_mode != 'ssh':\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-ssh-over-auth-none]',
          'test_exec_mode.py::test_exec_mode_ladder[explicit-ssh-over-auth]'),
         'mode:'),
    Case('ladder-explicit-delegate-ignored', '__init__.py',
         ((EXPLICIT_MODE, "        if self.exec_mode and self.exec_mode != 'delegate':\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-delegate-over-stdio]',
          'test_exec_mode.py::test_exec_mode_ladder[explicit-delegate-over-host]'),
         'mode:'),
    # An explicit -e that yields to one later-rung input: auth none (delegate falls to an
    # unauthenticated local shell), RCAC_SSH_HOST, or auth oidc.
    Case('ladder-explicit-delegate-yields-to-auth-none', '__init__.py',
         ((EXPLICIT_MODE, "        if self.exec_mode and not (self.exec_mode == 'delegate' and self.auth == 'none'):\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-delegate-over-auth-none]',
          'test_exec_mode.py::test_explicit_mode_decides_alone[delegate]'),
         'mode: resolved local, expected delegate'),
    Case('ladder-explicit-yields-to-env-host', '__init__.py',
         ((EXPLICIT_MODE, "        if self.exec_mode and not os.environ.get('RCAC_SSH_HOST'):\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-local-over-env-host]',
          'test_exec_mode.py::test_exec_mode_ladder[explicit-delegate-over-env-host]',
          'test_exec_mode.py::test_explicit_mode_decides_alone[local]',
          'test_exec_mode.py::test_explicit_mode_decides_alone[delegate]'),
         'mode: resolved ssh'),
    Case('ladder-explicit-yields-to-oidc', '__init__.py',
         ((EXPLICIT_MODE, "        if self.exec_mode and self.auth != 'oidc':\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[explicit-local-over-oidc]',
          'test_exec_mode.py::test_exec_mode_ladder[explicit-ssh-over-oidc]',
          'test_exec_mode.py::test_explicit_mode_decides_alone[local]',
          'test_exec_mode.py::test_explicit_mode_decides_alone[ssh]'),
         'mode: resolved delegate'),
    # Rung 1 decides, but to the wrong mode: every explicit row, including those whose later rungs
    # agree with the explicit value, reads the mode it resolved.
    Case('ladder-explicit-misrouted', '__init__.py',
         (("        if self.exec_mode:\n            return self.exec_mode\n",
           "        if self.exec_mode:\n"
           "            return {'local': 'delegate', 'delegate': 'ssh', 'ssh': 'local'}[self.exec_mode]\n"),),
         tuple(f'test_exec_mode.py::test_exec_mode_ladder[explicit-{mode}-over-{later}]'
               for mode in ('local', 'ssh', 'delegate')
               for later in ('stdio', 'host', 'env-host', 'auth-none', 'auth', 'oidc'))
         + tuple(f'test_exec_mode.py::test_explicit_mode_decides_alone[{mode}]' for mode in ('local', 'ssh', 'delegate')),
         'mode: resolved'),
    Case('ladder-env-host-ignored', '__init__.py',
         (("        elif self.ssh_host or os.environ.get('RCAC_SSH_HOST'):\n", '        elif self.ssh_host:\n'),),
         ('test_exec_mode.py::test_exec_mode_ladder[env-host-over-auth-none]',),
         'mode:'),
    Case('ladder-local-widened', '__init__.py',
         (("        elif self.auth == 'none':\n            return 'local'\n        else:\n            return 'delegate'",
           "        else:\n            return 'local'"),),
         ('test_exec_mode.py::test_exec_mode_ladder[jwt-without-host]',
          'test_exec_mode.py::test_exec_mode_ladder[oidc-without-host]'),
         'mode:'),
    Case('delegate-gets-shared-middleware', '__init__.py',
         (("        if exec_mode == 'delegate':\n", "        if exec_mode == 'delegate' and False:\n"),),
         ('test_exec_mode.py::test_exec_mode_ladder[jwt-without-host]',),
         'middleware:'),
    # The secret floor: create_jwt_auth() and --generate-token each refuse an unset, empty or
    # 31-character JWT_SECRET on their own, so each floor is lowered separately.
    Case('floor-auth-31', 'auth.py',
         (('    if len(secret) < 32:\n', '    if len(secret) < 31:\n'),),
         ('test_tokens.py::test_jwt_auth_refuses_short_secret[31-chars]',),
         'DID NOT RAISE'),
    Case('floor-cli-31', '__init__.py',
         (('            if len(secret) < 32:\n', '            if len(secret) < 31:\n'),),
         ('test_tokens.py::test_generate_token_refuses_short_secret[31-chars]',),
         'status'),
    Case('auth-unset-secret-defaulted', 'auth.py',
         (("        raise ValueError('JWT_SECRET environment variable required for jwt auth mode')",
           "        secret = 'x' * 32"),),
         ('test_tokens.py::test_jwt_auth_refuses_short_secret[unset]',
          'test_tokens.py::test_jwt_auth_refuses_short_secret[empty]'),
         'DID NOT RAISE'),
    # Signer and verifier agree on issuer, audience, algorithm and lifetime: drifting either side
    # rejects the tokens the other mints.
    Case('verifier-audience-changed', 'auth.py',
         (('        audience=AUDIENCE,\n', "        audience=AUDIENCE + '-other',\n"),),
         ('test_tokens.py::test_minted_token_is_accepted', 'test_tokens.py::test_cli_minted_token_is_accepted'),
         'error result'),
    Case('verifier-issuer-changed', 'auth.py',
         (('        issuer=ISSUER,\n', "        issuer=ISSUER + '-other',\n"),),
         ('test_tokens.py::test_minted_token_is_accepted',),
         'error result'),
    Case('verifier-algorithm-changed', 'auth.py',
         (('        algorithm=ALGORITHM,\n', "        algorithm='HS512',\n"),),
         ('test_tokens.py::test_minted_token_is_accepted',),
         'error result'),
    Case('signer-audience-changed', 'token.py',
         (("        'aud': AUDIENCE,\n", "        'aud': AUDIENCE + '-other',\n"),),
         ('test_tokens.py::test_minted_token_is_accepted', 'test_tokens.py::test_cli_minted_token_is_accepted'),
         'error result'),
    Case('signer-token-expired', 'token.py',
         (("        'exp': now + timedelta(seconds=lifetime),\n", "        'exp': now - timedelta(seconds=lifetime),\n"),),
         ('test_tokens.py::test_minted_token_is_accepted',),
         'error result'),
    # The rejection half: a verifier that stops checking a claim accepts a token minted for
    # another audience or issuer.
    Case('verifier-audience-unchecked', 'auth.py',
         (('        audience=AUDIENCE,\n', '        audience=None,\n'),),
         ('test_tokens.py::test_mismatched_token_is_rejected[audience]',),
         'expected 401'),
    Case('verifier-issuer-unchecked', 'auth.py',
         (('        issuer=ISSUER,\n', '        issuer=None,\n'),),
         ('test_tokens.py::test_mismatched_token_is_rejected[issuer]',),
         'expected 401'),
    # The error boundary: an exception an executor middleware raises around a tool call reaches the
    # client as a tool error result whose text is exactly str(exc); cancellation passes through.
    Case('tool-error-boundary-dropped', 'server.py',
         (('    server.add_middleware(ToolErrorMiddleware())\n', ''),),
         BOUNDARY_ROWS,
         'protocol error instead of a tool error result'),
    Case('boundary-leaks-traceback', 'middleware.py',
         (('raise ToolError(str(exc)) from exc', "raise ToolError(str(exc) + __import__('traceback').format_exc()) from exc"),),
         BOUNDARY_ROWS,
         'error text differs'),
    Case('boundary-catches-base-exception', 'middleware.py',
         (('        except Exception as exc:\n            raise ToolError(', '        except BaseException as exc:\n            raise ToolError('),),
         ('test_protocol.py::test_boundary_lets_cancellation_through',),
         'swallowed cancellation'),
    Case('boundary-reraises-mcp-error', 'middleware.py',
         (('        except FastMCPError:\n            raise\n',
           "        except (FastMCPError, __import__('mcp').MCPError):\n            raise\n"),),
         ('test_protocol.py::test_middleware_refusal_is_a_tool_error_result[mcp-error-memory]',),
         'protocol error instead of a tool error result'),
    # The identity the delegate middleware stores in the context state is request-scoped: it never
    # reaches the session store, where a later request on the same session would read it. Only the
    # handshake era has a session to leak into: under revision 2026-07-28 FastMCP keys each request's
    # state afresh, so the modern row holds whatever the scope and cannot catch this plant.
    Case('delegate-state-session-scoped', 'middleware.py',
         (("set_state('rcac_user', identity, serializable=False)", "set_state('rcac_user', identity)"),
          ("set_state('rcac_local_user', executor.local_user, serializable=False)",
           "set_state('rcac_local_user', executor.local_user)")),
         ('test_protocol.py::test_delegate_state_is_request_scoped[legacy]',),
         'state outlived its request'),
    # The server's instructions reach a client in both protocol eras: in the InitializeResult of a
    # handshake and in the DiscoverResult of revision 2026-07-28.
    Case('instructions-dropped', 'server.py',
         (('        instructions=SERVER_INSTRUCTIONS,\n', '        instructions=None,\n'),),
         ('test_protocol.py::test_client_era_is_served[legacy-memory]',
          'test_protocol.py::test_client_era_is_served[modern-memory]'),
         'instructions differ'),
    # Every tool's served description is its full docstring, as FastMCP 2.x served it; FastMCP 4
    # alone serves only the summary paragraph.
    Case('tool-description-summary-only', 'tools/__init__.py',
         (('    tool = Tool.from_function(func, description=inspect.getdoc(func))\n',
           '    tool = Tool.from_function(func)\n'),),
         ('test_surface.py::test_served_surface_matches_baseline',),
         'description differs'),
    # The ssh executor maps the remote exit status into its CommandResult.
    Case('ssh-exit-status-dropped', 'executor/ssh.py',
         (("            return bytes(out), bytes(err), chan.recv_exit_status(), b''\n",
           "            return bytes(out), bytes(err), 0, b''\n"),),
         ('test_ssh_smoke.py::test_ssh_executor_runs_over_a_loopback_server',),
         'exit status differs'),
    # `-t sse` serves: the CLI dispatches it to FastMCP's SSE transport and the server listens. The
    # target runs the installed console script, which imports this copy through PYTHONPATH.
    Case('sse-transport-unserved', '__init__.py',
         (("                mcp.run(transport='sse', host=self.host, port=self.port)\n",
           '                pass\n'),),
         ('test_transports.py::test_transport_lists_expected_tools[sse-legacy]',),
         'did not start'),
    # OIDC is all-or-nothing: a configuration missing any of the four variables is refused, naming
    # all four, before a provider is built. The proxy keeps the FastMCP 2.x posture.
    Case('oidc-check-dropped', 'auth.py',
         (('    if not all([config_url, client_id, client_secret, base_url]):\n', '    if False:\n'),),
         OIDC_ROWS,
         'raised no ValueError'),
    Case('oidc-secret-not-required', 'auth.py',
         (('    if not all([config_url, client_id, client_secret, base_url]):\n',
           '    if not all([config_url, client_id, base_url]):\n'),),
         tuple(row for row in OIDC_ROWS if '[OIDC_CLIENT_SECRET-' in row),
         'raised no ValueError'),
    Case('oidc-message-incomplete', 'auth.py',
         (("            'OIDC_CLIENT_SECRET, MCP_BASE_URL'\n", "            'OIDC_CLIENT_SECRET'\n"),),
         tuple(row for row in OIDC_ROWS if '[MCP_BASE_URL-' in row),
         'does not name MCP_BASE_URL'),
    Case('oidc-cimd-enabled', 'auth.py',
         (('        enable_cimd=False,\n', ''),),
         ('test_auth_modes.py::test_oidc_proxy_keeps_the_2x_posture',),
         'enable_cimd=True'),
    # `none` installs no provider.
    Case('none-mode-installs-provider', 'auth.py',
         (("    'none': lambda: None,\n",
           "    'none': lambda: JWTVerifier(public_key='x' * 32, issuer=ISSUER, audience=AUDIENCE, algorithm=ALGORITHM),\n"),),
         ('test_auth_modes.py::test_none_mode_installs_no_provider',),
         'none mode installed a provider'),
    # The registry's annotation resolves to a provider-factory map; the stray `:` made it a slice.
    Case('auth-modes-annotation-slice', 'auth.py',
         (('AUTH_MODES: Final[Dict[str, Callable[[], AuthProvider | None]]] = {',
           'AUTH_MODES: Final[Dict[str, Callable[[], AuthProvider | None]]:] = {'),),
         ('test_auth_modes.py::test_auth_modes_annotation_is_a_provider_factory_map',),
         'slice'),
    # The launcher: every argument one literal word; a UserPath's leading ~ through the guarded
    # $HOME and nothing else expanded; a relative path or cwd anchored with ./.
    Case('launcher-arg-unquoted', 'executor/launch.py',
         (('    return shlex.quote(str(arg))\n', '    return str(arg)\n'),),
         ('test_launch.py::test_program_quotes_each_argument_as_one_word[hostile]',
          'test_launch.py::test_values_arrive_literally[local]',
          'test_literal_args.py::test_literal_argument[local-squeue-user]'),
         'reaches the command as'),
    Case('launcher-tilde-user-expanded', 'executor/launch.py',
         (("        if arg == '~':\n            return HOME_WORD\n", "        if arg == '~':\n            return '~'\n"),
          ("return f'{HOME_WORD}/{shlex.quote(rest)}' if rest else HOME_WORD",
           "return f'~/{shlex.quote(rest)}' if rest else '~'")),
         ('test_launch.py::test_program_quotes_each_argument_as_one_word[user-paths]',
          'test_launch.py::test_an_empty_home_fails_a_tilde_path[local]'),
         'does not resolve through $HOME'),
    Case('launcher-tilde-name-expanded', 'executor/launch.py',
         (("        if arg and not arg.startswith('/'):\n",
           "        if arg.startswith('~'):\n"
           "            user, _, rest = arg[1:].partition('/')\n"
           "            return '~' + user + ('/' + shlex.quote(rest) if rest else '')\n"
           "        if arg and not arg.startswith('/'):\n"),),
         ('test_launch.py::test_program_quotes_each_argument_as_one_word[user-paths]',),
         'leaves ~name for bash to expand'),
    Case('launcher-cwd-cdpath', 'executor/launch.py',
         (("lines.append(f'command cd -- {word(UserPath(cwd))} || exec /usr/bin/false')",
           "lines.append(f'command cd -- {shlex.quote(cwd)} || exec /usr/bin/false')"),),
         ('test_launch.py::test_program_renders_the_cwd[sub]',
          'test_launch.py::test_cwd_resolves_in_the_running_users_home[local]'),
         'reaches cd as'),
    Case('launcher-relative-dash', 'executor/launch.py',
         (("            return shlex.quote('./' + arg)\n", '            return shlex.quote(arg)\n'),),
         ('test_launch.py::test_program_quotes_each_argument_as_one_word[user-paths]',
          'test_launch.py::test_user_paths_resolve_only_a_leading_tilde[local]'),
         'reaches the command as'),
    # The SSH wire: the program travels as one base64 word behind a fixed bootstrap that keeps its
    # trailing newlines and runs nothing when decoding fails.
    Case('bootstrap-raw-program', 'executor/launch.py',
         (("    wire = BOOTSTRAP + base64.b64encode(prog.encode('utf-8')).decode('ascii')\n",
           '    wire = BOOTSTRAP + shlex.quote(prog)\n'),),
         ('test_launch.py::test_bootstrap_is_inert_text[hostile]',),
         'not base64'),
    Case('bootstrap-newlines-stripped', 'executor/launch.py',
         (('base64 -d && printf .)', 'base64 -d)'), (r'-c -- \"${{p%.}}\"', r'-c -- \"$p\"')),
         ('test_launch.py::test_bootstrap_delivers_the_program_exactly[ssh-bash]',),
         'not the program sent'),
    Case('bootstrap-decode-unchecked', 'executor/launch.py',
         (('base64 -d && printf .)', 'base64 -d; printf .)'),),
         ('test_launch.py::test_bootstrap_fails_without_base64[ssh-bash]',),
         'without base64'),
    # The login initialisation runs with the caller's streams parked and none of its output, traps
    # or exits reaching a result.
    Case('stdin-not-parked', 'executor/launch.py',
         (("PARK: Final = 'exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 </dev/null >/dev/null 2>&1'",
           "PARK: Final = 'exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 >/dev/null 2>&1'"),),
         ('test_launch.py::test_profile_cannot_read_stdin_or_shadow_cd[local]',
          'test_executors.py::test_a_profile_cannot_read_stdin[local]',
          'test_executors.py::test_a_profile_cannot_read_stdin[delegate]'),
         'not the stdin sent'),
    Case('startup-not-parked', 'executor/launch.py',
         (("PARK: Final = 'exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 </dev/null >/dev/null 2>&1'",
           "PARK: Final = 'exec 57<&0 58>&1 59>&2 47<&0 48>&1 49>&2 </dev/null'"),),
         ('test_launch.py::test_startup_output_is_separated[local-argv]',),
         'differs from the quiet home'),
    # A startup file that took a parked descriptor leaves the command not run, and the result says so.
    Case('parked-descriptor-unchecked', 'executor/launch.py',
         (("PARKED_SAME: Final = '[[ /dev/fd/57 -ef /dev/fd/47 && /dev/fd/58 -ef /dev/fd/48 && /dev/fd/59 -ef /dev/fd/49 ]]'",
           "PARKED_SAME: Final = 'true'"),),
         ('test_launch.py::test_a_taken_parked_descriptor_runs_nothing[57]',
          'test_launch.py::test_a_taken_parked_descriptor_runs_nothing[58]'),
         'taken: the result is'),
    Case('parked-descriptor-unnoted', 'executor/launch.py',
         (('        if TAKEN_MARK in err:\n', '        if False:\n'),),
         ('test_launch.py::test_a_taken_parked_descriptor_runs_nothing[58]',
          'test_launch.py::test_a_taken_parked_descriptor_runs_nothing[49]'),
         'taken: the result is'),
    Case('startup-traps-kept', 'executor/launch.py',
         (("'set +euxvTkP +o errtrace +o posix; trap - EXIT ERR DEBUG RETURN; '",
           "'set +euxvTkP +o errtrace +o posix; '"),),
         ('test_launch.py::test_startup_output_is_separated[local-argv]',),
         'differs from the quiet home'),
    Case('prologue-unalias-shared-line', 'executor/launch.py',
         ((r"PROLOGUE: Final = ('\\unalias -a\n'" + "\n                   'unset -f builtin",
           "PROLOGUE: Final = ('unalias -a; '\n                   'unset -f builtin"),),
         ('test_launch.py::test_profile_cannot_hijack_the_prologue[local-alias]',),
         'differs from the quiet home'),
    Case('prologue-read-function-kept', 'executor/launch.py',
         (("export declare compgen read; unset IFS; '", "export declare compgen; unset IFS; '"),),
         ('test_launch.py::test_profile_cannot_hijack_the_prologue[local-read-function]',),
         'differs from the quiet home'),
    Case('stage-reads-bash-env', 'executor/launch.py',
         (("    return [*POSIX_STAGE, '-c', STAGE, 'cluster-mcp', prog]",
           "    return [BASH, NORC, '-c', STAGE, 'cluster-mcp', prog]"),),
         ('test_launch.py::test_startup_output_is_separated[local-stdin]',),
         'differs from the quiet home'),
    Case('script-in-login-shell', 'executor/launch.py',
         (("lines.append(SCRIPT.format(script=shlex.quote(_checked(command, 'command'))))",
           "lines.append(_checked(command, 'command'))"),),
         ('test_launch.py::test_startup_output_is_separated[local-exit]',),
         'differs from the quiet home'),
    Case('startup-output-kept', 'executor/launch.py',
         (('    kept_out, kept_err = _after(out, sentinel), _after(err, sentinel)\n',
           '    kept_out, kept_err = out, err\n'),),
         ('test_launch.py::test_separate_keeps_only_the_commands_output[chatty-prefix]',),
         'kept output from before the sentinel'),
    Case('not-started-exit-zero', 'executor/launch.py',
         (("        return b'', NOT_STARTED_NOTE.format(code=code).encode() + note, NOT_STARTED_EXIT_CODE\n",
           "        return b'', NOT_STARTED_NOTE.format(code=code).encode() + note, code\n"),),
         ('test_launch.py::test_separate_keeps_only_the_commands_output[not-started]',
          'test_launch.py::test_a_profile_that_exits_is_not_started[local]'),
         'did not start, but'),
    # Timeouts: the whole group is stopped, the captured output kept, and a command that already
    # exited keeps its own status.
    Case('timeout-group-not-killed', 'executor/launch.py',
         (('            os.killpg(proc.pid, sig)\n', '            os.kill(proc.pid, sig)\n'),),
         ('test_launch.py::test_timeout_kills_the_process_group',),
         'still running'),
    Case('timeout-partial-output-dropped', 'executor/launch.py',
         (("            out, err = proc.communicate(timeout=GRACE)\n            return out, err, code, b''\n",
           "            out, err = proc.communicate(timeout=GRACE)\n            return b'', b'', code, b''\n"),),
         ('test_launch.py::test_timeout_keeps_partial_output',),
         'lost the output'),
    Case('timeout-completed-misreported', 'executor/launch.py',
         (("            return last.stdout or b'', last.stderr or b'', status, HELD_NOTE\n",
           "            return last.stdout or b'', last.stderr or b'', TIMEOUT_EXIT_CODE, HELD_NOTE\n"),),
         ('test_launch.py::test_timeout_keeps_a_completed_status',),
         'before the deadline reported'),
    # Once the command has exited, what it or a startup file left running gets a bounded grace: the
    # exit is noticed while the output is still held, and the read after it is bounded.
    Case('exit-not-noticed', 'executor/launch.py',
         (('            status = proc.poll()\n', ''),),
         ('test_launch.py::test_a_held_output_does_not_hold_a_completed_command',
          'test_executors.py::test_a_profile_background_job_does_not_hold_the_call[local]',
          'test_executors.py::test_a_profile_background_job_does_not_hold_the_call[delegate]'),
         'held the call'),
    Case('drain-waits-for-holders', 'executor/launch.py',
         (("        elif now >= max(heard, exited) + GRACE:\n            _close(proc)\n"
           "            return last.stdout or b'', last.stderr or b'', status, HELD_NOTE\n", ''),),
         ('test_launch.py::test_a_held_output_does_not_hold_a_completed_command',
          'test_executors.py::test_a_profile_background_job_does_not_hold_the_call[local]',
          'test_executors.py::test_a_profile_background_job_does_not_hold_the_call[delegate]'),
         'held the call'),
    # Where a local command starts: the server's working directory, written into the program text,
    # never carried by a positional parameter (bash 4 and later let a startup file rewrite those).
    Case('local-start-dropped', 'executor/shell.py',
         (('        prog, sentinel = launch.program(command, cwd, start=os.getcwd())\n',
           '        prog, sentinel = launch.program(command, cwd)\n'),),
         ('test_executors.py::test_a_profile_cd_does_not_move_the_start[local]',),
         'a profile cd moved'),
    Case('local-start-positional', 'executor/launch.py',
         (("""-c -- "$1"'\n""", """-c -- "$1" cluster-mcp "$PWD"'\n"""),
          ("        lines.append(f'command cd -- {shlex.quote(start)} || exec /usr/bin/false')\n",
           """        lines.append('command cd -- "${1:?the start directory is not set}" || exec /usr/bin/false')\n""")),
         ('test_launch.py::test_program_reads_no_positional_parameter[argv-directory]',
          'test_launch.py::test_program_reads_no_positional_parameter[script-directory]'),
         'positional parameter'),
    # A result is complete unless only holders remain: once the command has exited, the output is
    # read while it keeps arriving, the read stops only after the grace with nothing arriving, and
    # says so; output still arriving at the timeout ends the call there, saying so too.
    Case('local-returns-at-exit', 'executor/launch.py',
         (('                exited = now\n',
           "                exited = now\n                return last.stdout or b'', last.stderr or b'', status, b''\n"),),
         ('test_executors.py::test_output_after_the_exit_is_read_while_it_arrives[local]',
          'test_executors.py::test_output_after_the_exit_is_read_while_it_arrives[delegate]'),
         'was cut'),
    Case('local-idle-grace-zero', 'executor/launch.py',
         (('        elif now >= max(heard, exited) + GRACE:\n', '        elif now >= max(heard, exited):\n'),),
         ('test_executors.py::test_output_after_the_exit_is_read_while_it_arrives[local]',),
         'was cut'),
    Case('local-fixed-grace-from-exit', 'executor/launch.py',
         (('        elif now >= max(heard, exited) + GRACE:\n', '        elif now >= exited + GRACE:\n'),),
         ('test_executors.py::test_output_after_the_exit_is_read_while_it_arrives[local]',),
         'was cut'),
    Case('held-stop-silent', 'executor/launch.py',
         (("            return last.stdout or b'', last.stderr or b'', status, HELD_NOTE\n",
           "            return last.stdout or b'', last.stderr or b'', status, b''\n"),),
         ('test_executors.py::test_a_profile_background_job_does_not_hold_the_call[local]',
          'test_executors.py::test_a_profile_background_job_does_not_hold_the_call[delegate]'),
         'plus the held-output note'),
    Case('separate-note-dropped', 'executor/launch.py',
         (("    return kept_out or b'', (kept_err or b'') + notes + note, code\n",
           "    return kept_out or b'', (kept_err or b'') + notes, code\n"),),
         ('test_executors.py::test_a_profile_background_job_does_not_hold_the_call[local]',
          'test_ssh_exec.py::test_a_startup_background_job_does_not_hold_the_call[zshenv]'),
         'plus the held-output note'),
    Case('holder-past-the-timeout', 'executor/launch.py',
         (("        elif deadline is not None and now >= deadline + GRACE:\n            _close(proc)\n"
           "            return last.stdout or b'', last.stderr or b'', TIMEOUT_EXIT_CODE, LATE_NOTE.format(code=status).encode()\n",
           ''),),
         ('test_executors.py::test_a_holder_that_keeps_writing_ends_at_the_timeout[local]',
          'test_executors.py::test_a_holder_that_keeps_writing_ends_at_the_timeout[delegate]'),
         'output still arriving'),
    Case('late-stop-silent', 'executor/launch.py',
         (("TIMEOUT_EXIT_CODE, LATE_NOTE.format(code=status).encode()\n    code = TIMEOUT_EXIT_CODE\n",
           "TIMEOUT_EXIT_CODE, b''\n    code = TIMEOUT_EXIT_CODE\n"),),
         ('test_executors.py::test_a_holder_that_keeps_writing_ends_at_the_timeout[local]',),
         'the stop at the timeout is silent'),
    # Every step of the timeout sequence: SIGTERM first, what follows it kept, then SIGKILL.
    Case('timeout-sigkill-dropped', 'executor/launch.py',
         (('    for sig in (signal.SIGTERM, signal.SIGKILL):\n', '    for sig in (signal.SIGTERM,):\n'),),
         ('test_launch.py::test_a_timeout_escalates_to_sigkill',),
         'no SIGKILL followed'),
    Case('timeout-sigterm-dropped', 'executor/launch.py',
         (('    for sig in (signal.SIGTERM, signal.SIGKILL):\n', '    for sig in (signal.SIGKILL,):\n'),),
         ('test_launch.py::test_a_timeout_sends_term_first_and_keeps_what_follows',
          'test_launch.py::test_a_timeout_signals_the_whole_group_term_first'),
         'SIGTERM'),
    # Nothing after the startup files depends on shell state they can change: the function export
    # neither splits nor globs, the prologue resets `set -k` and `set -P`, and a fixed script undoes
    # the functions and options a profile exported to it.
    Case('function-export-split', 'executor/launch.py',
         (("""SCRIPT: Final = ('while IFS= read -r cluster_mcp_fn; do export -f -- "$cluster_mcp_fn"; '\n"""
           """                 'done < <(compgen -A function); unset cluster_mcp_fn; '\n""",
           """SCRIPT: Final = ('fns=$(compgen -A function); [ -z "$fns" ] || export -f $fns; '\n"""),),
         ('test_launch.py::test_profile_state_does_not_reach_the_launcher[local-glob-options]',
          'test_launch.py::test_profile_state_does_not_reach_the_launcher[ssh-zsh-glob-options]'),
         'reached the launcher'),
    Case('prologue-keyword-kept', 'executor/launch.py',
         (("'set +euxvTkP +o errtrace", "'set +euxvTP +o errtrace"),),
         ('test_launch.py::test_profile_state_does_not_reach_the_launcher[local-keyword]',
          'test_launch.py::test_profile_state_does_not_reach_the_launcher[ssh-bash-keyword]'),
         'reached the launcher'),
    Case('prologue-physical-kept', 'executor/launch.py',
         (("'set +euxvTkP +o errtrace", "'set +euxvTk +o errtrace"),),
         ('test_launch.py::test_profile_state_does_not_reach_the_launcher[local-physical]',
          'test_launch.py::test_profile_state_does_not_reach_the_launcher[ssh-zsh-physical]'),
         'reached the launcher'),
    Case('fixed-reset-dropped', 'executor/launch.py',
         (("    return [*POSIX_STAGE, '-c', FIXED_RESET + script, name, *values]\n",
           "    return [*POSIX_STAGE, '-c', script, name, *values]\n"),),
         ('test_writes.py::test_a_profile_cannot_change_a_fixed_script[local]',
          'test_writes.py::test_a_profile_cannot_change_a_fixed_script[ssh]'),
         'changed what a fixed script does'),
    # No bash the launcher starts but the login bash reads a startup file, whatever sshd sets.
    Case('stage-reads-bashrc', 'executor/launch.py',
         (("    return [*POSIX_STAGE, '-c', STAGE, 'cluster-mcp', prog]",
           "    return [BASH, '--posix', '-c', STAGE, 'cluster-mcp', prog]"),),
         ('test_launch.py::test_no_stage_reads_a_startup_file_under_sshd[local-argv]',),
         'a stage read a startup file'),
    Case('bootstrap-reads-bashrc', 'executor/launch.py',
         (('''BOOTSTRAP: Final = (f"exec {' '.join(POSIX_STAGE)} -c ''', '''BOOTSTRAP: Final = (f"exec {BASH} --posix -c '''),),
         ('test_launch.py::test_no_stage_reads_a_startup_file_under_sshd[ssh-zsh-argv]',
          'test_launch.py::test_no_stage_reads_a_startup_file_under_sshd[ssh-sh-argv]'),
         'a stage read a startup file'),
    Case('script-reads-bashrc', 'executor/launch.py',
         (("f'BASH_ENV= ENV= exec {BASH} {NORC} -c -- {{script}}')", "f'BASH_ENV= ENV= exec {BASH} -c -- {{script}}')"),),
         ('test_launch.py::test_no_stage_reads_a_startup_file_under_sshd[local-script]',),
         'a stage read a startup file'),
    Case('fixed-script-reads-bashrc', 'executor/launch.py',
         (("    return [*POSIX_STAGE, '-c', FIXED_RESET + script, name, *values]\n",
           "    return [BASH, '--posix', '-c', FIXED_RESET + script, name, *values]\n"),),
         ('test_launch.py::test_no_stage_reads_a_startup_file_under_sshd[local-fixed-script]',),
         'a stage read a startup file'),
    # Feature-scoped spec ids stay out of the package.
    Case('spec-id-in-source', 'executor/base.py',
         (('# The exit code of a command stopped at its timeout.', '# The exit code of a command stopped at its timeout (R' + '8).'),),
         ('test_hygiene.py::test_no_spec_ids_in_the_package_or_the_suite',),
         'a spec id names'),
    # Every size limit is checked against the bytes that cross it.
    Case('local-size-unchecked', 'executor/launch.py',
         (("    _fits(prog, 'its program')\n", ''),),
         ('test_launch.py::test_local_launch_limit_is_the_per_argument_limit',),
         'over the per-argument limit'),
    Case('ssh-size-ignores-bootstrap', 'executor/launch.py',
         (("    return _fits(wire, 'its SSH exec string')\n", "    _fits(prog, 'its SSH exec string')\n    return wire\n"),),
         ('test_launch.py::test_ssh_wire_limit_counts_the_bootstrap',
          'test_ssh_exec.py::test_an_oversize_program_is_refused_before_connecting'),
         'over the per-argument limit'),
    # A failed SSH connect at startup is answered: every request until EOF (a 2026-07-28 client
    # retries with `initialize` after its `server/discover` is refused), naming the host, on stdout
    # nothing but the answers, then a nonzero exit; and the connect itself is bounded.
    Case('startup-answers-first-only', 'startup.py',
         (('            stdout.write(refusal(request_id, message))\n            stdout.flush()\n',
           '            stdout.write(refusal(request_id, message))\n            stdout.flush()\n            return\n'),),
         ('test_startup.py::test_responder_answers_every_request',
          'test_startup.py::test_raw_wire_answers_every_request',
          'test_startup.py::test_startup_failure_is_answered[modern]'),
         'not answered'),
    Case('startup-host-unnamed', 'startup.py',
         (('    cause = _cause_class(exc)\n', "    cause = _cause_class(exc)\n    host = 'the SSH host'\n"),),
         ('test_startup.py::test_raw_wire_answers_every_request',
          'test_startup.py::test_connect_failure_names_host_and_cause[unknown-host]',
          'test_startup.py::test_connect_failure_names_host_and_cause[other]'),
         'does not name the host'),
    Case('startup-exit-zero', '__init__.py',
         (('        StartupFailure: partial(exit_reported, status=exit_status.runtime_error),\n',
           '        StartupFailure: partial(exit_reported, status=exit_status.success),\n'),),
         ('test_startup.py::test_raw_wire_answers_every_request',
          'test_startup.py::test_connect_failure_names_host_and_cause[refused]'),
         'exit status 0'),
    Case('startup-stdout-noise', 'startup.py',
         (('    if stdin.isatty():\n        return\n',
           "    if stdin.isatty():\n        return\n    stdout.write(message + '\\n')\n"),),
         ('test_startup.py::test_responder_answers_every_request',
          'test_startup.py::test_raw_wire_answers_every_request'),
         'is not an error response'),
    Case('startup-no-connect-timeout', 'executor/ssh.py',
         (('        self._conn = Connection(self._host, **_bounded(self._connect_kwargs))\n',
           '        self._conn = Connection(self._host, **self._connect_kwargs)\n'),),
         ('test_startup.py::test_connect_is_bounded[default]',
          'test_startup.py::test_silent_banner_is_answered_within_the_bound'),
         'the connect has no bound'),
    # The filesystem tools hand the executor argvs (a shell only for a registered fixed script, with
    # values as its positional parameters), and write_file writes exactly the bytes sent, on stdin,
    # after validating them.
    Case('tool-command-string', 'tools/filesystem.py',
         (("        result = executor.run(['cat', '--', UserPath(path)])\n",
           "        result = executor.run(f'cat {path}')\n"),),
         ('test_tools.py::test_tool_reaches_only_the_installed_executor[read_file]',
          'test_literal_args.py::test_literal_argument[local-read_file-path]'),
         'passed a command string'),
    Case('fixed-script-interpolated', 'tools/filesystem.py',
         (("    result = executor.run(script_argv(LIST, 'list_directory', UserPath(path), *options))\n",
           "    result = executor.run(script_argv(LIST.replace('\"$1\"', __import__('shlex').quote(path)),"
           " 'list_directory', *options))\n"),),
         ('test_tools.py::test_tool_reaches_only_the_installed_executor[list_directory]',),
         'unregistered script'),
    Case('read-file-no-dashdash', 'tools/filesystem.py',
         (("        result = executor.run(['cat', '--', UserPath(path)])\n",
           "        result = executor.run(['cat', UserPath(path)])\n"),),
         ('test_tools.py::test_filesystem_argvs[read-cat]',),
         'argv differs'),
    Case('write-final-newline', 'tools/filesystem.py',
         (('    result = executor.run(argv, stdin=data)\n', "    result = executor.run(argv, stdin=data + b'\\n')\n"),),
         ('test_writes.py::test_writes_are_byte_exact[local-overwrite]',
          'test_writes.py::test_writes_are_byte_exact[delegate-overwrite]',
          'test_writes.py::test_writes_are_byte_exact[ssh-overwrite]'),
         'differs from the bytes sent'),
    Case('content-in-argv', 'tools/filesystem.py',
         (("""WRITE: Final = fixed('cat >"$1"')""", """WRITE: Final = fixed('printf %s "$2" >"$1"')"""),
          ("        argv = script_argv(APPEND if append else WRITE, 'write_file', UserPath(path))\n",
           "        argv = script_argv(APPEND if append else WRITE, 'write_file', UserPath(path), content)\n")),
         ('test_writes.py::test_content_never_travels_in_argv[overwrite]',),
         'content in argv'),
    Case('append-overwrites', 'tools/filesystem.py',
         (("""APPEND: Final = fixed('cat >>"$1"')""", """APPEND: Final = fixed('cat >"$1"')"""),),
         ('test_writes.py::test_writes_are_byte_exact[local-append]',
          'test_writes.py::test_writes_are_byte_exact[delegate-append]'),
         'differs from the bytes sent'),
    Case('create-dirs-dirname-subst', 'tools/filesystem.py',
         (("""MKDIR: Final = 'mkdir -p -- "$2" && '""", """MKDIR: Final = 'mkdir -p -- "$(dirname -- "$1")" && '"""),),
         ('test_writes.py::test_create_dirs_makes_the_exact_parent[local]',
          'test_writes.py::test_create_dirs_makes_the_exact_parent[delegate]'),
         'not written'),
    Case('base64-unvalidated', 'tools/filesystem.py',
         (('validate=True', 'validate=False'),),
         ('test_writes.py::test_invalid_write_leaves_the_target_untouched[local]',
          'test_writes.py::test_refused_writes_never_reach_the_executor[vertical-tab]'),
         'was accepted'),
    Case('write-before-validate', 'tools/filesystem.py',
         (('    data = _content_bytes(content, encoding)\n    executor = get_executor()\n',
           "    executor = get_executor()\n    executor.run(script_argv(WRITE, 'write_file', UserPath(path)))\n"
           '    data = _content_bytes(content, encoding)\n'),),
         ('test_writes.py::test_invalid_write_leaves_the_target_untouched[local]',
          'test_writes.py::test_invalid_write_leaves_the_target_untouched[delegate]'),
         'target changed'),
    # The Slurm tools hand the executor argvs: each option value its own element after its option, a
    # positional after `--`, the job script's arguments after its path, the extra options as a list
    # of single arguments, and script_content written exactly, on stdin.
    Case('sbatch-args-joined', 'tools/slurm.py',
         (("    argv += [*options, '--', path, *(args or [])]\n",
           "    argv += [*options, '--', path, *([' '.join(args)] if args else [])]\n"),),
         ('test_sbatch.py::test_args_follow_the_script_and_extra_args_precede_dashdash',
          'test_sbatch.py::test_sbatch_receives_each_value_as_one_argument[local]'),
         'does not end with the extra options'),
    Case('extra-args-accepts-str', 'tools/slurm.py',
         (('    error: Optional[str] = None,\n    extra_args: Optional[List[str]] = None,\n',
           '    error: Optional[str] = None,\n    extra_args: Optional[List[str] | str] = None,\n'),
          ('    me: bool = True,\n    extra_args: Optional[List[str]] = None,\n) -> str:\n    """\n    View the Slurm job queue.',
           '    me: bool = True,\n    extra_args: Optional[List[str] | str] = None,\n) -> str:\n    """\n    View the Slurm job queue.'),
          ('    if not extra_args:\n        return []\n',
           '    if not extra_args:\n        return []\n    if isinstance(extra_args, str):\n        extra_args = extra_args.split()\n')),
         ('test_sbatch.py::test_a_string_list_parameter_is_refused_by_the_schema[extra_args-legacy]',
          'test_sbatch.py::test_a_string_list_parameter_is_refused_by_the_schema[extra_args-modern]',
          'test_extra_args.py::test_a_string_extra_args_is_refused_by_the_schema[squeue-legacy]',
          'test_extra_args.py::test_a_string_extra_args_is_refused_by_the_schema[squeue-modern]'),
         'as a string was accepted'),
    Case('extra-args-swallow-dashdash', 'tools/slurm.py',
         (('        path = UserPath(script_path)\n', '        path = script_path\n'),),
         ('test_sbatch.py::test_sbatch_receives_each_value_as_one_argument[local]',
          'test_sbatch.py::test_sbatch_receives_each_value_as_one_argument[delegate]'),
         'readable as an option'),
    Case('option-value-joined', 'tools/slurm.py',
         (('            argv += [option, value]\n', "            argv.append(f'{option}{value}')\n"),),
         ('test_tools.py::test_slurm_and_site_argvs[squeue]',
          'test_tools.py::test_slurm_and_site_argvs[sinfo]',
          'test_tools.py::test_slurm_and_site_argvs[sbatch]',
          'test_literal_args.py::test_literal_argument[delegate-sinfo-format]',
          'test_literal_args.py::test_literal_argument[ssh-sbatch-mem]'),
         'argv differs'),
    Case('script-content-newline', 'tools/slurm.py',
         (('                               stdin=data)\n', "                               stdin=data + b'\\n')\n"),),
         ('test_writes.py::test_job_scripts_are_byte_exact[local]',
          'test_writes.py::test_job_scripts_are_byte_exact[delegate]',
          'test_writes.py::test_job_scripts_are_byte_exact[ssh]'),
         'differs from the bytes sent'),
    Case('empty-optional-passed-literally', 'tools/slurm.py',
         (('        if value:\n            argv += [option, value]\n',
           '        if value is not None:\n            argv += [option, value]\n'),),
         tuple(f'test_tools.py::test_empty_optional_scalars_mean_not_given[{row}]'
               for row in ('squeue-user', 'squeue', 'scancel', 'sacct', 'sinfo', 'sbatch'))
         + ('test_literal_args.py::test_literal_argument[local-squeue-state]',),
         'argv differs'),
    Case('scontrol-node-positional', 'tools/slurm.py',
         (("        argv += ['--', node]\n", '        argv += [node]\n'),),
         ('test_tools.py::test_slurm_and_site_argvs[scontrol-node]',
          'test_literal_args.py::test_literal_argument[local-scontrol_show_node-node]'),
         'argv differs'),
    # The site's `average_wait` passes options it does not know to `sacct`, whose partition option is
    # `--partition` (`-p` there is `--parsable`, which takes no value).
    Case('average-wait-short-partition', 'tools/rcac.py',
         (("        argv += ['--partition', partition]\n", "        argv += ['-p', partition]\n"),),
         ('test_tools.py::test_slurm_and_site_argvs[average-wait]',),
         'argv differs'),
    # An empty optional positional was not given: `scontrol show node` shows every node, and an
    # empty script path leaves sbatch nothing to submit.
    Case('empty-positional-passed-literally', 'tools/slurm.py',
         (('    if not script_path and script_content is None:\n',
           '    if script_path is None and script_content is None:\n'),
          ('    if script_path and script_content is not None:\n',
           '    if script_path is not None and script_content is not None:\n'),
          ('    if node:\n', '    if node is not None:\n')),
         ('test_tools.py::test_empty_optional_scalars_mean_not_given[scontrol-node]',
          'test_tools.py::test_empty_optional_scalars_mean_not_given[sbatch-script-path]',
          'test_literal_args.py::test_literal_argument[local-scontrol_show_node-node]',
          'test_literal_args.py::test_literal_argument[local-sbatch-script_path]'),
         'argv differs'),
    # An empty directory to list, or an empty cwd, was not given. (Bash runs `cd ''` as staying
    # put, so no matrix row can see either: the exact calls pin them.)
    Case('empty-list-path-passed-literally', 'tools/filesystem.py',
         (("    path = path or '.'\n", ''),),
         ('test_tools.py::test_empty_optional_scalars_mean_not_given[list-directory]',),
         'argv differs'),
    Case('empty-cwd-passed-literally', 'tools/shell.py',
         (('cwd=cwd or None', 'cwd=cwd'),),
         ('test_tools.py::test_empty_optional_scalars_mean_not_given[run-command-cwd]',),
         'argv differs'),
    # An empty transfer path names no file: refused, naming the parameter, before anything runs. The
    # row's calls run concurrently, so under this mutant the empty path's upload races another into
    # the same file and the call's own failure varies; its reaching the executor does not.
    Case('transfer-empty-path-passed', 'tools/transfer.py',
         (("    if not path:\n        raise ValueError(f'{name} is empty: an empty path names no file')\n", ''),),
         ('test_literal_args.py::test_literal_argument[ssh-upload_file-remote_path]',),
         'reaches the executor as an empty path'),
    # Every string argument with a lone surrogate is refused naming its parameter, in every mode.
    Case('surrogate-unnamed', 'tools/__init__.py',
         (('    func = _literal_text(func)\n', ''),),
         ('test_literal_args.py::test_literal_argument[local-squeue-user]',
          'test_literal_args.py::test_literal_argument[ssh-upload_file-local_path]'),
         'does not name'),
)

# The runner's own controls (--self-check). A docstring edit changes no behaviour, so its target
# passes and the runner must call it a survivor; this proves the child really runs the targets.
SURVIVOR_CONTROL = Case(
    'control-docstring-only', 'context.py',
    (('"""Context management for request-scoped state."""',
      '"""Context management for request-scoped state (unchanged behaviour)."""'),),
    ('test_isolation.py::test_concurrent_delegate_calls_stay_isolated',),
    'ran as')


def apply(case: Case, dest: Path) -> Path:
    """Copy the imported package under `dest/src`, apply the case's edits, and return `dest/src`."""
    src_root = dest / 'src'
    package = src_root / 'rcac_mcp'
    shutil.copytree(Path(rcac_mcp.__file__).parent, package, ignore=shutil.ignore_patterns('__pycache__'))
    path = package / case.file
    text = path.read_text()
    for old, new in case.edits:
        count = text.count(old)
        if count != 1:
            raise ValueError(f'{case.id}: the anchor occurs {count} times in {case.file}, expected once; '
                             f'the code this case plants against has moved: {old[:80]!r}')
        text = text.replace(old, new)
    path.write_text(text)
    return src_root


def run_child(src_root: Path, targets: Sequence[str], junit: Path) -> subprocess.CompletedProcess[str]:
    """Run `targets` against the package under `src_root` in a sandboxed child pytest."""
    env = {**os.environ, 'PYTHONPATH': str(src_root), 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run(
        [str(SANDBOX), 'sh', '-c', CHILD, 'sh', sys.executable, str(src_root), str(junit),
         *[f'tests/{target}' for target in targets]],
        env=env, capture_output=True, text=True, timeout=300)


def _node_id(case: ET.Element) -> str:
    # pytest writes classname "tests.test_x" and name "test_y[param]" for tests/test_x.py::test_y[param].
    module = case.get('classname', '').split('.')
    return '/'.join(module[1:-1] + [module[-1] + '.py']) + '::' + case.get('name', '')


def mutation_violations(returncode: int, junit_xml: str | None, targets: Sequence[str], why: str) -> list[str]:
    """Return every reason a child run does not prove the mutant was caught; empty means caught.

    The child must exit 1, and each target must hold a `<failure>` whose message or text carries
    `why`. Exit 0 means the mutant survived, 97 that the child imported something other than the
    mutated copy, anything else a harness failure.
    """
    violations: list[str] = []
    if returncode == 0:
        violations.append('the mutant survived: every target passed')
    elif returncode == WRONG_ORIGIN:
        violations.append('wrong import origin: the child did not import the mutated copy')
    elif returncode != 1:
        violations.append(f'harness failure: the child exited {returncode}')
    if junit_xml is None:
        violations.append('no JUnit report')
        return violations
    try:
        root = ET.fromstring(junit_xml)
    except ET.ParseError as exc:
        violations.append(f'unreadable JUnit report: {exc}')
        return violations
    children: dict[str, list[ET.Element]] = {}
    for testcase in root.iter('testcase'):
        children.setdefault(_node_id(testcase), []).extend(testcase)
    for target in targets:
        if target not in children:
            violations.append(f'{target}: missing from the report')
            continue
        failures = [c for c in children[target] if c.tag == 'failure']
        if not failures:
            others = sorted({c.tag for c in children[target] if c.tag in ('error', 'skipped')})
            if others:
                violations.append(f'{target}: {" and ".join(others)}, not a failure')
            else:
                violations.append(f'{target}: passed, so the mutant survived it')
        elif not any(why in (f.get('message', '') + (f.text or '')) for f in failures):
            violations.append(f'{target}: killed for the wrong reason (no {why!r} in the failure)')
    return violations


def run_case(case: Case, src_override: Path | None = None) -> list[str]:
    """Plant `case` in a scratch copy, run its targets, and return `mutation_violations`."""
    with tempfile.TemporaryDirectory(prefix=f'mutant-{case.id}-') as scratch:
        dest = Path(scratch)
        src_root = apply(case, dest)
        junit = dest / 'junit.xml'
        child = run_child(src_override or src_root, case.targets, junit)
        report = junit.read_text() if junit.exists() else None
        violations = mutation_violations(child.returncode, report, case.targets, case.why)
        if violations and child.returncode not in (0, 1):
            violations.append(f'child stderr: {child.stderr.strip()[-400:]!r}')
        return violations


def self_check() -> list[str]:
    """Run the runner's controls; return a report line per control, failures prefixed `FAILED`."""
    lines = []
    survived = run_case(SURVIVOR_CONTROL)
    ok = any('survived' in v for v in survived)
    lines.append(f'{"control" if ok else "FAILED"}  surviving mutant reported: {survived}')
    with tempfile.TemporaryDirectory(prefix='mutant-origin-') as elsewhere:
        origin = run_case(SURVIVOR_CONTROL, src_override=Path(elsewhere))
    ok = any('wrong import origin' in v for v in origin)
    lines.append(f'{"control" if ok else "FAILED"}  wrong origin reported: {origin[:1]}')
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run planted violations against a copy of the package.')
    parser.add_argument('--self-check', action='store_true', help="also run the runner's own controls")
    parser.add_argument('cases', nargs='*', metavar='CASE_ID')
    args = parser.parse_args(argv)
    known = {case.id: case for case in CASES}
    failed = False
    for case_id in args.cases:
        case = known.get(case_id)
        if case is None:
            print(f'FAILED  {case_id}: no such case')
            failed = True
            continue
        violations = run_case(case)
        if violations:
            failed = True
            print(f'FAILED  {case.id}: {"; ".join(violations)}')
        else:
            print(f'killed  {case.id}: {", ".join(case.targets)} failed with {case.why!r}')
    if args.self_check:
        for line in self_check():
            failed = failed or line.startswith('FAILED')
            print(line)
    if not args.cases and not args.self_check:
        parser.error('name at least one case, or pass --self-check')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
