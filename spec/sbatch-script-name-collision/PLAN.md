# PLAN — `sbatch` job scripts written in the same second share a name

> **Status:** Draft for review · **Last updated:** 2026-10-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). No `research/`: the lean path (`kind: fix`,
> `appetite: small`), and no research trigger fired (§4). Every design element traces to a GOAL R-ID.

## 1. Summary

Give every job script `sbatch` writes from content a name no other call can draw: append a
16-hex-digit token from the operating system's entropy source (`secrets.token_hex(8)`) between the
existing `<name>_<YYYYmmdd_HHMMSS>` prefix and `.sh`. Distinct names make the overwrite impossible,
so the executor's write contract stays as it is (Q1(a)), and the change is one helper in
`src/rcac_mcp/tools/slurm.py` plus its docstring (a named `sbatch.description` delta). Two phases:
the name and its tests, then the served docstring. It fits `small`.

## 2. Design

**The name.** `_job_script_path(job_name)` (`src/rcac_mcp/tools/slurm.py`) returns
`UserPath(f'~/jobs/{name}_{timestamp}_{token}.sh')`:

- `name`: unchanged (`UNSAFE_NAME.sub('_', …).lstrip('.-') or 'job'`), so the file still begins
  with the sanitised job name (R4, Q2) and `-J` still carries the name literally (non-goal).
- `timestamp`: unchanged, `datetime.now().strftime('%Y%m%d_%H%M%S')`, still read through the
  module's `datetime` name, so the suite can hold the clock inside one second by patching
  `slurm.datetime`, as the shaper's reproduction does. Scripts still sort by time (Q2).
- `token`: `secrets.token_hex(8)`, drawn inside the helper on every call: 64 bits, 16 lowercase
  hex digits. `secrets` reads the OS CSPRNG (`os.urandom`) on each draw and keeps no
  per-process state, so it is fork-safe and shares nothing between processes or hosts.

The helper stays undecorated (§7), and `sbatch` calls it once per submission, exactly as now: the
path it returns is the one written (`executor.write(path, …)`) and the one submitted
(`… '--', path, …`). Nothing else in `sbatch` changes: not the return value, not `script_path`,
not `-J`, not the sanitisation, not `~/jobs` (non-goals).

**Why it holds across hosts (R2, Q3).** A collision needs two calls with the same sanitised name,
in the same second, to draw the same 64-bit token. The token depends on nothing two processes
could share: no counter (one per process restarts at the same value), no seeded generator (two
processes seeded alike, or a forked child, repeat a sequence), no clock (two hosts read the same
second), no process id (pids repeat across hosts and containers), no host name (absent or alike
in containers). Each draw is independent, so for k same-name calls in one second the chance of any
coincidence is about k²/2⁶⁵: below 3·10⁻¹⁴ even for a thousand. No coordination through the shared
home is needed, so the design never reads `~/jobs`, takes no lock, and adds no executor call. That
is what keeps it leaf-only.

**What R3 rests on.** Every file already in `~/jobs` keeps its bytes because the call writes only
the one path it just drew, and no earlier call or user file has that path: old-form names
(`sweep_20261023_120000.sh`) lack the token, and a new-form name repeats only by the coincidence
above. A file planted at the exact chosen name is out of scope (Q1(a)).

**The docstring (R5).** The `sbatch` description's sentence about `script_content` becomes:

```
    Either script_path (existing file on cluster) or script_content
    (script text to write) must be provided. If script_content is given,
    the script is written byte for byte (no newline added) to a new file,
    ~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh, and made executable before
    submission. <name> is job_name with every character but A-Z, a-z, 0-9,
    '.', '_' and '-' made '_' and any leading '.' or '-' dropped ('job' if
    nothing is left); <token> is 16 random hex digits, new for each call.
    Every value reaches sbatch, or the job script, as exactly one argument.
```

It names the directory, every part of the name and the token's width, so a model can find a
script by job name and knows each call makes a new file. The `Args:`, `Returns:` and `Examples:`
lines are unchanged, so the only served delta is `sbatch.description`. It lands by §9's scripted
hand-edit of `tests/fixtures/served_surface.json` (load, set `tools.sbatch.description` to
`inspect.getdoc(sbatch.fn)`, dump with `indent=1, ensure_ascii=False` and the trailing newline;
the prototype's diff was exactly 1 line of the fixture). The commit body lists the delta, and §9
and § Testing (`tests/test_surface.py`) record it beside `safe-execution`'s. The helper's own
docstring states the form and why the token is drawn per call.

**Tests** (each tagged `unit` or `integration`; ids are pinned in `verify/ids-P1.txt` and
`verify/ids-P2.txt`). The clock is held inside one second by patching `slurm.datetime` with a
`datetime` subclass whose `now()` returns a fixed instant, as the reproduction does.

- `test_sbatch.py::test_same_second_scripts_get_their_own_paths[same-name|no-name|sanitised-alike]`
  (`unit`, `RecordingExecutor`): two calls with the frozen clock (`sweep`/`sweep`, none/none,
  `a/b`/`a_b`) write two different paths, each call submits the path it wrote, and each path is
  `~/jobs/<stem>_<frozen timestamp>_<16 hex>.sh`. A shared path fails with "calls share a script
  path".
- `test_writes.py::test_same_second_scripts_keep_their_own_bytes[local|delegate|ssh]`
  (`integration`, the `target` fixture, an argv-echo `sbatch`): `~/jobs` is seeded first with
  old-form files at the frozen second (`sweep_<ts>.sh`, `job_<ts>.sh`, `a_b_<ts>.sh`), a new-form
  file, and an unrelated file, each with known bytes and mode. Six calls run in one frozen second
  (the three pairs above). Each returns the job id; the six submitted paths are distinct ("calls
  share a script path"); each is an executable file in `~/jobs` whose name begins with its
  sanitised stem and holds exactly its own call's bytes; every seeded file is byte-identical
  afterwards ("changed a file already in ~/jobs").
- `test_sbatch.py::test_overlapping_submissions_each_submit_their_own_script[local|delegate-one-server|two-servers]`
  (`integration`, the `mode` fixture): two concurrent calls with the same job name, different
  content and the frozen clock, held together by `harness.Rendezvous(2)`. `one-server` is one
  `shared_server` with two clients; `two-servers` is two `shared_server`s with their own executors
  over one home (the in-process form of Q3). A test-local wrapper executor delegates to the real
  one, waits after each `write` on a `threading.Barrier(2, timeout=…)` so both writes land before
  either `run`, and at `run` reads the bytes of the script it is submitting. Each call's run sees
  its own bytes ("ran another call's script"), the two submitted paths differ ("calls share a
  script path"), and each file still holds its own bytes afterwards. The barrier forces the
  interleaving the GOAL describes rather than hoping for it; FastMCP runs sync tools in its
  threadpool, so the two bodies do overlap.
- `test_sbatch.py::test_separate_server_processes_name_scripts_apart` (`integration`): two child
  interpreters (`sys.executable -c …`), each launched through `bin/sandbox.sh` (the
  `test_cli_drives_are_sandboxed` rule), each with the clock frozen at the same instant and
  `os.getpid`, `socket.gethostname` and `platform.node` pinned to the same values, print
  `_job_script_path('sweep')` and the origin of `rcac_mcp`. The paths differ ("calls share a
  script path"), both have the full form, and each child imported the package the parent did (so a
  mutant run tests the mutated copy). This is the cross-process half of R2/Q3: a per-process counter
  or a seeded generator passes every in-process row and fails here.
- `test_sbatch.py::test_script_content_is_written_then_submitted` keeps its rows; its path regex
  gains `_[0-9a-f]{16}` before `\.sh`.
- `test_sbatch.py::test_the_docstring_states_the_script_path_the_tool_writes` (`unit`, P2): reads
  the `~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh` template from `inspect.getdoc(sbatch.fn)`, turns
  it into a pattern (`<name>` from an oracle the test applies itself to the stated rule,
  `<YYYYmmdd_HHMMSS>` the frozen instant, `<token>` sixteen hex digits, the width read from the
  docstring's "16 random hex digits"), and requires `_job_script_path` to match it for `train`,
  `../up/a b;c`, `-x`, `...` and no name.

**Planted cases** (`tests/mutants.py`, one `Case` each; `test_mutations.py::test_mutation_anchor_applies`
keeps their anchors applying):

| Case | Edit | Targets | `why` |
|---|---|---|---|
| `script-name-per-second` | drop the token from the f-string | the `same-name` unit row, `test_same_second_scripts_keep_their_own_bytes[local]`, `…overlapping…[local-one-server]` | `share a script path` |
| `script-name-process-counter` | token from a module-level `itertools.count()` formatted to 16 hex digits | `test_separate_server_processes_name_scripts_apart` | `share a script path` |
| `script-token-short` (P2) | `secrets.token_hex(4)` | `test_the_docstring_states_the_script_path_the_tool_writes` | `does not match the docstring` |

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | Per-call token in `_job_script_path`; `test_same_second_scripts_get_their_own_paths` (paths), `test_same_second_scripts_keep_their_own_bytes` (bytes, all three backends); `script-name-per-second` |
| R2   | The token depends only on the OS entropy source (§2, "Why it holds across hosts"); `test_overlapping_submissions_each_submit_their_own_script` (one server and two servers on one home, forced interleaving), `test_separate_server_processes_name_scripts_apart` (separate processes identical in clock, pid and host name); `script-name-process-counter`; gate `static` forbids counters, seeded generators, clock, pid and host-name sources in `slurm.py` |
| R3   | The call writes only its freshly drawn path; `test_same_second_scripts_keep_their_own_bytes` seeds old-form and new-form files and checks each byte-identical |
| R4   | `name` and `timestamp` unchanged as the prefix, `.sh` kept, `executable=True` and the job-id return unchanged; checked in `test_same_second_scripts_keep_their_own_bytes` and `test_script_content_is_written_then_submitted` |
| R5   | The new `sbatch` docstring sentence; the `sbatch.description` fixture delta by §9's scripted edit; `test_the_docstring_states_the_script_path_the_tool_writes`, `test_served_surface_matches_baseline`; `script-token-short`; gate `surface` |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against `AGENTS.md` § Invariants before design and again after it. The change touches no
path on the high-blast-radius list and none of the profile's `coupled core`; it changes
`tools/slurm.py`, tests, the served-surface fixture and `AGENTS.md`.

- **§1 (per-request executor isolation).** `sbatch` still calls `get_executor()` and never builds
  an executor. The overlap test's wrapper executor lives in the test and reaches the tool only
  through `SharedExecutorMiddleware`, as `RecordingExecutor` does.
- **§2 (executor write contract).** Used, not changed: one `write(path, data, parent='~/jobs',
  executable=True)` per call, as now. The design relies on nothing beyond "an overwrite replaces the
  target whole", which it now avoids by never sharing a target. No executor file changes (gate
  `static` checks `executor/*`, `context.py`, `middleware.py`, `server.py` and `bin/sandbox.sh`
  against the base).
- **§7 (tool registration).** `_job_script_path` stays an undecorated helper, which is why the
  cross-process test and the gate can call it directly. `@mcp_tool` serves the full docstring, so
  the `sbatch` sentence is served whole.
- **§9 (served surface) [human gate].** The docstring is API surface. The change is one named delta,
  `sbatch.description`, landed by the scripted hand-edit, listed in the P2 commit body and recorded
  in §9 and § Testing (`tests/test_surface.py`). `SERVER_INSTRUCTIONS` and the README name no script
  path (checked: neither mentions `~/jobs`), and no tool is added, renamed or removed, so neither
  changes. `server.py` is not edited.
- **Conventions.** Comments and docstrings are declarative, with no spec ids
  (`test_no_spec_ids_in_the_package_or_the_suite` is in both id lists). Every new test carries
  `unit` or `integration`, and gates name test ids, never a marker selection. Every launch goes
  through `bin/sandbox.sh` (the child interpreters, the mutant children, the gate's pytest drives).
  The § Testing entry for `tests/test_sbatch.py`, which states the old file name, changes in the
  same commit as the name; "point to a rule" holds, since the docstring is the one place that
  spells the form out for users.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

No research fan-out. Trigger 1 (unknown root cause): no, the GOAL reproduces and locates it.
Trigger 2 (high blast radius): no, the design touches no listed path. Trigger 3 (gate-shaped
deliverable): no, the deliverable is a fix; its tests and gates are ordinary regression evidence.
No adversarial design review ran (not required on this path). Targeted reads settled the unknowns:

- **Can the suite force the overlap?** Yes. FastMCP runs sync tools in its threadpool
  (`fastmcp/tools/function_tool.py`, `call_sync_fn_in_threadpool`), and `harness.Rendezvous` already
  holds two calls in flight together (`tests/test_isolation.py`). A barrier after `write` in a
  wrapper executor fixes the order write, write, run, run.
- **Can a test reach two separate server processes offline?** Yes. A child interpreter launched as
  `bin/sandbox.sh <python> -c …` imports this tree (checked on the base: the child resolved
  `src/rcac_mcp/tools/slurm.py` of the worktree), and the mutant runner's `PYTHONPATH` passes
  through the sandbox, so a mutant's grandchild imports the mutated copy; the test checks the origin
  anyway.
- **Which existing tests read the name?** Only `test_script_content_is_written_then_submitted`'s
  regex pins the whole form. `test_job_scripts_are_byte_exact`, `test_a_profile_cannot_change_a_fixed_script`,
  the ssh rc-file row and `test_literal_args`' `drive_content` glob `<name>_*` or split at the first
  `_` of names without one. On a prototype of the change, the affected slice under the sandbox
  failed exactly those four regex rows and passed the other 107 (2026-10-23).
- **Does the fixture edit stay one delta?** Yes: on the prototype, dumping with `indent=1,
  ensure_ascii=False` changed one line, and gate `surface` passed.
- **Prior art.** `safe-execution` kept the naming on purpose (research 04 §7, PLAN risk 7) and
  moved it into `_job_script_path`, which is why this fix is one function. The seed's sketch
  offered two other routes: an exclusive create is the executor change Q1 rules out, and a
  sub-second clock is a value two hosts can read alike.

## 5. Risks & open questions

- **Name-length headroom shrinks by 17 bytes.** A sanitised job name longer than 219 bytes (was
  236) now makes the file name exceed the common 255-byte limit, and the write fails with "Failed
  to write job script" before anything is submitted. That is a clean refusal, as it already was for
  longer names; truncating would change the sanitisation (a non-goal) and break R4's "begins with
  the sanitised job name". Recorded here as accepted; for the foreman to rule whether it needs a
  seed.
- **The description grows by about 230 characters**, on the tool `issues/slurm-capability.md`
  already names as the largest. R5 asks for the naming to be stated; the width goes with it.
  `job_submit` inherits this naming and its wording.
- **Randomness is not uniqueness.** The guarantee is probabilistic (§2). Q1(a) accepts "cannot in
  practice coincide"; a hard guarantee is Q1(b), out of scope.
- **The gate's `static` part pins the mechanism** (`secrets.token_hex(8)`, no clock, pid, host,
  counter or seeded-generator source in `slurm.py`). Tests cannot see every shared source (two hosts
  reading one fine-grained clock, say), so the plan pins it here; a builder who finds a reason to
  draw it differently amends PLAN and the check in the same commit, and says so.
- **`issues/slurm-capability.md`** already says only "a name derived from the job name", which stays
  true; it needs no edit. `ROADMAP.md`'s seed entry still states the old form; `code rcac-mcp
  roadmap` retires the seed and its entry once the cycle lands.

## 6. Verification strategy

`verify/gate.sh` holds every phase gate; `verify/check.py` its static and id checks; `verify/ids-P1.txt`
and `verify/ids-P2.txt` the named test ids. Offline (`UV_OFFLINE=1`), with every pytest drive inside
`bin/sandbox.sh` from the repo root (`sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest …'`), one
child at a time.

- **P1** (`gate.sh P1`): `static` (only `tools/slurm.py` changes under `src/`; the executor, context,
  middleware, server and sandbox untouched; the token drawn by one `secrets.token_hex(8)` with no
  forbidden source; six frozen-clock names distinct and of the full form; `AGENTS.md` free of the
  old form); `tests1` (`uv lock --check`, then every P1 id in both eras, each passing exactly once);
  `mutants1` (`script-name-per-second` and `script-name-process-counter` each killed for the stated
  reason).
- **P2** (`gate.sh P2`): `static`; `surface` (the fixture differs from the base only at
  `tools.sbatch.description`, which equals the served docstring and states the new form; `AGENTS.md`
  records the delta in §9 and § Testing); `tests1` and `tests2`; `mutants2` (`script-token-short`
  killed); `full`, the profile's verify, with each pytest run inside the sandbox.

There is no CLI drive to add: `sbatch` needs a cluster, and the suite already drives it through the
served schema against the real `local`, `delegate` and `ssh` (loopback) backends with an argv-echo
`sbatch`. `full` keeps the profile's `bin/sandbox.sh uv run rcac-mcp --help`.

Drafted gates were dry-run on two trees: on this branch (the base code), `static` and `surface` are
red for the expected reasons; on a prototype carrying the design's source, docstring, fixture and
`AGENTS.md` edits (`.worktrees/sbatch-script-name-collision-prototype`, removed after the PLAN/TECH
sign-off), both are green. The id parts need the builder's tests, so they were not dry-run.
