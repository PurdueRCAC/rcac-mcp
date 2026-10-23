# 06 — Local and delegate backends; R10 docs sweep

Probed on CPython 3.14.6 (the locked venv), macOS `/bin/bash` 3.2.57. Nothing was run with sudo;
the sudo behaviour below comes from sudo(8)/sudoers(5), and the uncertain parts are marked **[unverified]**.

## A1. Timeout with partial capture

**What 3.14 does.** `TimeoutExpired.stdout`/`.stderr` are **always bytes**, even with `text=True`
(`Popen._check_timeout` passes `b''.join(...)`; an empty buffer gives `None`, not `b''`). Measured:
`run(['bash','-c','echo out; echo err >&2; sleep 30'], capture_output=True, text=True, timeout=1)`
→ `exc.stdout == b'out\n'`. **Today's code stores those bytes in `CommandResult.stdout: str`**
(`shell.py:89`, `delegate.py:152`). That is a latent bug, and no test catches it.
`subprocess.run` on POSIX does `process.kill()` (SIGKILL to the **direct child only**), then
`wait()`, and never drains. So run() itself does not hang, but grandchildren (`sleep`, a pipeline,
the user's program under `sudo`) are orphaned and keep running, and output written after the
deadline is lost.

**Recommended sequence** (verified for plain, TERM-ignoring, backgrounded, and `setsid`-escaped children):

```python
p = Popen(argv, stdin=PIPE, stdout=PIPE, stderr=PIPE, start_new_session=True)
try:
    out, err = p.communicate(stdin_bytes or b'', timeout=timeout)   # normal path
except TimeoutExpired:
    for sig in (SIGTERM, SIGKILL):
        try: os.killpg(p.pid, sig)
        except OSError: pass          # ProcessLookupError, and macOS EPERM on a zombie-only group
        try:
            out, err = p.communicate(timeout=GRACE)   # drains; returns ALL output, earlier bytes too
            break
        except TimeoutExpired as e:
            last = e
    else:                              # something outside the group still holds the pipe
        out, err = last.stdout or b'', last.stderr or b''
        p.stdout.close(); p.stderr.close(); p.wait()
    exit_code = TIMEOUT_SENTINEL       # never p.returncode
```

- Always pass `stdin=PIPE` and `b''` when there is no content, so a child never inherits the
  server's stdin. On stdio transport that stdin is the JSON-RPC stream.
- The second `communicate()` keeps the earlier partial bytes: CPython keeps per-pipe buffers in
  `_fileobj2output`. Measured: `('term', -15, b'a\n', b'b\n')`.
- **The grandchild hang is real only for an escaped process** (`setsid`, a daemon) that still holds
  the pipe. Backgrounded children in the same group die with `killpg`. A bounded drain plus closing
  our read ends handles it (measured: returns after 2×GRACE). The escaped process keeps running
  (it is outside our group), and its next write gets SIGPIPE.
- macOS returns **EPERM** from `killpg` when every member of the group is a zombie (observed), so
  catch `OSError`, not just `ProcessLookupError`.
- GRACE ≈ 1–2 s. SIGTERM first lets a program flush, and it is also what makes the non-root delegate
  case work (A2).

## A2. Delegate under sudo: killing it

- `start_new_session=True` makes `sudo` a session and group leader (pgid = `p.pid`). **[unverified]**
  Without a tty, sudo's no-pty path runs the command in sudo's own process group. Since sudo 1.9.14,
  `use_pty` is on by default, but sudoers(5) says it "has no effect" when sudo is not attached to a
  terminal, and a new session has no controlling terminal. So `killpg(pid, …)` reaches sudo, the
  command, and its same-group descendants.
- sudo(8), Signal handling: sudo **relays** signals it receives (SIGTERM, SIGHUP, SIGINT/SIGQUIT
  when user-sent, and others) to the command. **SIGKILL cannot be caught or relayed**: killing only
  sudo orphans the command. That is today's exact behaviour, since `run()`'s `process.kill()` hits
  sudo only.
- **Server as root:** `killpg` with SIGKILL reaches every process in the group whatever its uid.
  **Server as a non-root sudo-capable service account** (which SECURITY.md allows): the kernel lets
  it signal `sudo` (real uid = caller) but **not** the command (all uids = target), so `killpg`
  signals only sudo. SIGTERM is then relayed by sudo. That is why the sequence must be
  **TERM → grace → KILL**, not KILL alone. A command that ignores TERM survives under a non-root
  server. That is a known limit; document it.
- When the command dies by a signal, sudo kills itself with the same signal (sudo(8), EXIT VALUE).
  So `returncode` shows `-N` through sudo as well. **[unverified on the target sudo version]**
- PAM session modules (pam_systemd) move cgroups, not process groups. **[unverified]** They should
  not affect `killpg`.

## A3. HOME, `~`, and transfers

- `-H` asks the policy to set `HOME` to the target user's home. With the default `env_reset`,
  sudoers also initialises HOME, SHELL, USER and LOGNAME from the target user. **[unverified]** If
  a site adds `env_keep += HOME`, I believe `-H` still wins. A test should keep pinning `-H` in the
  argv. So inside the delegated bash, `"$HOME"/'rest'` is the mapped user's home, which satisfies
  R3. Keep resolving `~` **inside** the program, never in Python.
- `os.path.expanduser` also expands `~name` (measured: `~root/x → /var/root/x`). That violates the
  GOAL's "`~name` stays literal" rule in **every** place it is used today: `shell.py:71,105-106,119-120`,
  `delegate.py:130,168-169,191-192`, and `tools/transfer.py:62`. Local put/get need a helper that
  expands only `~` and `~/`.
- **`put`/`get` today** expand both paths with the server's `HOME`, pass them without `--`, and
  do part of the file I/O in the server process rather than as the mapped user (`put` copies with
  `sudo -n cp` and then `chown`s; `get` writes `local_path` from the server process). Under the
  restrictive sudoers example (`(alice,bob) NOPASSWD`), `put` fails unless the server is root.
- **Proposal.** In delegate mode, "local" and "remote" are the same host, so do both sides as the
  mapped user and keep every file access out of the server process:
  `put(l, r)` = `self.run(['cp', '--', UserPath(l), UserPath(r)])`, and `get(r, l)` is the same with
  the arguments swapped. Raise on a non-zero exit with stderr. This is byte-exact, uses no memory,
  needs no chown, and `~` resolves in the program. If the plan prefers the streamed form, the read
  side must also run as the user: `run(['cat','--',UserPath(l)])` → bytes →
  `run(['bash','-c','cat >"$1"','put',UserPath(r)], stdin=bytes)`. Reading `local_path` in Python
  would keep that side outside the mapped user's permissions. `download_file`'s return value should come from the
  executor (e.g. `readlink -f -- <path>` as the user), not from `os.path`. Local mode can keep
  `shutil.copy2` with the `~`-only helper.

## A4. Exit codes

`Popen.returncode` is 0..255, or `-N` for death by signal N (measured: `-15`, `-9`, and `-1` for
SIGHUP via `kill -HUP $$`). Bash reports a child **it** saw die by a signal as `128+N` (measured
`137`, and `143` for the outer `kill -TERM $$` seen by an outer bash). A launcher that ends in
`exec <argv>` lets the target's signal death surface as `-N`. A script whose last command is a pipeline
or a compound command gives `128+N`. Both stay inside -64..255, so **-1001 cannot collide**.
`CommandResult` is `run_command`'s output schema, pinned in `served_surface.json` (`outputSchema`:
exactly `stdout, stderr, exit_code, hostname`). **A new `timed_out` field changes the pinned
surface, but a sentinel alone does not.** That favours sentinel-only, with a stderr note such as
`timed out after Ns`. Put the note on stderr only if the plan accepts mixing it into user stderr.
Otherwise leave it out and let the exit code speak.

## A5. Decoding

Today's `text=True` decodes with the locale codec and `errors='strict'`. **Measured: any non-UTF-8
byte raises `UnicodeDecodeError` out of `run()`**, so `run_command('cat binfile')` crashes.
Recommendation: bytes I/O, then decode once, at the end, with `out.decode('utf-8', 'replace')`
(decoding per chunk can split a multi-byte sequence). `'surrogateescape'` is wrong here: measured,
`pydantic_core.to_json` raises `PydanticSerializationError … surrogates not allowed`, so the tool
result would fail to serialise. `'replace'` also matches Invoke's `Runner.decode`
(`data.decode(encoding, "replace")`), so ssh mode decodes the same way.

## A6. Which bash

Use an absolute **`/bin/bash`** for local and delegate. It is deterministic. Under sudo, `bash`
would resolve through `secure_path`, and a restrictive sudoers rule can name `/bin/bash` exactly.
It also matches the ssh bootstrap. RHEL's `/bin` → `/usr/bin` symlink makes it exist on clusters.
On a Mac, `/bin/bash` is 3.2. Measured working there: `cd -- 'x' || exit`, `exec -- printf …`,
`"$HOME"/'rest'`, `cat >"$1"` with `$0`/`$1` positionals, `shlex.quote` output including
newlines. **Avoid in the launcher** (bash 4+): `${v@Q}`, `mapfile`/`readarray`, `declare -A`,
`${v,,}`/`${v^^}`, `|&`, `&>>`, `;&`/`;;&`, negative array subscripts, `${arr[-1]}`, `wait -n`, and
`BASH_ARGV0`. `run_command` scripts may use what the host's bash has. That is the user's concern.

## B. R10 docs sweep

**Agent-facing (§9; every change here moves `tests/fixtures/served_surface.json`)**
- `tools/shell.py:26-28`: `command: The shell command` → it runs under bash. `cwd (supports ~
  expansion)` → "a leading `~` or `~/` is the running user's home; nothing else expands". `timeout`
  → "on timeout the result carries partial output and exit_code -1001".
- `tools/filesystem.py:41,127,164` and `tools/transfer.py:21,46`: "(supports ~ expansion)" gets the
  same narrowed wording. `transfer.py` `local_path` lines (`:20,47`) should say whose filesystem
  is meant in delegate mode.
- `tools/slurm.py:79` `extra_args … as a string` → list (R6); also the new `args` and `write_file`
  `encoding` lines. **Flag:** `squeue`/`scancel` `extra_args: str` (`slurm.py:178,193,242,258`) are
  also caller strings. Under R2 they would become one literal argument, which breaks
  `"-u alice -t R"`, but R6 changes only `sbatch`. The plan must rule on this.
- `server.py:81` `SERVER_INSTRUCTIONS` "run_command: Execute shell commands" is still true. Saying
  "bash" is optional and would change the fixture's `instructions`.
- `run_command` `outputSchema`: unchanged only if no field is added (A4).

**Internal (no fixture impact)**
- `executor/base.py:36-53`: argv-or-script protocol, `stdin`, the timeout contract and sentinel,
  and the pinned bash.
- `executor/shell.py:4,28,37,62,69-93`: "$SHELL" in the class docstring and code.
- `executor/delegate.py:85,115,122-135,150-156,158-201`: the `_shell` attribute, put/get docstrings
  ("readable by root").
- `executor/ssh.py:115-138`: the docstring and the `cd {cwd}` comment.
- `AGENTS.md:134` repo map ("subprocess via `$SHELL`"); `:166` diagram ("local $SHELL");
  `:244` testing bullet (`<$SHELL> -c` argv, "$SHELL captured at construction"); `:713-714`
  footgun ("`shlex.quote` on `cwd` only, the server's `$SHELL`"); the ssh footgun ("*uncaught*
  Fabric timeout"); `:774-781` §3 (both the `-1` bullet and the asymmetry bullet are replaced by
  the sentinel and the one contract, and the sentinel is documented there first, per §3);
  `:823-827` §5 (the argv `sudo -n -H -u <user> /bin/bash … -c <program>`, the `_shell` bullet,
  and what is quoted); §2 (the `run` signature, if it is listed).
- `README.md:31` and `__init__.py:81` (APP_HELP): "locally via `$SHELL`" → "locally under bash".
- `SECURITY.md:54` diagram "local $SHELL". `:94` is still true. Add that transfers run as the mapped user
  if it lands.
- Test docstrings that describe today's argv and are rewritten with the tests:
  `tests/test_executors.py:6-11,52`, `test_no_executor.py:79`, `test_isolation.py:58`,
  `test_identity.py:44`, `mutants.py:254-275`.
- `tools/filesystem.py:182` and `slurm.py:114` heredoc code and comments go with the code.
- History, left as is: `ROADMAP.md:76-78` (an "Absorbed" entry, closed by the landing convention),
  both seed issues, and `spec/restore-test-coverage/*`. `INSTRUCTIONS.md` and `compose.yml` have no
  hits.

## Transfers run as the mapped user

Outcome to aim for: in `delegate` mode every file access a transfer makes, on both sides, happens as
the mapped user through the same launch as `run`, so a transfer can do exactly what that user could
do with `cp`, and nothing more. The A3 proposal delivers it as part of R3.
