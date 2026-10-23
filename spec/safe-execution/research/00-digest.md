# 00 — Research digest: decisions for safe-execution

Six briefs fed this plan: one measured by the planner on two clusters (01), five by parallel
read-only workers (02–06). This digest records the decisions and settles where the briefs disagree.

| # | Brief | Decides |
|---|---|---|
| 01 | [cluster-shell](01-cluster-shell.md) | `/bin/bash -l -c`; the base64 bootstrap; `scontrol … -- NAME`; `average_wait` forwards to `sacct` |
| 02 | [ssh-backend](02-ssh-backend.md) | raw Paramiko channel for `run`; SFTP `~` mapping; connect-failure classes |
| 03 | [startup-failure](03-startup-failure.md) | answer every request until EOF with −32603; exit 5; tests in both eras |
| 04 | [tool-argv](04-tool-argv.md) | the per-parameter argv table; `--` and option binding; the `extra_args` conflict |
| 05 | [tests](05-tests.md) | rewrites, mutants, the matrix without sudo or a cluster, the fixture deltas |
| 06 | [local-delegate-docs](06-local-delegate-docs.md) | process-group timeouts, decoding, `/bin/bash`, transfers as the mapped user, the R10 sweep |

## Decisions

1. **Interpreter:** `/bin/bash -l -c PROGRAM` in every backend (01: the only launch whose `module`
   state is the same for every login shell; also puts `jobcmd`/`jobenv`/`jobscript` on `PATH`).
2. **One program per command** (`executor/launch.py`): argv elements `shlex.quote`d; `UserPath`
   `~`/`~/` → `"$HOME"`; relative cwd gains `./` (04: `CDPATH` and `cd -` apply even after `--`); a
   script with no cwd is the program verbatim and the last argv element (05: the launch-marker
   readers keep working). NUL and non-`str` arguments refused.
3. **SSH wire:** fixed bootstrap + one base64 word, with `printf .` to keep trailing newlines
   (01 measured it under real zsh and sshd-like bash/zsh/tcsh/sh; 02 confirmed `base64 -d` and the
   128 KiB single-argument cap).
4. **SSH `run`:** raw channel, feeder thread for stdin then EOF, drain both streams, deadline →
   `signal` TERM request (best effort) + close + `-1001`; Fabric keeps the connection (02).
5. **Local/delegate `run`:** `Popen(start_new_session=True, stdin=PIPE)`, TERM → drain → KILL →
   drain, close pipes; `stdin` is `b''` when none, never inherited (06, 02 both found the server's
   stdin leaking to commands today).
6. **Timeout marker:** `exit_code = -1001`, no new field, no note appended to stderr; stdout and
   stderr are exactly what was captured (06 A4: a field changes `run_command`'s served output
   schema; a note would alter the captured stream). The `run_command` docstring names the value.
7. **Decoding:** bytes end to end, `decode('utf-8', 'replace')` once at the end (06 A5:
   `surrogateescape` breaks JSON serialisation).
8. **Transfers:** `delegate` copies as the mapped user (`cp -- "$1" "$2" && readlink -f -- "$2"`)
   through `run`; `ssh` uses SFTP with `~/rest` → `rest`, `~` → `.`; `local` uses `shutil.copy2` with
   the `~`-only `expand_local`. `put`/`get` return the absolute destination.
9. **Tool argv:** option values as two elements (04; resolves 05's mention of `--long=value`: the
   split form lets a `UserPath` value stand alone); `--` before positionals; fixed-script argvs for
   redirection; `average_wait` gets `--partition` (01: `-p` is `sacct --parsable`).
10. **`resources.py`:** keep the listing a constant `find … | sort` script (resolves 04's
    `-print0` + Python sort, which could reorder files and would break §8's ordering rule).
11. **`sbatch`:** `extra_args` before `--`, refuse an element equal to `--`; script filename from
    `job_name` sanitised to `[A-Za-z0-9._-]`; `-J` keeps the literal name (04 §7).
12. **R9:** stack-free responder in `src/rcac_mcp/startup.py`, every request answered until EOF,
    −32603, `StartupFailure` → `exit_status.runtime_error`; cause classes from 02 §4. A default
    connect timeout and the endless reconnect loop are deferred as seeds (02 recommended folding
    them in; neither is in the GOAL, and R9 holds without them).
13. **Tests:** `executes` fixture (bash-only, sandboxed, exact `sudo` prefix stripped for delegate
    with a separate mapped `HOME`); executing loopback SSH server over bash/zsh/sh/tcsh login
    shells; the matrix with schema-derived coverage; `ls` is an argv-echo shim on every OS; the PATH
    shim re-pinned through `BASH_ENV` (planner measured macOS `path_helper` defeating it under
    `bash -l`); mutants re-planted and added per phase (05 §2).
14. **Open for the GOAL gate:** `extra_args` on four Slurm tools vs R2 (04 §2, 05, 06 all raised it;
    recommendation `list[str]`); the fixture method for the surface deltas (§9 asks it be raised);
    `~` on server-side transfer paths (04 §8).
