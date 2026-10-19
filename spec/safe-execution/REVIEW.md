# REVIEW — Every tool passes exactly what it was given, under one interpreter

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 944a132a1300d2c9e0eac15e74c460444b102971  ·  **Base:** develop (`2b006fa`)  ·  **Date:** 2026-10-18
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Variant:** debate (the diff touches the profile's coupled core): two blind reviewers, one arguing
  ship and one block, each in its own clone of the reviewed commit; reconciled here.
- **Contract drift:** `GOAL.md` moved after the shape commit only by `ce0b7af`, the foreman's
  PLAN/TECH-gate Clarification (R-IDs unreworded); graded against it.

## Verification run

Both reviewers, each in its own clone:

- The profile's full `verify`: `uv lock --check`; `1052 passed` in the handshake era and `1052 passed`
  in revision 2026-07-28; `bin/sandbox.sh uv run rcac-mcp --help` exit 0. Nothing hollow.
- Literal values in all three modes (CR, tab, ESC, emoji, 40 KB, `'\''`, `$'\n'`, `~`, `-`, `!!`,
  `${HOME}`, `$(touch …)`, `-n`, `*`): byte-identical, nothing executed. `UserPath`/`cwd` forms
  (`~`, `~/a b`, `~//z`, `-`, `~name`, `$HOME`, `~/My $Data`) resolved or stayed literal as the GOAL
  states.
- `write_file` in three modes: empty, no final newline, `RCAC_MCP_EOF`, CRLF, NUL, shell text;
  overwrite and append byte-exact; base64 over all 256 byte values with 76-column wraps exact;
  malformed base64 and `encoding='latin-1'` refused naming the parameter, target untouched.
- Startup separation, locally: a `.bash_profile` printing to both streams, aliasing `printf`/`exec`,
  exporting a `cat` function, `set -euxv`, `exec >/dev/null 2>&1 </dev/null` changed no result;
  `exit 3` → `-1002`; chatty rc files under zsh, tcsh, bash and sh login shells dropped.
- Timeouts in three modes: partial output kept with `-1001`; TERM-ignoring command returns in
  under 8 s; SIGHUP `-1`, SIGTERM `-15`, `exit 255` → 255.
- R9 on the raw wire, both eras: `--ssh-host 127.0.0.1:1` and an unresolvable alias → exit 5, one
  stderr line naming host and cause, every request answered `-32603` with it; notifications, garbage,
  batch arrays and float ids skipped; EOF ends the process.
- Schema: string `extra_args`/`args` refused, "Input should be a valid list", naming the parameter.
- Concurrency: 24 concurrent stdin-and-argv runs per executor, no cross-talk; `context.py` and
  `middleware.py` untouched; the sentinel nonce is per call.
- Mutants: the ship reviewer killed 23 of 23 (behavioural kills re-confirmed with text pins
  deselected); the block reviewer killed 14 of 17 (one equivalent, one killed only by an argv pin,
  one coverage observation — below).
- A second checkout of `origin/develop` for regression comparison (the block reviewer).

## Requirement → evidence matrix

| R-ID | Status | Verified how | By |
|------|--------|--------------|----|
| R1 | ✅ | only `tools/shell.py:45` passes a string; fixed-script registry; literal-text mutant killed | both |
| R2 | ✅ | `test_literal_args` matrix in three modes; hostile-value drives | both |
| R3 | ✅ | delegate `cwd`/transfer rows; `UserPath` drives in three modes; start-in-`$HOME` mutant killed | both |
| R4 | ❌ finding 2 | byte-exact rows and drives in three modes, except an rc file that reads stdin over ssh | both |
| R5 | ✅ | refusal drives; both base64 mutants killed | both |
| R6 | ✅ | schema refusals; `test_sbatch` argument order; `--` mutants killed | both |
| R7 (test half) | ✅ | `test_bash_runs_whatever_the_login_shell[bash|zsh|sh|tcsh]`; non-login mutant killed | both |
| R7 (`module` half) | ✅ | `research/01-cluster-shell.md` → "Built-launcher re-measurement": identical module state across six launchers on Gautschi and Negishi, read-only, within the dogfood class, through the launcher as built | orchestrator |
| R8 | ✅ | timeout drives in three modes; HUP `-1` vs `-1001`; four timeout mutants killed | both |
| R9 | ✅ | raw-wire drives, both eras | both |
| R10 | ❌ finding 4 | docstrings and § Invariants §2–§5 updated, except the separation overclaim | both |

Scope beyond the R-IDs (no defect, listed for the landing): delegate transfers moved from root
`cp`+`chown` to the mapped user (security-positive, §5 updated); empty transfer paths refused;
lone surrogates refused naming the parameter; empty optional scalars mean "not given" (foreman
ruling); `list_directory` paths read from `$PWD`; five seeds filed; and `average_wait`'s `-p` became
`--partition`, unverified (finding 6).

## Findings

### [MEDIUM/CONFIRMED] A startup file's `cd` moves where commands start (ship F1)

- **Where:** `src/rcac_mcp/executor/ssh.py:261` (and `executor/shell.py:71`); `ssh.py:249`'s docstring.
- **Failure scenario:** with a login bash, a `.bash_profile` containing `cd ~/elsewhere` makes ssh
  commands start in `elsewhere`: `write_file('rel.txt')` lands in `elsewhere/rel.txt`, `read_file`
  reads it there, an SFTP `put(…, 'up.txt')` lands in the home, and `pwd` reports `elsewhere`.
  Relative paths and the default `list_directory()` follow the profile. On `develop`, ssh commands
  never read `.bash_profile`. Contradicts "it starts in the login home, as SSH commands do".
- **Evidence:** loopback ssh drive with that profile (ship reviewer's clone).
- **Touches:** a high-blast-radius path (`executor/ssh.py`); R3's neighbourhood (`~` still resolves
  correctly via `$HOME`).

### [MEDIUM/CONFIRMED] Over ssh, the remote login shell's rc can consume the caller's stdin (ship F2)

- **Where:** `src/rcac_mcp/executor/ssh.py:272-277`; the claims at `executor/base.py:51` and
  `AGENTS.md` §2.
- **Failure scenario:** stdin is sent as soon as the exec starts, before the bootstrap parks the
  streams, so a zsh `.zshenv` containing `read -r line` eats the first line:
  `write_file('~/job.sh', '#!/bin/bash\necho hi\n')` reports success and the file holds
  `b'echo hi\n'`. With an empty `.zshenv` it is exact. On `develop` the content travelled in the
  command string.
- **Evidence:** loopback ssh drive with zsh as the login shell.
- **Touches:** R4; a high-blast-radius path; §2 (*[human gate]*).

### [MEDIUM/CONFIRMED] A background job started by a startup file holds the call open (block 1)

- **Where:** `src/rcac_mcp/executor/launch.py:69` (`PARK`), `:88` (`STAGE`).
- **Failure scenario:** every command now runs in a login bash, and anything a startup file starts
  in the background inherits the parked caller descriptors 57–59, so the call waits until it exits.
  `.bash_profile` = `sleep 4 &`, `LocalShellExecutor().run('echo hi')`: HEAD `'hi\n' '' 0` after
  4.03 s; `origin/develop` 0.01 s. A daemonizing `eval "$(ssh-agent -s)"` does not hold it.
- **Evidence:** `probes/p2.py` under `bin/sandbox.sh`, HEAD and an `origin/develop` checkout.
- **Touches:** a high-blast-radius path (`launch.py`). A regression against `develop`; the cycle
  seeded it (`issues/startup-background-jobs.md`), which does not take it out of scope.

### [MEDIUM/CONFIRMED] The texts promise separation the code cannot give over ssh (block 2)

- **Where:** `AGENTS.md` §3 ("Nothing the login initialisation prints reaches a result", *[human
  gate]*), the served `run_command` docstring, the `Executor` docstring, `README.md:41`.
- **Failure scenario:** over ssh with a zsh login shell, `.zshenv` = `(sleep 1; echo RC-LEAK-OUT;
  echo RC-LEAK-ERR >&2) &!`: `run('sleep 2; echo command-output')` → stdout
  `'RC-LEAK-OUT\ncommand-output\n'`, stderr `'RC-LEAK-ERR\n'`, exit 0; the argv form leaks the same,
  and the leak would corrupt `read_file` content. Pre-existing in kind, but §3 now states the
  opposite and cites only `timeout-process-cleanup` as an exception.
- **Evidence:** a temporary probe test on the loopback `serve` fixture (deleted afterwards).
- **Touches:** R10; §3 (*[human gate]*).

### [LOW/PLAUSIBLE] The program size check ignores the ssh bootstrap (both reviewers)

- **Where:** `src/rcac_mcp/executor/launch.py:57-59` (`MAX_PROGRAM`).
- **Failure scenario:** at 98,304 bytes the ssh exec string is 131,245 bytes and its base64 word
  131,072; programs of 98,173–98,304 bytes pass validation but exceed Linux's 131,072-byte
  per-argument limit inside sshd, returning `-1002` instead of the promised `ValueError`.
- **Evidence:** computed from the HEAD source (`len(BOOTSTRAP)` = 173); macOS has no per-argument
  limit, so not observed.
- **Triage (foreman, under the autonomy decision):** *fix*. The arithmetic is exact and both
  reviewers reached it independently.

### [LOW/PLAUSIBLE] `average_wait` now passes `--partition`, unverified against the site script

- **Where:** `src/rcac_mcp/tools/rcac.py:218-224`.
- **Failure scenario:** if the site's `average_wait` accepts only `-p`, every call with a partition
  fails on the clusters. No R-ID asks for the change.
- **Triage (foreman):** *fix*. Verify the script's option form read-only on a login node within the
  dogfood class, and keep `--partition` only if it is accepted.

### Coverage observation

- `ssh-timeout-no-drain` (block reviewer) survives: the drain after a timeout is defence in depth.
  It sits in the code finding 3 reworks, so the remediation pins it rather than seeding it.

## Routing (each as its class)

1. **No startup file, in any mode, changes where a command starts.** ssh starts in the login home,
   delegate in the mapped user's home, local in the server's working directory, whatever a
   profile does; the docstring says so.
2. **No startup file, in any mode, consumes or observes the caller's stdin.** Over ssh, nothing is
   sent until the program has signalled it runs; local and delegate keep stdin parked.
3. **A call's duration and result depend only on the command.** Processes a startup file leaves
   running never hold a call open: once the command's exit status is known, the backend drains
   for a bounded grace and returns, in all three modes, the drain after a timeout pinned too.
4. **Every text that promises separation states its exception exactly**: over ssh, a background
   job started by the remote login shell's own rc can write into a result while the command runs
   (`issues/startup-background-jobs.md`); a test pins the documented exception.
5. **Every size limit is checked against the bytes that actually cross it**, per mode.
6. **`average_wait`'s option form matches the site script**, verified read-only.

## Human-gate triggers

- **Triggered:** findings 1–3 sit in high-blast-radius paths (`executor/ssh.py`,
  `executor/launch.py`), and findings 2 and 4 touch §2 and §3 (*[human gate]*). Under the
  autonomy decision (`decision/2026/2026-10-16-cluster-mcp-autonomous.md` in the operator's KB) the
  foreman is judge at human gates; Geoffrey was told of this trigger on 2026-10-18 and left it to
  the foreman. **Foreman's sign-off for remediation:** none of the four lets a command run as
  another user, on another machine, or with the caller's arguments interpreted; each is a
  start-directory, stdin, latency or documentation defect, routed above. The trigger holds until
  publish: a clean later cycle still needs this sign-off recorded before landing.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run.

## Review cycle 2 — changes-requested (2026-10-18)

- **Reviewed commit:** b32778d31d93b7e055e53ec04bb772470f52b903 · **Mode:** a fresh blind pass over the
  full spec-excluded diff, debate variant again (ship and block, separate clones).
- **Cycle 1's routing:** classes 1, 2, 4, 5 and 6 hold (start directory in every mode including a
  profile `set --`/`shift`, stdin held over ssh, the stated exception and its pin, per-wire size
  limits, `average_wait --partition` against `sacct`). **Class 3 regressed**: the bounded post-exit
  drain the foreman routed cuts the command's own output still in flight over ssh (finding 1). The
  routing named a fixed grace from the exit status; the class should have been "return once only
  holders remain", which a fixed grace cannot tell apart from a slow link.

### Verification run

- Both reviewers: the full `verify` in both eras (`1092 passed` each, sandboxed `--help` exit 0).
- Ship: an end-to-end drive over raw stdio with a hostile path (`~/My Data/-x $(touch PWNED) \`id\`
  *.txt;|&\nnl`), content with CRLF, NUL and `RCAC_MCP_EOF`: byte-exact, nothing executed; base64,
  empty content, empty append, schema refusals, the recorded `sbatch` argv, job-script mode 0755;
  R9 on the raw wire with three ids answered; startup files aliasing `exec`/`printf`/`command`/`cd`,
  exporting functions over builtins, `SHELLOPTS` with `set -eu`, `set -- a b c; shift`: clean;
  timeouts and the drain (a setsid child bounded at 3 s; `(sleep 4; echo late) & exit 7` → 7 in
  1.06 s); ssh at the size limit under bash, zsh and tcsh; 8 MB stdin; 8 concurrent calls isolated;
  17 planted mutants sampled, all killed, plus three of its own.
- Block: a throttled sshd model (`probes/slow_link2.py`: eager pipe reads, a 2 MiB window, one shared
  link, exit-status on reap) against HEAD and an `origin/develop` checkout; raw-wire R9; the largest
  ssh exec string under sh, bash, zsh, ksh, dash, csh and tcsh.

### Requirement → evidence matrix (cycle 2)

| R-ID | Status | Note |
|------|--------|------|
| R1 | ✅ | only `run_command` passes a string |
| R2 | ✅ | matrix in three modes; hostile end-to-end drive |
| R3 | ✅ | delegate `cwd`/transfer rows; mutants killed |
| R4 | ❌ finding 1 | writes byte-exact; reads over ssh truncate on a slow link |
| R5 | ✅ | refusals; `base64-unvalidated` killed |
| R6 | ✅ | argv; schema refusal |
| R7 (test half) | ✅ | `test_bash_runs_whatever_the_login_shell` |
| R7 (`module` half) | ✅ | unchanged evidence (orchestrator, cycle 1) |
| R8 | ✅ | timeout rows in three modes (coverage gaps below) |
| R9 | ✅ | raw wire, both eras |
| R10 | ❌ findings 1, 3, 4 | the §3 grace text, the separation promise and the stdin "times out" claim overreach |

### [CRITICAL/CONFIRMED] Over ssh, a fixed post-exit grace truncates the command's own output (block 1)

- **Where:** `src/rcac_mcp/executor/ssh.py:130-136` (`_collect`), and the §3 text describing the holder.
- **Failure scenario:** `_collect` returns `GRACE` (1 s) after `exit-status` whether or not EOF has
  come; sshd sends `exit-status` on reaping the child while the command's output can still be in the
  channel, up to the window. On a link slower than about 2 MiB/s a large result is cut short and
  reported exit 0, empty stderr. **A regression:** `origin/develop` (Fabric's `run`) waited for EOF.
- **Evidence:** `read_file` of 4 MiB at 10 Mbit/s → 2,990,080 bytes, no error; at 20 Mbit/s →
  3,833,856; at 4 Mbit/s a 1 MiB file → 434,144 bytes; `run_command('cat big.txt; echo DONE >&2')`
  → 442,336 bytes with `exit_code=0`, `stderr='DONE\n'`. The same model against `origin/develop` at
  10 Mbit/s → 4,194,304 bytes, complete.
- **Touches:** a high-blast-radius path; §3 (*[human gate]*): a numbered invariant section, so
  CRITICAL per the rubric; R4.

### [HIGH/CONFIRMED] Same root: a command that ran is reported as never started (block 2)

- **Failure scenario:** with a 1 MiB chatty `.zshenv` (the size the suite's own chatty home uses)
  on a 4 Mbit/s link, `touch ~/ran` returns `-1002` "the command did not start" while `~/ran`
  exists. `sbatch` would report a failed submission for a submitted job, and a retry submits it
  twice. A variant attaches a false "login initialisation redirected the command's stdout" note.
- **Evidence:** `probes/slow_link2.py`.
- **Touches:** `executor/ssh.py`; §3.

### [HIGH/CONFIRMED] Spec ids in test docstrings (ship 1)

- **Where:** `tests/test_startup.py:173`, `:283` ("differs from R9's report"), introduced in `1077c2c`.
- **Evidence:** `git grep -nP '\b[RP][0-9]{1,2}\b' -- src/ tests/*.py`.
- **Touches:** § Conventions (HIGH per the rubric).

### [MEDIUM/CONFIRMED] A startup file's `IFS` breaks the function export for `run_command` (both)

- **Where:** `src/rcac_mcp/executor/launch.py:101` (`export -f $fns`, unquoted).
- **Failure scenario:** `IFS=:` or `IFS=` left by `.bash_profile`, with two profile functions:
  `export: ml\nmymod: not a function … mymod: command not found`, exit 127, in local, delegate and ssh;
  profile functions (Lmod's `module` among them) are lost to scripts and a launcher error lands in
  every result. Contradicts `launch.py:15-17` and §3 ("nothing after the login initialisation rides
  on state a startup file can change").
- **Touches:** a high-blast-radius path; §3.

### [MEDIUM/CONFIRMED] Tools that send stdin pass no timeout, so the documented time-out never comes (both)

- **Where:** `src/rcac_mcp/tools/filesystem.py:203` (`write_file`), `tools/slurm.py:163` (`sbatch`
  `script_content`); the claims in README, `AGENTS.md` §2/§3 and `executor/base.py:88-91`.
- **Failure scenario:** over ssh with `.zshenv: read -r line`, `write_file` and `sbatch` are still
  running after 30 s and never return (nothing written); with an explicit `timeout=2` the same call
  returns `-1001` as documented.
- **Touches:** `executor/ssh.py`'s neighbourhood; §2 (*[human gate]*).

### [LOW/PLAUSIBLE] The ssh bootstrap's POSIX stage can read `~/.bashrc` (both)

- **Where:** `src/rcac_mcp/executor/launch.py:114`.
- **Failure scenario:** on a bash built with `SSH_SOURCE_BASHRC` whose login shell is bash, the
  `exec`'d `bash --posix -c` stage sees `SSH_CLIENT` and sources `~/.bashrc` before parking; on
  bash 5, which binds `$1` first, a `.bashrc` `set --`/`shift` empties the program (fails closed,
  `-1002`), and an rc background job there falls outside the stated exception. An sshd-like
  simulation logged the rc running in both `posix=off` and `posix=on` stages; not reproducible on
  bash 3.2.
- **Triage (foreman):** *fix*, and pin it with an sshd-like environment (`SSH_CLIENT` set).

### Coverage observations (pinned in the remediation, not seeded)

- Dropping SIGKILL from `run_process` (`launch.py:305`) survives every timeout row; a
  TERM-ignoring command then takes 30 s instead of about 2 s.
- A post-exit grace of 0 in `ssh.py:132` survives the full suite: nothing pins output arriving after
  the exit status over ssh, which is how finding 1 shipped.

## Routing (each as its class) — cycle 2

1. **A result is complete unless only holders remain.** Over every wire, the call keeps reading
   while the command's own output is still arriving after the exit status, and stops early only
   when the streams have gone idle for the grace with no EOF. A stop without EOF is never silent:
   the result says so. A command whose sentinel arrives late still counts as started. Pin it with a
   throttled sshd model in the suite (exit-status before the data, a bounded window, both streams on
   one link) at several rates, plus the grace-0 mutant; findings 1 and 2.
2. **Every call that holds stdin has a bound.** A tool that sends stdin passes a timeout, or the
   backend bounds the wait for the program to start, so the documented `-1001` actually comes;
   finding 4.
3. **Nothing after the startup files depends on shell state they can change**, `IFS` first: the
   prologue resets it, and the function export does not split on it; finding 3.
4. **No POSIX stage reads a startup file, whatever the login shell or sshd sets**; the low finding.
5. **Spec ids stay out of source and tests**, with a check that keeps them out.
6. **Every timeout escalation is pinned**: the SIGKILL drop dies.

## Human-gate triggers — cycle 2

- **Triggered:** findings 1, 2, 4 and the low finding sit in high-blast-radius paths
  (`executor/ssh.py`, `executor/launch.py`); 1–3 contradict §3 and 4 contradicts §2 (*[human gate]*).
  The foreman signs it off for remediation as judge under the autonomy decision, and records that
  finding 1 regressed from the foreman's own cycle-1 routing of class 3. Geoffrey is told in the
  foreman's next report. A third cycle that does not converge escalates to Geoffrey.

## Review cycle 3 — changes-requested; bound reached, escalated (2026-10-19)

- **Reviewed commit:** 8a13f06fc5eb15dc38b5a5d6a30ece8f6bdfb791 · **Mode:** a fresh blind pass, debate variant
  (ship and block, separate clones), with CI run 39207258885 on the same commit (ubuntu-24.04,
  bash 5.2, zsh/tcsh/ksh/csh installed: `1447 passed` per era, 0 skipped) as Linux evidence.
- **History note:** before this pass the branch was redacted and squashed for its first push
  (`a9a2901` on `404580c`), then merged develop `5153e47` (CI installs the login shells). The
  cycle's earlier commits stay local; the GOAL's shape and gate clarifications are carried in the
  squash unchanged.
- **Cycle 2's routing held for classes 2–6** (the stdin bound, profile shell state, no POSIX stage
  reading an rc, spec ids, timeout escalation), and the Linux-CI fixes held (the `pwd -L` rows, the
  parked-descriptor refusal). **Class 1 did not converge:** its idle cutoff after the exit status
  still truncates on a slow or stalled link.

### Verification run

- Both reviewers: the full `verify` in both eras (`1447 passed`; one ship-side run had one failure,
  finding 3, under its own concurrent load); sandboxed `--help` exit 0; CI 39207258885 green.
- Ship: 16 hostile login profiles locally (chatty, stdin-reading, `cd`, `IFS`+glob options, `set
  -k`, exported `SHELLOPTS`, `CDPATH`, functions over builtins, aliases, traps, a taken parked
  descriptor, `set -P`): argv, stdin, writes and start directory exact; a real server over stdio
  with hostile paths, base64 and refusals; reads over the throttled ssh model from 64 KiB/s to
  unthrottled, exact while the link flows; 6 MiB writes under bash, zsh and tcsh with chatty rcs,
  exact; R9 on the raw wire; size limits per wire; 1,200 concurrent local launches, no false
  refusal; six of its own mutants killed.
- Block: the same throttled model at 6,000 B/s and 24,000 B/s against HEAD and `origin/develop`
  (`5153e47`); a zsh `.zshenv` background job against `write_file`; R9; the size limit under six
  login shells; 1,920 concurrent local launches, no false refusal.

### Requirement → evidence matrix (cycle 3)

| R-ID | Status | Note |
|------|--------|------|
| R1 | ✅ | only `run_command` passes a string |
| R2 | ✅ | matrix, three modes; hostile drives |
| R3 | ✅ | delegate rows; profile `cd` ignored |
| R4 | ❌ findings 1, 2 | reads truncate on a stalled link; a zsh rc job can read a write's stdin |
| R5 | ✅ | refusals, target untouched |
| R6 | ✅ | schema and argv |
| R7 (test half) | ✅ | bash, zsh, sh, tcsh login shells; on Linux in CI with every shell installed |
| R7 (`module` half) | ✅ | unchanged evidence (orchestrator) |
| R8 | ✅ timeouts; ❌ finding 1 | a command that ran reported `-1002` on a slow link |
| R9 | ✅ | raw wire, both eras |
| R10 | ❌ findings 1, 2 | §3 and README promise completeness and "no startup file reads stdin" |

### [CRITICAL/CONFIRMED] The post-exit idle cutoff truncates on a slow or stalled link (both reviewers)

- **Where:** `src/rcac_mcp/executor/ssh.py:145-150` (`_collect`), with `launch.py:276-282`; every
  tool but `run_command` drops stderr on exit 0 (`tools/filesystem.py:141-144`).
- **Failure scenario:** sshd sends `exit-status` ahead of output still waiting for window space;
  when the link then delivers nothing for `GRACE` (1 s) — a link below about 32 KB/s delivering
  whole 32 KiB packets, or any stall of a second — `_collect` stops, returns exit 0 with the "held"
  note though nothing was left running, and the tool returns the truncated stdout silently. A late
  start sentinel on such a link turns a submitted job into "did not start" (`-1002`): the `sbatch`
  double-submit class. **A regression:** `origin/develop` waited for EOF.
- **Evidence:** throttled model, 512 KiB/s with one stall right after the exit status: 1.6 s and
  1.2 s stalls → `read_file` 65,536 of 1,048,576 bytes, `is_error=False`; 0.7 s → complete. At
  6,000 B/s: `read_file` 8,192 of 49,152 bytes, exit 0; `sbatch` with a 40 KB `.bashrc` → "Failed
  to submit job: … did not start (exit 0)" while the stand-in shows the submission ran; develop's
  checkout returns the whole 49,152 bytes. At 24,000 B/s with 32 KiB pieces: 32,768 of 256 KiB.
- **Touches:** a high-blast-radius path; §3 (*[human gate]*): CRITICAL per the rubric.

### [MEDIUM/CONFIRMED] A remote zsh rc's background job can read a write's stdin (block 2)

- **Where:** `src/rcac_mcp/executor/ssh.py:351-352` (stdin fed to the channel the rc's jobs share).
- **Failure scenario:** zsh, unlike bash, sh and ksh, gives a non-interactive shell's background
  jobs the real stdin; `.zshenv` = `head -c 1 > "$HOME/rc-took" &`, then `write_file` of 160,000
  bytes → `is_error=False`, 143,616 bytes on disk, a different hash, deterministic over three runs.
  The stated exception covers rc jobs writing into a result, not reading the caller's stdin.
  **A regression:** develop carried content inside the command text.
- **Touches:** R4; §3's "no startup file reads the caller's stdin" (*[human gate]*).

### [LOW/CONFIRMED] A new holder test races its own output order (ship 2)

- **Where:** `tests/test_executors.py:397` (`startswith('done\ntick\n')`).
- **Failure scenario:** the background `tick` can print before the foreground `done`; one of six
  concurrent runs of `test_a_holder_that_keeps_writing_ends_at_the_timeout` failed with complete,
  correct output. The gate can go red spuriously.

### Coverage observations

- `noclobber-kept` (bash 3.2 refuses an exported `SHELLOPTS`; CI's bash 5 is the evidence),
  `pipefail-kept` (only an unreachable pipeline), `ssh-final-drain-dropped` (a race guard).

## Escalation — cycle 3

- **The bound is reached** without convergence (cycle 1: four MEDIUM, two LOW; cycle 2: one
  CRITICAL, two HIGH, two MEDIUM, one LOW; cycle 3: one CRITICAL, one MEDIUM, one LOW). Findings 1
  and 2 sit in high-blast-radius paths and §3. The cycle STOPs here for Geoffrey; nothing is
  remediated until he rules. Class 1 has now failed twice as a timing heuristic (a fixed grace,
  then an idle cutoff); the foreman's recommendation to him replaces the heuristic rather than
  tuning it.
