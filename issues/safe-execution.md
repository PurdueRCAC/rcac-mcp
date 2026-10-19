---
status: adopted:safe-execution
kind: fix
appetite: big
lane: public
---

# Every tool passes exactly what it was given, under one interpreter

## Problem

The bespoke tools build their command lines by formatting strings, and the result is a family of
correctness problems that users hit before they hit anything else:

- **Writes are not byte-exact.** `write_file` (`tools/filesystem.py:154`) adds a trailing newline to
  every file it writes, and binary content cannot be written at all. Found while submitting and
  watching test jobs through the server: a job script written by the tool hashed differently from
  the one sent, and the only reliable workaround was to strip the final newline and compare
  `git hash-object` before every submission.
- **Paths with spaces or special characters are mishandled.** `list_directory`, `read_file`,
  `write_file` and `sbatch` do not pass the caller's path through as one literal argument, so a
  directory named `My Data` arrives as two arguments, `My` and `Data`.
- **Job arguments cannot be passed cleanly.** `sbatch` has no parameter for the script's own
  arguments, and `extra_args` is one free-form string of `sbatch` options. The only route found was
  to append arguments to `script_path` after the path, which works by accident — and stops working
  the moment `script_path` is treated as the one literal path it is meant to be, which is what this
  cycle does. So the `args` parameter has to land in the same cycle.
- **Command semantics depend on the user's login shell.** In `ssh` mode the remote `sshd` runs every
  command under the user's login shell; in `local` mode it is the server's `$SHELL`. The same
  `run_command` means different things to a bash user and a zsh user: under zsh, `[ a == a ]`
  fails, a bare `=word` expands to a command path, and an unmatched glob aborts the command.
- **Two timeout contracts.** `SSHExecutor.run` lets Invoke's `CommandTimedOut` escape while the
  local and delegating executors return `exit_code=-1`, which also means "killed by SIGHUP" (see
  [`executor-timeout-asymmetry.md`](executor-timeout-asymmetry.md), absorbed here).
- **A failed connection dies silently.** A wrong `--ssh-host` (an MCP server name used in place of
  the SSH alias, say) kills the server during `initialize`. The reason is printed only to stderr,
  which many clients discard, so the user sees a server that did not start and no reason why.

Security-relevant detail, if any, is tracked privately per `AGENTS.md` § Factory.

## Why it was deferred

Not deferred — a roadmap cycle. It sits in Part 0 because every later cycle builds tools on the
executor, and a tool family built on string formatting would have to be rebuilt once the executor
takes argv. It changes the `Executor` protocol, so all three backends move together.

**Pre-existing:** yes — every item above is on `main` today.

## Outcome / vision

**An argv-based executor API.** Tools hand the executor a list of arguments, not a command string;
the executor quotes them (`shlex.join`) and puts `--` before every caller-supplied path where the
command accepts it. File content travels over SFTP in `ssh` mode and over stdin in `local` and
`delegate` mode, never inside a command line, so a write is byte-exact whatever it contains.

**One pinned interpreter.** Every command runs under one named interpreter (bash, invoked
explicitly), whatever the user's login shell. Login versus non-login startup, and how Lmod
initializes under each, are measured on a real cluster in plan research rather than assumed: the
answer decides whether `module` exists in a non-interactive call.

**One timeout contract**, chosen at shaping as
[`executor-timeout-asymmetry.md`](executor-timeout-asymmetry.md) lays out, with "timed out"
distinguishable from "killed by a signal".

**`sbatch` gains `args`**, a list passed after the script path, so job arguments have a supported
route before the old one closes. `extra_args` becomes a list in the same move.

**Startup says why it failed.** An unreachable host, an unknown SSH alias or a refused key ends
startup with a message naming the host and the reason, on stderr and, where the transport allows,
in the MCP error the client shows.

`run_command` stays what it is — the one tool that takes a command string — but it runs under the
pinned interpreter like everything else.

## Sketch of the acceptance criteria

- **R1** — The `Executor` protocol SHALL accept a command as a list of arguments, and every bespoke
  tool SHALL use that form; only `run_command` SHALL pass a command string.
- **R2** — WHEN a bespoke tool receives a path containing spaces, quotes, `$`, `*`, a newline or a
  leading `-`, it SHALL operate on exactly that path, verified against a fixture tree of such names.
- **R3** — WHEN `write_file` writes content, the bytes on disk SHALL equal the bytes sent — including
  content with no final newline, empty content and binary content — verified by hash.
- **R4** — `sbatch` SHALL accept `args: list[str]`, passed to the script after its path, and
  `script_path` SHALL be passed as one literal path.
- **R5** — Every command SHALL run under the one pinned interpreter regardless of the user's login
  shell; a test SHALL show `[ a == a ]` behaving identically with a bash and a zsh login shell.
- **R6** — WHEN a command exceeds its `timeout`, all three executors SHALL behave identically, per the
  contract chosen at shaping, and a timeout SHALL be distinguishable from a signal death (carried
  from `executor-timeout-asymmetry` R1–R3).
- **R7** — IF the SSH connection cannot be established at startup, THEN the server SHALL exit
  nonzero with a message naming the host and the cause, rather than dying inside `initialize`.
- **R8** — `AGENTS.md` § Invariants §2 and §3 SHALL describe the new protocol and timeout contract in
  the same commit as the code.

## Notes

- Tests run without a cluster: the SSH path against a stubbed connection, as `bin/sandbox.sh`
  cannot reach one by design. The interpreter and Lmod measurements are plan research on a real
  login node, read-only.
- The `file_*` family in [`tool-surface.md`](tool-surface.md) and the `job_*` family in
  [`slurm-capability.md`](slurm-capability.md) are built on this API; doing this first means they
  are built once.
- Related: [`executor-timeout-asymmetry.md`](executor-timeout-asymmetry.md) (absorbed),
  [`spec/restore-test-coverage/`](../spec/restore-test-coverage/) (the executor tests land on its
  suite; landed on `develop` 2026-10-16),
  [`agents-d-instructions.md`](agents-d-instructions.md) (reads through this executor at startup).
- Found by: roadmap revision, 2026-10-16 (out of cycle).
- 2026-10-16: in `delegate` mode, `DelegatingExecutor.run` expands `cwd` with the server process's
  `HOME` before `sudo`, so `~` names the server user's home rather than the mapped user's. The
  command still runs as the mapped user (no escalation); it is a correctness defect in how an
  executor resolves `cwd`. The redesign resolves `~` as the user the command runs as, or rejects it,
  and pins that in a test. Found while shaping `restore-test-coverage`, which leaves it unpinned.
- 2026-10-16: `restore-test-coverage` pins today's launch in `tests/test_executors.py`: the exact
  `sudo -n -H -u <user> <$SHELL> -c <command>` argv rows, which this cycle rewrites on purpose, and
  the `Executor` protocol rows for all three backends (the `ssh` row fakes
  `rcac_mcp.executor.ssh.Connection`, as `tests/test_exec_mode.py` does). The planted violations in
  `tests/mutants.py` anchor on today's text in `executor/delegate.py`, `executor/shell.py`,
  `executor/ssh.py` and `tools/transfer.py` (and `tools/slurm.py`, `tools/rcac.py`): an edited
  anchor fails `test_mutation_anchor_applies[<case>]`, and the case is re-planted against the new
  code and re-run in the same cycle (`AGENTS.md` § Testing). The timeout path and `cwd` expansion
  are left unpinned there; they are this seed's to pin.
