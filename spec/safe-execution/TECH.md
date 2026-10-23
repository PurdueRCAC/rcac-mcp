---
slug: safe-execution
title: Every tool passes exactly what it was given, under one interpreter
kind: fix
appetite: big
status: blocked
branch: fix/safe-execution
base: develop
current_phase: done
last_updated: '2026-10-22'
phases:
- id: P1
  name: 'Launcher: one bash program per command, one wire per backend'
  status: done
  satisfies:
  - R2
  - R7
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/safe-execution/verify/sweep.sh && bin/sandbox.sh uv run rcac-mcp --help
- id: P2
  name: 'Protocol: argv, stdin and one timeout contract in all three backends'
  status: done
  satisfies:
  - R3
  - R7
  - R8
  - R10
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/safe-execution/verify/sweep.sh && bin/sandbox.sh uv run rcac-mcp --help
- id: P3
  name: 'Startup: a failed SSH connect is answered, in both eras'
  status: done
  satisfies:
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/safe-execution/verify/sweep.sh && bin/sandbox.sh uv run rcac-mcp --help
- id: P4
  name: Filesystem, transfer and resource callers on argv; byte-exact writes
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/safe-execution/verify/sweep.sh && bin/sandbox.sh uv run rcac-mcp --help
- id: P5
  name: Slurm and site tools on argv; sbatch args and list extra_args
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R6
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/safe-execution/verify/sweep.sh && bin/sandbox.sh uv run rcac-mcp --help
- id: P6
  name: 'Literal-argument matrix: every string parameter, all three modes'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 1
  verify: uv lock --check && uv run pytest -q tests/test_literal_args.py && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q tests/test_literal_args.py && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && uv run python tests/mutants.py --self-check tool-command-string read-file-no-dashdash option-value-joined scontrol-node-positional launcher-arg-unquoted delegate-tilde-server-home empty-optional-passed-literally empty-positional-passed-literally empty-list-path-passed-literally empty-cwd-passed-literally transfer-empty-path-passed surrogate-unnamed transfer-local-fallback && bin/sandbox.sh uv run rcac-mcp --help
- id: P7
  name: 'Sweep: retired texts, every mutant, deferral ledger'
  status: done
  satisfies:
  - R7
  - R10
  depends_on:
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 1
  verify: uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && sh spec/safe-execution/verify/sweep.sh && bin/sandbox.sh uv run rcac-mcp --help
review:
  last_reviewed_commit: 5465485e3bf79653fe1b854ad4e789baffdc45d1
  verdict: changes-requested
  blocked_reason: 'Scoped review: 4 findings fixed; new LOW (loop message) CONFIRMED, MEDIUM PLAUSIBLE (exec race); for Geoffrey'
  cycle: 5
---

# TECH.md — Every tool passes exactly what it was given, under one interpreter

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground truth (`uv run --with pyyaml python "$KB/bin/factory/next_phase.py"
spec/safe-execution/TECH.md`); the per-phase checklists below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R1–R10 are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§ numbers below refer to it).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–06.

## Conventions (apply to every phase)

- `AGENTS.md` is the constitution. This cycle touches § Invariants §1, §2, §3, §5, §7, §8, §9 and the
  high-blast-radius list; the executor and `__init__.py` are human-gated paths.
- One phase per `code-build`; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build safe-execution P<n>: …`, no trailers.
- **Same-commit rules:** a text describing a contract changes in the commit that changes the
  contract (R10, § Conventions); a tool docstring change moves `tests/fixtures/served_surface.json`
  and the README in the same commit (§9); a red `test_mutation_anchor_applies[<case>]` is re-planted
  in the same commit, with the reason in the commit body.
- **Mutation ids in a `verify:` are a contract.** A phase plants every case its gate names, each
  killed for its own `why`. A case that cannot be killed is a design question to raise, not a case
  to drop.
- **Committed text carries outcomes only.** Commit messages, docstrings and AGENTS.md describe the
  hardened behaviour ("every value reaches the command as one literal argument"; "transfers run as
  the mapped user"), never how the old behaviour could be abused, and never point at the private findings lane.
- No command in any phase reaches a cluster, runs `sudo` or runs `ssh`. Real executions happen only
  under `bin/sandbox.sh` (the `executes` fixture, the loopback SSH server, CLI drives).

---

## Phase P1 — Launcher: one bash program per command, one wire per backend
**Satisfies:** R2, R7, R8 (the mechanism; the backends adopt it in P2) · **Depends on:** —
**Goal:** `src/rcac_mcp/executor/launch.py` exists, is pure apart from `run_process`, and is proved
by execution under every local login shell; nothing calls it yet.

- [x] `executor/base.py`: add `UserPath(str)`, `TIMEOUT_EXIT_CODE: Final = -1001` and
      `NOT_STARTED_EXIT_CODE: Final = -1002` (exported from `rcac_mcp.executor`). Protocol signatures
      unchanged in this phase.
- [x] `AGENTS.md` §3: document `-1001` (timeout) and `-1002` (startup ended before the command
      began) before any backend returns them (the §3 below-−1000 rule). The `-1`/asymmetry bullets
      stay until P2 changes the code.
- [x] `executor/launch.py` per PLAN §2.2 and §2.2a: `BASH = '/bin/bash'`, `BASH_ARGS = ('-l',)`,
      `program() -> (program, sentinel)` (the prologue exactly as §2.2a; the two-half sentinel on
      both streams; `start_home`; `command cd -- … || exec /usr/bin/false`; scripts in the fresh
      non-login bash with functions exported; plain `exec`, never a redirection-only
      `command exec`, which stalls bash 3.2), `separate()` (first-occurrence cut per stream;
      not-started → `-1002` + fixed note + WARNING log; one-stream anomaly → note + WARNING; a
      timeout with no sentinel → empty output, `-1001`), `bash_argv()` (the `bash --posix` stage
      parking fds 57–59, `-c --`), `ssh_command()` (the bootstrap text exactly as PLAN §2.2, with
      `&& printf .` and the same parking), `check_timeout()`, `run_process()` (poll at the deadline; TERM → drain → KILL →
      drain → close; `stdin=PIPE` always), `decode()`, `expand_local()`. Words: `${HOME:?…}`,
      `~//` collapsed, relative `UserPath` gains `./`. Refuse NUL, non-strict-UTF-8 text and a
      program over 96 KiB (`ValueError` naming the argument), a non-`str` element (`TypeError`), an
      empty argv, a bare `UserPath` as the command.
- [x] `tests/test_launch.py` (`unit`): `launch_violations` over rendered programs: each element one
      word; `UserPath('~')`, `'~/'`, `'~/a b'` via `"$HOME"`; `'~name/x'`, `'~'` as a plain `str`,
      `'$HOME'`, `'*'` literal; relative cwd gains `./`; script with no cwd is verbatim; bootstrap
      contains no `!`, backslash or newline and only the base64 alphabet after its fixed prefix.
      Planted negatives for each.
- [x] `tests/test_launch.py` (`integration`, launches via `[SANDBOX, …]`): render a program over a
      hostile value set (PLAN §2.6) and execute it as `bash_argv` and as `ssh_command` under `/bin/sh`,
      `/bin/bash`, `/bin/zsh`, `/bin/ksh`, `/bin/csh`, `/bin/tcsh` (`env -i HOME=<fixture>`); output
      must equal the values byte for byte, including a value ending in newlines and a script ending in
      a here-document, a script starting with `-`, and `cat` under a `~/.bash_profile` that runs
      `read` and defines a `cd()` that prints (stdin and stdout must be untouched). With `PATH`
      lacking `base64`, the bootstrap exits nonzero and runs nothing. With `HOME=''`, a `~/x` path
      fails instead of reading `/x`. `run_process`: `echo part; sleep 30` with `timeout=0.5` keeps
      `part`, returns `-1001` within ~3 s; a backgrounded `sleep` in the group is gone afterwards; a
      `setsid`-escaped child cannot hold the call past the bounded drain; `sleep 4 & echo done; exit
      0` with `timeout=1` returns `0`, not the marker; `1e12`, `nan`, `0` and `-1` are refused before
      anything starts. Unit (stubbed `Popen`, `os.killpg` patched): a SIGHUP death returns `-1`.
- [x] `tests/test_launch.py` (`integration`), **startup separation** (PLAN §2.2a): a planted chatty
      startup set in the fixture home — `.bash_profile`, `.bash_login`, `.profile`, `.bashrc`,
      `.bash_logout`, `.zshenv`, `.zprofile`, `.cshrc`, `.tcshrc`, `.login` and a `BASH_ENV` file —
      each writing to stdout and stderr (partial lines, binary, 1 MiB from `.zshenv`), plus `set -x`,
      an EXIT and a DEBUG trap, functions named `printf` and `cd`, a `read` from stdin and a
      background writer. For an argv, a script with `exit 4`, a `kill $$` script, `cat` with stdin
      bytes, and an empty script, each result (stdout, stderr, exit code) is **byte-identical** to
      the quiet home's, through `bash_argv` and through `ssh_command` under `/bin/sh`, `/bin/bash`,
      `/bin/zsh`, `/bin/tcsh`. Plus: a profile `exit 0` → `-1002` and the note; a profile `sleep 30`
      with `timeout=1` → `('', '', -1001)`; a profile `exec 58>file` → the note, never silent; a
      command printing its own nonce keeps it. Unit: `separate` over planted byte strings
      (`separation_violations`, with planted negatives).
- [x] `tests/conftest.py`: the PATH shim also exports `BASH_ENV` → a file that re-prepends the shim
      directory (a login bash on macOS reorders `PATH` via `path_helper`). `tests/test_hygiene.py`:
      a self-test that `command -v sudo` and `command -v ssh` inside `bash_argv('…')` launched under
      the sandbox resolve to the shim.
- [x] `tests/mutants.py`: plant `launcher-arg-unquoted`, `launcher-tilde-user-expanded` (a
      `UserPath` left as a bare `~` for bash), `launcher-tilde-name-expanded` (`~name` resolved),
      `launcher-cwd-cdpath` (no `./` prefix on a cwd), `launcher-relative-dash` (no `./` on a
      relative `UserPath`), `bootstrap-raw-program` (program sent unencoded),
      `bootstrap-newlines-stripped` (no `printf .`), `bootstrap-decode-unchecked` (`; printf .`),
      `stdin-not-parked` (stdin not parked for the login initialisation), `timeout-group-not-killed` (kill the leader only),
      `timeout-partial-output-dropped`, `timeout-completed-misreported` (no `poll()` at the
      deadline), `startup-output-kept` (`separate` returns the raw streams), `startup-not-parked`
      (the stage without the fd parking), `startup-traps-kept` (no trap reset in the prologue),
      `stage-reads-bash-env` (stage without `--posix`), `not-started-exit-zero` (the startup's own
      status returned), `script-in-login-shell` (the script run in the login shell, so `exit` runs
      `~/.bash_logout`).
- **Build amendment (2026-10-17).** Under macOS bash 3.2 a profile's `exec 58>file` does not take
  the parked descriptor: the command's output is still captured, so there is no loss to note. The
  `exec 58>file` row therefore asserts that nothing is lost *silently* (each stream arrives or is
  noted), and the note path is pinned where a stream really is redirected: the remote login shell's
  own `.zshenv` (`exec >/dev/null`, `exec 2>/dev/null`). The hygiene self-test launches
  `bash_argv(program([...probe]))`. `AGENTS.md`'s repo map also gains `launch.py`, which exists now.
- **Build amendment (2026-10-17, foreman's ruling).** Bash reads a whole line before running any
  of it, so a profile that enabled `expand_aliases` and aliased a word on the prologue's shared
  first line (`exec`, say) had that alias expanded before `unalias -a` ran: the command ran with
  parked streams and was reported as −1002. The prologue now opens with `\unalias -a` on a line of
  its own (PLAN §2.2a corrected to match). Sibling check: the only word the program uses after the
  prologue that `unset -f` did not cover was `[` (the script line's `[ -z "$fns" ]`), so `[` joins
  the `unset -f` list. Pinned by `test_profile_cannot_hijack_the_prologue[<wire>-alias|
  <wire>-bracket-function]` (byte-identical to a quiet home) and the mutants
  `prologue-unalias-shared-line` and `prologue-bracket-function-kept`, added to the gate. Known
  limit, not designed around: a profile that defines functions named after the words the prologue
  itself relies on before its reset (`unset`, `unalias`, `builtin`, `set`) is a pathological
  configuration, not a supported one.
- **Remediation amendment (2026-10-18, review cycle 1).** Reopened with P2 and landed in one commit
  with it; the classes, the choices and the pins are in P2's remediation amendment.
- **Remediation amendment 2 (2026-10-18, foreman's rulings).** Reopened again with P2, one commit;
  see P2's second note.
- **Remediation amendment 3 (2026-10-18, review cycle 2).** Reopened with P2, P3 and P4 and landed
  in one commit with them; the classes, the choices, the pins and the gate retune are in P2's third
  note.
- **Linux CI amendment (2026-10-19).** The first build amendment's reading of `exec 58>file` held
  only for bash 3.2: bash 5 lets a profile take the parked descriptor, and the program now refuses
  to run on a broken restore. The decision and its pins are in P7's Linux CI note.
- **Remediation amendment 4 (2026-10-22, Geoffrey's ruling after review cycle 3).** Reopened with
  P2, P4, P5, P6 and P7 and landed in one commit with them; the end record, the SFTP route, the pins, the
  measurements and the mutants are in P2's fourth note.
- **Remediation amendment 5 (2026-10-22, Geoffrey's ruling after review cycle 4).** Reopened with
  P2, P4, P5 and P7 and landed in one commit with them; the classes, the pins, the decisions and the
  mutants are in P2's fifth note.
- **Verify:** frontmatter `P1.verify`.
- **Touches:** `src/rcac_mcp/executor/{base,launch,__init__}.py`, `tests/{test_launch,conftest,test_hygiene,mutants,test_mutations}.py`, `AGENTS.md` (§3, § Testing, high-blast-radius list: add `src/rcac_mcp/executor/launch.py`).

## Phase P2 — Protocol: argv, stdin and one timeout contract in all three backends
**Satisfies:** R3 (cwd and transfer paths), R7, R8, R10 (executor texts) · **Depends on:** P1
**Goal:** `Executor.run(command: Sequence[str] | str, cwd, timeout, stdin)` and `put`/`get -> str`
land in `base.py` and in **all three** backends in one commit (§2); every command runs under
`/bin/bash -l`; a timeout returns `-1001` with captured output everywhere. Tools still pass strings
(as scripts) until P4/P5.

- [x] `executor/base.py`: the new `run` signature and docstring (argv vs script, `UserPath`,
      `stdin`, the pinned interpreter, the timeout contract), `put`/`get` return the absolute
      destination.
- [x] `executor/shell.py`, `executor/delegate.py`, `executor/ssh.py` per PLAN §2.3. Delegate argv
      exactly `['sudo', '-n', '-H', '-u', user, '/bin/bash', '-l', '-c', program]`; `_shell` and
      every `os.path.expanduser` removed; delegate programs start in `$HOME`; delegate `put`/`get`
      copy as the mapped user through `run` and return the file written (unresolved, read exactly).
      SSH `run` on the raw channel (stdin fed then EOF, both streams drained with a `select`/sleep
      wait, at the deadline the exit status if it has arrived, else `signal` TERM request, close,
      `-1001`; `-1` kept for signal death / no status); `put`/`get` on Paramiko's `SFTPClient`
      directly with the `~/` mapping (leading slashes collapsed).
- [x] `tests/harness.py`: `RecordingExecutor.run(command, cwd, timeout, stdin)` records the command
      as given and `stdin`; `put`/`get` return a path. `launched_script(argv)` returns the script and
      sentinel from a recorded launch, and `RecordedPopen` answers with the sentinel first on both
      streams so `separate` keeps its reply. Its docstring keeps the rewrite-point note.
- [x] Every backend's `run` passes its streams through `launch.separate` (PLAN §2.2a), so the
      startup rows hold in all three modes: the chatty set in the `executes` mapped home (delegate)
      and in the loopback server's home (ssh, login shells bash, zsh, sh, tcsh, with `.zshenv`,
      `.cshrc` and `.bashrc` chatty) gives results byte-identical to the quiet home's.
- [x] `tests/conftest.py`: the `executes` fixture (PLAN §2.6): patches `Popen` in
      `rcac_mcp.executor.launch` only; bash-only; `[SANDBOX, 'env', '-i', …]` with a sudo-like
      environment; the exact `sudo` prefix asserted and replaced by the relaying stand-in with a
      separate mapped `HOME`, from a foreign cwd; self-tests in `tests/test_hygiene.py` (non-bash argv
      refused; a wrong `sudo` prefix refused and never launched; every launch starts with the
      sandbox).
- [x] `tests/ssh_server.py` (helper, not collected) + `tests/test_ssh_exec.py` (`integration`): the
      executing loopback server (sshd-like `env -i`, starts in `$HOME`, SFTP subsystem, no kill on
      channel close) over login shells `/bin/bash`, `/bin/zsh`, `/bin/sh`, `/bin/tcsh`. Rows: R7's
      probe (`[ -n "$BASH_VERSION" ] && ! shopt -qo posix && [ "$CLUSTER_MCP_TEST_LOGIN" = 1 ] &&
      [ a == a ]`, the marker exported by a quiet `.bash_profile`) succeeds for a bash and a zsh
      login shell; stdin bytes arrive exactly and EOF is delivered
      (`cat` returns); a timeout returns partial stdout/stderr and `-1001`, never raises (R8), and
      the test removes what it left running; an argv with hostile values arrives literally;
      `put`/`get` with `~/x`, `~//x` and a `{host}`-shaped local name land where named. Unit (fake
      channel): exit status before EOF does not busy-wait (bounded iterations); a completed command
      at the deadline returns its status.
- [x] `tests/test_identity.py`, `test_isolation.py`, `test_no_executor.py`, `test_protocol.py`: read
      the launched script through one shared decoder (`launched_script(argv)` in the harness), since
      the program now begins with the prologue.
- [x] `tests/test_executors.py`: rewrite the argv rows and drift controls (`wrong-shell` →
      `wrong-interpreter`); retire `test_shell_is_captured_at_construction` for "the argv names
      `/bin/bash -l` whatever `$SHELL` is" (`SHELL=/bin/zsh`); add R8 rows for `local` and `delegate`
      through `executes` (`echo part; sleep 30`, `timeout=0.5`) and a control that a SIGHUP death
      (`kill -HUP $$`) reports `-1`, not the marker; R3 delegate rows: `cwd='~'` and
      `cwd='~/sub dir'` resolve under the mapped home, never the server's `HOME`; `put`/`get` with
      `~/…` on both sides land under the mapped home and return that path; `~name/x` stays literal.
      R7 rows for `local`/`delegate` with `SHELL=/bin/zsh`.
- [x] `tests/test_ssh_smoke.py`: the server decodes the bootstrap and checks the program; it keeps
      "no process is launched".
- [x] Re-plant `sudo-n-dropped`, `sudo-H-dropped`, `command-quoted`, `cwd-unquoted` (now in
      `launch.py`), `ssh-exit-status-dropped`; retire `shell-read-per-call` (reason in the body);
      plant `interpreter-from-shell-env`, `ssh-timeout-raises`, `timeout-sentinel-minus-one`,
      `delegate-tilde-server-home`, `delegate-server-cwd` (no `start_home`), `transfer-as-server`
      (delegate copy outside `run`), `ssh-stdin-not-closed`, `ssh-bootstrap-dropped`,
      `ssh-drain-spins`, `sftp-tilde-root` (`~//x` → `/x`), `sftp-path-formatted` (Fabric's `get`),
      `backend-separation-skipped` (the SSH `run` returns the streams without `separate`).
- [x] R10, same commit: `AGENTS.md` §2 (signature, `stdin`, returns, `UserPath`), §3 (replace the
      `-1` overload and asymmetry bullets with the one contract), §5 (argv, what is quoted, transfers
      as the mapped user, `_shell` bullet removed), footguns (`delegate.py`, `ssh.py`, `launch.py`),
      repo map, architecture diagram, § Testing (new modules, `executes`, the shim re-pin, the
      internals-bound list); `README.md` (`local` mode line; commands run in a login bash);
      `APP_HELP` in `__init__.py`; `SECURITY.md` diagram; `run_command`'s docstring (runs under
      bash after the login initialisation, whose own output is never returned; on timeout
      `exit_code` is −1001 and the output is what was captured; −1002 means it never started) with the fixture's
      `run_command.description` delta (PLAN risk 2).
- [x] Deferrals (each an `issues/{slug}.md` from the KB `ISSUE.md` template, `status: unshaped`, plus
      a `ROADMAP.md` entry): `startup-background-jobs` (PLAN risk 5's residual limits),
      `ssh-reconnect-loop` (the endless `_ensure_connected`) and
      `timeout-process-cleanup` (whether the `signal` request stops a timed-out remote command, and
      grandchildren under a non-root delegate server; PLAN risk 4). The seed states that measuring
      it needs a long-running process on a login node, outside the dogfood class, so it waits for
      Geoffrey's approval; no phase attempts it.
- **Build amendment (2026-10-17).** The delegate argv is `['sudo', '-n', '-H', '-u', user] +
  launch.bash_argv(program)` — `/bin/bash --posix -c <stage> cluster-mcp <program>`, the stage then
  execs `/bin/bash -l` — as PLAN §2.3 and P1's two-stage launch define it; the
  `/bin/bash -l -c program` written in the checklist above predates the stage. Same contract (§5's
  text names the real argv). The delegate transfer's fixed script runs as `/bin/bash --posix -c
  TRANSFER …`, so the bash that runs it reads no `BASH_ENV` a profile exported (a chatty one would
  print into the returned path); P4's fixed scripts meet the same read, and `--posix` before `-c`
  leaves the element after `-c` the script, so the `FIXED_SCRIPTS` pin is unaffected.
  `interpreter-from-shell-env` is planted in `launch.bash_argv`, the one place both `local` and
  `delegate` take their interpreter from, and targets both backends' argv rows. `put`/`get` deliver
  into a directory destination under the source's name in all three backends (Fabric's `put` did),
  and §2 says so. The loopback SFTP refuses any path outside the test's tree, which is what lets
  `sftp-tilde-root` fail without touching the real filesystem; the loopback server sends no exit
  status for a signalled process (Paramiko's server side has no `exit-signal`). `executes` is not
  yet accepted by the harness fence (no P2 test calls a server in-process through it); P4/P6 extend
  the fence when their matrix needs it. The fixture delta is `run_command.description` only, and
  `tests/test_surface.py`'s docstring names it with § Testing and §9. `SECURITY.md`'s delegate model
  gains that transfers run as the mapped user. The three deferral entries sit in `ROADMAP.md`
  Part III; their order is Geoffrey's call. `local-put-renamed` was re-planted (the `put` signature
  now returns `str`) and `shell-read-per-call` retired: its contract (the server's `$SHELL`,
  captured at construction) is gone, replaced by `interpreter-from-shell-env`.
- **Remediation amendment (2026-10-18, review cycle 1; P1 and P2 bundled).** `REVIEW.md` routed
  five classes into the launcher and the backends; the foreman signed the human gate off for this
  remediation. P1 and P2 are reopened together and land in one commit: `launch.program`'s start
  parameter, the stage, the size check and `run_process` change with the three backends that call
  them, and §2, §3, §5 and the footguns change with the code (R10, § Conventions), so neither phase's
  half stands alone. Their gates are retuned to P7's, the latest done phase's (the skill's rule for
  a reopened earlier phase): it runs the full suite in both eras and the sweep, whose mutation step
  runs every planted case, a superset of the ids each gate named.
  - **Class 1, start directory.** `program(command, cwd, start)` replaces `start_home`: `start='home'`
    renders `command cd -- "${HOME:?…}"`, `start='stage'` renders `command cd -- "${1:?…}"`, both
    after the login initialisation, so no startup file's `cd` moves the start. `ssh` now passes
    `'home'` (the login home), `delegate` `'home'` as before, `local` `'stage'`: the stage passes its
    own `"$PWD"` (the directory the launch started in) as the login bash's `$1`, which bash binds
    after its startup files run (measured on bash 3.2: a profile's `set --` leaves it alone). The
    `ssh.py` docstring says so. Pins: `test_a_profile_cd_does_not_move_the_start[local|delegate]`,
    `test_a_startup_cd_does_not_move_the_start[bash-profile|zshenv]` (red first in `local` and both
    `ssh` rows; `delegate` already held).
  - **Class 2, stdin.** Over `ssh`, `_exec` sends the caller's bytes from a thread only once
    `_collect` has seen the program's sentinel on either stream; a program that never starts gets
    none. **Choice:** an empty stdin's EOF goes at once. It carries no byte of the caller's, and
    withholding it too would make every call of a user whose rc file reads stdin hang to its
    timeout. Consequence, recorded: with stdin to send, an rc file that reads stdin waits for bytes
    that never come before the program starts, so that call ends at its timeout (`-1001`, empty
    output) rather than run with its stdin shortened; the rows with no stdin are byte-identical to a
    quiet home's. `local` and `delegate` keep stdin parked. Pins:
    `test_a_remote_rc_never_reads_the_callers_stdin` (zsh `.zshenv` `read -r line`; red first: the rc
    took `line one`, cat returned the rest), `test_a_profile_cannot_read_stdin[local|delegate]`.
  - **Class 3, call duration.** **Grace:** `launch.GRACE`, 1 s, the bound already used after each
    signal at a timeout. **Mechanism:** `run_process` calls `communicate` in `POLL` (0.05 s) steps
    and checks `poll()` between them; once the process (the exec chain ends in the command; `sudo`
    exits with its child) has exited, `_settled` reads for `GRACE` more, then closes the pipes and
    returns the command's status, signalling nothing (a background job the command meant to leave
    keeps running). `_collect` starts the same grace when the exit status arrives and returns that
    status when it ends, whether or not the output has ended. **With R8:** a command whose exit
    status is known is never reported as a timeout, even if the deadline passes during its grace
    (a call can last its timeout plus `GRACE`); a command still running at the deadline is
    `-1001` as before, and after the TERM each backend reads for `GRACE` what it prints as it stops
    (`run_process` already did; `_collect` now does too, `_stopped`), keeping all partial output.
    The `timeout-completed-misreported` row's `sleep 4 &` case is now this path. The loopback SSH
    server sends the exit status when its process exits, as sshd does, not after the output ends.
    Pins: `test_a_profile_background_job_does_not_hold_the_call[local|delegate]`,
    `test_a_startup_background_job_does_not_hold_the_call[bash-profile|zshenv]` (each against the
    quiet home's elapsed time; red first at 6.0–6.1 s for a `sleep 6` holder),
    `test_a_held_output_does_not_hold_a_completed_command`, and
    `test_a_timeout_drains_what_follows_the_term` (the block reviewer's surviving
    `ssh-timeout-no-drain`, now planted and killed).
  - **Class 4, the separation texts.** `AGENTS.md` §3, the `run_command` description (fixture delta
    `run_command.description`, scripted), the `Executor` docstring, the README and the launcher's
    module docstring state the exception: over `ssh`, a background job started by the remote login
    shell's own rc file can write into a result while the command runs. §2 and §3 also state the
    start directories, the stdin rule and the grace. `test_a_remote_rc_background_job_can_write_into_a_result`
    pins that it still can, `test_separation_texts_state_the_remote_rc_exception` that the four
    texts say so (red first: all four). `issues/startup-background-jobs.md` and its `ROADMAP.md`
    entry gain dated lines: the held call is fixed, the rc-file case is the documented exception.
  - **Class 5, size limits.** `MAX_PROGRAM` (96 KiB of program) is replaced by `MAX_ARGUMENT`
    (131,072 bytes, Linux's per-argument limit with the NUL), checked against the bytes that cross
    it: `bash_argv` checks the program (one argv element), `ssh_command` the exec string (bootstrap,
    base64 word), each a `ValueError` naming the limit; `SSHExecutor.run` builds the exec string
    before connecting. The largest `ssh` program is 98,172 bytes; a local or delegated one 131,071.
    `program()` no longer checks size. Pins: `test_local_launch_limit_is_the_per_argument_limit`,
    `test_ssh_wire_limit_counts_the_bootstrap` (boundary computed from `len(BOOTSTRAP)`; red first:
    a 98,173-byte program passed with a 131,074-byte exec string),
    `test_an_oversize_program_is_refused_before_connecting`.
  - **Mutants.** Re-planted where the anchor moved: `delegate-tilde-server-home`,
    `delegate-server-cwd`, `ssh-bootstrap-dropped`, `ssh-drain-spins`, `bootstrap-raw-program`,
    `timeout-completed-misreported`; `stdin-not-parked` gains the two executor rows. New, one or more
    per class: `ssh-start-not-home`, `local-start-not-stage`, `stage-start-home` (1);
    `ssh-stdin-before-start` (2); `exit-not-noticed`, `drain-waits-for-holders`,
    `ssh-drain-waits-for-holders`, `ssh-timeout-no-drain` (3); `separation-exception-unstated` (4);
    `local-size-unchecked`, `ssh-size-ignores-bootstrap` (5).
- **Remediation amendment 2 (2026-10-18, foreman's rulings on the first).** Rulings: an empty
  stdin's EOF at once, accepted; a call with stdin to a user whose remote rc reads stdin ending
  `-1001` with nothing written, accepted, to be stated and pinned; the gate retunes, accepted; the
  local start directory, **rejected as built**, which reopened class 1.
  - **The finding.** The foreman probed Gautschi's bash 5.1.8 (the shell local mode runs there): a
    `.bash_profile` of `set -- clobbered-by-profile` and `cd /tmp` made `bash -l -c '…' stage-name
    /original/start` print `1=clobbered-by-profile`. Bash 4 and later bind a login bash's positional
    parameters before its startup files run; macOS's bash 3.2 binds them after, which is why the
    first note's measurement passed. So the stage's `"$PWD"`-as-`$1` route let a startup file move
    a local command's start.
  - **The mechanism now.** `program(…, start)` takes `'home'` (unchanged: `ssh`, `delegate`) or an
    absolute directory, written into the program as one quoted word; `LocalShellExecutor` passes
    `os.getcwd()`, which is the stage's `$PWD`, since the launch inherits it. `STAGE` hands the login
    bash nothing after the program again. The `executes` fixture's `local_cwd` now changes the test
    process's working directory (default: a fresh directory under the test's tree) instead of
    relaying the launch through a `cd`.
  - **The sweep.** Every other use of positional parameters runs where no startup file can reach
    it: the stage (`bash --posix -c`, reads no startup file, not even `BASH_ENV`), the SSH
    bootstrap's stage (the same, its `$1` set by its own argv after the remote rc has run), the
    fixed scripts and the delegate transfer (`bash --posix -c SCRIPT NAME …`), and a `run_command`
    script (a fresh non-login `bash -c` with `BASH_ENV=` and `ENV=` empty). The login bash's own
    program reads none: `test_program_reads_no_positional_parameter` checks rendered programs for
    `$0`–`$9`, `$@`, `$*`, `$#`, `BASH_ARGV`, `BASH_ARGC` and that the stage and bootstrap give the
    login bash none. `$0` (`cluster-mcp`) appears only in bash's own error prefix. Not version-
    dependent and left as found: the prologue does not reset `IFS`, so a profile that sets it
    globally could keep `export -f $fns` from exporting functions to a script.
  - **Bash 5 red/green, by hand** (a Gautschi login node, `GNU bash, version 5.1.8(1)-release`, as
    `<user>`, within the dogfood class). A throwaway tree
    `/scratch/<cluster>/<user>/<dev-dir>/se-start-probe/` with `home/.bash_profile` =
    `set -- clobbered` / `shift` / `cd /tmp` and a `start/` directory; from `start/`, each built
    argv (`bash_argv(program(['pwd'], …))`, NUL-separated, base64) through
    `xargs -0 env -i HOME=…/se-start-probe/home PATH=/usr/bin:/bin`; then
    `rm -rf` of that tree and an `ls` confirming it gone.
    Output (stdout | stderr | xargs exit), sentinels included:
    - old code (d1a568a, `start='stage'`): `24493b5a05846d473fc159259813eec3 |
      24493b5a05846d473fc159259813eec3cluster-mcp: line 4: 1: the start directory is not set | 123`
      (the profile emptied `$1`; xargs reports a child exit of 1 as 123);
    - new code (`start=<that start/ directory>`):
      `4b029526e4c75e9013abb1f54572d8c5/scratch/<cluster>/<user>/<dev-dir>/se-start-probe/start |
      4b029526e4c75e9013abb1f54572d8c5 | 0`.
    Locally (bash 3.2), `test_a_profile_cd_does_not_move_the_start[local]` now carries
    `set -- clobbered; shift` in its profile; it cannot go red here, hence the transcript.
  - **Ruling 2's texts and pins.** AGENTS §2, the README and the `Executor.run` docstring state that
    over `ssh`, if the remote rc file reads stdin, "a call that sends stdin times out and never
    writes partial content"; `test_stdin_texts_state_the_rc_timeout` pins the phrase in all three
    (mutant `stdin-rule-unstated`). `test_a_remote_rc_never_reads_the_callers_stdin` now sends its
    stdin to `sh -c 'cat > "$HOME/written"'` and asserts that after the timeout, once the rc file's
    read has ended, nothing was written. For that row to mean what sshd does, the loopback server
    now closes a process's output pipes before its stdin when the client closes the channel (sshd
    closes the child's descriptors), so a command that starts late dies on its first write (its
    sentinel). Residual, recorded: an rc file that ignores SIGPIPE would let such a late program
    run with an empty stdin.
  - **Mutants.** `stage-start-home` retired with the route it mutated; `local-start-not-stage`
    re-planted as `local-start-dropped`; new `local-start-positional` (the positional route back:
    the stage passes `"$PWD"` and the start line reads `$1`), killed by
    `test_program_reads_no_positional_parameter`; new `stdin-rule-unstated`.
  - **A P6 gate flake, fixed.** P6's re-run went red once: `transfer-empty-path-passed` killed "for
    the wrong reason". Its matrix row runs its calls concurrently, so under that mutant the empty
    path's upload and the `~` one race into the same file in the login home, and about one run in
    twelve fails as Paramiko's `size mismatch in put` instead of "was accepted, not refused" (twelve
    repeated children: eleven and one). Both failures carry "reaches the executor as an empty path",
    the contract the case breaks, so that is now its `why`. Pre-existing (SFTP and the matrix are
    untouched here); recorded as P6 attempt 1.
- **Remediation amendment 3 (2026-10-18, review cycle 2; P1–P4 bundled).** `REVIEW.md` cycle 2
  routed six classes; the foreman signed the human gate off for this remediation, the last cycle
  the bound allows. P1, P2, P3 and P4 reopen and land in one commit: the launcher (P1), the
  backends and the texts that describe them (P2, R10), the fixed scripts' argv (P4's `scripts.py`)
  and the spec-id fix (P3's `tests/test_startup.py`) change together, and `tests/mutants.py` and
  `AGENTS.md` carry rows of every class. P3's and P4's gates are retuned to P7's, the latest done
  phase's (the skill's rule for a reopened earlier phase): the full suite in both eras is a
  superset of their test subsets, and the sweep's mutation step runs every planted case, a
  superset of the ids each named.
  - **Class 1, a result is complete unless only holders remain.** Cycle 1's fixed grace from the
    exit status is gone. `run_process` (local, delegate) and `_collect` (ssh) now read on after the
    exit status while output arrives: they track when a byte last arrived and when the status was
    first seen, and stop early only once nothing has arrived for `GRACE` (1 s) since the later of
    the two. That stop is never silent: the wire returns a fourth element, a note, and
    `separate(…, note)` ends stderr with it whatever else it does (`launch.HELD_NOTE`: "the command
    exited, but something left running held its output open; nothing arrived for 1 s, so reading
    stopped there"). Output that keeps arriving ends at `GRACE` past the deadline as `-1001`, with
    `launch.LATE_NOTE` naming the command's own status; nothing is signalled there, and closing the
    pipes (or the channel) gives the writer a broken pipe. A completed command's status is still
    never turned into a timeout while its output has gone quiet: an idle holder at the deadline
    ends the call by `GRACE` past it with the status and `HELD_NOTE`. Pinned at both sides of the
    trade-off: a background holder adds only the note and at most `GRACE` (the held rows, three
    modes, now expect the note); output arriving in bursts within `GRACE` of each other is read
    whole (`test_output_after_the_exit_is_read_while_it_arrives[local|delegate]`, red first: "now
    late" without "later"; the unit `test_output_after_the_exit_status_is_read_to_the_end`); a
    writer that never stops ends at the timeout with the note (`test_a_holder_that_keeps_writing_
    ends_at_the_timeout`, local, delegate, ssh under bash and zsh; red first: exit 0 after 8 s). A
    link that stalls for over `GRACE` after the status would stop early, noted; no rate that keeps
    arriving is cut. **The slow link** is `tests/ssh_server.py`'s `ThrottledSSH`, the suite's own
    model: both pipes read eagerly into a 2 MiB window (Paramiko's default), one link carrying both
    streams in arrival order at a fixed rate in 8 KiB pieces, the exit status sent as soon as the
    process exits, ahead of the queue. `read_file` through the tool, bytes returned (HEAD b32778d →
    now): 256 KiB/s, 640 KiB file: 229,376 → 655,360; 1 MiB/s, 3 MiB: 1,744,896 → 3,145,728; 4 MiB/s,
    6 MiB: 6,291,456 → 6,291,456 (the window drains within a fixed grace, so HEAD passed this one);
    `run_command('cat big.txt; echo DONE >&2')` at 256 KiB/s: stdout 237,568 → 655,360 bytes, stderr
    `'' → 'DONE\n'`. **A late sentinel still counts as started:** a zsh login shell whose `.zshenv`
    prints 1 MiB, at 512 KiB/s: `touch ~/ran` returned `-1002` with `~/ran` created at HEAD, now
    `('', '', 0)`; `cat` with stdin bytes behind the same rc gets them exactly.
  - **Class 2, every call that holds stdin has a bound.** **Choice: the backend bounds the wait
    for the start, not the tools.** Over `ssh`, while the caller's stdin is held back (`started`
    pending), `_collect` ends the call as a timeout (TERM request, `_stopped`, `-1001`, nothing
    sent) once `ssh.START_TIMEOUT` (30 s) has passed with nothing arriving, or at the call's own
    deadline if sooner. Why the backend: a timeout passed by `write_file` or `sbatch` would bound the
    whole write, so a long legitimate write on a slow link could be cut, leaving a partial file; the
    start bound covers only the wait the hazard creates, and counting it from the last byte keeps a
    chatty rc on a slow link (output still arriving) from tripping it. Only `ssh` needs it: `local`
    and `delegate` park stdin, so no profile can read it. `write_file` and `sbatch` now name the
    exit code in their failure (`Failed to write file (exit -1001): …`), so the documented `-1001`
    reaches the caller. The texts (AGENTS §2, the README, `Executor.run`) state the bound, and
    `test_stdin_texts_state_the_rc_timeout` now also holds each to `f'{START_TIMEOUT:g} s'` and
    "with nothing arriving". Pin: `test_a_call_holding_stdin_is_bounded[run|write_file|sbatch]`
    (`.zshenv` `read -r line`, no timeout, the bound lowered to 1.5 s; red first: none returned in
    11.5 s), nothing written and `sbatch` never called.
  - **Class 3, nothing after the startup files depends on shell state they can change.** The sweep,
    each item measured locally (bash 3.2) and, where it differs, on Gautschi's bash 5.1.8:
    - `IFS` (`:` or empty) split `export -f $fns`: `export: greet\nml: not a function`, functions lost
      to scripts (both bashes). **Fixed twice over:** the prologue runs `unset IFS`, and the export
      reads `compgen -A function` one name per line (`while IFS= read -r …; do export -f -- …; done
      < <(…)`), so neither splitting nor globbing touches it.
    - Glob options (`failglob`, `nullglob`, `extglob`, `dotglob`, `nocaseglob`, `GLOBIGNORE`) with a
      function named like a pattern (`mod?x`): `failglob` aborted the export (`no match: mod?x`).
      Fixed by the same loop; `noglob` and the rest then touch nothing the program expands.
    - `set -k`: an argv element shaped like an assignment (`b=c`, which `shlex.quote` leaves bare)
      became one for the command's environment, so `printf %s\\n b=c x` printed only `x` (an R2
      break). **Fixed:** the prologue resets `k`.
    - `set -P`: the start directory was named by its physical path (a symlinked home printed its
      target), which `list_directory`'s absolute paths read. **Fixed:** the prologue resets `P`.
    - Exported functions over the programs a fixed script runs (`cat`, `mkdir`, `chmod`, …): a
      POSIX-mode bash imports them, so `cat >"$1"` ran the profile's `cat` and wrote its output
      (both bashes). Exported `SHELLOPTS` with `noclobber` (bash 5 only; 3.2 refuses to import it):
      an overwrite failed, "cannot overwrite existing file". **Fixed:** every fixed script runs
      behind `launch.FIXED_RESET` (`unset -f cat cd chmod cp find mkdir printf sort; set +C +o
      pipefail`; `unset` and `set` are POSIX special builtins, which no function can replace there).
      `scripts.script_argv` and the delegate transfer build it through `launch.fixed_argv`;
      `FIXED_SCRIPTS` keeps the composable constants, and `scripts.script_of(argv)` is the
      registry check the tool tests use.
    - `CDPATH`, `POSIXLY_CORRECT`: already harmless (a relative cwd gains `./`, a start is absolute;
      `set +o posix` also unsets `POSIXLY_CORRECT`); now pinned with the rest.
    - Left as found, recorded: a profile that disables builtins (`enable -n`) or exports a function
      named `[` (a POSIX-mode bash prints, on stderr, that it cannot import it) joins the recorded
      limit of functions named after `unset`, `unalias`, `builtin` or `set`. A profile that
      reassigns `HOME` moves the start and `~` with it, as it does for the user's own shell; raised
      for the foreman's ruling, not changed. The prologue's `unset IFS` is defence in depth once the
      export no longer splits: a mutant that drops it alone is equivalent, so none is planted.
    - Pins: `test_profile_state_does_not_reach_the_launcher[<local|ssh-bash|ssh-zsh>-<state>]` (the
      home reached through a symlink; red first: every `ifs-*`, `glob-options`, `keyword` and
      `physical` row), `test_a_profile_cannot_change_a_fixed_script[<mode>]` (overwrite,
      `create_dirs`, a job script; red first in all three modes), and the hijack row `read-function`
      (the export now uses `read`, so `read` joins the prologue's `unset -f` list and `[`, no longer
      used, leaves it).
  - **Class 4, no POSIX stage reads a startup file.** Bash built with `SSH_SOURCE_BASHRC` (RHEL's
    5.1.8 and macOS's 3.2 both are) sources `~/.bashrc` for a non-login, non-interactive `-c` shell
    at shell level 1 when `SSH_CLIENT` or `SSH2_CLIENT` is set or stdin is a socket, POSIX mode or
    not; `exec` keeps the level, so every stage the chain reaches qualifies. `--norc` is the one
    switch that path honours (measured: clearing `SSH_CLIENT` leaves `SSH2_CLIENT` and the socket
    test). **Fixed:** `--norc` on every bash the launcher starts but the login one: the local and
    delegated stage and the SSH bootstrap's stage (`launch.POSIX_STAGE`), a `run_command` script's
    bash (`SCRIPT`), and every fixed script (`fixed_argv`). The size limit moves with the bootstrap:
    the largest `ssh` program is now 98,166 bytes (AGENTS §2). Pin:
    `test_no_stage_reads_a_startup_file_under_sshd[<local|ssh-sh|ssh-bash|ssh-zsh>-<argv|script|
    fixed-script>]`, `SSH_CLIENT`/`SSH_CONNECTION` set, each stage reached by `exec` (a bash login
    shell reads it once itself, as under sshd); red first locally in all twelve rows (1–3 reads).
    The loopback server's environment now carries both variables, as sshd's does. **Bash 5
    red/green, by hand** (a Gautschi login node, `GNU bash, version 5.1.8(1)-release`, as `<user>`,
    within the dogfood class): each built argv from HEAD b32778d and from this change, NUL-separated
    and base64-encoded, run from a throwaway tree under the literal path
    `/scratch/<cluster>/<user>/<dev-dir>/se-c4-probe/` with `env -i HOME=<fresh home there>
    PATH=/usr/bin:/bin SSH_CLIENT=… SSH_CONNECTION=…`, the home's `.bashrc` appending a line to a
    marker; then `rm -rf` of that tree and an `ls`
    confirming it gone. `.bashrc` reads, HEAD → now: local stage, argv 1 → 0; script 2 → 0; fixed
    script 2 → 0; `bash -c <exec string>` (bash as login shell) 2 → 1 (its own); `zsh -c <exec
    string>` 1 → 0; every row printed its sentinel and `ran`, exit 0. The class-3 rows, same run:
    a `.bash_profile` exporting a `cat` function and `noclobber` in `SHELLOPTS`, a fixed-script
    overwrite of `old` with `new`: HEAD exit 1, "cannot overwrite existing file", file `old` → now
    exit 0, file `new`; `set -k` with `printf %s\\n b=c x`: HEAD `x` → now `b=c\nx`; `IFS=:` and
    `failglob` with a function `mod?x`, script `greet`: HEAD exit 1, the function list as an
    `export` error → now `greeted`, exit 0.
  - **Class 5, spec ids stay out of source and tests.** `tests/test_startup.py`'s two docstrings
    now say "the reported startup failure". `test_no_spec_ids_in_the_package_or_the_suite`
    (`tests/test_hygiene.py`) scans the imported package and `tests/*.py` for
    `\b[RP][0-9]{1,2}\b` (red first on HEAD's two lines), with planted controls; AGENTS §
    Conventions points at it.
  - **Class 6, every timeout escalation is pinned.** `test_a_timeout_escalates_to_sigkill` (a
    command that ignores SIGTERM ends within `0.5 + 2·GRACE + 2` s and its job is gone) and
    `test_a_timeout_sends_term_first_and_keeps_what_follows` (a command that traps SIGTERM keeps
    `stopping`).
  - **Mutants.** Re-planted where the anchor moved: `interpreter-from-shell-env`,
    `stage-reads-bash-env`, `startup-traps-kept`, `not-started-exit-zero`,
    `timeout-partial-output-dropped`, `timeout-completed-misreported` (now the held return),
    `exit-not-noticed`, `drain-waits-for-holders`, `ssh-timeout-raises`, `ssh-timeout-no-drain`,
    `ssh-exit-status-dropped`, `backend-separation-skipped`, `stdin-rule-unstated`;
    `prologue-bracket-function-kept` retired with the `[` it protected, `prologue-read-function-kept`
    in its place. New, by class: (1) `ssh-returns-at-exit-status`, `ssh-idle-grace-zero`,
    `ssh-fixed-grace-from-exit` (the cycle-2 regression itself), `ssh-held-stop-silent`,
    `ssh-holder-past-the-timeout`, `ssh-late-stop-silent`, `local-returns-at-exit`,
    `local-idle-grace-zero`, `local-fixed-grace-from-exit`, `held-stop-silent`,
    `separate-note-dropped`, `holder-past-the-timeout`, `late-stop-silent`; (2)
    `ssh-stdin-wait-unbounded`; (3) `function-export-split`, `prologue-keyword-kept`,
    `prologue-physical-kept`, `fixed-reset-dropped`; (4) `stage-reads-bashrc`,
    `bootstrap-reads-bashrc`, `script-reads-bashrc`, `fixed-script-reads-bashrc`; (5)
    `spec-id-in-source`; (6) `timeout-sigkill-dropped`, `timeout-sigterm-dropped`.
  - **Unchanged on purpose.** `run_command`'s description still says what a timeout returns; a held
    or late stop states itself in the result, so the served surface has no delta this round.
- **Remediation amendment 4 (2026-10-22, Geoffrey's ruling after review cycle 3).** `REVIEW.md`
  cycle 3 reached the review bound; Geoffrey ruled (`GOAL.md`, newest Clarification): an
  end-of-output marker instead of any timing rule, SFTP for content over ssh, the racy holder test
  fixed, every class closed whole. P1, P2, P4, P5, P6 and P7 reopen and land in one commit: the
  launcher's program and wire (P1), the protocol, the three backends and every text that describes
  them (P2, R10), the two tools that write content (P4, P5), the matrix's recording executor (P6)
  and the sweep's mutants and texts (P7) change together, and AGENTS §2, §3, §5 and the footguns
  move with the code. P3 is untouched. No gate is retuned: P1–P5 and P7 run the stored P7 gate
  (the full suite in both eras, the sweep with every planted case, the sandboxed `--help`), P6 its
  own; the review fields are left as cycle 3 wrote them.
  - **The end record (ruling 1).** `launch.program` now runs the command in a subshell that changes
    to its start and `exec`s it (`( command cd -- … || exit` / `exec 'a' 'b'` / `) 2>&59 59>&-`),
    so `exec` semantics hold (a program from `PATH`, never a profile function or a builtin) while
    the program outlives the command. After it the program prints, on stdout and on stderr, an end
    record: a second per-call nonce (`secrets.token_hex(16)`, printed in two halves like the start
    sentinel, so it is never whole in the program text), then `x<N>` for an exit status or `s<N>` for
    a signal death, then a full stop (`launch.record`, `RECORD_TAIL`). The program's own stderr is
    `/dev/null` from the start sentinel on (`QUIET`; the caller's stays on 59 for the subshell and
    the record), because bash reports a child killed by a signal on its own stderr ("line 4: 123
    Hangup …", measured on 3.2), which is not the command's output. **Signal death:** bash reports a
    signal death and an exit of 128+N alike, so the program reads a status of 128+N whose
    `kill -l` name is a terminating signal (`TERMINATING`: stops, continues and ignored signals
    excluded, and the platform-specific IO, POLL, PWR, EMT, LOST and STKFLT left out) as that
    signal: it records `s<N>`, then re-raises it on itself (`ulimit -c 0`, the trap reset, `kill -s`),
    and keeps the status with `(exit "$cluster_mcp_s")` if it lives on (a signal ignored on entry);
    `exit` in a subshell never runs `~/.bash_logout` (measured on 3.2, and bash resets `login_shell`
    in a subshell). **Decision, in the ruling's spirit:** the record, not the wire, is the result's
    status in every mode. Paramiko has no `exit-signal` (a signal death reads as a lost status, and
    only once the channel closes), and the exit-status message races the records on the link, so
    over ssh the record is the only status that arrives with the output; local and delegate see the
    same through Popen, which the re-raise makes match. Consequence, stated in §3, base.py and the
    `run_command` description: a signal death is `-N` in every mode (over ssh it was `-1`), and so
    is an exit of 128+N for a terminating N (`exit 130` reads `-2`; `exit 200` and `exit 255` stay).
    **Completion:** `separate(out, err, code, marks, note)` cuts each stream after the start
    sentinel and before the first record after it, and takes the code from the record; a timeout
    stays a timeout whatever arrived; a started stream without a record (a launch killed before its
    end) keeps the wire's code and says so (`UNFINISHED_NOTE`). `run_process` is now a selector loop
    over non-blocking pipes (stdin fed in pieces, then closed): it is complete once each stream has
    carried its record (or ended), or at the launch's exit, after reading everything the pipes hold
    (an exited process has nothing more to write, so this is not a timing rule); a stream closed
    early waits for the exit. Over ssh `_collect` is complete at both records, or at the channel's
    end, and never at the exit status; at the deadline a command whose exit status arrived ends as a
    timeout with `LATE_NOTE` naming it (kept, reworded: "the command exited N, but not all of its
    output had arrived at the timeout"), otherwise TERM, the `GRACE` drain and a timeout. `GRACE`
    now bounds only the TERM and KILL drains and the reap after the records. Removed in every mode:
    the idle cutoff, `HELD_NOTE`, the late-output rule past the deadline, `START_TIMEOUT` and the
    stdin gating behind the sentinel. **Recorded limits** (AGENTS §3): over ssh, a launch killed
    before its records while something it started still holds the channel, or a program that never
    starts behind an rc file that leaves such a job, is held until the timeout (as on `develop`,
    which waited for EOF); a profile that makes `cluster_mcp_s`, `cluster_mcp_k` or `cluster_mcp_r`
    read-only joins the pathological configurations of P1's first amendment.
  - **SFTP content (ruling 2). Decision, in the ruling's spirit: one route for content.** The
    protocol loses `run`'s `stdin` (no caller but the two writes used it, and over ssh it may carry
    nothing) and gains a ninth member, `write(path, data, append=False, parent=None,
    executable=False) -> CommandResult`, in all three backends (§2). `write_file` and `sbatch`
    `script_content` call it; `sbatch` passes `parent='~/jobs'` and `executable=True`. In `local` and
    `delegate` it runs a fixed script (`launch.write_argv`: `cat >"$1"`, `cat >>"$1"`, `mkdir -p --
    "$2" &&` first, `&& chmod -- +x "$1"` after) with the bytes on the stdin the launch parks: no
    startup file reaches them, which `test_a_profile_cannot_read_a_writes_content[local|delegate]`
    pins with a profile that reads stdin in the foreground and from a background `head -c 1 &`
    (160,000 bytes, exact; both readers saw nothing). Over ssh: the channel's stdin carries no byte
    and its end goes at once (the loopback server now counts the bytes each exec received). `write`
    stats over SFTP (a parent that is already a directory needs no `mkdir -p` run); follows a
    symbolic link to the file it names (`normalize`, which OpenSSH resolves); refuses a directory
    and an empty path (`OSError`, which the tools report as a failed write); makes a fresh directory
    `.cluster-mcp-<16 hex>` of mode 0700 beside the target (so on its filesystem); creates `content`
    in it exclusively, makes it 0600 before any byte, writes the bytes; then runs one fixed program
    (`ssh.PLACE`: `chmod -- "$3" "$1" && mv -f -- "$1" "$2"`, `$3` the target's own permission bits
    from its `stat`, or `=rw`, which the umask narrows as `cat >` would): the target is replaced by
    a rename, whole or not at all. An append runs `cat -- "$1" >>"$2"` instead, and
    `executable` adds `chmod -- +x "$2"`. The staging file and directory are removed in a `finally`,
    whatever happened. **Decisions:** a target that is not a regular file (a FIFO, a device) is
    written through (`cat -- "$1" >"$2"`, staged in the login home), since a rename would replace
    the node; a regular file in a directory closed to new entries (EACCES or EPERM making the
    staging directory) is written through the same way, as `develop` could write it. Recorded limits
    of the move (AGENTS §2): hard links keep the old content, and the new file is owned by its
    writer, without the old one's ACL. The loopback SFTP server now honours requested permissions,
    implements `mkdir`, `rmdir`, `remove` and `chattr`, and resolves links in `canonicalize`, as
    OpenSSH's sftp-server does.
  - **Edge cases, each pinned.** A command that exits while a background child keeps writing:
    the call ends at the records, the child's later output is not kept, no note
    (`test_the_call_ends_at_its_records[<5 wires>]`, `test_a_background_writer_does_not_hold_the_call`
    and `test_output_after_the_end_is_not_kept` in local and delegate, the ssh writer row). A link
    that stalls for seconds mid-result or after the exit status, and a 32 KiB-packet link at 6,000
    and 24,000 B/s with a 1.6 s stall after the exit status:
    `test_a_stalling_link_returns_the_whole_file[stall-1.6s-after-exit|stall-1.2s-after-exit|stall-5s-mid-result|packets-6000Bps|packets-24000Bps]`
    (`ThrottledSSH` gained `piece`, `stall_after_exit` and `stall_at`). A late start sentinel:
    `test_a_late_sentinel_still_counts_as_started[zshenv|bashrc]` (now with a write behind the 1 MiB
    rc instead of stdin). The `sbatch` double-submit case:
    `test_a_job_that_ran_is_never_reported_as_not_started` (40 KB `.bashrc`, 6,000 B/s, 32 KiB
    packets, 1.6 s stall: the job id, `sbatch` called once). A signal death:
    `test_a_signal_death_is_reported_and_reraised[<local|ssh-sh|ssh-bash|ssh-zsh|ssh-tcsh>-<hup|term|kill|script-kill|exit-3|exit-130|exit-200|exit-255>]`
    (the result's code and the launch's own status, read by a parent outside the sandbox's shell,
    which reports a signal as 128+N), `test_a_signal_death_is_reported[bash|zsh]` over the loopback.
    The timeout before the records: `test_a_timeout_before_the_records_names_the_status` (64 KiB/s,
    1 MiB, `timeout=2`: `-1001`, a prefix, `LATE_NOTE`), and the fake-channel units
    `test_one_record_is_not_the_end`, `test_a_command_whose_records_have_not_come_by_the_deadline_is_a_timeout`,
    `test_the_read_ends_at_both_records`. Marker-like output: `test_a_command_printing_record_lookalikes_keeps_them`
    (a zero nonce, another call's, `deadbeef`×4, a bare `x0.`) and the start-sentinel row. The zsh
    `.zshenv` `head -c 1 &` case and its bash twin, byte-exact:
    `test_a_remote_rc_never_reads_a_writes_content[zshenv|bashrc]` (160,000 bytes through
    `write_file` as base64 and an 80,000-byte job script through `sbatch`; the rc files read nothing,
    no channel carried stdin). The writes: `test_an_ssh_write_replaces_the_file_whole_and_keeps_its_mode`,
    `test_an_ssh_write_stages_its_content_privately`, `test_an_ssh_write_follows_a_link`,
    `test_an_ssh_write_into_a_closed_directory_writes_through`, `test_a_failed_ssh_write_leaves_nothing_behind`;
    and the texts: `test_content_texts_state_the_sftp_route` (AGENTS §2, the README, `Executor.write`).
  - **The reviewers' cases, bytes returned before (the tree at 3598bfe) → now**, measured with this
    commit's `ThrottledSSH` model through `read_file` and `sbatch` over `in_memory`:
    1.6 s stall after the exit status, 512 KiB/s, 1,048,576 bytes: 8,192 → 1,048,576; 1.2 s stall:
    32,768 → 1,048,576; 5 s stall mid-result: 262,144 → 1,048,576; 32 KiB packets at 6,000 B/s,
    49,152 bytes: 32,768 → 49,152; at 24,000 B/s, 262,144 bytes: 32,768 → 262,144, all
    `is_error=False` before; `sbatch` behind a 40 KB `.bashrc` at 6,000 B/s: "Failed to submit job:
    … did not start (exit 0)" with the stand-in called once → the job id, called once.
    The zsh `.zshenv` `head -c 1 > "$HOME/rc-took" &` case, `write_file` of 160,000 bytes over
    the loopback: 143,616 bytes on disk, a different file, `is_error=False`, the job holding one
    byte → 160,000 bytes, exact, the job holding none; its bash `.bashrc` twin was exact before
    (bash gives a background job `/dev/null`) and is exact now.
  - **The racy holder test (ruling 3).** `test_a_holder_that_keeps_writing_ends_at_the_timeout`
    asserted `done` before the first `tick`, which a background job may print first. Its successor,
    `test_a_background_writer_does_not_hold_the_call` (local, delegate, ssh under bash and zsh),
    asserts what holds in any order: the call returns within 2 s at the command's status, with
    `done` once and nothing but `tick` lines beside it.
  - **Mutants.** New: `ssh-one-record-is-the-end`,
    `end-record-one-stream` (marker on one stream only), `end-record-without-nonce`,
    `command-exec-in-place` (exec instead of a child), `signal-death-not-reraised`,
    `signal-death-unreported`, `record-status-ignored`, `content-on-stdin-over-ssh`,
    `staging-not-cleaned` (temp file not cleaned), `move-not-atomic`, `mode-not-kept`,
    `staging-not-private`, `staged-file-not-private`, `staging-not-beside-target`, `link-replaced`,
    `ssh-append-overwrites`, `ssh-write-renamed`, `content-route-unstated` (18). Re-planted where the
    anchor moved: `ssh-returns-at-exit-status` (return at the exit status, before the records; killed
    by the slow-link, stall, packet, late-sentinel and double-submit rows), `cwd-unquoted`, `delegate-tilde-server-home`, `delegate-server-cwd`,
    `ssh-timeout-raises`, `ssh-stdin-not-closed` (now the EOF sent at once),
    `backend-separation-skipped`, `ssh-start-not-home`, `ssh-timeout-no-drain`,
    `ssh-late-stop-silent`, `separation-exception-unstated`, `launcher-cwd-cdpath`,
    `stdin-not-parked`, `prologue-read-function-kept`, `startup-output-kept`,
    `timeout-partial-output-dropped`, `timeout-completed-misreported` (an exited launch read as a
    timeout), `exit-not-noticed`, `local-start-dropped`, `local-start-positional`,
    `separate-note-dropped`, `write-final-newline`, `content-in-argv`, `append-overwrites`,
    `create-dirs-dirname-subst` (now in `launch.WRITE_MKDIR`), `write-before-validate`,
    `script-content-newline`. Retired with the mechanism they mutated: `ssh-stdin-before-start`,
    `ssh-stdin-wait-unbounded`, `stdin-rule-unstated` (no stdin is held or sent),
    `ssh-drain-waits-for-holders`, `ssh-idle-grace-zero`, `ssh-fixed-grace-from-exit`,
    `ssh-held-stop-silent`, `ssh-holder-past-the-timeout`, `drain-waits-for-holders`,
    `local-returns-at-exit`, `local-idle-grace-zero`, `local-fixed-grace-from-exit`,
    `held-stop-silent`, `holder-past-the-timeout`, `late-stop-silent` (no idle cutoff, no held note,
    no late-output rule). An equivalent left unplanted: dropping the local wire's record check
    changes nothing a test can see, since a launch exits right after its records and the exit path
    reads the same bytes. The sweep's count goes from 165 to 168 cases. **The first P7 gate run went
    red on three survivors (P7 attempt 1)**, each a pin the end record had made hollow, now
    re-targeted: `ssh-exit-status-dropped` (the smoke server's record now carries the status, so the
    channel's status matters only when no record comes: pinned by
    `test_ssh_exec.py::test_a_profile_that_exits_is_not_started`, "did not start (exit 7)");
    `startup-not-parked` (the chatty profile's background writer prints 0.2 s in, after a quick
    command's records, so it was no longer read: a separation case `slow`, `sleep 1; echo slow`,
    gives it the time, in all three modes); `script-in-login-shell` (a script's `exit` now runs in
    the subshell, which never runs `~/.bash_logout`, so the remaining difference is `$$`: pinned by
    `test_a_signal_death_is_reported_and_reraised[<local|ssh-bash>-script-kill]`, where a script in
    the login shell kills the launch before its record).
  - **Texts (R10, same commit).** AGENTS §2 (nine members, `run` without `stdin`, `write` and its
    limits), §3 (signal deaths, `-1001` before the records, the separation's end, no startup file
    reading a write's content, completeness at the end records and its limit), §5 (writes as the
    mapped user), the `launch.py` and `ssh.py` footguns, the repository map line for the tools and
    § Testing's entries for the tool, write, executor, launcher, smoke and ssh tests; the README's
    execution paragraph; the `Executor`, `run` and new `write` docstrings in `base.py`; the module
    and function docstrings in `launch.py`, `ssh.py`, `shell.py` and `delegate.py`; and the
    `run_command` description, whose served-surface delta (`run_command.description`, already a
    named delta) follows by the scripted hand-edit: its text copied from the served surface into
    `tests/fixtures/served_surface.json`, nothing else changed. `issues/startup-background-jobs.md`
    and its `ROADMAP.md` entry gain dated lines. PLAN §2.2 and §2.2a are records and stay as written.
  - **Gates.** Local, macOS (bash 3.2), on the tree of this commit. The P7 gate (shared by P1–P5 and P7), first
    run: both eras 1530 passed, then the sweep red on the three survivors above (attempt 1,
    recorded); after the re-targeting, run again: `1538 passed` in each era, 0 skipped; the sweep
    green, 168 of 168 cases killed and both controls reporting correctly; sandboxed `--help` exit 0;
    13 min 56 s. P6's stored gate: the matrix `162 passed` in each era, the suite `1538 passed` in
    each, its 13 named mutants killed, both controls correct, `--help` exit 0; 8 min 15 s. Linux,
    bash 5: CI on the pushed head (below).
- **Remediation amendment 5 (2026-10-22, Geoffrey's ruling after review cycle 4).** `REVIEW.md`
  cycle 4, the named exception, came back not clean; Geoffrey ruled (`GOAL.md`, newest
  Clarification): a fix scoped to the failure paths it found, then a review scoped to verifying
  them. P1, P2, P4, P5 and P7 reopen and land in one commit, since the code, its pins, the
  mutants and every text that describes them move together (R10): the launcher's record tests and
  the `sudo` stand-in (P1), the ssh backend, the protocol texts, the loopback server and
  `run_command`'s description (P2), the write rows in every mode (P4), `sbatch` (P5), and the
  sweep's mutants (P7). P3 and P6 are untouched. No gate is retuned: P1, P2, P4, P5 and P7 run the
  stored P7 gate, and P6's stored gate is run as well; the review fields are left as cycle 4 wrote
  them.
  - **Class 1: no failed transfer is reported as a write.** `ssh._upload` turns pipelining off
    (Paramiko 5.0.0 files a pipelined write's reply under `type(None)`, where nothing raises it), so
    every 32 KiB write waits for its reply; closes the file through `_close`, which sends the
    close itself (`sftp._request(CMD_CLOSE, …)`) and so raises on a failed close, which
    Paramiko's own close swallows; then checks the staged size by `stat`. Any failure raises
    `ssh.Short`. The bytes' POSIX `cksum` (CRC and size; `ssh.cksum`, zlib's reflected CRC-32 on
    the bit-reversed bytes, checked against the host's `cksum`) is staged as `expected` in the
    same 0700 directory, and every placing program starts with `ssh.VERIFY`, which compares the
    staged file's `cksum` with it and otherwise exits 1 with a note, the target untouched. On a
    failed transfer, `_reason` runs `ssh.PROBE` (64 KiB of zeros appended to the staged file) and
    takes the cause from its error, since OpenSSH's SFTP (version 3) says only `Failure`; the
    write raises `OSError` "the transfer of N bytes to 'path' failed: <cause>; the file is
    unchanged", and the staging (both files) is removed. **Pins:** `tests/ssh_server.py` gains
    `sftp_server` (relaying SFTP to OpenSSH's own `sftp-server`, `_RealSFTP`), `write_limit`
    (fail a write past a size with an errno, or drop the rest and report success), `write_flip`
    and `close_failure` (`_Subsystem`). `test_a_write_that_runs_out_of_room_keeps_the_file`: on
    macOS a 4 MB HFS+ image attached and detached by the test, filled to 8 KiB and 32 KiB free,
    served by OpenSSH 10's `sftp-server`; EDQUOT injected at 32 KiB; each with a 36,027-byte
    `write` over a 416-byte file and an `sbatch` of a 36,027-byte job script.
    `test_a_staged_file_that_is_not_what_was_sent_is_never_placed[short|changed|close-fails]`;
    `test_cksum_is_posix_cksum`, `test_cksum_matches_the_hosts_cksum`. The suite's
    `test_a_failed_ssh_write_leaves_nothing_behind` lost its row that patched `_upload` to raise
    (the real stack never did); the rows above replace it. **Red → green** (the new tests run
    against this commit's parent, with only the new names added so they import): `8k-free`: the
    416-byte file became 0 bytes, `exit 0`, and the job script was submitted → the file keeps its
    416 bytes, "failed: No space left on device", nothing submitted or left; `32k-free`: 416 → 0
    bytes, exit 0, submitted → 416 kept, the same cause; `edquot`: 416 → 32,768 bytes, exit 0,
    submitted → 416 kept, "failed: Failure"; `short` and `changed` were placed with exit 0 → refused
    ("32768 of 36027 bytes arrived", "is not what was sent"). **Decision (CI), in the ruling's
    spirit:** the runner has OpenSSH's `sftp-server` (its `ssh` package), but no filesystem can be
    attached without root, so on Linux the `8k-free` and `32k-free` rows inject ENOSPC at that size
    through the in-process subsystem and expect the SFTP server's `Failure` as the cause; macOS
    runs them on the real full volume. No row skips anywhere (`conftest.need_sftp_server` fails a
    host without the server). **Decision:** pipelining off, not every reply collected: collecting
    them needs Paramiko's request table, which a reply read for another request empties; the cost
    is a round trip per 32 KiB (about 1 s per MiB at 30 ms).
  - **Class 2: over ssh, a write refuses where `cat >` would.** An overwrite of an existing
    regular file runs `ssh.WRITABLE` first (`[ -w "$2" ]`, the access check `cat >`'s open makes:
    a read-only file, another user's file in a group-writable directory), failing with
    `<path>: Permission denied`, the target untouched; an append and a write-through already fail
    in `cat` itself. `_staging` refuses a symbolic link whose target's directory does not exist
    (OpenSSH's `realpath` fails there; the loopback's never does, so `_staging` also checks the
    directory), naming the link's target, the link kept. The mode a replaced file keeps drops
    setuid and setgid (`ssh.SET_ID`). **Decision, in the ruling's spirit:** "refuse a dangling
    symlink" is read as `cat >` reads it, which the parity pin requires: a link to a missing file
    in an existing directory creates that file through the link in every mode, as `cat >` does
    (measured on macOS bash 3.2; the parity rows run on Linux CI too), and only a link whose
    target's directory is missing is
    refused. **Pin:** `test_a_write_refuses_what_cat_refuses[local|delegate|ssh|ssh-openssh]` (the
    last with OpenSSH's `sftp-server`): a 0444 file refused with `Permission denied` and unchanged;
    a link into a missing directory refused, the error naming its target over ssh, the link kept;
    a link to a missing file written through; 4755 and 2755 files left 755. Another user's file
    needs root to plant, so the 0444 row stands for the same check. Red: over ssh the 0444 file was
    replaced (mode 444 kept, content new), the 4755 and 2755 files kept their bits, and with
    OpenSSH's server the link into a missing directory was replaced by a regular file. **Recorded
    limits** (AGENTS §2): setgid is cleared even without group execute, which a Linux `cat >` keeps;
    and the checks and the move are two steps, so a target the same user changes between them is
    still replaced.
  - **Class 3: a lost link is never "did not start".** New reserved code
    `CONNECTION_LOST_EXIT_CODE = -1003` (`base.py`, exported), with
    `launch.LOST_NOTE` = "cluster-mcp: the connection was lost; the command may have run". `_collect`
    reads a channel that closed without its end of output (`_lost`: sshd sends that end before it
    closes a channel; the transport closes it without one when the link goes), before both records
    arrived, as lost; `_exec` returns the same when the exec request fails on a connection that is
    no longer active (a live server's refusal still raises). `separate` keeps a lost result lost
    whatever arrived, as it keeps a timeout, and never turns it into `-1002`. `sbatch` raises "The
    connection was lost during the submission, so the job may have been submitted: check squeue
    before submitting it again", never "Failed". **Decision, in the ruling's spirit:** a result
    lost after its start sentinel or one record arrived is `-1003` too, its output kept: the status
    is not known, as at a timeout. **Pin:** `tests/ssh_server.py`'s `DroppingSSH`, the block
    reviewer's drop shape (the command runs to its end, its output, sentinel and records included,
    never sent, then the connection is cut), dropping after the exec request is answered or before:
    `test_a_lost_connection_is_never_did_not_start[after-the-command|before-the-reply]` (`~/ran`
    exists, `-1003` with the note, `sbatch` called once and reported as "may have been
    submitted"); the unit `test_a_channel_closed_without_its_end_is_a_lost_connection`. Red: both
    rows returned `-1002` "did not start (exit -1)" and `sbatch` "Failed to submit job". §3,
    `base.py`'s `run` docstring and the `run_command` description state it; the served surface's
    `run_command.description` (already a named delta) follows by the scripted hand-edit of that
    one string.
  - **Class 4: coverage.** `test_an_end_record_split_across_reads_is_found` (the ssh loop, a fake
    channel whose output never ends and whose exit status never comes) and
    `test_a_local_end_record_split_across_reads_is_found` (a local launch that `exec`s a sleep
    after its records): the nonce, then the status without its full stop, then the stop. The
    `sudo` stand-in (`conftest.RELAY`) no longer crashes setting SIGKILL's action, so it dies of
    a SIGKILL as `sudo` does: `test_a_signal_death_is_reported_and_reraised` gains a `delegate`
    wire, whose `kill` row was red (the stand-in exited 1).
  - **Mutants.** New (15): `sftp-writes-pipelined`, `sftp-close-unchecked`,
    `staged-size-unchecked`, `staged-checksum-unchecked`, `transfer-cause-unnamed` (killed on the
    macOS full-volume row only), `unwritable-target-replaced`, `dangling-link-replaced`,
    `set-id-bits-kept`, `lost-link-read-as-ended`, `lost-link-at-exec-raised`,
    `lost-link-not-started`, `lost-submission-failed`, `ssh-record-scan-from-end`,
    `local-record-scan-from-end`, `record-without-full-stop`. Re-planted where the anchor moved:
    `content-on-stdin-over-ssh`, `ssh-stdin-not-closed`, `content-route-unstated`,
    `staging-not-cleaned`, `staged-file-not-private`, `link-replaced`. Each layer of class 1 is
    killed by the row only it catches: pipelining by the injected EDQUOT row's cause (with it, the
    size check still refuses, but with "bytes arrived" in place of the server's `Failure`), the
    size check by the silent short write's message, the checksum by the changed byte. Left
    unplanted as equivalent: dropping `_staging`'s own directory check, which only the loopback
    needs (its `realpath` never fails; the staging `mkdir` then fails in the missing directory, so
    the link still stays); with OpenSSH's server `dangling-link-replaced` covers the path. The
    sweep goes from 168 to 183 cases.
  - **Texts (R10, same commit).** AGENTS §2 (the checked transfer, the checksum, the refusals, the
    cause, the recorded limits), §3 (`-1003`, the split-record pins), the `ssh.py` footgun, §
    Testing's entries for the write, launcher, executor fixture and ssh tests and the skip rule;
    the README's execution paragraph; `base.py` (`CONNECTION_LOST_EXIT_CODE`, the `run` and `write`
    docstrings); the `launch.separate` and `ssh.py` module, `_collect`, `_exec`, `_staging`,
    `_upload` and `write` docstrings; the `run_command` description and its fixture string.
  - **Gates.** Local, macOS (bash 3.2, OpenSSH 10 `sftp-server`), on the tree of this commit but
    this note. The stored P7 gate (shared by P1, P2, P4, P5 and P7), first run: `1615 passed` in
    each era, 0 skipped; the sweep green, 183 of 183 cases killed and both controls reporting
    correctly; sandboxed `--help` exit 0; 15 min 5 s. P6's stored gate: the matrix `162 passed` in
    each era, the suite `1615 passed` in each, its 13 named mutants killed, both controls correct,
    `--help` exit 0; 8 min 44 s. Every image the full-volume rows attached was detached. Linux,
    bash 5: CI on the pushed head.
- **Verify:** frontmatter `P2.verify`.
- **Touches:** `src/rcac_mcp/executor/*`, `src/rcac_mcp/tools/shell.py` (docstring), `src/rcac_mcp/__init__.py` (`APP_HELP` only), `tests/{harness,conftest,ssh_server,test_ssh_exec,test_ssh_smoke,test_executors,test_hygiene,mutants,test_mutations}.py`, `tests/fixtures/served_surface.json`, `AGENTS.md`, `README.md`, `SECURITY.md`, `issues/`, `ROADMAP.md`.

## Phase P3 — Startup: a failed SSH connect is answered, in both eras
**Satisfies:** R9 · **Depends on:** P2
**Goal:** `rcac-mcp --ssh-host <unreachable>` prints `cannot connect to <host>: <cause>` on stderr,
answers every stdio request with that message as a −32603 error until EOF, and exits 5.

- [x] `src/rcac_mcp/startup.py` (stack-free): `answer_until_eof(stdin, stdout, message)`,
      `StartupFailure(RuntimeError)`, `connect_failure_message(host, exc)` with the cause classes
      (PLAN §2.5).
- [x] `executor/ssh.py`: default `connect_timeout`, `banner_timeout`, `auth_timeout` (15 s) through
      Fabric's config, an ssh_config `ConnectTimeout` still winning.
- [x] `src/rcac_mcp/__init__.py`: wrap only `SSHExecutor(ssh_host)`; log once, answer on `stdio`,
      raise `StartupFailure`; `StartupFailure` first in `exceptions`, mapped to
      `exit_status.runtime_error` with no second log line. The ladder and the missing-host
      `ValueError` are untouched.
- [x] `tests/harness.py`: verb `stdio_refusal(argv, era)` → `Refused(code, message)`; refuses an
      argv not starting with `SANDBOX`; raises if the connect succeeds. Add it to AGENTS.md's harness
      verb list.
- [x] `tests/test_startup.py`: `startup_refusal_violations` over in-memory streams with planted
      negatives (first request only, a notification answered, wrong id, extra stdout, wrong code);
      the raw wire through `[SANDBOX, CLI, '--ssh-host', '127.0.0.1:<closed port>']` (bind port 0,
      close, use it): `server/discover` + notification + `initialize` in → two error lines with ids
      1 and 2, the message on stderr naming `127.0.0.1:<port>`, exit 5, nothing else on stdout;
      `test_startup_failure_is_answered[legacy|modern]`, each era pinned on its own row whatever the
      run selects; a loopback listener that never sends a banner → answered "timed out" within the
      bound; a unit row per cause class with a stubbed `Connection` (unknown host, refused,
      authentication, timeout) asserting the host and the class appear.
- [x] Plant `startup-answers-first-only`, `startup-host-unnamed`, `startup-exit-zero`,
      `startup-stdout-noise`, `startup-no-connect-timeout`.
- [x] `AGENTS.md`: § Testing entries; §4's note that a failed connect is answered (no ladder change);
      `README.md` troubleshooting line.
- **Build amendment (2026-10-17).** Paramiko reports a key exchange cut off by the connect
  timeout as `SSHException('No existing session')` (its `start_client` stops waiting without
  raising), and a banner wait that runs out as `Error reading SSH protocol banner` raised while
  handling a socket timeout; both are classed `timed out` (the cause chain is read, the first by its
  exact text). The silent-banner row runs in-process through `MCPServerApp.main` with
  `CONNECT_TIMEOUT` lowered to 1 s and an 8 s deadline: Paramiko's own banner wait is 15 s, so at
  the real bound a missing default would still be answered "timed out" at about the same time and
  the row could not fail; the closed-port, raw-wire and era rows use the CLI as planned. The
  defaults also reach the connect arguments when a caller passes its own `connect_kwargs` (Fabric
  then ignores the config's), and a `timeout` already among them is left alone. One addition, no
  contract change: the responder reads nothing from a terminal (a person running the command at a
  prompt sees the stderr line and the exit, not a hang), pinned by a unit row. Extra unit rows:
  `-t http` exits 5 without reading stdin, and a failure of no known class is named by its own
  text. The repo map gains `startup.py`; the `ssh.py` footgun gains the connect bound.
- **Remediation amendment 3 (2026-10-18, review cycle 2).** Reopened with P1, P2 and P4 and landed
  in one commit with them; the classes, the choices, the pins and the gate retune are in P2's third
  note.
- **Verify:** frontmatter `P3.verify`.
- **Touches:** `src/rcac_mcp/{startup,__init__}.py`, `src/rcac_mcp/executor/ssh.py`, `tests/{harness,test_startup,mutants}.py`, `AGENTS.md`, `README.md`.

## Phase P4 — Filesystem, transfer and resource callers on argv; byte-exact writes
**Satisfies:** R1, R2, R3, R4, R5 (the filesystem half) · **Depends on:** P2
**Goal:** `list_directory`, `read_file`, `write_file`, `upload_file`, `download_file` and
`resources.py` call the executor with argvs only; `write_file` is byte-exact and takes `encoding`.

- [x] `tools/filesystem.py` per PLAN §2.4: `read_file` → `['cat', '--', UserPath(p)]` /
      `['head', '-c', str(n), '--', UserPath(p)]`; `list_directory` → the one fixed-script call
      (relative path gains `./`; `-a` its own element; the entries returned are those
      `ls -l [-a] DIR` lists today, absolute paths from the printed `$PWD`); `write_file` → validate
      and encode first (base64: ASCII whitespace removed, then strict), then the fixed script with
      `stdin=` bytes and, for `create_dirs`, the parent computed in Python as `$2`;
      `encoding: Literal['utf-8', 'base64'] = 'utf-8'`. Every fixed script is a constant in
      `FIXED_SCRIPTS` (PLAN §2.4).
- [x] `tools/transfer.py`: return what `put`/`get` return (no extra `readlink` call, no local
      `os.path`).
- [x] `resources.py`: the constant listing script and `['cat', '--', path]`; glob, order and header
      unchanged (§8).
- [x] `tests/test_tools.py`: responders read argvs; `tool_violations` gains `passed a command string`
      (any non-`run_command` tool whose recorded command is a `str`) and `unregistered script` (an
      argv whose element after `-c` is not in `FIXED_SCRIPTS`), each with a control; the
      `write_file` row reaches the executor through `stdin`.
- [x] `tests/test_writes.py` (`integration`, three modes: `executes` for local and delegate, the
      loopback server for ssh): sha256 of the file equals sha256 of the bytes sent for no final
      newline, empty, a line `RCAC_MCP_EOF`, CRLF, NUL, 1 MiB, invalid-UTF-8 binary via base64; in
      overwrite and append (append to a missing file creates it); `create_dirs` with `~/new dir/x`
      and with a directory whose name ends in a newline; R5: base64 wrapped with space, tab, CR and LF is accepted and byte-exact; invalid base64, a vertical
      tab or form feed, URL-safe characters,
      bad padding, an unknown `encoding` (schema), a lone surrogate in `utf-8` content → an error
      naming the parameter and **the target unchanged** (hash before == hash after, or still
      absent); GNU-wrapped base64 (76 columns, final newline) is accepted and byte-exact. No element
      of any recorded argv contains the content (unit row).
- [x] Fixture (PLAN risk 2): `write_file.description` and `write_file.properties.encoding` only.
- [x] Plant `tool-command-string` (read_file back to an f-string), `fixed-script-interpolated` (a
      value formatted into a fixed script), `write-final-newline`, `content-in-argv`,
      `append-overwrites`, `create-dirs-dirname-subst` (`$(dirname …)` back), `base64-unvalidated`,
      `write-before-validate`, `read-file-no-dashdash`; re-check `transfer-local-fallback`.
- [x] Deferral: `issues/list-directory-parsing.md` (names with newlines, symlink arrows, device
      sizes) + ROADMAP entry.
- **Build amendment (2026-10-17).** `FIXED_SCRIPTS` lives in a new leaf module,
  `src/rcac_mcp/scripts.py` (`fixed()` registers a module-level constant, `script_argv()` builds
  `[/bin/bash, --posix, -c, SCRIPT, NAME, *values]`, POSIX mode per P2's amendment); the repo map
  gains it. The `passed a command string` pin exempts the Slurm and site tools
  (`test_tools.PASSES_A_SCRIPT`) until P5 moves them to argvs; P5 shrinks the set to `run_command`.
  The harness fence accepts `executes`: the fixture now also replaces the global `Popen` with a
  launcher that refuses any argv not starting with `bin/sandbox.sh` (the loopback SSH server's
  launches do), pinned by `test_executes_opens_the_fence_only_to_sandboxed_launches`.
  `RecordingExecutor.put`/`get` return `<name>:<destination>`, so a transfer's result is traced to
  the executor. `read-file-no-dashdash` is killed by the exact-argv pin
  (`test_filesystem_argvs[read-cat]`): a relative `UserPath` already gains `./`, so no path can
  reach `cat` starting with `-`, and no behavioural row can see a dropped `--`. `list_directory` now
  changes into the directory: a symlink to a directory lists the target's entries, a path naming a
  file is an error ("Not a directory") instead of a one-entry listing, and `show_hidden` works
  (today's `long-isoa` was rejected). The context listing stays the constant
  `find … 2>/dev/null | sort` pipeline (PLAN §2.4), not research 04's `-print0`. The unknown
  `encoding` is refused by the schema (`literal_error` naming `encoding`), before the tool runs.
  `test_writes`' ssh leg uses a bash login shell; the login-shell matrix is `test_ssh_exec`'s.
- **Remediation amendment 3 (2026-10-18, review cycle 2).** Reopened with P1, P2 and P3 and landed
  in one commit with them; the classes, the choices, the pins and the gate retune are in P2's third
  note.
- **Remediation amendment 4 (2026-10-22, Geoffrey's ruling after review cycle 3).** Reopened with
  P1, P2, P5, P6 and P7 and landed in one commit with them; the end record, the SFTP route, the pins, the
  measurements and the mutants are in P2's fourth note.
- **Remediation amendment 5 (2026-10-22, Geoffrey's ruling after review cycle 4).** Reopened with
  P1, P2, P5 and P7 and landed in one commit with them; the classes, the pins, the decisions and the
  mutants are in P2's fifth note.
- **Verify:** frontmatter `P4.verify`.
- **Touches:** `src/rcac_mcp/tools/{filesystem,transfer}.py`, `src/rcac_mcp/resources.py`, `src/rcac_mcp/scripts.py` (new), `tests/{test_tools,test_writes,mutants,test_mutations,conftest,harness,test_hygiene,test_surface}.py`, `tests/fixtures/served_surface.json`, `README.md`, `AGENTS.md` (§ Testing, §9 deltas), `issues/`, `ROADMAP.md`.

## Phase P5 — Slurm and site tools on argv; sbatch args and list extra_args
**Satisfies:** R1, R2, R4 (`script_content`), R6 · **Depends on:** P4
**Goal:** every tool in `tools/slurm.py` and `tools/rcac.py` calls the executor with an argv;
`sbatch` takes `args` and list `extra_args`; `script_content` is written byte-exactly.

- [x] `tools/slurm.py` per research 04's table: option values as separate elements; `--` before
      `sbatch`'s script path and `scontrol show node`'s name; `sbatch(args: list[str] | None,
      extra_args: list[str] | None)`, the tool's own options first, `extra_args` last before `--`, an
      element `--` refused, the script path a `UserPath` (never starts with `-`); `script_content` through
      the `write_file` path plus `chmod +x`; the filename part sanitised (`-J` literal). `sacct`'s
      `whoami` → `['whoami']`.
- [x] `extra_args` on `squeue`, `scancel`, `sacct`, `sinfo` becomes `list[str] | None` (GOAL
      Clarifications, PLAN/TECH gate): each element one literal argument, after the tool's own
      options, an element `--` refused; a string refused by schema validation naming the type
      (`tests/test_extra_args.py`, both eras on their own rows).
- [x] `tools/rcac.py`: argvs; `average_wait` uses `--partition` and `-A` (PLAN §2.4).
- [x] `tests/test_sbatch.py`: `args` after the script path, `extra_args` before `--`, a string
      `extra_args` refused by schema validation with an error naming the array type (both eras on
      their own rows); `script_path='-x y'` reaches `sbatch` as one element after `--` (as `./-x y`),
      also when `extra_args=['-J']` swallows the `--`.
- [x] `tests/test_writes.py`: `sbatch.script_content` rows (byte-exact, three modes; `sbatch` is an
      argv-echo shim).
- [x] `tests/test_tools.py`: rows for `args` and list `extra_args`; `PASSES_A_SCRIPT` shrinks to
      `run_command` (P4 amendment).
- [x] Fixture (scripted hand-edit, deltas listed in the commit body, named in § Testing and §9):
      `sbatch.description`, `sbatch.properties.args`, `sbatch.properties.extra_args`, and
      `properties.extra_args` (plus each tool's description if its `Args:` line changes) on
      `squeue`, `scancel`, `sacct`, `sinfo`; README tool lines.
- [x] Re-plant `tool-builds-local-executor`, `tool-builds-ssh-executor`; plant `sbatch-args-joined`,
      `extra-args-accepts-str`, `extra-args-swallow-dashdash` (script path rendered without `./`), `option-value-joined`, `script-content-newline`,
      `scontrol-node-positional`.
- [x] Deferral: `issues/sbatch-script-name-collision.md` (one-second timestamps) + ROADMAP entry.
- **Build amendment (2026-10-17).** The job script's fixed script is `mkdir -p -- "$2" && cat >"$1"
  && chmod -- +x "$1"` (`slurm.WRITE_JOB_SCRIPT`, built from `write_file`'s `MKDIR` and `WRITE`):
  PLAN §2.4's `chmod +x -- "$1"` fails on BSD `chmod`, whose option parsing stops at the mode, so
  the `--` after it is read as a file (`chmod: --: No such file or directory` in `local` mode on
  macOS); `--` before the mode is read as the end of options by both GNU and BSD. **Empty
  optional option values (foreman's ruling):** an optional scalar string whose only use is an
  option's value (`squeue`: `user`, `account`, `partition`, `state`; `scancel`: those and `name`;
  `sacct`: `user`, `account`, `name`, `state`, `starttime`, `endtime`, `format`; `sinfo`:
  `partition`, `nodes`, `state`, `format`; `sbatch`: `job_name`, `account`, `partition`, `qos`,
  `time`, `mem`, `gpus`, `constraint`, `output`, `error`; `average_wait`: `partition`, `account`)
  means "not given" when it is `''`, as for `None`, so the tool's default applies
  (`squeue(user='')` sends `--me`). R2's value classes do not include the empty string, and some
  clients send `''` for an unset optional. Every list element (`args`, `extra_args`) stays literal,
  `''` included. *(Amended at P6, foreman's ruling: the rule covers optional scalars, option values
  and optional positionals alike, so `scontrol_show_node`'s `node` and `sbatch`'s `script_path`
  mean "not given" when `''` too; required parameters and list elements stay literal.)* Pinned by `test_empty_optional_scalars_mean_not_given`
  with `test_empty_rows_cover_every_optional_scalar_option`, and the mutant
  `empty-optional-passed-literally`, added to the gate; PLAN §2.6's oracle is corrected, so P6's
  matrix expects the default argv for `''` on these. The schema's refusal of a string list reads `Input should be a valid list` (pydantic's
  `list_type`, the schema's `array`), with the parameter's name; the rows assert both.
  `script_content` with a lone surrogate is refused naming it, before any executor call, and an
  `extra_args` element `--` is refused before anything runs, including the script write. The
  argv-echo stand-ins live in a helper, `tests/argv_echo.py` (`install`, `calls`: one file per
  call under the home, prepended to a login bash's `PATH` through `.bash_profile`), which P6's
  matrix can reuse. `test_slurm_and_site_argvs` pins every Slurm and site tool's exact argv, with
  `test_slurm_argvs_cover_the_slurm_and_site_tools`; `option-value-joined` is planted in the
  shared `_options` helper and `scontrol-node-positional` is killed by that pin, as P4's
  `read-file-no-dashdash` is. `extra-args-accepts-str` widens `sbatch` and `squeue` to accept a
  string and split it. The fixture deltas are `description` and `properties.extra_args` on each of
  `sbatch`, `squeue`, `scancel`, `sacct` and `sinfo`, and `sbatch.properties.args`.
- **Remediation amendment (2026-10-18, review cycle 1, class 6).** `average_wait`'s `--partition`
  (this phase's change from `-p`, which `REVIEW.md` found unverified) is kept: the site script
  accepts it. Evidence, read-only as `<user>`, within the dogfood class, on
  Gautschi and Negishi: the site's `average_wait` is a bash script whose argument loop takes only
  `--raw`/`--raw-wait`, `-v`/`--verbose`, `--quiet` and `-h`/`--help`, and passes every other
  argument verbatim to one `sacct` call, as its own usage text says (the loop is identical on both
  clusters). `sacct --help` there (Slurm 26.05.4) lists `-r, --partition` and `-p, --parsable`, so
  `-p cpu` would reach `sacct` as `--parsable` and a stray `cpu`, while `--partition cpu` filters by
  partition. `-A` is `sacct`'s `--account`. No code changes; the new mutant
  `average-wait-short-partition` (`--partition` back to `-p`) is killed by the exact-argv row
  `test_slurm_and_site_argvs[average-wait]`. The gate is retuned to P7's, the latest done phase's
  (Step 1.3), whose sweep runs every planted case.
- **Remediation amendment 4 (2026-10-22, Geoffrey's ruling after review cycle 3).** Reopened with
  P1, P2, P4, P6 and P7 and landed in one commit with them; the end record, the SFTP route, the pins, the
  measurements and the mutants are in P2's fourth note.
- **Remediation amendment 5 (2026-10-22, Geoffrey's ruling after review cycle 4).** Reopened with
  P1, P2, P4 and P7 and landed in one commit with them; the classes, the pins, the decisions and the
  mutants are in P2's fifth note.
- **Verify:** frontmatter `P5.verify`.
- **Touches:** `src/rcac_mcp/tools/{slurm,rcac}.py`, `tests/{test_tools,test_writes,test_sbatch,test_extra_args,argv_echo,test_surface,mutants}.py`, `tests/fixtures/served_surface.json`, `README.md`, `AGENTS.md`, `issues/`, `ROADMAP.md`.

## Phase P6 — Literal-argument matrix: every string parameter, all three modes
**Satisfies:** R1, R2, R3, R4 (the proof) · **Depends on:** P5
**Goal:** `tests/test_literal_args.py` drives every string parameter of every tool with the hostile
value set in `local`, `delegate` and `ssh` against a fixture tree, through the served schema
(`in_memory(shared_server(executor))`), and proves its own coverage.

- [x] Fixture tree under the test's home: files and directories named exactly each value; argv-echo
      shims (`printf '%s\0' "$0" "$@"` to a log) for `sbatch squeue scancel sacct sinfo scontrol ls
      jobinfo jobcmd jobenv jobscript myquota findscratch showpartitions slist sfeatures
      average_wait whoami`; real `cat head mkdir readlink cp chmod`.
- [x] Value set and crossings exactly as PLAN §2.6 (including `~`, `~/x`, `~//x`, `~name/x` for every
      parameter, a component ending in a newline, empty, `--`, `+x`, a lone surrogate refused naming
      the parameter); each string parameter crossed with the bool/optional parameters that select a
      different script.
- [x] Oracles (PLAN §2.6): path → effect on the file named by the value; option value → exactly one
      log element equal to the value, right after its option; content → sha256; `~` on a path → the
      mode's home (the mapped home in delegate), on any other parameter → literal; `~name/x` →
      literal; a leading `-` never acts as an option (the `-n`/`--help` file is the one read or
      written). The `ls` shim records argv and cwd and prints a canned GNU long-iso listing with a
      name containing spaces, which `list_directory` must parse.
- [x] `test_literal_matrix_covers_every_string_param`: walking `anyOf`, `items` and `enum` in the
      live served schema (`served_tools`, never the fixture), every (mode, tool, parameter) whose type
      includes `string` or an array of `string`, minus `run_command.command`, has a row; planted
      controls (an extra `Optional[str]`, an extra `list[str]`, a missing mode) fail it.
- [x] Row ids `<mode>-<tool>-<param>`, stable for mutant targets; the phase's mutants are killed by
      matrix rows as well as their unit rows.
- [x] If either era's run exceeds ~90 s, merge value classes (never modes) and record it.
- **Build amendment (2026-10-17).** The matrix as built, and what it found:
  - **Value classes merged** (PLAN risk 10): one value carries a leading `-n`, space, quotes,
    `$( )`, backticks, globs, `;|&<>(){}!#\` and a newline; `--help`, `+x`, `--`, `''`, `~`,
    `~/x y`, `~//x`, `~name/x` and `nl\n/x` stay apart, as their meaning depends on how they start
    or on being exactly that; the lone surrogate is `\udc80`, the one a local path would turn into
    byte `0x80`. A row's calls run concurrently (the first alone over SSH, so the connection is
    open). The matrix takes about 30 s per era; modes are never merged. The full suite already took
    about 89 s per era before it (about 122 s with it), so the ~90 s ceiling is read against the
    matrix's own step.
  - **Rows and oracles.** 52 parameters × 3 modes = 156 rows, `test_literal_argument[<mode>-<tool>-<param>]`.
    Coverage reads `served_surface` (the live server's schema; `served_tools` returns names only),
    never the fixture. The stand-ins are the matrix's own (one record file per process, with the
    cwd, so concurrent calls never collide) for `sbatch squeue scancel sacct sinfo scontrol
    average_wait ls`, the programs a string parameter reaches; `whoami` stays real (it is `sacct`'s
    default user), and the site tools with no string parameter have no row. The option oracle is
    the exact argv each call must produce, compared as a multiset; `~` on `read_file` and
    `write_file` expects the program's error naming the home (BSD `head` says "Error reading", not
    "Is a directory"). `executes.local_cwd` lets a `local` launch start in the server's home, so a
    relative path means the home in all three modes; the server process runs from its own HOME,
    apart from the mapped and remote homes.
  - **The `''` class** (foreman's ruling): an optional scalar expects the tool's default call; a
    list element or required parameter is one literal empty argument, which the program refuses
    (`No such file`); an empty transfer path is refused naming the parameter (below).
  - **Defects found in P1–P5 code, fixed here.** (a) A lone surrogate reached the launcher, which
    refused it as "argument N is not valid UTF-8 text", naming no parameter; in `local` mode a
    transfer path carrying `\udc80` reached the filesystem as byte `0x80`. `@mcp_tool` now wraps
    every tool so a string argument, or string list element, holding a lone surrogate is refused
    naming the parameter before the tool runs (§7 says so; the served schema is unchanged).
    (b) An empty transfer path: BSD `cp` reads `''` as `.`, so a `delegate` upload or download to
    `''` copied into the mapped home and reported success, and an SFTP server may resolve `''` to
    the login home (the loopback's did). `upload_file` and `download_file` refuse an empty path
    naming the parameter. Seen red first: `delegate-upload_file-remote_path`,
    `ssh-upload_file-remote_path`, `delegate-download_file-local_path`. (c) Per the foreman's
    ruling, `scontrol_show_node(node='')` shows every node (`if node:`, as on origin/develop), and
    `sbatch(script_path='')` is "not given" (the other optional positional in the Slurm tools; with
    no `script_content` it is refused as a call with no script). Ruling 3 makes the matrix expect
    the default for every optional scalar, so `list_directory(path='')` lists `.` and
    `run_command(cwd='')` passes no cwd, explicitly rather than by how bash runs `cd ''`.
    `script_content=''` stays an empty job script (R4). `test_empty_optional_scalars_mean_not_given`
    gains `scontrol-node`, `sbatch-script-path` (a refusal row: no call), `list-directory`,
    `read-file-encoding` and `run-command-cwd`; its coverage test now holds every optional string
    of every tool to a row, but enums and `script_content`.
  - **Mutants.** Matrix rows join the targets of `tool-command-string`, `option-value-joined`,
    `scontrol-node-positional`, `launcher-arg-unquoted`, `delegate-tilde-server-home` and
    `empty-optional-passed-literally`. New: `empty-positional-passed-literally` (node and script
    path, killed by the new unit rows and the matrix), `empty-list-path-passed-literally`,
    `empty-cwd-passed-literally`, `transfer-empty-path-passed`, `surrogate-unnamed`.
    `transfer-local-fallback` is re-planted: the line it edits now refuses empty paths. All join
    the gate.
  - **Exempt from "killed by matrix rows as well"** (foreman's ruling 1: behaviour-equivalent
    defence in depth, pinned by exact-call unit rows): `read-file-no-dashdash` (a relative
    `UserPath` already gains `./`, so no path reaches `cat` with a leading `-`);
    `empty-list-path-passed-literally` (bash runs `cd ''` as staying put, so listing `''` lists the
    same directory as `.`); `empty-cwd-passed-literally` (the launcher already drops an empty cwd).
- **Remediation amendment 4 (2026-10-22, Geoffrey's ruling after review cycle 3).** Reopened with
  P1, P2, P4, P5 and P7 and landed in one commit with them; the end record, the SFTP route, the pins, the
  measurements and the mutants are in P2's fourth note.
- **Verify:** frontmatter `P6.verify`.
- **Touches:** `tests/{test_literal_args,conftest,mutants,test_tools}.py`, `src/rcac_mcp/tools/{__init__,slurm,filesystem,shell,transfer}.py`, `AGENTS.md` (§ Testing, §7), `PLAN.md` (§2.6's empty-string line).

## Phase P7 — Sweep: retired texts, every mutant, deferral ledger
**Satisfies:** R10 (the sweep), R7 (the `module` evidence, orchestrator-graded) · **Depends on:** P6
**Goal:** nothing outside the retained records describes the old interpreter, the old timeout
contract or the heredoc writes; every planted violation is killed; every deferral is recorded.

- [x] `sh spec/safe-execution/verify/sweep.sh` green: no string-literal command at any `run(` call
      (AST scan; the runtime pins are P4's `passed a command string` and `unregistered script`), the
      retired phrases absent from `src/`, `AGENTS.md`, `README.md`, `SECURITY.md`, the new contract
      texts present, and the full mutation suite killed. The script was committed with the plan;
      the phase does not edit it (a needed change is a TECH amendment, noted in the commit body).
- [x] Re-read § Invariants §2, §3, §5 and the footguns against the code (the profile says the code is
      ground truth).
- [x] **R7's `module` half is an artifact deliverable:** `research/01-cluster-shell.md` (measured
      2026-10-17, read-only, within the dogfood class) is the evidence. The orchestrator grades it;
      the blind reviewer cannot see `spec/`. If build changed `BASH_ARGS`, the measurement is re-run
      read-only and the brief updated.
- [x] **Deferral ledger:** walk P1–P6 bodies for "defer", "known limitation", "follow-up",
      "out of scope"; each has an `issues/{slug}.md` (KB `ISSUE.md` template, `status: unshaped`) and
      a `ROADMAP.md` entry: `startup-background-jobs`, `ssh-reconnect-loop`, `timeout-process-cleanup` (measurement awaits
      Geoffrey's approval), `list-directory-parsing`, `sbatch-script-name-collision`.
- [x] Landing notes for publish: the surface changes — `sbatch.args`; `extra_args` becomes
      `list[str]` on all five of `sbatch`, `squeue`, `scancel`, `sacct`, `sinfo`;
      `write_file.encoding`; the `run_command` description — commands run in a login bash (≈0.25 s
      each on the clusters) whose startup output never reaches a result, and `-1001` on timeout.
- **Landing notes for publish** (the squash commit body and the release notes take these):
  - **Surface changes** (the served schema; tool names and the install line are unchanged):
    - `sbatch` gains `args: list[str]`, each element one argument to the job script, after its path.
      `script_path` is one literal path: a caller that appended job arguments to it moves them to
      `args`.
    - `extra_args` becomes `list[str]` on `sbatch`, `squeue`, `scancel`, `sacct` and `sinfo`: each
      element one literal argument after the tool's own options; a string is refused by schema
      validation (`Input should be a valid list`, naming the parameter), and an element `--` is
      refused.
    - `write_file` gains `encoding` (`"utf-8"`, the default, or `"base64"`), and writes are
      byte-exact: no final newline is added, so a caller that stripped one to compensate stops.
      `sbatch.script_content` is written byte-exactly too.
    - `run_command`'s description: commands run in a login bash (≈0.3 s each on the clusters,
      re-measured at P7) whose startup output never reaches a result, but for one exception it
      now states (over ssh, a background job the remote login shell's own rc file starts can write
      into the result; remediation, 2026-10-18); on timeout `exit_code` is
      −1001 with the output captured so far; −1002 means the command never started.
  - **Behaviour changes a caller can see:**
    - An optional scalar parameter sent as `''` means "not given", as `None` does, so the tool's
      default applies (`squeue(user='')` lists the caller's own jobs; `scontrol_show_node(node='')`
      shows every node; `list_directory(path='')` lists `.`, its default); list elements and required
      parameters stay literal (foreman's rulings, P5 and P6).
    - `list_directory` changes into the directory: a symlink to a directory lists the target's
      contents, a path naming a file is an error (`Not a directory`) instead of a one-entry
      listing, and `show_hidden` works (foreman's ruling, P4).
    - Every value reaches its command as one literal argument; `~` and `~/` in a path are the home
      of the user the command runs as (the mapped user in `delegate`); `~name`, `$VAR` and globs
      are literal.
    - Timeouts return a result in every mode (−1001), never an error; a wrong `--ssh-host` is
      answered with `cannot connect to <host>: <cause>` on every request, exit 5.
    - `module` and the site's job tools (`jobcmd`, `jobenv`, `jobscript`) now resolve for users
      whose login shell is not bash; `average_wait` passes the partition as `--partition`,
      which the site script hands to `sacct` (verified read-only, P5 remediation, 2026-10-18).
    - A lone surrogate in any string, and an empty transfer path, are refused naming the
      parameter.
  - **Pages outside the repository** that name the tools or the install line (ROADMAP's release
    0.1.2 entry): no tool is renamed and the install line is unchanged; a page that documents the
    parameters of `sbatch`, `write_file` or an `extra_args` needs the changes above.
- **Build amendment (2026-10-17, P7).**
  - **R7 re-measured through the built launcher** (foreman's ruling: the build changed how bash
    starts). Read-only, Gautschi and Negishi, as `<user>` (login shell zsh): `SSHExecutor.run`
    over the real zsh login, the built exec string under `env -i` `bash -c`, `zsh -c`, `tcsh -c`
    and `sh -c`, and the local/delegate wire. Module state is identical across every launcher on
    each cluster; recorded in `research/01-cluster-shell.md` § Built-launcher re-measurement, with
    the driver and raw output in `research/01-cluster-shell/built.{py,out}`.
  - **§ Invariants re-read against the code.** §2, §3, §5 and the footguns match the code but for
    one claim: `put` and `get` "return the absolute path they wrote, as that side names it
    (symlinks unresolved)", while `SSHExecutor.put` returns `sftp.normalize(remote)` (PLAN §2.3),
    the SFTP server's REALPATH, which OpenSSH resolves through symlinks (read-only on Gautschi:
    `normalize('/bin/ls')` → `/usr/bin/ls`). The loopback SFTP server does not resolve, so no test
    could see it. The code is ground truth (profile): §2 and the `put` docstrings in
    `executor/base.py` and `executor/ssh.py` now say so. No code changed.
  - **Deferral ledger.** P1–P6 and PLAN §5 hold five deferrals, each with a seed and a ROADMAP
    entry, placed per the foreman's ruling: `ssh-reconnect-loop` and `sbatch-script-name-collision`
    in Part 0 after this cycle (Part 0 now counts six cycles); `timeout-process-cleanup` in Part I
    ahead of the tool families, and `list-directory-parsing` absorbed by `tool-surface` (Part I now
    counts eight); `startup-background-jobs` stays in Part III. PLAN risk 9 (the context listing's
    `sort` follows the login profile's locale) was left to `agents-d-instructions` with no line in
    that seed; it now has one. P1's prologue limit (functions named `unset`, `unalias`, `builtin`,
    `set`) is a recorded limit in § Testing, declined rather than deferred.
- **Rebase amendment (2026-10-18, moved base onto 404580c).** `develop` moved from `2b006fa` to
  `404580c`: release 0.1.1, `audit-uv-portability` (`bin/audit_lock.py`, `tests/test_audit.py`, the
  uv floor), `ci-workflow` (`.github/workflows/`, `bin/sdist_hygiene.py`), the `ci-workflow` seed's
  retirement, Part 0 closing with 0.1.2, and a release-recipe caveat. The cycle's commits were
  replayed onto it; the tree differs from `404580c` only by the cycle's own changes.
  - **Conflicts: `ROADMAP.md` only**, at the P2 and P7 replays. Part 0's opening takes `develop`'s
    text (release 0.1.2, `ci-workflow` landed, its 2026-10-18 correction) and is recounted: five
    cycles remain (this one, `ssh-reconnect-loop`, `sbatch-script-name-collision`,
    `agents-d-instructions`, `resource-reads-without-executor`). Part III keeps `develop`'s
    `sdist-hygiene-truncated-gzip` entry beside this cycle's `startup-background-jobs`;
    `ssh-reconnect-loop` and `timeout-process-cleanup` leave Part III as P7 moved them. This
    cycle's "ships in 0.1.1" (the `ssh-reconnect-loop` entry and its 2026-10-17 correction) and the
    landing notes' "release 0.1.1 entry" now say 0.1.2, the release that closes Part 0.
  - **`AGENTS.md` merged without conflict** and was re-read: `develop`'s CI, audit and uv-floor text
    (the repository map's paths outside the package, § Testing's `test_audit.py`,
    `test_sdist_hygiene.py` and `mutants.yml`, § Packaging, § Conventions, the profile's `ci` row,
    the recipe's caveats) and this cycle's executor, launcher and startup text sit in separate
    lines; neither contradicts the other.
  - **Mutants:** every anchor still applies (`tests/test_mutations.py` green); none re-planted.
    No spec id in the package or the suite, `develop`'s new tests included (`test_hygiene.py`'s
    check reads every `tests/*.py`).
  - **Gates:** P7's stored gate (shared by P1–P5) and P6's re-run green on the rebased tree.
- **Redaction amendment (2026-10-18, foreman's ruling).** The repository is public, and the
  cycle's records carried site-specific detail from the read-only probes and the by-hand bash 5
  transcripts. Before the branch's first push, everything the cycle adds was redacted to the rule:
  cluster names, bash and Lmod versions and the evidence facts stay; login node names become "a
  Gautschi login node", the operator's account `<user>`, dev-scratch paths
  `/scratch/<cluster>/<user>/<dev-dir>/…`, Lmod's install prefix `<lmod>/`, the site's scripts
  directory `<site-scripts>/`. Copied text of site scripts and system files, and the account's own
  startup files, are cut from the raw outputs to their section headings; one output that held only
  a site script's text (probe F) is dropped. `research/01-cluster-shell.md` states what each
  redacted section showed. Redaction only removed: no finding, number or verdict changed. The
  branch is pushed as one commit on `develop`; its unredacted history stays in the local clone.
- **Linux CI amendment (2026-10-19).** The first CI run of the pushed head (a9a2901; ubuntu-24.04,
  bash 5.2, GNU coreutils) failed five tests that pass on macOS (bash 3.2, BSD tools). Two causes:
  - **A non-portable test (four rows).** `test_profile_state_does_not_reach_the_launcher
    [<local|ssh-bash>-<physical|cdpath>]` ran the external `pwd`, which is `-P` by default in GNU
    coreutils and `-L` in BSD, so on Linux even the quiet home printed its physical path: the rows
    could not see the `set -P` leak they guard. They now run `pwd -L`, which prints the `$PWD` the
    launcher leaves (what `list_directory`'s fixed script reads) on both. The mutant
    `prologue-physical-kept` is still killed on macOS; its Linux kill is recorded below.
  - **A real defect on bash 5 (one row).** A startup file's `exec 58>file 59>file` replaces the
    parked descriptors on bash 5; the 3.2 measured leaves any open descriptor above 9 as it was
    (`exec 57<&0; exec 57<file` keeps the pipe), which is why P1's build amendment saw no loss. On
    bash 5 the program then wrote its sentinel and the command's output into the profile's files,
    and `separate` reported `-1002` "did not start (exit 3)": the command had run and exited 3.
    That is the class a resubmitted `sbatch` makes costly, and a taken stdin (`exec 57<file`)
    would have fed a write the wrong bytes. **Decision:** the program refuses to run on a broken
    restore. The stage parks a second copy of each stream (`PARK`: 47–49 beside 57–59); the
    prologue checks `[[ /dev/fd/5N -ef /dev/fd/4N ]]` for all three (a keyword, so no profile
    function or alias reaches it) and, on any mismatch, runs nothing: it prints `TAKEN_MARK` on 49
    and 59 (whichever is still the caller's stderr) and execs `/usr/bin/false`. `separate` turns a
    result with no sentinel and the mark into `-1002` with `TAKEN_NOTE` ("the command did not
    start: the login initialisation took a descriptor its streams were parked on, so it was not
    run"). So the command is never reported as not started when it ran, and nothing it printed is
    lost unnoted, because it prints nothing. Rejected: keeping the old "lost or noted" assertion
    (it passed only because bash 3.2 cannot take the descriptor), and an "unknown whether it ran"
    outcome (a new reserved code, and the not-started case would lose its meaning). Recorded
    limits: a profile that takes a descriptor and its copy onto the same file defeats the check;
    a system with neither `/dev/fd` nor `/proc/self/fd` would refuse every command, loudly.
    **A false refusal, found by the local gate and fixed.** The first local run of P7's and P6's
    gates after c547e12 failed one `test_literal_args` row each (a different row each run, every
    mode): the check refused with no profile at all. Diagnosed with the refusal block printing each
    pair: the `-ef` failed on one pair (58 or 59, once 57), while `stat -L` and an immediate re-check
    of the same descriptors right after agreed. Measured through `bin/sandbox.sh` with 24
    concurrent launches: 2 first checks in 3,000 failed, each passing on its first retry; none in
    2,000 without the sandbox wrapper's extra process. The cause, found in the next run (below), is
    that macOS's `/dev/fd` is shared across processes by descriptor number. The prologue now asks `PARKED_SAME` up to three times before refusing; a taken
    descriptor fails every time. After the change: 0 refusals in 4,000 sandboxed launches and in
    four concurrent `test_literal_args.py` runs (648 rows), against 1–3 failed rows per run before.
    Linux CI never showed it (its `-ef` reads `/proc/self/fd`).
  - **CI evidence, by pushed head** (workflow CI, job `gate`; "passed/skipped" per era):
    a9a2901 run 39199991590, failure (5 failed, 1341 passed, 70 skipped: the five rows above);
    c547e12 run 39202286076, success (1359 passed, 70 skipped, both eras); 4cfea29 run
    39203683155, success (the same counts); 320192b, a probe commit carrying the mutants
    `prologue-physical-kept` and `parked-descriptor-unchecked` together, run 39203706779, failure
    as intended: `[local-physical]` and `[ssh-bash-physical]` red with "the profile's state reached
    the launcher" (so the `pwd -L` rows kill the leak on Linux), all six
    `test_a_taken_parked_descriptor_runs_nothing` rows red, and the four real-profile rows red with
    exactly the defect, `-1002` "did not start (exit 3)" for `streams` and the decoy's bytes as
    stdin with exit 3 for `stdin` (so the probe found bash 5 keeping the takeover); the rest of its
    48 failures are the rendered-program and mutant-anchor checks the edit also trips. The next
    commit reverts the probe; its run is the final head's.
  - **Local gates** on 4cfea29 (macOS, bash 3.2): P7's stored gate green (1429 passed in each era,
    0 skipped; the sweep's mutation step killed all 165 cases and both controls reported
    correctly); P6's stored gate green.
    **Pins, exact on both bashes:** `test_a_taken_parked_descriptor_runs_nothing[57|58|59|47|48|49]`
    builds the takeover in the stage itself (each descriptor parked on a file), so it runs on any
    bash: `('', TAKEN_NOTE, -1002)` and the command's marker file absent;
    `test_a_profile_taking_a_parked_descriptor_is_not_run[<local|ssh-bash>-<streams|stdin>]` uses a
    real profile and probes whether this `/bin/bash` lets a profile take an open descriptor 58: if
    so, the same refusal and nothing run; if not (3.2), the whole result `('out\n', 'err\n', 3)`
    with the caller's stdin. A unit separation row and the planted `taken-unnoted` pin `separate`.
    Mutants: `parked-descriptor-unchecked` (no `-ef` check) and `parked-descriptor-unnoted`
    (`separate` ignores the mark), both killed locally; `stdin-not-parked` and
    `startup-not-parked` re-planted on the new `PARK`. The bootstrap grows by 18 bytes, so the
    largest `ssh` program is now 98,154 bytes (AGENTS §2). AGENTS §3's `-1002`, the high-blast
    list, § Testing and the `Executor.run` docstring say so (R10); PLAN §2.2's stage text is a
    record and is left as written. `run_command`'s description already said −1002 means the
    command never started, so the served surface has no delta.
  - **The 70 skips on CI** are all "`/bin/<shell>` is not installed here" (the runner has no zsh,
    tcsh, ksh or csh): zsh 40, tcsh 18, ksh 6, csh 6. None hides a Linux behaviour of the code;
    the CI workflow is unchanged (a separate concern, routed by the foreman).
- **Shells-on-CI amendment (2026-10-19, foreman's rulings).** `develop` gained 5153e47, which
  installs zsh, tcsh, ksh and csh on the CI runner. The branch was already pushed, so `develop`
  came in by merge (2194a59) rather than a rebase, which only a force push could have published;
  it merged cleanly (AGENTS.md § Conventions and the workflow), and the landing squashes either way.
  - **The `-ef` cause, named.** macOS's `/dev/fd` (devfs `fdesc`) keeps one node per descriptor
    number, shared by every process; a stat of `/dev/fd/N` while another process stats its own `N`
    can fail with EBADF. Measured: twelve processes each statting a pipe's two dups 20,000 times,
    on the same two numbers in every process, 82,817 of 240,000 stats failed with EBADF (73,991 on
    another pair of numbers); on numbers distinct per process, none. In bash, twelve loops of 3,000
    `[[ /dev/fd/N -ef /dev/fd/N+1 ]]` on a pipe: 7,065 failures with the same `N` everywhere, 0 with
    distinct ones; `fstat` of the same descriptors always agreed. Every launch parks on 47–49 and
    57–59, so concurrent launches collide. The failure is one-sided (a failed stat reads as "not
    the same", never as "the same"), so the retry cannot pass a taken descriptor and is the right
    response; it stays at three asks, and `PARKED_SAME`'s comment names the cause. Linux stats
    `/proc/self/fd`, per process, and never showed it.
  - **No silent skip on CI.** All three missing-shell skips (`test_launch._wire_argv`,
    `test_ssh_exec`'s `serve` and `slow`) now call `conftest.need_shell`: it skips off CI and fails
    on CI (`CI=true`), naming the shell. Pins in `tests/test_hygiene.py`:
    `test_a_missing_shell_fails_on_ci_and_skips_elsewhere` (CI `true` fails, unset or `false`
    skips) and `test_only_need_shell_skips`, a static scan that no other `skip`, `skipif`,
    `importorskip` or `xfail` appears in `tests/*.py`, with planted controls. AGENTS § Testing
    and § Conventions say so.
  - **Bash twins** for the remote-rc rows that only zsh drove: `test_a_remote_rc_never_reads_the_
    callers_stdin`, `test_a_remote_rc_background_job_can_write_into_a_result`,
    `test_a_late_sentinel_still_counts_as_started` and `test_a_call_holding_stdin_is_bounded` now
    run `[zshenv]` (zsh, `.zshenv`) and `[bashrc]` (bash, the `.bashrc` it reads for a shell sshd
    started), and `test_a_redirected_stream_is_reported` runs `ssh-zsh` and `ssh-bash` (with
    `SSH_CLIENT` set). The rc texts are now valid in both shells (`printf` for `print -rn`, `&` for
    `&!`). The mutants `ssh-stdin-before-start`, `ssh-fixed-grace-from-exit` and
    `ssh-stdin-wait-unbounded` now target both twins, and each kills both.
- **Remediation amendment 4 (2026-10-22, Geoffrey's ruling after review cycle 3).** Reopened with
  P1, P2, P4, P5 and P6 and landed in one commit with them; the end record, the SFTP route, the pins, the
  measurements and the mutants are in P2's fourth note.
- **Lock amendment (2026-10-22).** The first CI run of the remediation's head (55014e7) ran the
  suite green on Linux (`1538 passed` per era, 0 skipped) and failed only at the release gate's
  `bin/audit_lock.py`: advisories published since the last run against two locked packages,
  `certifi` 2026.1.4 (GHSA-6q48-6c7w-9gw2, fixed in 2026.10.19) and `opentelemetry-api` 1.39.1
  (GHSA-xcq7-p3pf-p88h, fixed in 1.45.1). `uv lock --upgrade-package` moved exactly those two
  (and dropped `importlib-metadata` and `zipp`, which the newer `opentelemetry-api` no longer
  needs); `pyproject.toml` is unchanged. Not this cycle's code: `develop` carries the same lock, so
  it goes in a commit of its own, which landing can keep or leave to `develop`. Locally after it:
  `uv lock --check`, the audit clean, `1538 passed` in each era, sandboxed `--help` exit 0.
- **Remediation amendment 5 (2026-10-22, Geoffrey's ruling after review cycle 4).** Reopened with
  P1, P2, P4 and P5 and landed in one commit with them; the classes, the pins, the decisions and the
  mutants are in P2's fifth note.
- **Verify:** frontmatter `P7.verify`.
- **Touches:** `AGENTS.md`, `issues/`, `ROADMAP.md`, any straggler text the sweep names.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: the worktree is clean, on `fix/safe-execution`, and `develop` is reachable.
3. Execute every `[ ]` in the phase (PLAN.md and research/ for detail).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (`set_phase.py`; note it in the commit body). STOP and
   escalate only on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
