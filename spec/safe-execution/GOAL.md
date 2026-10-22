# GOAL — Every tool passes exactly what it was given, under one interpreter

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** safe-execution
- **kind:** fix
- **appetite:** big

## Problem

The bespoke tools build their command lines by formatting strings, and users hit the resulting
correctness problems before anything else. All of them are **pre-existing**: every faulty line below
landed before the factory existed (`a2a46ff`, 2026-01-30; `5cbaae9`, 2026-02-02), and anchors are
confirmed against `origin/develop` `2b006fa`.

- **Writes are not byte-exact.** `write_file` (`tools/filesystem.py:154`) wraps the content in a
  heredoc (`:187`) that adds a final newline to every file, and binary content cannot be written at
  all. `sbatch` writes `script_content` the same way (`tools/slurm.py:114`). Found while submitting
  test jobs through the server: a job script the tool wrote hashed differently from the one sent, and
  the only reliable workaround was to strip the final newline and compare `git hash-object` before
  every submission.
- **Arguments are not passed literally.** `list_directory`, `read_file`, `write_file`, `sbatch` and
  the other bespoke tools interpolate the caller's strings into a command line (e.g.
  `tools/filesystem.py:60`, `:143`, `:145`), so a directory named `My Data` arrives as two arguments,
  `My` and `Data`, and a path beginning with `-` is read as an option. `run_command`'s `cwd` is
  interpolated unquoted in `ssh` mode (`executor/ssh.py:127`).
- **`~` names the wrong home in `delegate` mode.** `DelegatingExecutor` expands `cwd`
  (`executor/delegate.py:130`) and the transfer paths (`:168`–`:169`, `:191`–`:192`) with the server
  process's `HOME` before `sudo`, so `~` means the server user's home, not the mapped user's. The
  command still runs as the mapped user (no escalation); it is a correctness defect.
- **Job arguments cannot be passed cleanly.** `sbatch` has no parameter for the script's own
  arguments, and `extra_args` is one free-form string of `sbatch` options (`tools/slurm.py:54`,
  `:152`). The only route found was to append arguments to `script_path` after the path, which works
  by accident and stops working once `script_path` is passed as the one literal path it is meant to
  be. So the supported route has to land in the same cycle.
- **Command semantics depend on the user's login shell.** In `ssh` mode the remote `sshd` runs every
  command under the user's login shell; in `local` and `delegate` mode it is the server's `$SHELL`
  (`executor/shell.py:37`, `executor/delegate.py:85`, `:135`). The same `run_command` means different
  things to a bash user and a zsh user: under zsh, `[ a == a ]` fails, a bare `=word` expands to a
  command path, and an unmatched glob aborts the command.
- **Two timeout contracts.** `SSHExecutor.run` (`executor/ssh.py:133`) passes `timeout` to Fabric and
  lets Invoke's `CommandTimedOut` escape, while the local (`executor/shell.py:87`) and delegating
  (`executor/delegate.py:150`) executors return `exit_code=-1`, a value a real process killed by
  SIGHUP also returns. So `run_command("sleep 60", timeout=1)` returns a result in `local` mode and
  raises in `ssh` mode, the default. The protocol docstring (`executor/base.py:36`) promises a
  `CommandResult`. No test covers `timeout` in any backend.
- **A failed connection dies silently.** A wrong `--ssh-host` (an MCP server name used in place of
  the SSH alias, say) kills the server during `initialize`. The reason is printed only to stderr,
  which many clients discard, so the user sees a server that did not start and no reason why.

Security-relevant detail, if any, is tracked privately per `AGENTS.md` § Factory.

## Outcome / vision

**An argv-based executor.** Tools hand the executor a list of arguments, not a command string, and
every caller-supplied value reaches the remote command as exactly one literal argument. File content
never travels inside a command line, so a write is byte-exact whatever it contains. `run_command`
stays the one tool that takes a command string.

**One pinned interpreter.** Every command, `run_command` included, runs under bash invoked
explicitly, whatever the user's login shell. Whether it starts as a login shell, and how Lmod
initializes under each choice, is measured read-only on a real login node during plan research; the
contract here is the observable parity, not the flag.

**One timeout contract**: a timed-out command returns a `CommandResult` in every mode, marked so it
cannot be mistaken for an exit status or a signal death.

**`sbatch` gains `args`**, and `extra_args` becomes a list, so job arguments and `sbatch` options both
have a supported, literal route.

**Startup says why it failed**, to the user, not only to a stderr the client may discard.

The `Executor` protocol changes, so all three backends move together (`AGENTS.md` § Invariants §2).
The `file_*` family (`issues/tool-surface.md`) and the `job_*` family (`issues/slurm-capability.md`)
build on this API afterwards.

## Acceptance criteria (the contract)

Broken today → fixed. Tests run without a cluster: the `ssh` path against a stubbed connection
(`bin/sandbox.sh` cannot reach one by design), `delegate` without invoking `sudo`.

- **R1** — The `Executor` protocol SHALL accept a command as a list of arguments, and every tool
  other than `run_command` SHALL call the executor in that form; `run_command` SHALL be the only
  caller that passes a command string. *(Today every tool formats a string.)*
- **R2** — WHEN any tool receives a string parameter (every one except `run_command`'s `command`)
  whose value contains spaces, quotes, `$`, `*`, a newline, any other shell metacharacter, or a
  leading `-`, it SHALL operate on exactly that value as one literal argument, with no part of it
  interpreted by a shell or parsed as an option (a leading `~` aside, per R3); a test SHALL drive every such parameter of every
  tool with these values against a fixture tree, in all three modes. *(Today `My Data` arrives as
  two arguments.)*
- **R3** — WHEN a path or `cwd` parameter begins with `~` or `~/`, it SHALL resolve to the home
  directory of the user the command runs as — in `delegate` mode the mapped user, never the server
  process's `HOME` — and no other expansion SHALL apply; a `delegate`-mode test SHALL pin `cwd`
  and transfer paths. *(Today `delegate` expands `~` to the server's home.)*
- **R4** — WHEN `write_file` writes `content`, or `sbatch` writes `script_content`, the bytes on disk
  SHALL equal the bytes sent — including content with no final newline, empty content, and content
  containing any line whatsoever — in overwrite and append mode, verified by hash in all three
  modes. *(Today a newline is added.)*
- **R5** — `write_file` SHALL accept `encoding`, with values `"utf-8"` (the default: `content` is
  text, written as its UTF-8 bytes) and `"base64"` (`content` is decoded and the decoded bytes are
  written byte-exactly); IF `content` is not valid base64 under `"base64"`, or `encoding` has any
  other value, THEN the tool SHALL fail with an error naming the parameter and SHALL leave the
  target untouched. *(Today binary content cannot be written.)*
- **R6** — `sbatch` SHALL accept `args: list[str]`, each element passed to the job script as one
  argument after its path; `script_path` SHALL be passed as one literal path; and `extra_args` SHALL
  be `list[str]`, each element passed to `sbatch` as one argument, with a string value refused by
  schema validation in an error naming the expected type. *(Today job arguments ride on
  `script_path` by accident and `extra_args` is one free-form string.)*
- **R7** — Every command SHALL run under bash, invoked explicitly, regardless of the user's login
  shell or the server's `$SHELL`: WHEN the same `run_command` is issued for a user whose login shell
  is bash and one whose login shell is zsh, `[ a == a ]` SHALL succeed for both (pinned by a test),
  and `module` SHALL behave the same for both (shown on a real login node, read-only, within the
  dogfood class, and recorded in the cycle's evidence; never part of `verify`). *(Today `[ a == a ]` fails for a zsh user.)*
- **R8** — WHEN a command exceeds its `timeout`, all three executors SHALL return a `CommandResult`
  and never raise, carrying whatever stdout and stderr were captured before the deadline, reporting
  failure, and carrying a timeout marker that no completed or signal-killed command can produce, so
  a timeout is distinguishable from a SIGHUP death; tests SHALL cover the timeout path in all three
  modes. *(Today `ssh` raises and the others return `-1`.)*
- **R9** — IF the SSH connection cannot be established at startup (unreachable host, unknown alias,
  refused key), THEN the server SHALL exit nonzero with a message naming the host and the cause on
  stderr, and on stdio the first request a client sends SHALL receive an MCP error response carrying
  the same message, in both protocol eras, rather than the server dying with no reason the client
  shows.
- **R10** — The `Executor` protocol docstring (`executor/base.py`) and `AGENTS.md` § Invariants §2,
  §3 and the footgun list SHALL describe the argument-list protocol, the pinned interpreter and the
  timeout contract (including any new sentinel, per §3's below-`-1000` rule), in the same commit as
  the code; every text that today describes the asymmetry or the server's `$SHELL` as the state of
  the world SHALL be updated.

## Non-goals (no-gos)

- The `shlex` guard rails on `run_command` (tool-surface, Part I): its `command` stays a free string,
  run under the pinned interpreter.
- New tools, and new parameters beyond `sbatch.args` and `write_file.encoding`.
- Renames: tools keep their names and parameters keep theirs (`file_*`, `job_*` are later cycles).
- A matching binary read: `read_file` gains no encoding; it belongs to tool-surface's `file_*`
  family.
- The hosted/HTTPS work.
- Expansion beyond a leading `~`/`~/` in tool paths (`~name`, `$VAR`, globs): passed literally.
- Persistent shell state between calls, and per-call `modules`/`env` composition.

## Clarifications

- **Q:** One cycle, or a pilot plus follow-ups? — **A:** One cycle. The roadmap designed this as one
  executor change, and the seed's criteria sit at the circuit-breaker threshold; more than 10 R-IDs
  would have stopped shaping for a split. Shaped at 10 (resolved 2026-10-17).
- **Q:** What happens to the two seeds? — **A:** `issues/safe-execution.md` and
  `issues/executor-timeout-asymmetry.md` both take `status: adopted:safe-execution`, as do their
  `ROADMAP.md` `**Seed:**` markers, in this shape commit (resolved 2026-10-17).
- **Q:** Which timeout contract — always return a `CommandResult`, or always raise a shared
  exception? — **A:** always return (proposed at shaping; the foreman's ruling at the GOAL gate). (1) It is the protocol's
  documented contract and two of three backends already honour it, so the coupled core moves least.
  (2) `run_command` hands the `CommandResult` straight to the client, so the partial output of a
  long command that timed out reaches the user; an exception becomes a tool error and drops it.
  (3) Every tool already treats a non-zero exit as failure, so a timeout is reported by paths that
  exist, and no tool can crash by forgetting a `try`. The case for raising — forcing callers to
  notice — is met by the distinct marker, which cannot be read as an exit status. Whether the marker
  is a sentinel below `-1000`, an explicit field, or both is the plan's (resolved 2026-10-17).
- **Q:** How does `sbatch` take job arguments, and what form does `extra_args` take? — **A:**
  `args: list[str]`, passed after the script path. `extra_args` becomes `list[str]` only, with no
  string form: agents re-read the schema each session, and a string is refused by validation naming
  the type. The landing lists both as surface changes (resolved 2026-10-17).
- **Q:** How far does byte-exactness go? — **A:** Text with no final newline, empty content, and
  binary content sent through an explicit encoding parameter. The GOAL names the parameter
  (`encoding`) and its values (`"utf-8"`, `"base64"`); the plan picks the transport. A matching
  binary read is a non-goal (resolved 2026-10-17).
- **Q:** What does `~` in a `delegate`-mode `cwd` mean? — **A:** The mapped user the command runs
  as, never the server's `HOME`, pinned by a test (resolved 2026-10-17). Shaping extends the same
  rule to every path parameter, since `delegate`'s transfer paths share the defect.
- **Q:** Which interpreter, and is it a login shell? — **A:** Bash, invoked explicitly. Login versus
  non-login is the plan's research, measured read-only on a real login node; the GOAL states the
  observable (`[ a == a ]` and `module` behave the same for bash and zsh login users), not the flag
  (resolved 2026-10-17).
- **Q:** Do startup errors (the seed's R7) stay in scope? — **A:** Yes, as R9 (resolved 2026-10-17).
- **Q:** What is out of scope? — **A:** The `shlex` guard rails on `run_command`, new tools, renames,
  and the hosted/HTTPS work (resolved 2026-10-17).
- **Q:** The three questions shaping left for the gate? — **A:** (1) `~name` stays literal: a path
  passes exactly as given, and a leading `~`/`~/` is the one documented exception. (2)
  `sbatch.script_content` is in R4's byte-exact scope; it is the write the seed's evidence came from.
  (3) R9 names the client's first request rather than `initialize`, since revision 2026-07-28 has
  no handshake. R7's `module` evidence is gathered read-only within the dogfood class
  (`repo/rcac-mcp.md` in the operator's KB) and recorded with the cycle; phase `verify` never
  reaches a cluster (foreman, GOAL gate, 2026-10-17).
- **Q:** The plan's four questions for the PLAN/TECH gate? — **A:** (1) `extra_args` on `squeue`,
  `scancel`, `sacct` and `sinfo` becomes `list[str]`, like `sbatch`'s: each element is one literal
  argument that reaches the program as an option on purpose, never shell-interpreted and never split,
  and a string is refused by schema validation naming the type. That is R2's carve-out for these five
  parameters; the landing lists all five surface changes. (2) The served-surface fixture follows by a
  scripted hand-edit limited to the named deltas, each listed in its commit body and named in
  `AGENTS.md` § Testing and §9; the long-term method stays with
  `issues/surface-fixture-after-a-surface-change.md`. (3) For a server-side `local_path`, `~` is the
  home of whoever touches the file: the mapped user in `delegate`, the server process's user in
  `ssh` and `local` (R3's "user the command runs as", applied to the server side). (4) Under
  `encoding="base64"`, ASCII whitespace (space, tab, CR, LF) is ignored, per RFC 2045 line
  wrapping; any other non-alphabet character, or bad padding, fails per R5. A test pins both sides
  (foreman, PLAN/TECH gate, 2026-10-17).
- **Q:** How does a result become complete, and how does content reach an ssh command, after
  review cycle 3 reached the bound? — **A (Geoffrey, at the escalation, 2026-10-22):** by an
  end-of-output marker, not a timing rule. The launcher runs the command, then prints a per-call
  end marker on both streams once it exits, preserving its exit status and any signal death; a
  result is complete exactly when both markers have arrived, however slow the link, and nothing a
  startup file or the command leaves running can cut a result short or hold a call past its
  markers. Timeouts (R8) still bound everything. Over ssh, file content (`write_file`,
  `sbatch` `script_content`) travels by SFTP to a private temporary file that the program moves
  into place, so nothing the content needs rides stdin and no remote startup file can read it.
  One more debate review follows, as a named exception to the bound; if it is not clean, the
  cycle stops for him.

## Related materials

- Seed: [`issues/safe-execution.md`](../../issues/safe-execution.md)
- Seed (absorbed): [`issues/executor-timeout-asymmetry.md`](../../issues/executor-timeout-asymmetry.md)
- `ROADMAP.md` → *Every tool passes exactly what it was given*
- Built on: [`spec/restore-test-coverage/`](../restore-test-coverage/) — `tests/test_executors.py`
  pins today's `sudo … <$SHELL> -c` argv rows, which this cycle rewrites on purpose; the planted
  violations in `tests/mutants.py` anchor on today's executor and tool text and are re-planted in
  the same cycle (`AGENTS.md` § Testing).
- Builds toward: [`issues/tool-surface.md`](../../issues/tool-surface.md),
  [`issues/slurm-capability.md`](../../issues/slurm-capability.md),
  [`issues/agents-d-instructions.md`](../../issues/agents-d-instructions.md).
- Source: `src/rcac_mcp/executor/{base,shell,delegate,ssh}.py`,
  `src/rcac_mcp/tools/{filesystem,slurm,rcac,transfer,shell}.py`.
