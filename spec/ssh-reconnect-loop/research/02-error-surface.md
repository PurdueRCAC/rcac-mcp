# Research 02 — Where a reconnect failure surfaces, and what each caller shows the agent

Paths are relative to the worktree; line numbers are as of `d34de62`.

## 1. Call paths and what the agent sees

**Into `_ensure_connected`** (`executor/ssh.py:455-482`): `run` (`:513`, after the exec string is
built, so an oversize program still fails first, pinned by
`test_an_oversize_program_is_refused_before_connecting`); `write` (`:574`, SFTP open, *before* any
command), then its inner `self.run` for `MAKE_PARENT` (`:577`), the place step (`:615`) and the
`_reason` probe (`:627`); `put` (`:649`); `get` (`:669`). `open()` (`:442-447`) is the startup path
and is not reached through `_ensure_connected` at startup.

**How a failure reaches the agent.** Tools are sync functions. FastMCP 4.1 runs them on an anyio
worker thread and turns any exception from the body into
`ToolError("Error calling tool '<name>': <str(exc)>")` (`.venv/.../fastmcp/server/server.py:1494-1532`;
`mask_error_details` is off; its `is_timeout_error` matches only httpx timeouts, so an
`OSError`/`TimeoutError` text is never replaced). `ToolErrorMiddleware` (`middleware.py:44-51`) lets
that `FastMCPError` through unchanged. The agent sees an error result reading
`Error calling tool '<name>': <message>`, the same in both eras (`tests/test_identity.py:171` and
`tests/test_no_executor.py:216` pin this form through the era-switched harness). A `CommandResult`
is a normal (non-error) result.

| Tool (file:line) | Executor calls | (a) `run` → `CommandResult` −1002 | (b) raised `ConnectionError`/`OSError` |
|---|---|---|---|
| `run_command` (`tools/shell.py:52`) | run | returned as is: exit −1002, stderr shown | tool error, raw text |
| `list_directory`, `read_file` (`filesystem.py:68`, `:128/130`) | run | `RuntimeError('Failed to … : <stderr>')` | tool error, raw text |
| `write_file` (`filesystem.py:193-198`) | write | `Failed to write file (exit -1002): <stderr>` | caught as `OSError` (`ConnectionError` is one): `Failed to write file: <msg>` |
| `upload_file`, `download_file` (`transfer.py:43`, `:64`) | put / get | n/a | tool error, raw text (no wrapper) |
| `myquota`, `jobinfo`, `jobcmd`, `jobenv`, `jobscript`, `showpartitions`, `average_wait` (`rcac.py:53,80,107,134,161,185,227`) | run | `Failed to get …: <stderr>` | raw |
| `storage_paths` (`rcac.py:362`, `:368`) | run ×2 | first failure raises; one window | raw |
| `sbatch` (`slurm.py:158-162`, `:180-188`) | write (content only) then run | `Failed to submit job: <stderr>`; only −1003 has its own branch | write: `Failed to write job script: <msg>` |
| `squeue`, `scancel`, `sinfo`, `scontrol_show_job/node`, `slist`, `sfeatures` (`slurm.py:247,305,415,443,477,501,525`) | run | `Failed to …: <stderr>` | raw |
| `sacct` (`slurm.py:361-370`) | run ×2 | **`whoami`'s exit is unchecked**: a −1002 gives `-u ''` and a second full reconnect window, about 2× the bound | raw |

Findings that bear on R1/R4:
- **`sbatch` (R4).** A −1002 shows `Failed to submit job: cannot connect …`. Unless the note itself
  says so, nothing states "not submitted". Add a `NOT_STARTED_EXIT_CODE` branch beside the −1003
  one (`:182-186`), e.g. `The job was not submitted: <stderr>`. It is correct for every −1002
  cause. The content path already fails at the write, before any submit.
- **`sacct`** must check `whoami` (or stop on −1002), or it breaks R1's bound.
- **`write` / `_reason`.** `_reason` (`:620-633`) swallows a raise (returns None), but a −1002
  return falls through: −1002 is not in its `(0, TIMEOUT, LOST)` tuple (`:630`). The last stderr
  line, split at `': '`, would then be shown as the "reason", and the probe could spend a whole
  reconnect window inside a write whose link dropped mid-transfer. Add −1002 to the tuple, or skip
  the probe when the transport is down.
- **Resources (not tool calls).** `load_cluster_context` (`resources.py:51-55`) caches `''` for the
  host on *any* non-zero exit. Once `run` returns −1002 instead of hanging, an outage at the first
  `rcac://context` read empties the context for the life of the process. The per-file `cat` loop
  (`:66`) would also spend one window per file. Don't cache on −1002 (or on any exit but a clean
  "none found"). `rcac://storage` goes through `storage_paths` (one window).

## 2. `connect_failure_message` and its home

`startup.py:84-139` (`_chain`, `_refused`, `_cause_class`, `_detail`, `connect_failure_message`,
`CHAIN_DEPTH`) imports only stdlib and Paramiko. Today `rcac_mcp/__init__.py:25` imports it at top
level and imports `executor.ssh` lazily (`:230`). Nothing in `executor/` imports above
`executor.base`/`launch`. `executor/ssh.py` importing `rcac_mcp.startup` creates **no import cycle**:
startup imports nothing from the package, and the package `__init__` has already run. It is a
**layering inversion**, though: the backend would reach up into the CLI's "server that cannot start"
module (§ Conventions, "put logic where it belongs").

**Move it into `executor/ssh.py`.** It is SSH-connect knowledge, the file already imports
`SSHException`, and the move adds no module and no repository-map row. `startup.py` then imports
it from there (or `__init__.py:25` does), keeping its `__all__`/name if desired.

What pins the current home and text:
- `tests/test_startup.py` imports only `answer_until_eof` from `rcac_mcp.startup` (`:47`). The
  message is checked end to end through `MCPServerApp` (`CAUSES` `:248-259`,
  `test_connect_failure_names_host_and_cause` `:300-314`, `:331`), so **a move breaks none of them**.
- `tests/mutants.py:1038-1043` `startup-host-unnamed` edits `startup.py`'s `cause = _cause_class(exc)`.
  A move turns `test_mutation_anchor_applies[startup-host-unnamed]` red (`tests/test_mutations.py:40`),
  so the case must be re-planted on the new file in the same commit, with the reason in the commit
  body, and the case re-run.
- `mutants.py:1056-1061` `startup-no-connect-timeout` anchors on `open()`'s
  `self._conn = Connection(self._host, **_bounded(self._connect_kwargs))`. Any rewrite of `open()`
  re-plants it.
- The test fakes `Failing`/`Refusing` (`test_startup.py:304-309`, `:320-325`) have no `close()` or
  `is_connected`. If `open()` starts closing a failed `Connection`, that close must be best effort,
  or the fakes must gain one (see also the `Connection` fakes in `test_exec_mode.py` and
  `test_executors.py`). Otherwise an `AttributeError` replaces the startup cause.
- Docs: the AGENTS.md repository-map rows for `startup.py` (`:131`) and `executor/ssh.py` (`:140`).

A new note constant: if it goes in `launch.py`, its `__all__` (`:72`) lists notes. If it is
SSH-only, it belongs in `ssh.py`. `SERVER_INSTRUCTIONS` and the tool descriptions list no notes.

## 3. Every text on −1002, −1003, reconnection, `MAX_RECONNECT_DELAY`

- `executor/base.py:22-24` (comment: −1002 = login init ended or launch failed), `:105-108`
  (`Executor.run` Returns: the −1002 causes), `:164-169` (`write` Raises `OSError`: "content could
  not be staged"), `:173-206` (`put`/`get`: no Raises section).
- `executor/ssh.py:4-11` (module docstring), `:46-48` (constants comment), `:409-415` (class
  docstring: "automatic reconnection"; R5's bound goes here), `:455-461` (`_ensure_connected`
  docstring), `run`/`write`/`put`/`get` docstrings.
- `tools/shell.py:39-43` (`run_command` description: "-1002 means the command never started";
  −1003). This **stays accurate** for a connect failure. Leave it alone: any change trips
  `tests/fixtures/served_surface.json` (`test_served_surface_matches_baseline`) and needs the
  scripted fixture edit (§9).
- `tools/slurm.py:182-186` (−1003 sbatch message, code not description).
- `README.md:26-28` (startup `cannot connect to`, 15 s per step) and `:56-58` (−1001, −1003; no
  −1002). Not required by R5. An optional sentence on the post-startup bound would need no fixture
  change.
- `AGENTS.md`: `:131`, `:140` (map rows; `:140` says "reconnect with exponential backoff");
  `:1097-1098` (the footgun that R5 replaces); §2 write bullet `:1196-1199` (`OSError` over ssh);
  **§3 `NOT_STARTED_EXIT_CODE` bullet `:1249-1256`**; §3 −1003 bullet `:1258-1270` ("never −1002");
  § Testing for new rows.
- `SERVER_INSTRUCTIONS` (`server.py:65-114`): **nothing** on exit codes or connection, so no change.
- `ROADMAP.md:30,63-74` and `issues/ssh-reconnect-loop.md`: landing housekeeping.
- `docs/` does not exist; `CLAUDE.md` is a symlink to `AGENTS.md`.

**§3 contract text does change.** The −1002 bullet lists exactly two causes and the exact stderr of
each: the profile/launch line and `TAKEN_NOTE`. A connect failure is a third cause with a third
stderr form, so that [human gate] text must gain it in the same commit, even though no code is
added to `base.py`. The same goes for `base.py`'s comment and `Executor.run` docstring (§2's
gated file). Q3's "§3 is unchanged" holds only in the sense of *no new reserved code*.

## 4. Concurrency

There is one `SSHExecutor` per process (`__init__.py:192-194`), and `SharedExecutorMiddleware`
re-sets that same instance on every call. The MCP dispatcher spawns a task per request
(`mcp/shared/jsonrpc_dispatcher.py:590-623`), and each sync tool runs on its own worker thread, so
concurrent calls hit `_ensure_connected` concurrently. **There is no lock anywhere in `src/`.**
Races today:
- (i) Thread A sets `self._conn = ConnA` and opens it, then thread B sets `self._conn = ConnB`.
  A's `return self._conn` hands back B's possibly half-opened `ConnB`, and
  `.client.get_transport()` is `None` → `AttributeError: 'NoneType' … open_session`. That error
  names neither host nor cause, so it fails R1 for that call.
- (ii) `ConnA`, opened and live, is orphaned and never closed: a leaked transport thread and socket.

There is a similar leak on every failed attempt. Paramiko 5's `SSHClient.connect` does not close
the transport when auth or host-key checks fail after `start_client`, and `open()` overwrites
`self._conn` without closing it.

R1–R3 per call do not need a shared reconnect, but each call does need (i) fixed. Minimum fix:
`open()`/the retry builds the `Connection` in a local, closes it (best effort) on failure, and
publishes it under a short `threading.Lock` only on success. If another thread already published a
live one, use that and close the local one. If the lock is held across the whole reconnect instead
(which serialises it, also fine), a call's wait for the lock must count against its own deadline,
or a queued call exceeds the bound.

## 5. R3: nothing makes an outage permanent

The server process doesn't exit: a tool exception becomes an error result and nothing re-raises it
(`ToolErrorMiddleware` catches `Exception`, `__init__.py:198-209` only wraps `mcp.run`). Nothing
caches the executor as failed: `_create_executor` runs once at startup, and `_conn` is re-checked
on every call. Two hazards remain:
- **A failed `Connection` left in `_conn` can read as connected.** After an authentication (or
  host-key) failure the Paramiko transport stays `active`, so `is_connected` is True. The next call
  then skips reconnecting and fails on `open_session` with a raw Paramiko error, possibly after
  `OPEN_TIMEOUT` (30 s). This is likely from Paramiko's code but not yet driven against the
  in-process server, so verify it. Assigning only on success fixes it (§4).
- `resources.py:51-55` caches an empty context (§1).

## Recommendation

- **`run`:** on reconnect failure, return
  `CommandResult('', '<connect_failure_message>; the command was not run\n', NOT_STARTED_EXIT_CODE, host)`.
  Don't raise.
- **`write`:** return the same −1002 result when the *initial* `_ensure_connected` fails. This
  matches Q3 (it returns a `CommandResult`) and gives `write_file` `Failed to write file (exit -1002): cannot connect …`
  and `sbatch` `Failed to write job script (exit -1002): …`. Raising `ConnectionError` would also
  read well, because both tools catch `OSError`, but it would widen `Executor.write`'s documented
  Raises in `base.py` and AGENTS §2. Inner `run`s already yield −1002. Fix `_reason` to treat −1002
  as no reason.
- **`put`/`get`:** raise `ConnectionError(<message>; nothing was transferred)` with a single
  argument, so `str(exc)` is the message. Agent sees: `Error calling tool 'upload_file': cannot connect to <host>: <cause>; …`.
- **Tools:** add an explicit `sbatch` −1002 branch ("not submitted"); check `sacct`'s `whoami`; stop
  `load_cluster_context` caching on −1002.
- **Helper:** move `connect_failure_message` and its private helpers into `executor/ssh.py`, with
  `startup.py`/`__init__.py` importing it from there.
- **Connection lifecycle:** build locally, close on failure, publish under a lock on success.
- **Same-commit texts:** AGENTS.md §3 −1002 bullet, `:1097-1098` footgun (R5), `:140` map row (and
  `:131` if moved), § Testing rows, and §2 write bullet only if `write` raises. `base.py:22-23`,
  `:105-108` (and `put`/`get`/`write` Raises if raising). `ssh.py` module, class (R5 bound and
  ssh_config `ConnectTimeout` effect), constants and method docstrings. `tests/mutants.py` anchors
  `startup-host-unnamed` and `startup-no-connect-timeout` re-planted. **Not** `run_command`'s
  description, `SERVER_INSTRUCTIONS` or `served_surface.json`. README is optional.
