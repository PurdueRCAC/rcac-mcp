# Research 05 — test impact and test design

Baseline: `uv run pytest -q` (legacy era) = **423 passed in 17.5 s** (18.9 s wall). Measured launch
costs on this host: `bin/sandbox.sh env HOME=… bash -c …` ≈ **18 ms**, a bare `bash -c` ≈ 3 ms.

## 1. Tests that pin the string-command shape

- **`tests/test_executors.py`** (deliberate rewrite): `sudo_argv_violations`, `ARGV_ROLES`
  (`'shell'`, `'command line'`), `command_line_for`, `SHELL_A/SHELL_B`;
  `test_sudo_argv[no-cwd|cwd-spaces|cwd-metacharacters]` (expects `[sudo,-n,-H,-u,ubob,$SHELL,-c,
  'cd <quoted> && <cmd>']` and `shlex.split(argv[-1])[:3] == ['cd', cwd, '&&']`);
  `test_shell_is_captured_at_construction` (**retire**: asserts `$SHELL`; replace with "argv names
  bash whatever `$SHELL` is", R7); `test_argv_check_catches_drift[n-dropped, H-dropped,
  command-quoted, cwd-unquoted, wrong-shell, wrong-user]` (`wrong-shell` → `wrong-interpreter`).
  `FakeConnection` only needs new members if `SSHExecutor.__init__` touches more of `Connection`.
- **`tests/harness.py::RecordingExecutor.run(command: str, cwd, timeout)`** records
  `('run', command, cwd, timeout)`: add `stdin`, record the command as given (list or str).
- **`tests/test_tools.py`**: responders compare strings — `ls_listing` (`startswith('ls ')`),
  `quota_and_scratch` (`== 'findscratch'`, `== 'myquota'`), `submitted` (`startswith('sbatch')`);
  the `write_file` row's `reaches='written-by-the-tool'` will only be in stdin; `CALLS =
  [('run', 'sinfo -p gpu', None, None)]` in the controls. Add to `tool_violations` the R1 pin: any
  recorded `run` whose command is a `str` from a tool other than `run_command` → `passed a command
  string` (plus a control). Add `sbatch` `args`/`extra_args` list values to its row, and a separate
  R6 test: `extra_args='--x'` refused naming the array type.
- **Launch-marker readers** — `RecordedPopen` answers `<user>|<argv[-1]>`, and `test_identity.
  delegation_violations`, `test_isolation.launched_as`, `test_no_executor.launched_as` /
  `a[-1] != OTHER`, `test_protocol` (delegate rows) all require **`argv[-1] == run_command's
  script`**. They survive unchanged only if the launcher (a) keeps PROGRAM last (no trailing `$0`
  after `-c PROGRAM`) and (b) renders a script with no `cwd` as exactly itself. Otherwise give them
  one shared decoder (`launched_script(argv) -> (user, script)`). Their docstrings say
  `<shell> -c` — R10 wording.
- **`tests/test_ssh_smoke.py`**: `smoke_violations` requires `received == [command]` and
  `stdout == 'smoke:'+command`; with a bootstrap the server receives the wrapper, so the fake server
  must decode or execute it; the `command-rewritten` control changes meaning.
- No test besides `test_executors.py` (and the mutant `shell-read-per-call`) reads `$SHELL`.
- **Hazard**: `RecordedPopen.pid = 4242` is real-looking and the unit guard does not patch
  `os.kill`/`os.killpg`. A unit timeout test with a fake Popen must patch `killpg`, or a
  process-group kill could hit a real pid.

## 2. `tests/mutants.py` (68 cases) — anchors this cycle edits

| case | file | verdict |
|---|---|---|
| `tool-builds-local-executor` | tools/slurm.py (sinfo `f'-p {partition}'`) | re-plant on new sinfo text |
| `tool-builds-ssh-executor` | tools/rcac.py (`executor.run('myquota')`) | re-plant (`['myquota']`) |
| `transfer-local-fallback` | tools/transfer.py | keep, if transfer.py is untouched |
| `sudo-n-dropped`, `sudo-H-dropped` | delegate.py `SUDO_PREFIX` | re-plant, same contract, new targets |
| `cwd-unquoted` | delegate.py `f'cd {shlex.quote(cwd)} && …'` | re-plant into `launch.py` (cwd rendered raw) |
| `command-quoted` | delegate.py `full_command = command` | re-plant into `launch.py` (script quoted) |
| `shell-read-per-call` | delegate.py `self._shell` | **retire**, replaced by `interpreter-from-shell-env` |
| `ssh-exit-status-dropped` | ssh.py `exit_code=result.exited` | re-plant on the channel's `recv_exit_status()` |
| `executor-unmapped-default`, `map-*` (4), `delegate-hostname-renamed`, `local-put-renamed`, `ssh-close-renamed` | executor/* | anchors likely survive; `test_mutation_anchor_applies` says |
| `sse-transport-unserved`, ladder cases | `__init__.py` | survive unless R9's change touches `run()` (`mcp.run(transport='sse'…)` is near) |

All others (context, middleware, auth, token, server) are outside this cycle. **New plants**
(target ids in brackets are suggestions):

- `tool-command-string` — `read_file` back to `executor.run(f'cat {path}')` [test_tools R1 row; matrix `local-read_file-path`].
- `launcher-arg-unquoted` — drop `shlex.quote` on one argv element [launcher unit test; matrix rows].
- `launcher-tilde-user-expanded` — `~name/x` rendered via `$HOME` or `~` left bare for bash [R3 rows].
- `delegate-tilde-server-home` — `os.path.expanduser` before sudo (today's bug) [R3 delegate cwd + transfer rows, why `server HOME`].
- `interpreter-from-shell-env` — local/delegate use `os.environ['SHELL']` [R7 row, `SHELL=/bin/zsh`].
- `ssh-bootstrap-dropped` — PROGRAM sent raw to the login shell [R7 ssh row under zsh/tcsh login].
- `ssh-timeout-raises` — re-raise on deadline [R8 ssh row, `raised`].
- `timeout-sentinel-minus-one` — sentinel set to `-1` [R8 sentinel check, `indistinguishable`].
- `timeout-partial-output-dropped` — empty stdout on timeout [R8 rows].
- `write-final-newline` / `append-overwrites` / `content-in-argv` [R4 hash rows; unit "content in no argv element"].
- `base64-unvalidated` (`b64decode` without `validate=True`), `encoding-unknown-accepted` [R5].
- `sbatch-args-joined`, `extra-args-accepts-str` [R6].
- `startup-error-unanswered` — R9 path exits without answering [R9 `legacy`/`modern` rows].

Mutant children run single rows, so give the matrix stable ids (`<mode>-<tool>-<param>`).

## 3. The R2/R4 matrix without a cluster or sudo

**Shape:** `tests/test_literal_args.py` (`integration`), parametrised `mode × (tool, param)`, all
values looped inside one row (≈52 string params today: avg_wait 2, download 2, list_dir 1,
read_file 2, run_command.cwd 1, sacct 8, sbatch 14, scancel 6, scontrol_show_node 1, sinfo 5,
squeue 5, upload 2, write_file 3). Values: space (`My Data`), quotes, `$HOME$(id)`, `*?[a]`,
newline, `;|&<>()\`\\!#{}`, leading `-n`/`--help`; plus R3's `~`, `~/x`, `~name/x` for path params
and R4's content set (no final newline, empty, a `RCAC_MCP_EOF` line, NUL, CRLF; base64 binary;
overwrite and append). Drive tools through `in_memory(shared_server(executor))`, so the served
schema validation is in the loop.

**Coverage proof (unit):** `ORACLES: dict[(tool, param), Oracle]` hard-coded, and
`test_literal_matrix_covers_every_string_param` compares its keys with every property of every
tool in `served_surface(create_mcp_server('none'))` whose type includes `string` or `array of
string`, minus `('run_command', 'command')` — the `test_tool_cases_cover_expected_tools` pattern; a
new parameter fails it. Oracle kinds: **path** (effect on a fixture file named exactly the value),
**option value** (argv-echo shim shows it as one element, or one `--long=value` element),
**content** (sha256 on disk), **local path** (server-side file, `download/upload.local_path`),
**enum** (`write_file.encoding`: metachar values refused naming the parameter; `read_file.encoding`
is served but ignored today — decide its oracle).

**Fixture tree:** `HOME` = the tree, holding files named by each value; argv-echo shims
(`printf '%s\0' "$0" "$@"` to a log) on PATH for `sbatch squeue sacct scancel sinfo scontrol jobinfo
jobcmd jobenv jobscript myquota findscratch showpartitions slist sfeatures`, plus whatever
`average_wait` calls. Real `ls`/`cat`/`head` act on the tree. Process cwd = tree, so a relative
`-n` is tested as a leading dash.

**(a) local** — the unit guard refuses launch, so `integration`. A new conftest fixture
`executes` (sibling of `launches`) replaces `subprocess.Popen` with a subclass carrying
`test_fence = True` (the harness fence accepts it) that refuses any argv whose program is not
bash, and launches `[str(SANDBOX), 'env', f'HOME={home}', …]` (cwd = tree). The static hygiene check
cannot see a launch inside a fixture (as for `stdio_tools`), so add self-tests in `test_hygiene`:
non-bash argv refused, every real launch starts with the sandbox.
**(b) delegate** — the same fixture: argv must start with exactly `['sudo','-n','-H','-u',<mapped>]`
(else refuse, never launch), strip those five, set `HOME` to a fake *mapped* home distinct from
the server process's `HOME` (simulating `-H`), run the rest. No argv names sudo, so the PATH shim
and `test_cli_drives_are_sandboxed` hold. R3: `cwd='~'` → `pwd` prints the mapped home;
`write_file('~/x')` lands there, never under the server's `HOME`. This needs `put`/`get` to go
through the same `sudo -n -H -u <user> bash -c` launch (today `put` copies with `sudo -n cp`);
if they keep root `cp`, the fixture needs a second, narrower rule.
**(c) ssh** — extend `test_ssh_smoke`'s `_Server` into a module-scoped fixture: exec requests run
`[str(SANDBOX), 'env', f'HOME={home}', <login shell>, '-c', <exec string>]` (`start_new_session`),
pump channel stdin to the process until EOF (this tests `shutdown_write`), send stdout/stderr and
the exit status, and `killpg` on channel close (R8). Parametrise the login shell over
`/bin/bash`, `/bin/zsh`, `/bin/sh`, `/bin/tcsh` (all present here; `fish` is not) for the bootstrap and
R7 rows. One connection per module; generate keys once (ECDSA is faster than RSA-2048).

**Login-shell hazard:** if the plan pins `bash -l`, every real execution sources `/etc/profile`;
macOS `path_helper` then puts `/usr/bin` ahead of the PATH shim, so a `sudo`/`ssh` inside a
command reaches the real binary. The fixture must check this, for example by asserting
`command -v sudo` resolves to the shim from inside a launched program.

**R8/R7 rows** reuse the same fixture: `echo partial; sleep 30` with `timeout=0.5` → partial
stdout, failure, the sentinel (`< -1000`), in all three modes; unit controls show a SIGHUP death (`-1`) is
not the marker. R7: `[ a == a ]` with ssh login shell zsh vs bash, and local/delegate with
`SHELL=/bin/zsh`.

**Cost:** about 52 × 8 values × 3 modes, roughly 1,250 tool calls at 18–25 ms each, is ~30 s per era, ~60 s
added to `verify`. If that is too much, merge the metachar classes into one value per param
(~10 s/era) and keep separate values only for space, newline and leading `-`.

**Gate question (raise it):** `sacct`, `scancel`, `sinfo`, `squeue` also take `extra_args: str`.
R2 makes each one literal argument, which breaks `extra_args='--me --long'`. R6 only re-types
`sbatch`'s. Pick one: literal (a documented behaviour change), `list[str]` for all of them (a
surface change beyond the GOAL), or amend R2. The `list_directory` helpers `echo $HOME`/`pwd` must
also become argv (R1). `path.replace('~', home, 1)` mis-resolves `~name` (R3).

## 4. The served-surface fixture

Before the fixture is edited, `test_served_surface_matches_baseline` will report:
`sbatch: description differs`; `sbatch: argument added: args`; `sbatch: argument extra_args anyOf
differs: [{string},{null}] -> [{array of string},{null}]`; `write_file: description differs`;
`write_file: argument added: encoding`. It will also report `<tool>: description differs` for any
other reworded docstring (for example "supports ~ expansion" in `read_file`/`list_directory`), and
`instructions differ` if `SERVER_INSTRUCTIONS` changes.
Served shapes (measured on FastMCP 4): `Optional[list[str]]=None` →
`{"anyOf":[{"items":{"type":"string"},"type":"array"},{"type":"null"}],"default":null}`;
`Literal['utf-8','base64']='utf-8'` → `{"default":"utf-8","enum":[…],"type":"string"}`.

**Least risk:** a scripted, restricted hand-edit in the 2.x shape. Copy only the named deltas
(`tools.sbatch.description`, `…properties.args`, `…properties.extra_args`,
`tools.write_file.description`, `…properties.encoding`) from `served_surface` with each property's
`description` key dropped, and change nothing else. The commit body lists the deltas.
`test_surface_fixture_is_the_baseline_shape` still passes: no `additionalProperties`, no argument
`description`, the tool set is unchanged, and `$defs` is untouched. Update the docstring of
`test_surface.py`, AGENTS § Testing and §9 ("snapshot of `3d010ec`" + named deliberate deltas).
Alternative if the foreman wants the fixture to stay byte-identical to the re-derivable snapshot:
an `AMENDMENTS` list in `test_surface.py` (cycle, tool, key path, expected old value, new value),
applied by `load_fixture`, each asserting its old value first. §9 says to raise this before the
change, so it is a gate item. **`EXPECTED_TOOLS` is unchanged** (no tool added).
`test_instructions` is unaffected unless `SERVER_INSTRUCTIONS` is reworded; if it is, the fixture's
`instructions` field and the README change in the same commit.

## 5. Eras

`verify` runs `uv run pytest -q` then `CLUSTER_MCP_TEST_ERA=modern uv run pytest -q`. The matrix
is era-neutral and simply runs in both passes, which doubles its cost. **R9 must be pinned per
era on its own rows** (`test_startup_failure_is_answered[legacy|modern]`): the first request is
`initialize` in one era and `server/discover` in the other. This needs a harness verb like `stdio_tools` that
returns the first response's error, and it should drive `--ssh-host 127.0.0.1:<closed port>`
(refused on loopback; not `.invalid`, which makes a DNS lookup) under the sandbox. The R6
schema-refusal row may optionally pin both eras; nothing else needs both.
