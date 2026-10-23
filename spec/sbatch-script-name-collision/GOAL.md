# GOAL — `sbatch` job scripts written in the same second share a name

> **Origin spec.** The *what* and *why*: the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).

- **slug:** sbatch-script-name-collision
- **kind:** fix
- **appetite:** small

## Problem

When `sbatch` is given `script_content`, it writes the script under `~/jobs/` and submits that
file. The file's name is the sanitised job name (or `job` when none is given) and a timestamp of
one-second resolution (`src/rcac_mcp/tools/slurm.py:62-66`, `_job_script_path`). Two
submissions with the same job name in the same second therefore get the same file. The second
write replaces the first, since the executor's write overwrites an existing target whole
(`AGENTS.md` § Invariants §2).

What a user or agent sees:

- **One after the other** (an agent submitting a sweep in a loop): both calls succeed and return
  job ids, and both submit the same path. Slurm copied the first script at submission, so the first
  job runs what it was given, but the file left on disk for it now holds the second script, and
  only one file remains for two jobs. Anyone reading `~/jobs` to see what a job ran sees the wrong
  script.
- **Overlapping** (two clients of one server, or two servers for the same user): the second write
  can land between the first call's write and its `sbatch`, so the first job runs the second job's
  script. Nothing in either result reveals it.

**Reproduced on `develop` d34de62** (shaping, 2026-10-23), with the clock fixed inside one second:
through the suite's `RecordingExecutor`, two calls with `job_name='sweep'` and different content
both wrote and submitted `~/jobs/sweep_20261023_120000.sh`; through the real `local` executor in
the suite's fenced home (`argv_echo` standing in for `sbatch`), both calls returned a job id,
`~/jobs` held one file, `sweep_20261023_120000.sh`, containing only the second script, and both
submissions named that one path.

**Pre-existing.** The one-second name dates from the tool's first commit (`5cbaae9`, 2026-02-02),
before the factory. `safe-execution` (`fbd21e8`) moved it into `_job_script_path`, sanitised the
name and made the write byte-exact, and kept the naming deliberately: changing where the tool writes
was outside that cycle's contract (`spec/safe-execution/` research 04 §7, PLAN risk 7).

**Blast radius.** The defect is in `src/rcac_mcp/tools/slurm.py`, a leaf: it is on neither the
high-blast-radius list (§ Invariants) nor the profile's `coupled core`. Two paths could pull the
cycle wider. The `sbatch` docstring, which states the file name, is served surface: §9 pins it in
`tests/fixtures/served_surface.json`, and any rewording is a named delta. A fix that changed the
executor's write contract would touch `executor/*`, which is coupled core, with `executor/base.py`,
`ssh.py` and `launch.py` human-gated (Clarification Q1).

## Outcome / vision

Each script `sbatch` writes from content gets its own file, and that file keeps the content its own
call sent. A job never runs another submission's script, and the file on disk for a job is the script
that job was given.

## Acceptance criteria (the contract)

- **R1** — WHEN two `sbatch` calls with `script_content` are made one after the other within the
  same second, and each has the same `job_name`, no `job_name`, or a `job_name` that differs from
  the other only in characters the file name replaces (`a/b` and `a_b`), each call SHALL submit a
  different path, and afterwards each path SHALL hold exactly the bytes its own call sent.
- **R2** — WHEN two such calls overlap in time, through one server or through two servers whose
  `~` is the same directory, each submitted job SHALL be given exactly its own call's bytes, and
  each call's file SHALL still hold them afterwards.
- **R3** — WHEN `sbatch` writes a script from content, every file already in `~/jobs` before the
  call SHALL be byte-identical afterwards.
- **R4** — WHEN a submission from `script_content` succeeds, `sbatch` SHALL return the job id, as
  it does today, and the script SHALL be an executable file in `~/jobs` whose name begins with the
  sanitised job name (or `job`) (Q2).
- **R5** — The `sbatch` docstring SHALL describe where a script written from content goes and how
  it is named, matching what the tool does.

## Non-goals (no-gos)

- Changing the directory scripts are written to, or letting the caller choose a path or a scratch
  default. That is `job_submit`'s scope in `issues/slurm-capability.md`, which inherits this
  cycle's naming.
- Returning the script's path in `sbatch`'s result, or any other change to its return value.
- Removing, rotating or listing old scripts in `~/jobs`.
- `script_path` submissions, the `-J` value, and the job-name sanitisation `safe-execution`
  landed: all are unchanged.
- Recovering from collisions that already happened: files already overwritten stay as they are.

## Clarifications

Ruled by the foreman at the GOAL gate, 2026-10-23.

- **Q1** How strong is R3? — **A:** (a): a per-call name that cannot in practice coincide with
  another call's, across processes and hosts sharing a home, so that a leaf-only change in
  `tools/slurm.py` meets R1–R3. R3 is graded on files present before the call that another call
  wrote or a user placed under a name the tool did not just choose; a hard no-clobber guarantee
  against a file planted at the exact chosen name (option (b)) is out of scope, since it reaches
  the executor's write contract (coupled core, human-gated). A plan that finds (a) cannot meet R2
  brings the executor question back to the gate and does not take it on silently.
- **Q2** May the file name change form? — **A:** yes, within limits: keep `~/jobs/`, the
  `<name>_<YYYYmmdd_HHMMSS>` prefix and the `.sh` suffix, so scripts stay findable by job name and
  sort by time; a distinguishing part may go between the prefix and the suffix. A docstring change
  lands as a named `sbatch.description` delta to `tests/fixtures/served_surface.json` (§9's
  scripted hand-edit), listed in the commit body.
- **Q3** Does R2 cover two separate server processes, possibly on different login nodes, whose `~`
  is one shared home? — **A:** yes. A shared home across login nodes is the normal cluster case,
  and two stdio sessions for the same user are two servers.

## Related materials

- Seed: [`issues/sbatch-script-name-collision.md`](../../issues/sbatch-script-name-collision.md)
  (`ROADMAP.md` Part 0)
- [`spec/safe-execution/`](../safe-execution/): research 04 §7, PLAN risk 7
- [`issues/slurm-capability.md`](../../issues/slurm-capability.md): `job_submit` inherits the naming
- Source: `src/rcac_mcp/tools/slurm.py` (`_job_script_path`, `sbatch`); `AGENTS.md` § Invariants
  §2 (executor write), §9 (served surface)
- Tests: `tests/test_writes.py::test_job_scripts_are_byte_exact` (distinct job names per row, so
  it never meets the collision), `tests/test_sbatch.py`, `tests/test_surface.py`
