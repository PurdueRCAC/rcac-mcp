# PLAN — Every tool passes exactly what it was given, under one interpreter

> **Status:** Draft for review · **Last updated:** 2026-10-17
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/), consolidated in [`research/00-digest.md`](research/00-digest.md).
> Every design element traces to a GOAL R-ID.

## 1. Summary

A new pure module, `executor/launch.py`, turns every command into **one bash program**: an argv
becomes `exec 'a' 'b' …` with each element quoted as one word, a path marked `UserPath` resolves a
leading `~`/`~/` through `$HOME` inside the program, and a `run_command` script is the program
itself. All three backends run that program under the **pinned interpreter, `/bin/bash -l -c`**:
`local` directly, `delegate` behind `sudo -n -H -u <user>`, and `ssh` behind a fixed bootstrap whose
only variable part is one base64 word, so the remote login shell (sh, bash, zsh, ksh, csh or tcsh)
parses no caller byte. File content always travels on stdin. A timeout returns a `CommandResult`
with the captured output and exit code **`-1001`** in every mode, and nothing the login
initialisation prints ever reaches a result (streams parked during startup, a per-call sentinel cut
at the start of the command's output). `sbatch` gains `args` and a list
`extra_args`; `write_file` gains `encoding`. A failed SSH connect at startup is answered on stdio
with a JSON-RPC error carrying `cannot connect to <host>: <cause>`, in both protocol eras. Seven
phases, launcher first; big appetite as shaped.

## 2. Design

### 2.1 The protocol (`executor/base.py`, §2)

```python
TIMEOUT_EXIT_CODE: Final = -1001          # documented in AGENTS.md §3 first
NOT_STARTED_EXIT_CODE: Final = -1002      # startup ended before the command began (§2.2a)

class UserPath(str): ...                  # a path argument: leading "~" or "~/" = the running user's home

class Executor(Protocol):                 # still eight members
    hostname: str (property)
    def run(self, command: Sequence[str] | str, cwd: str | None = None,
            timeout: float | None = None, stdin: bytes | None = None) -> CommandResult
    def put(self, local_path: str, remote_path: str) -> str   # returns the absolute destination
    def get(self, remote_path: str, local_path: str) -> str   # returns the absolute destination
    open / close / __enter__ / __exit__   # unchanged
```

- A `str` command is a bash script, and **only `run_command` passes one** (R1). Any other
  sequence is an argv: every element must be a `str` (an `int` is a `TypeError`, so a tool that
  forgets `str(job_id)` fails loudly), none may contain NUL (a `ValueError` naming the argument), and
  the list may not be empty. A bare `UserPath` as the command is refused (it is a `str`).
- `cwd` is always path-like: `~`/`~/` resolve as for `UserPath`; nothing else expands (R3).
- `stdin` is the exact bytes the command reads; `None` means an empty, closed stdin. No backend
  ever lets a command inherit the server's own stdin (on `stdio` that is the JSON-RPC stream).
- `put`/`get` now return the absolute destination they wrote, computed where the file is, so
  `upload_file`/`download_file` stop guessing it from the server's `os.path` (R3).
- `CommandResult` keeps its four fields. Timeout is a value of `exit_code`, not a new field: a field
  would change `run_command`'s served output schema for no gain the sentinel does not give (GOAL
  Clarification "always return"; research 06 A4).

### 2.2 The launcher (`executor/launch.py`, new, coupled core, high-blast-radius)

Pure functions plus one process runner (research 01, 02, 04, 06):

- `program(command, cwd, start_home=False) -> (str, bytes)` returns the program and its sentinel
  (§2.2a), one line per step:
  1. the prologue (§2.2a): neutralise the profile's aliases, functions over the builtins we use,
     shell options and traps, then take back the caller's streams, which the launch parked on fds
     57–59 while the login shell initialised;
  2. the sentinel, printed on stdout and on stderr;
  3. with `start_home` (delegate): `command cd -- "${HOME:?HOME is not set}" || exec /usr/bin/false`,
     so relative paths mean the mapped user's home, as they do over SSH, not the server's cwd;
  4. with `cwd`: `command cd -- <word> || exec /usr/bin/false` (`command` bypasses a profile's `cd`
     function; `exec false` avoids `exit`, which runs `~/.bash_logout` in a login shell);
  5. the body: an argv as `exec` + each element `shlex.quote`d; a `run_command` script as
     `fns=$(compgen -A function); [ -z "$fns" ] || export -f $fns; BASH_ENV= ENV= exec /bin/bash -c -- '<script>'`:
     a fresh non-login bash that inherits the login environment and its functions (`module`), so the
     script's `exit` never runs `~/.bash_logout`, its `$$` is its own (so `kill $$` stops it), no
     startup file runs again, and an empty or comment-only script is valid.

  Words: a `UserPath` equal to `~` renders `"${HOME:?HOME is not set}"`; one starting `~/` renders
  that plus `/'rest'` with leading slashes of `rest` collapsed (`~//etc/x` stays under `$HOME`); a
  relative `UserPath` gains `./` (so `CDPATH` and `cd -` cannot apply and no path can start with
  `-`); anything else (`~name`, `$X`, `*`) is quoted literally. Refused with a `ValueError` naming
  the argument: NUL, text that is not strict UTF-8 (a lone surrogate), an empty argv, a program
  over 96 KiB (Linux caps one argv element at 128 KiB and the SSH wire is base64); a non-`str`
  element is a `TypeError`. The program is the last argv element; the launch-marker readers in
  `test_identity`, `test_isolation`, `test_no_executor` and `test_protocol` move to one shared
  decoder (`launched_script(argv)` → script and sentinel), and `RecordedPopen` answers with the
  sentinel first so `separate` keeps its reply.
- `bash_argv(prog) = ['/bin/bash', '--posix', '-c', 'exec 57<&0 58>&1 59>&2 </dev/null >/dev/null 2>&1 && exec /bin/bash -l -c -- "$1"', 'cluster-mcp', prog]`.
  The stage runs in POSIX mode, which reads no startup file (not even `BASH_ENV`, which RHEL's
  module setup exports), parks the three streams on fds 57–59 (high, so a profile's own `exec 8>…`
  or `9>>…` cannot take them) and gives the login shell `/dev/null`. **Login, by measurement**
  (research 01): under `-l` the module state was identical whether the launcher was a real zsh
  login, or sshd-like `bash`, `zsh`, `tcsh` or `sh` launches, on Gautschi and Negishi; without `-l`
  it differed for every launcher, and under today's zsh `MODULEPATH` is empty and
  `jobcmd`/`jobenv`/`jobscript` are not on `PATH`. `-c --` keeps a script that starts with `-` or
  `+` from being read as bash options. `/bin/bash` is absolute: deterministic under `sudo`'s
  `secure_path`, present on RHEL, and bash 3.2 on a Mac, which the launcher's constructs support.
  Plain `exec` for the redirections: bash 3.2 spins on `exit` after a redirection-only
  `command exec` (found while folding the review).
- `ssh_command(prog)`: the fixed bootstrap
  `exec /bin/bash --posix -c 'p=$(printf %s "$1" | base64 -d && printf .) && exec 57<&0 58>&1 59>&2 </dev/null >/dev/null 2>&1 && exec /bin/bash -l -c -- "${p%.}"' cluster-mcp <BASE64(prog)>`.
  It holds no `!`, backslash or newline, so every permitted login shell parses it identically
  (measured on both clusters, and locally under sh, bash, zsh, ksh, csh, tcsh); `&& printf .` keeps
  trailing newlines `$( )` would strip **and** fails the launch when decoding fails, instead of
  running an empty program with status 0 (`separate` then reports it as not started).
- `run_process(argv, stdin, timeout) -> (bytes, bytes, int)` for `local` and `delegate`:
  `check_timeout` first (finite, `0 < t ≤ 10⁷` s, else `ValueError` before anything starts);
  `Popen(start_new_session=True, stdin=PIPE)`, `communicate(stdin or b'', timeout)`; on the deadline,
  `poll()` first (a command that already exited keeps its own status even if a background child
  still holds a pipe: no completed command reports the marker), then `killpg` SIGTERM, a bounded
  drain, `killpg` SIGKILL, a bounded drain, close our pipe ends (research 06 A1–A2). SIGTERM first is
  what reaches the command through `sudo` when the server is not root. Exit code:
  `TIMEOUT_EXIT_CODE` unless `poll()` had a status.
- `decode(bytes)`: UTF-8 with `'replace'`, once, at the end (today `text=True` raises on non-UTF-8
  output, and `TimeoutExpired.stdout` is bytes that reach a `str` field).
- `expand_local(path)`: the server-side `~`/`~/` rule (`os.path.expanduser` also expands `~name`);
  an unset or empty `HOME` is a named `ValueError`.

### 2.2a Startup output never reaches a result (the PLAN/TECH gate's required change)

A login bash sources `/etc/profile`, `profile.d` and the user's startup files on every command, and
over SSH the remote login shell runs its own rc files (`.zshenv`, `.cshrc`, a sshd-sourced
`.bashrc`) before the bootstrap. Two layers keep all of it out of every result, in every mode:

1. **Parking.** The stage moves stdin, stdout and stderr to fds 57–59 and gives the login shell
   `/dev/null` on all three, so nothing a startup file reads or prints, or a background job it starts
   prints later, touches the command's streams. The prologue then neutralises what a profile can
   leave behind — `\unalias -a` on a line of its own (bash reads a whole line before running it, so
   an alias on a later word of a shared line would already be expanded; build amendment), then
   `unset -f builtin command exec set printf trap cd export declare compgen [;
   set +euxvT +o errtrace +o posix; trap - EXIT ERR DEBUG RETURN` — before
   `exec 0<&57 1>&58 2>&59 57<&- 58>&- 59>&-` takes the streams back (any trace or error from those
   lines still goes to `/dev/null`).
2. **A per-call sentinel.** The program prints a fresh 32-hex nonce (`secrets.token_hex(16)`) on
   stdout and on stderr, as two halves (`printf %s%s HEAD TAIL`), so the whole nonce never appears in
   the program text, an argv or a trace. `separate(out, err, code, sentinel)` drops, byte-wise, each
   stream up to and including the **first** occurrence of the sentinel. This removes what ran before
   the program could park anything: the remote login shell's rc output, of any size or encoding.

Edge cases, settled:

- **Nothing printed the sentinel, not a timeout** (a profile that `exit`s, `exec`s another shell,
  `set -n`; `sudo -n` refusing; the bootstrap's decode failing): the command did not start. The
  result is stdout empty, stderr the fixed line `cluster-mcp: the command did not start (exit N); the
  server log has the details`, and exit code **`NOT_STARTED_EXIT_CODE = -1002`** (below −1000, §3), so
  no caller reads success from a profile's `exit 0`. The dropped stderr (last 4 KiB) goes to the
  server log at WARNING — never the result.
- **A timeout before the sentinel** (a profile that hangs): R8's result, empty output and `-1001`.
- **The sentinel on one stream only** (startup redirected one of the parked fds): that stream is
  empty, a fixed note naming it is appended to stderr, and a WARNING is logged; never a silent loss.
- **Command output that contains the sentinel, or the same hex text:** kept, since only the first
  occurrence is cut and the program prints it before the command runs.
- **Recorded limits** (risk 5): a startup file that starts a long-lived background process keeps the
  parked descriptors open, so the call waits for it or for the timeout; over SSH, a background job the
  *remote login shell's* rc file starts (before the bootstrap can park anything) can still write into
  the channel after the sentinel. Both are outside what the server can separate without a PTY.

The separation is pinned with planted chatty startup files in the sandbox `HOME` that write to both
streams (`.bash_profile`, `.bash_login`, `.profile`, `.bashrc`, `.bash_logout`, `.zshenv`,
`.zprofile`, `.cshrc`, `.tcshrc`, `.login`, `BASH_ENV`), including partial lines, binary, 1 MiB,
`set -x`, traps, and functions named `printf`/`cd`: every result is **byte-identical** to the quiet
case in `local`, `delegate` and on the loopback SSH stub under bash, zsh, sh and tcsh login shells.

### 2.3 Backends

- **`local`** (`executor/shell.py`): `run_process(bash_argv(program(command, cwd)), stdin, timeout)`;
  `put`/`get` are `shutil.copy2` with `expand_local`. `$SHELL` is no longer read.
- **`delegate`** (`executor/delegate.py`, §5): `['sudo', '-n', '-H', '-u', user] + bash_argv(prog)`
  with `program(…, start_home=True)`. `-n` and `-H` stay; `HOME` inside the program is the mapped
  user's, so `~` and relative paths resolve there (R3). `put`/`get` run **as the mapped user**
  through the same `run` (local and remote are the same host in this mode): one fixed-script argv
  that copies with `cp -- "$1" "$2"` and prints the path it wrote — `$2`, or `$2/<basename of $1>`
  when `$2` is a directory, made absolute against `$PWD`, without resolving symlinks — read back
  exactly (no `.strip()`); a non-zero exit raises `RuntimeError` with the stderr. The server process
  does no file I/O on a caller's path. `_shell` is removed.
- **`ssh`** (`executor/ssh.py`): Fabric's `Connection` stays for ssh_config, keys, proxies and
  `open()`. `run` replaces `conn.run` with a raw Paramiko channel (research 02 §3): `exec_command(
  ssh_command(prog))`, a feeder thread `sendall(stdin)` then `shutdown_write()`, the caller drains
  stdout and stderr until EOF plus exit status, or until the deadline, waiting with `select` on the
  channel (or a short sleep once the exit status is in), never spinning. Fabric's `run` is unusable
  here: its `in_stream` is not byte-exact and feeds ~100 B/s, it forwards the server's stdin when
  given none, and it splits UTF-8 at chunk edges. At the deadline: if the exit status has arrived,
  return it (a completed command is never reported as a timeout); otherwise a best-effort RFC 4254
  `signal` TERM request, `close()`, `TIMEOUT_EXIT_CODE`. Signal death and lost status stay `-1`, as
  Paramiko reports them. `put`/`get` call Paramiko's `SFTPClient` directly (Fabric's `get` runs
  `str.format` on the local path, an expansion R3 forbids, and creates parent directories), with
  `~/rest` → `rest` (leading slashes of `rest` collapsed, so `~//etc/x` stays in the home) and `~` →
  `.` (SFTP starts in the login home and expands nothing); they return `sftp.normalize(remote)` /
  the absolute local path.
- **Connect (R9):** `SSHExecutor` passes default `connect_timeout`, `banner_timeout` and
  `auth_timeout` (15 s each) through Fabric's config, so an ssh_config `ConnectTimeout` still wins
  and an unreachable host fails within a bound the client will wait for (a black-holed host
  otherwise waits for the OS TCP timeout, 75 s or more).

### 2.4 Tools on argv (R1, R2, R4–R6)

Full parameter table: research 04. The rules:

- **Option values are two elements**, `'-A', value` (getopt takes a required argument from the next
  element whatever it starts with: measured `squeue -u -x`, `-p -probe`, `sacct -A -probe`). A path
  value is a `UserPath` element (`'-o', UserPath(output)`).
- **Positional user values follow `--`** where the program takes positionals: `cat`, `head`,
  `mkdir`, `readlink`, `cp`, `sbatch` (`[…options, *extra_args, '--', UserPath(script_path),
  *args]`), `scontrol show node -- NAME` (measured: `scontrol show node -x` is rejected as an
  option, `-- -x` is literal). Programs with no positionals (`squeue`, `sacct`, `sinfo`) get none.
- **Fixed-script argvs** where one call needs redirection or two steps:
  `[BASH, '-c', SCRIPT, '<tool>', *values]` with a constant `SCRIPT` and the caller's values only as
  positional parameters. Every such `SCRIPT` is a module-level constant registered in one set
  (`FIXED_SCRIPTS`), and the R1 pin checks that the element after `-c` in any tool's argv is a member,
  so a script with a value formatted into it fails like a command string does. `write_file`:
  `cat >"$1"` / `cat >>"$1"`, with `mkdir -p -- "$2" && ` first when `create_dirs`, where `$2` is the
  parent computed in Python (`posixpath.dirname` of the `UserPath`, skipped when empty), not
  `$(dirname …)`, which strips a trailing newline from a directory name. Content on stdin.
  `list_directory`:
  `cd -- "$1" && printf '%s\0' "$PWD" && shift && exec ls -l --time-style=long-iso "$@" .`, one call
  instead of today's 1 + 2·N, with `-a` as its own element (today's `long-isoa` is rejected by GNU
  `ls`, measured). These are argv lists (R1), and nothing a caller sent is ever parsed as shell.
- **`write_file`** (R4, R5): `encoding: Literal['utf-8', 'base64'] = 'utf-8'`, so the schema refuses
  any other value naming the parameter. The bytes are computed **before any executor call**:
  `content.encode('utf-8')` (a lone surrogate → `ValueError` naming `content`) or
  space, tab, CR and LF removed (exactly those four), then `base64.b64decode(…, validate=True)`
  (`binascii.Error` → `ValueError` naming `content` and `encoding`). Those four are accepted because
  GNU `base64` wraps at 76 columns and ends with a newline (RFC 2045 line breaks); every other
  non-alphabet byte, URL-safe characters and bad padding are refused (GOAL Clarifications, PLAN/TECH
  gate). The docstring says so. Nothing touches the target until
  validation has passed.
- **`sbatch`** (R4, R6): `args: list[str] | None = None` after the script path; `extra_args: list[str]
  | None = None`, each element one argument, placed **after** the tool's own options and before `--`,
  an element equal to `--` refused. The script path is a `UserPath`, so it is absolute or starts
  with `./` and can never be read as an option even if an `extra_args` option with a required value
  (`-J`) swallows our `--`. `script_content` is written byte-exactly by the `write_file` path to
  `~/jobs/<name>_<ts>.sh` plus `chmod +x -- "$1"` in the same fixed script; the filename part derived
  from `job_name` keeps `[A-Za-z0-9._-]` only (`-J` still gets the literal name).
- **`average_wait`** forwards its arguments to `sacct` verbatim (measured, line 230), where `-p` is
  `--parsable`; the argv uses `--partition`, `value` and `-A`, `value`, which is what the parameter
  always meant.
- **`resources.py`** (not a tool, but an executor caller, so R1 binds it): the listing stays the
  constant `find /etc/agents.d -maxdepth 1 -name "*.md" -type f 2>/dev/null | sort` as a fixed-script
  argv, so §8's glob, order and header are unchanged; each file is `['cat', '--', path]`.
- `read_file.encoding` stays as it is (served, ignored); the non-goals give `read_file` no change.

### 2.5 Startup failure (R9) — `src/rcac_mcp/startup.py` (new, stack-free) + `__init__.py`

Research 03. `MCPServerApp.run` wraps only the `SSHExecutor(ssh_host)` construction; the ladder
(§4) is untouched. On any exception: `message = f'cannot connect to {ssh_host}: {cause}'`, where
`cause` names the class of failure (`unknown host or SSH alias`, `connection refused`,
`authentication failed`, `timed out`) followed by `str(exc)` when it says more. Then:

1. `log.critical(message)` (stderr, at once);
2. on `stdio` only, `answer_until_eof(sys.stdin, sys.stdout, message)`: read newline-delimited
   JSON-RPC; answer **every** request (`method` and `id`) with
   `{"jsonrpc":"2.0","id":…,"error":{"code":-32603,"message":message}}`; skip notifications,
   responses and junk; return at EOF. Every request, because a 2026-07-28 client in `auto` mode
   swallows the `server/discover` error and falls back to `initialize` (measured: a first-only
   responder leaves it `Connection closed`);
3. `raise StartupFailure(message)`, a `RuntimeError` subclass mapped first in `exceptions` to
   `exit_status.runtime_error` (5) without a second log line.

`http`/`sse` log and exit 5. FastMCP as the failure server was measured and rejected (the modern
client gets `-32022`, not the message).

### 2.6 Tests and gates

- `tests/test_launch.py` (`unit`): rendering, `UserPath`, cwd `./`, NUL/type refusals, the
  bootstrap's character set; (`integration`) the rendered program and bootstrap executed under every
  local login shell (`/bin/sh`, `bash`, `zsh`, `ksh`, `csh`, `tcsh`), and `run_process` timeouts
  (partial output kept, group killed, a `setsid`-escaped child bounded by the drain).
- **`executes`** (new conftest fixture, `integration`): replaces `Popen` **in
  `rcac_mcp.executor.launch` only** (so the loopback server's own launches are not caught) with a
  fenced launcher that runs only `/bin/bash` argvs, each through `bin/sandbox.sh` under an `env -i`
  environment shaped like the real one (`HOME`, `USER`, `LOGNAME`, a `secure_path`-like `PATH` with
  the shim first, the shim's `BASH_ENV`). In delegate mode it refuses any argv not starting with
  exactly `['sudo', '-n', '-H', '-u', <mapped user>]` and replaces those five with a **relaying
  stand-in** (a small script, also sandboxed, that starts the command in its own session, relays
  only TERM, INT and HUP, and sets `HOME` to a separate fake mapped home, simulating `-H`), from a
  cwd that is not the mapped home. No argv names `sudo`, so the PATH shim and
  `test_cli_drives_are_sandboxed` hold.
- **Signal-death controls are unit rows**: `bin/sandbox.sh` turns a child's signal death into
  `128+n`, so the "SIGHUP death is `-1`, not the marker" controls run on `run_process` with a
  stubbed `Popen` (and `os.killpg` patched: the recorder's pid is real-looking) and on the SSH loop
  with a fake channel; the fake channel also bounds the drain loop's iterations (no busy wait).
- **Login bash and the PATH shim.** macOS `/etc/profile` runs `path_helper`, which moves `/usr/bin`
  ahead of the shim under `bash -l` (measured). The conftest shim also exports `BASH_ENV` pointing at
  a file that re-prepends the shim directory (a login non-interactive bash reads `BASH_ENV` after
  `/etc/profile`, measured), and `test_hygiene` gains a self-test that `command -v sudo` inside a
  launched program resolves to the shim.
- **`tests/ssh_server.py`** (helper, not collected): the `test_ssh_smoke` in-process Paramiko server
  extended to *execute* each exec string as `[SANDBOX, 'env', '-i', 'HOME=…', 'USER=…', 'PATH=…',
  <login shell>, '-c', 'cd "$HOME" && …']` — an sshd-like minimal environment, starting in `$HOME`
  as sshd does — pumping channel stdin until EOF and returning the exit status. It serves an SFTP
  subsystem rooted at that home (Paramiko's `SFTPServer` with a small local handler), so `put`/`get`
  have an ssh leg too. On channel close it does **not** kill the process (OpenSSH does not); a test
  that times out cleans up its own leftovers and asserts only what R8 promises. The login shell is a
  parameter (`/bin/bash`, `/bin/zsh`, `/bin/sh`, `/bin/tcsh`). It is the `ssh` leg of R2–R4, R7 and
  R8 without a real host. `test_ssh_smoke` keeps its "no process" contract and checks the bootstrap
  decodes to the expected program.
- **R7's probe is specific to the pinned interpreter**: `[ -n "$BASH_VERSION" ] && ! shopt -qo posix
  && [ "$CLUSTER_MCP_TEST_LOGIN" = 1 ] && [ a == a ]`, where a quiet `.bash_profile` in the test home
  exports `CLUSTER_MCP_TEST_LOGIN=1` (proving the login initialisation ran; the script itself runs in
  the fresh non-login bash, §2.2). `[ a == a ]` alone also passes under `/bin/sh` and `ksh` on
  macOS, so it could not tell a dropped `-l` or a `/bin/sh` interpreter.
- **The matrix**, `tests/test_literal_args.py` (`integration`): rows are `(mode, tool, parameter)`
  triples; each row loops a value set: space, single and double quotes, `$HOME$(id)` and backticks,
  `*?[a]`, a newline, a component ending in a newline, `;|&<>(){}!#\`, a leading `-n`/`--help`, a
  leading `+`, `--`, the empty string, `~`, `~/x`, `~//x`, `~name/x` — the `~` forms for **every**
  parameter (a non-path parameter must receive them literally) — and a lone surrogate, which must be
  refused naming the parameter in every mode. Each string parameter is also crossed with every bool
  or optional parameter that selects a different script (`write_file.append`/`create_dirs`,
  `list_directory.show_hidden`, `read_file.max_size`, `sbatch` path vs content, `extra_args` with a
  required-value option such as `-J` against a script path starting with `-`). Oracles: a path value
  must reach a fixture file named exactly the value (real `cat`/`head`/`mkdir`/`readlink`/`cp`); an
  option value must reach an argv-echo shim (`sbatch`, `squeue`, `scancel`, `sacct`, `sinfo`,
  `scontrol`, the site tools) as one element, in the expected position, except that the empty
  string on an optional scalar (an option's value or an optional positional) means "not given", so
  its oracle is the tool's default argv; content is checked by sha256. `ls` is a shim on every OS that records its argv and cwd and prints a canned GNU
  `--time-style=long-iso` listing (including a name with spaces), so `list_directory`'s parser is
  exercised and the oracle does not depend on GNU `ls`. **Coverage is proved, not listed:**
  `test_literal_matrix_covers_every_string_param` walks `anyOf`, `items` and `enum` in the schema
  the live server serves (`served_tools`, never the fixture) and requires a row for every
  (mode, tool, parameter) whose type includes `string` or an array of `string`, minus
  `run_command.command`; planted controls (an extra `Optional[str]`, an extra `list[str]`, a
  missing mode) must fail it.
- `tests/test_startup.py`: the responder over in-memory streams (`startup_refusal_violations`, with
  planted negatives); the raw wire (`[SANDBOX, CLI, '--ssh-host', '127.0.0.1:<closed port>']`, a
  `server/discover` line, a notification and an `initialize` line in; exit 5, stderr and both answers
  out); a row against a loopback listener that accepts and never sends an SSH banner, which must be
  answered with a "timed out" cause within the configured bound (a closed port fails at once and
  would never show a missing timeout); and `test_startup_failure_is_answered[legacy|modern]` through
  a new harness verb `stdio_refusal(argv, era)` (it refuses an argv not starting with `SANDBOX`, like
  `stdio_tools`).
- *Correction (P5 build, foreman's ruling, 2026-10-17; widened at P6):* `''` on an optional scalar
  (an option's value such as `user`, `partition` or `-J`'s name, or an optional positional such as
  `scontrol show node`'s name) is dropped like `None`; list elements and required parameters stay
  literal, `''` included.
- **Rewrites** (research 05): `test_executors` argv rows (`/bin/bash -l -c <program>`),
  `test_shell_is_captured_at_construction` retired for "argv names `/bin/bash` whatever `$SHELL`
  is"; `RecordingExecutor` records the command as given plus `stdin`; `test_tools` responders read
  argvs, and `tool_violations` gains `passed a command string` for any non-`run_command` tool that
  hands the executor a `str`.
- **Mutation cases** (`tests/mutants.py`): re-plant `tool-builds-local-executor`,
  `tool-builds-ssh-executor`, `sudo-n-dropped`, `sudo-H-dropped`, `cwd-unquoted`, `command-quoted`,
  `ssh-exit-status-dropped` on the new text; retire `shell-read-per-call` (its contract is gone) for
  `interpreter-from-shell-env`; plant the new cases each phase names in `TECH.md`.

### 2.7 Documents (R10, same commit as the code each describes)

`executor/base.py`'s docstrings; `AGENTS.md` §2 (the `run` signature, `stdin`, `put`/`get` returns,
`UserPath`), §3 (the `-1001` sentinel, documented before it is used, and one timeout contract
replacing both the `-1` overload and the asymmetry bullets), §5 (the new argv, what is quoted,
transfers as the mapped user, `_shell` gone), the footgun list (`delegate.py`, `ssh.py`, the new
`launch.py`), the repo map, the architecture diagram, § Testing (the new modules, `executes`, the
harness verb, the shim re-pin, the fixture deltas), and the high-blast-radius list
(`src/rcac_mcp/executor/launch.py` added); `README.md` and `APP_HELP` ("locally via `$SHELL`");
`SECURITY.md`'s diagram; the agent-facing docstrings of `run_command` (bash; the timeout marker),
`write_file` (`encoding`) and `sbatch` (`args`, `extra_args`). Research 06 B has the line-level list.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `Executor.run(Sequence[str] \| str, …)` (§2.1); every tool and `resources.py` on argv (§2.4); `tool_violations` "passed a command string" and the `tool-command-string` mutant (§2.6) |
| R2 | `program()` quotes every element as one word; option values as separate elements; `--` before positionals and path positionals that never start with `-`; fixed scripts take values only as positional parameters; the matrix in all three modes with schema-derived coverage (§2.2, §2.4, §2.6). **`extra_args` (all five) is R2's ruled carve-out** (GOAL Clarifications, PLAN/TECH gate): `list[str]`, each element one literal argument that reaches the program as an option on purpose, never shell-interpreted or split |
| R3 | `UserPath`/cwd render `~`/`~/` as `"$HOME"` inside the program; `sudo -H` makes it the mapped user's; delegate transfers run as the mapped user; SFTP maps `~/` to the login home; `expand_local` for server-side paths; delegate-mode rows for `cwd` and both transfer paths (§2.2, §2.3) |
| R4 | Content on stdin only (`stdin=` bytes, `cat >`/`cat >>`), never in argv; raw-channel stdin in `ssh`; hash checks in all three modes for overwrite/append, no final newline, empty, a terminator-like line, CRLF, NUL, binary (§2.3, §2.4, §2.6) |
| R5 | `encoding: Literal['utf-8','base64']`; decode-before-execute with `validate=True`; errors name the parameter; target untouched (checked by hash/absence) (§2.4) |
| R6 | `sbatch.args: list[str]`, `extra_args: list[str]`, `--` before the script path; schema refusal of a string naming the array type, pinned (§2.4) |
| R7 | `/bin/bash -l -c` in every backend; the SSH bootstrap; `[ a == a ]` pinned for a zsh and a bash login shell on the executing loopback server, and for `SHELL=/bin/zsh` in local and delegate; `module` parity is research 01's evidence (§2.2, §2.6) |
| R8 | `run_process` and the raw-channel deadline return captured output and `-1001`; never raise; a profile that hangs yields empty output and `-1001`; rows in all three modes plus a SIGHUP-death control (`-1`) (§2.2, §2.2a, §2.3) |
| Gate ruling (risk 5) | Startup output never reaches a result: parking on fds 57–59, the prologue, the per-call sentinel and `separate`, `-1002` for a command that never started; chatty-startup rows byte-identical to quiet in all three modes (§2.2a, P1, P2) |
| R9 | `startup.py` responder + `StartupFailure` → exit 5; message names host and cause; default connect, banner and auth timeouts so an unreachable host fails within a bound; unit, raw-wire, silent-banner and both-era rows (§2.3, §2.5) |
| R10 | §2.7, landed in the same commit as the code each text describes; P7's grep gate over the old phrasings |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (from the GOAL) and again against this design. Touched: §1, §2, §3, §4 (read
only), §5, §7, §8, §9, § Conventions, the high-blast-radius list and footguns.

- **§1 isolation** — tools still call `get_executor()` and construct nothing; no new module state
  (the launcher is pure; the SSH channel is per call). `executes` and the loopback server are test
  scaffolding. The main-context check still runs after every test.
- **§2 protocol** — still eight members; `run`, `put`, `get` change in all three backends **in one
  commit** (P2); `runtime_checkable` checks presence only, so P2's tests call each backend's `run`
  with `stdin=` and assert the `put`/`get` return values.
- **§3 CommandResult** — four fields unchanged; `-1001` and `-1002` are below −1000 and are written
  into §3 in P1's commit before code relies on them; the `-1` overload and the asymmetry bullets are replaced, not
  appended to.
- **§4 ladder** — not changed. R9 wraps `SSHExecutor(...)` after the mode is resolved; the
  missing-host `ValueError` and its `bad_argument` exit stay.
- **§5 delegate** — `sudo -n -H -u <local_user>` kept and still pinned; the interpreter becomes
  `/bin/bash -l` instead of the server's `$SHELL` (the "chosen at construction, not per command" rule
  is met more strongly: it is a constant). What is quoted changes: every argv element and the cwd,
  inside one program argument. Transfers no longer run anything outside the mapped user. The user
  map and claim precedence are untouched.
- **§7 registration** — no tool added or removed; `@mcp_tool` and the import list untouched; new
  parameters documented in `Args:`.
- **§8 resources** — the `/etc/agents.d` glob, sort order and `<!-- Source: … -->` header are
  preserved by keeping the listing a constant script; the hostname-keyed cache is untouched.
- **§9 surface** — `SERVER_INSTRUCTIONS` and `EXPECTED_TOOLS` unchanged; the README's tool lines for
  `sbatch`/`write_file` and `run_command` change with the docstrings; the fixture follows (§5,
  risk 2).
- **Conventions** — exit statuses from `cmdkit.app.exit_status`; comments declarative, no R/P ids;
  every new test marked; gates name test ids and mutant ids, never a bare marker selection; CLI
  drives only through `bin/sandbox.sh`.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| New module `executor/launch.py` (added to the high-blast-radius list) | One renderer and one wire per backend is what makes "exactly one literal argument" true in all three modes at once | Quoting in each backend triples the code that decides what runs, and the SSH side still needs a login-shell-proof wire |
| SSH `run` on a raw Paramiko channel, not Fabric's `run` | Byte-exact stdin (R4), partial output and no raise on timeout (R8), no forwarding of the server's stdin | Fabric's `in_stream` replaces invalid bytes, feeds ~100 B/s, and its timeout raises (research 02, probed) |
| Fixed-script argvs (`[BASH, '-c', CONST, name, *values]`) in `write_file`, `list_directory`, `sbatch`, delegate transfers, `resources.py` | Redirection and two-step operations with values only as positional parameters | A ninth protocol member (`write`) widens §2 for one use; `tee`/`dd` either echo the content back or cannot carry a `UserPath` |
| `bash -l` costs ≈0.25–0.3 s per command on the clusters (research 01) | It is the only launch with the same `module` state for every login shell | Non-login was measured inconsistent and broken (`MODULEPATH` empty, site job tools missing) |
| A two-stage launch in every backend (a POSIX-mode bash parks all three streams on fds 57–59, then execs the login bash) | Startup files run before the program and could read `write_file`'s content (R4) or print into a result (gate ruling) | One login bash on the real streams lost content to a profile `read` (review M2) and leaked profile output |
| A per-call sentinel and `separate` in every backend | Output printed before any parking is possible (the remote login shell's rc files) must still be dropped | Parking alone cannot reach the remote login shell, which runs before our first byte |
| `run_command` scripts run in a fresh non-login bash after the login initialisation | The script's `exit` would run `~/.bash_logout` into the result; a subshell guard broke `kill $$` (focused review) | Running the script in the login shell itself leaks logout output |
| Delegate programs start in the mapped user's `$HOME` | Relative paths meant the server process's cwd, a directory the mapped user may not own (review M14) | Leaving `sudo`'s inherited cwd keeps a server-side detail in every relative path |
| `conftest` exports `BASH_ENV` to re-pin the PATH shim | A login bash on macOS reorders `PATH` past the shim | Making `local` non-login only in tests would test a launch production never uses |
| `tests/fixtures/served_surface.json` edited by hand for the named deltas only | The cycle's surface change has no FastMCP 2 build to snapshot (`issues/surface-fixture-after-a-surface-change.md`) | Settling the fixture's future is that issue's cycle; see risk 2 |

## 4. Rabbit holes (resolved)

- **Login or non-login bash, and how `module` initialises** → login; identical across real zsh and
  sshd-like bash/zsh/tcsh/sh launches on two clusters; Lmod comes from `/etc/profile.d/{00-modulepath,
  modules,z01_default_module}.sh`; non-login is broken for zsh users today
  ([`research/01-cluster-shell.md`](research/01-cluster-shell.md)).
- **Getting a command past an unknown login shell** → base64 bootstrap, measured under every shell
  `/etc/shells` permits ([01](research/01-cluster-shell.md), [02 §7](research/02-ssh-backend.md)).
- **Fabric's stdin, timeout and decoding** → raw channel; loop and pitfalls pinned
  ([`research/02-ssh-backend.md`](research/02-ssh-backend.md)).
- **What each client era does with an error at startup** → answer every request until EOF, code
  −32603; FastMCP rejected ([`research/03-startup-failure.md`](research/03-startup-failure.md)).
- **Each tool's argv, `--` and option binding, and the site scripts** → table and probes
  ([`research/04-tool-argv.md`](research/04-tool-argv.md), probes D–G in
  [01](research/01-cluster-shell/)).
- **Test impact, the matrix without sudo or a cluster, mutants, the fixture**
  ([`research/05-tests.md`](research/05-tests.md)).
- **Killing a timed-out process group under `sudo`, decoding, which bash, the docs sweep**
  ([`research/06-local-delegate-docs.md`](research/06-local-delegate-docs.md)).
- **Adversarial design review** → see §4.1.

### 4.1 Adversarial review

Run 2026-10-17 by a fresh subagent given only `GOAL.md`, this plan's draft and a **copy of a
prototype** (`executor/{base,launch,shell,delegate,ssh}.py`, `tools/filesystem.py`), attacking by
execution: local for real, delegate with the `sudo` prefix stripped, SSH by executing the bootstrap
under sh, bash, zsh, ksh, csh, tcsh and dash. What held: hostile argv round-trips (quotes, `$( )`,
globs, newlines, CR, unicode, `-n`, empty, `=word`, 200 KB) in every mode and shell; byte-exact
writes for every content class in every mode; cwd forms with `CDPATH` set; local timeouts. Findings
and folds (each fold re-run against the prototype where marked ✓):

| # | Sev | Finding | Fold |
|---|---|---|---|
| H1 | HIGH | `; printf .` hid a failed `base64 -d`: with `base64` off `PATH`, `run_command` and `write_file` returned success having run nothing | `&& printf .` (✓ 127 under all six shells); unit row with `PATH=/nonexistent`; mutant `bootstrap-decode-unchecked` |
| H2 | HIGH | SFTP mapped `~//etc/x` to `/etc/x`, while the program kept it under `$HOME` | leading slashes after `~/` collapsed in both (✓); `~//x` in the value set; mutant `sftp-tilde-root` |
| H3 | HIGH | no connect timeout: an unreachable host blocks R9's answer past client timeouts, and the closed-port test cannot see it | default connect/banner/auth timeouts (§2.3) moved into this cycle (was a deferral); silent-banner row; mutant `startup-no-connect-timeout` |
| M1 | MED | `create_dirs` with a directory ending in a newline: `$(dirname)` strips it | parent computed in Python, passed as `$2`; bools crossed in the matrix; mutant `create-dirs-dirname-subst` |
| M2 | MED | the login profile shares the shell: a `cd()` wrapper printed into stdout, `exit` ran `~/.bash_logout`, a profile `read` ate `write_file` content | stdin parked during login init (✓ under every shell and local; later fds 57–59 with stdout and stderr, §4.2), `command cd`, `exec false` instead of `exit`; mutant `stdin-not-parked` |
| M3 | MED | an `extra_args` option with a required value (`-J`) swallows our `--`, so a script path `-x.sh` became an option | own options first, `extra_args` last; script path always absolute or `./` (✓ relative `UserPath` gains `./`); matrix crossing; mutant `extra-args-swallow-dashdash` |
| M4 | MED | the delegate timeout row could not fail: stripping `sudo` hides a command left alive behind a relaying `sudo` | relaying stand-in in `executes` (§2.6); grandchildren of a TERM-ignoring command under a non-root server are a recorded limit (risk 4) |
| M5 | MED | `bin/sandbox.sh` turns a signal death into `128+n`, so the `-1` controls cannot run through it | those controls are unit rows (§2.6) |
| M6 | MED | the SSH drain loop busy-spun when exit status arrived before EOF | `select`/sleep wait; fake-channel row bounding iterations; mutant `ssh-drain-spins` |
| M7 | MED | `sleep 4 & echo done; exit 0` with a 2 s timeout reported the marker | `poll()` / exit-status check at the deadline (✓ returns 0); mutant `timeout-completed-misreported` |
| M8 | MED | timeouts `1e12`, `1e308`, `nan` raised after the child started, orphaning it | `check_timeout` before launch (✓) |
| M9 | MED | a naive coverage check finds 10 of 51 string parameters | walk `anyOf`/`items`/`enum` on the live surface; (mode, tool, param) triples; planted controls |
| M10 | MED | value-set gaps (`~` on non-path parameters, `~//`, newline-terminated components, empty, `--`, `+`, surrogates) | added (§2.6) |
| M11 | MED | lone surrogates: byte `0x80` locally, `UnicodeEncodeError` over SSH | strict UTF-8 check naming the argument in every mode (✓) |
| M12 | MED | Fabric `get` runs `str.format` on the local path and makes parent directories; the loopback had no SFTP | Paramiko `SFTPClient` directly; SFTP in the loopback; mutant `sftp-path-formatted` |
| M13 | MED | an empty `HOME` made `~/etc/hosts` read `/etc/hosts` | `${HOME:?…}` (✓ fails 127) and a named error in `expand_local` |
| M14 | MED | delegate resolved relative paths against the server's cwd | delegate programs start in `$HOME`; foreign-cwd delegate row; mutant `delegate-server-cwd` |
| M15 | MED | `[ a == a ]` also passes under `/bin/sh` and `ksh` | probe also requires bash, login, not POSIX mode |
| M16 | MED | the R1 pin was type-only: `[BASH, '-c', f'cat {path}']` passes | `FIXED_SCRIPTS` membership; mutant `fixed-script-interpolated` |
| M17 | MED | test environments kinder than `sudo`/`sshd` (full env, cwd, loopback `killpg` contradicting risk 4) | `env -i` shaped environments, start in `$HOME`, no kill on close (§2.6) |
| M18 | MED | strict base64 refused GNU-wrapped output | whitespace stripped then strict; docstring and a row pin it |
| L1 | LOW | a script starting with `-`/`+` was read as bash options (pre-existing) | `-c --` (✓) |
| L2 | LOW | `put`/`get` returned the directory, a symlink-resolved path, or a `.strip()`ped name | returns the file written, unresolved, read exactly (§2.3) |
| L3 | LOW | Linux's 128 KiB argument cap is invisible on macOS | explicit 96 KiB program limit with a named error, same in every mode |
| L4 | LOW | `sort` order can follow a locale the login profile sets | kept as `sort` (§8 forbids changing the order rule here); risk 9 |
| L5 | LOW | the R10 gate was negative only | `verify/sweep.sh` also asserts the new texts are present |
| L6 | LOW | the `ls` shim never fed the parser | canned GNU listing |
| L7 | LOW | the R2 row hid the `extra_args` carve-out | marked pending in the map |
| L8 | LOW | `executes` patched `Popen` globally | scoped to `rcac_mcp.executor.launch` |

### 4.2 Focused review of the startup separation (§2.2a)

Run 2026-10-17 by a fresh subagent on the amendment alone, against a copy of the prototype, by
execution in `local`, `delegate` (prefix stripped, fake mapped home) and SSH under sh, bash, zsh,
ksh, csh and tcsh. What held: chatty output from every startup file, on both streams, partial lines,
binary, 2 MiB from a remote `.zshenv`, `set -x`/`-v`/`-u`/`-o posix`, closing or redirecting 0–2,
reading stdin, a parked background writer: byte-identical to quiet everywhere; command output kept
exactly (no final newline, empty, binary, 1 MiB + 300 KiB, a here-document last, `exec`, closing
stdout early, output containing its own nonce); a hanging profile gives `('', '', -1001)`. Findings
and folds (each fold re-run against the prototype, ✓):

| # | Sev | Finding | Fold |
|---|---|---|---|
| 1 | HIGH | "did not start" carried the startup's exit status, so a profile `exit 0` (or `exec /bin/zsh`) read as success, and a delegate `put` returned `''` without raising | `NOT_STARTED_EXIT_CODE = -1002`, the note keeps the real code (✓); mutant `not-started-exit-zero` |
| 2 | MED | a profile's EXIT/DEBUG/RETURN traps leaked into results and overrode exit codes | prologue resets traps and `set +euxvT +o errtrace +o posix` (✓); mutant `startup-traps-kept` |
| 3 | MED | the `( script )` logout guard changed `kill $$` (the script kept running) | scripts run in a fresh non-login bash with functions exported (✓ `kill $$` → −15, `exit 4` without logout output) |
| 4 | MED | a profile reusing fd 8/9 swallowed the command's output silently | park on fds 57–59 (✓); a sentinel on one stream only is reported with a note and a WARNING |
| 5 | MED | the stage read `BASH_ENV` before parking (a `read` there ate stdin; a background writer leaked) | stage and bootstrap run `bash --posix`, which reads no startup file (✓); the login bash still gets `BASH_ENV` |
| 6 | MED | over SSH, a background job from the remote login shell's rc file leaks after the sentinel | not separable without a PTY: recorded limit (risk 5, `issues/startup-background-jobs.md`) |
| 7 | LOW | an empty or comment-only script became a syntax error | gone with the fresh-bash form (✓) |
| 8 | LOW | a profile function or alias named `printf`/`command` leaked | `unalias -a; unset -f …` first in the prologue (✓) |
| 9 | LOW | profile `set -e` reached the script (pre-existing) | `set +eu` in the prologue (✓) |

## 5. Risks & open questions

1. **Ruled (GOAL Clarifications, PLAN/TECH gate): `extra_args` on `squeue`, `scancel`, `sacct`,
   `sinfo` becomes `list[str]`**, like `sbatch`'s: each element is one literal argument that reaches
   the program as an option on purpose, never shell-interpreted and never split, placed after the
   tool's own options; a string is refused by schema validation naming the type. This is R2's
   carve-out for the five `extra_args`. The landing lists all five surface changes (P5, P7).
2. **Ruled: the fixture follows by a scripted hand-edit** of `tests/fixtures/served_surface.json` in
   its 2.x shape, copying only the named deltas from the served surface with argument
   `description`s dropped: `sbatch` (description, `args`, `extra_args`), `write_file` (description,
   `encoding`), `run_command` (description), and `extra_args` on `squeue`, `scancel`, `sacct`,
   `sinfo`. `test_surface_fixture_is_the_baseline_shape` still passes; each commit body lists its
   deltas; § Testing and §9 name them. The long-term method stays with
   `issues/surface-fixture-after-a-surface-change.md`.
3. **Ruled: server-side `local_path`.** `~` is the home of whoever touches the file: the mapped user
   in `delegate` (both sides run as that user, same host), the server process's user in `ssh` and
   `local` (`expand_local`). That is R3's "user the command runs as", applied to the server side.
4. **A timed-out command may outlive the call.** Over SSH, closing a non-PTY channel does not stop
   the remote process; the design sends a `signal` TERM request first, but whether the clusters'
   OpenSSH (8.0p1/8.7p1) acts on it is unmeasured. Under a non-root delegate server, `sudo` relays
   SIGTERM to the command only, so grandchildren of a command that ignores it can survive. R8's
   contract (a result, captured output, the marker, no raise) holds either way. Recorded as
   `issues/timeout-process-cleanup.md` (P2), with the candidate fix (a remote `timeout -k` wrapper in
   the bootstrap). Measuring it needs a long-running process on a login node, which is **outside
   the dogfood class**: the seed says the measurement needs Geoffrey's approval, and no phase
   attempts it.
5. **Startup output is separated, not tolerated** (the gate's required change; §2.2a). A login bash
   sources `/etc/profile`, `profile.d` and the user's startup files on every command, and the remote
   login shell runs its own rc files before the bootstrap; none of their output may reach a result.
   Residual limits, recorded in `issues/startup-background-jobs.md` (P2): a startup file that leaves
   a long-lived background process holding the parked descriptors makes the call wait for it or for
   the timeout; over SSH, a background job started by the remote login shell's own rc file (before
   anything can be parked) can write into the channel after the sentinel. A script runs in a fresh
   non-login bash, so a profile's *unexported* shell variables are not visible to `run_command`
   scripts (exported variables and all functions are); sites set what commands need in exported
   variables, as Lmod does.
6. **Program size.** The SSH exec string is one argument; Linux caps one at 128 KiB, so a
   `run_command` script is limited to ≈96 KiB. Content is unaffected (stdin). Documented.
7. **Pre-existing defects found, not in the GOAL:** `_ensure_connected` loops forever once its delay
   reaches 10 s; `list_directory` parsing of names with embedded newlines, symlink arrows or device
   sizes; `sbatch` script filenames collide within one second. Each is recorded as an `issues/` seed
   with a `ROADMAP.md` entry in the phase that meets it (TECH.md names which), not fixed here. (The
   missing connect timeout was in this list until the review showed R9 needs it; it is now P3's.)
8. **Ruled: base64 whitespace.** Under `encoding="base64"`, ASCII space, tab, CR and LF are ignored
   (RFC 2045 line wrapping; GNU `base64` wraps at 76 columns and ends with a newline); any other
   non-alphabet character (including vertical tab and form feed), URL-safe characters, or bad
   padding fails per R5, naming the parameter and leaving the target untouched. A test pins both
   sides (P4).
9. **Locale and `sort`.** `resources.py` keeps `sort`, whose order follows the locale the login
   profile sets, as today it followed the zsh environment's. Making it `LC_ALL=C` is an §8
   compatibility decision, left to `issues/agents-d-instructions.md`'s cycle.
10. **Test time.** The matrix plus the executing loopback server add an estimated 10–30 s per era
   (research 05 §3). The value set is compact by design; if a phase gate exceeds ~90 s per era, the
   value classes are merged, never the modes.

## 6. Verification strategy

- Every phase runs the profile's full `verify` (both eras, `uv lock --check`, the sandboxed `--help`)
  plus its own test modules by name and the mutation cases it plants, by id
  (`uv run python tests/mutants.py --self-check <ids>`). A mutant that survives, or dies for the
  wrong reason, fails the phase.
- Real executions run only under `bin/sandbox.sh`: local programs through `executes`, delegate with
  the exact `sudo` prefix asserted and stripped, ssh through the in-process server. No phase gate
  reaches a cluster or runs `sudo`/`ssh`.
- R9 is also proved through the installed CLI in the sandbox (the raw-wire row).
- R7's `module` half is research 01's recorded evidence, graded by the orchestrator, never a gate.
- P7 runs [`verify/sweep.sh`](verify/sweep.sh) (committed with this plan, so the gate is not
  written by the phase it grades): an AST scan for string-literal commands at any `.run(` call, a
  fixed-string scan for every retired phrasing (found at `04e04c3`) in `src/`, `AGENTS.md`,
  `README.md` and `SECURITY.md`, positive checks that the new contract texts exist (`base.py`'s
  `run` docstring names the argv form, `stdin` and `TIMEOUT_EXIT_CODE`; AGENTS.md §3 names `-1001`;
  §5 names `/bin/bash -l`), and the whole mutation suite.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
