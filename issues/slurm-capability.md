---
status: unshaped
kind: feature
appetite: big
lane: public
---

# A job_* family that accepts what a model naturally sends

## Problem

The Slurm tools mirror the command-line programs one to one, and they reject reasonable input in
ways that cost a job submission. Found while submitting and watching test jobs through the server:

- **`sbatch` `gpus` must be a string.** An integer (`gpus=1`) is refused by the tool's own schema
  before Slurm sees it, and no job id comes back. `nodes`, `ntasks` and `cpus_per_task` are
  integers, `mem` and `time` strings; a model can't guess which is which.
- **`squeue` and `sacct` take `job_ids` only as a list.** A single id — the common case — fails.
- **`squeue` on a purged job id exits 1** rather than returning an empty result, so "the job
  finished a while ago" reads as an error.
- **`script_content` writes into `~/jobs`** under a name derived from the job name, and can't take
  arguments. ([`spec/safe-execution/`](../spec/safe-execution/) added `args` to `sbatch`; this cycle carries
  it into the new family.)
- **Validation errors don't say what to send.** A rejected call names the failed check, not the
  accepted forms.

The surface is also wider than it needs to be. Five tools answer "tell me about this job" —
`jobinfo`, `jobcmd`, `jobenv`, `jobscript` (Purdue wrappers in `tools/rcac.py`) and
`scontrol_show_job` — and the Slurm definitions are among the largest the server sends: `sbatch`
2.9K characters, `sacct` 2.0K, `scancel` 1.6K, `squeue` 1.5K. Reading a job's output means finding
its `StdOut` path in `scontrol` and then reading the file. Waiting for a job means polling in a
loop, one tool call per poll.

## Why it was deferred

Not deferred — a roadmap cycle in Part I. It is the Slurm capability distribution that
[`capability-extras.md`](capability-extras.md) makes possible, it follows the naming, annotation and
budget rules from [`tool-surface.md`](tool-surface.md), and it rides the argv executor from
[`spec/safe-execution/`](../spec/safe-execution/).

**Pre-existing:** every problem above is on `main` today.

## Outcome / vision

`cluster-mcp[slurm]` installs a `job_*` family:

- `job_submit(script_path | script_content, args, modules, …resources)` — resources typed to accept
  an int or a string where Slurm does (`gpus=1` and `gpus="a100:1"`); `modules=[…]` loads modules in
  the job script, since module state doesn't persist between calls; `script_content` goes to a
  caller-chosen path or a scratch default, with `args`.
- `job_list(job_ids, user, state)` — accepts a scalar or a list; a purged id returns empty with a
  note (or falls through to accounting), never an error.
- `job_history(...)` — accounting, accepting a scalar or a list of ids.
- `job_info(job_id, include=[…])` — one consolidated view replacing `jobinfo`, `jobcmd`, `jobenv`,
  `jobscript` and `scontrol_show_job`: state, command, environment, script, built from generic
  Slurm sources (`scontrol show job`, `scontrol write batch_script`, `sacct`), which a site extra
  may enrich.
- `job_output(job_id, stream, tail)` — reads the job's stdout or stderr without the caller finding
  the path.
- `job_cancel(...)`.
- `job_wait(job_ids, timeout)` — **considered, not committed**: a bounded wait might make "submit,
  then act on the result" one call instead of a polling loop. Shaping decides whether it earns its
  place and what bound it keeps under client timeouts.

Every validation error names the accepted forms with an example. The remaining scheduler views
(`sinfo`, `scontrol show node`) get names in the family at shaping.

## Sketch of the acceptance criteria

- **R1** — WHERE the `slurm` capability is installed and detected, the server SHALL register the
  `job_*` family; `jobinfo`, `jobcmd`, `jobenv`, `jobscript` and `scontrol_show_job` SHALL be
  replaced per the aliases decision in `tool-surface`.
- **R2** — `job_submit` SHALL accept `gpus`, `nodes`, `ntasks` and `cpus_per_task` as an integer or a
  string, and SHALL return the job id on success.
- **R3** — `job_submit` SHALL accept `args: list[str]` and `modules: list[str]`, and SHALL write
  `script_content` to the caller's path or a documented scratch default.
- **R4** — `job_list` and `job_history` SHALL accept a single id or a list of ids.
- **R5** — WHEN `job_list` is asked about an id Slurm no longer tracks, it SHALL return an empty
  result with a note, not an error.
- **R6** — `job_info` SHALL return what the five tools it replaces returned, selectable by `include`.
- **R7** — IF a call fails validation, THEN the error SHALL name each accepted form of the failing
  parameter with an example.
- **R8** — The total size of the family's definitions SHALL be measured and SHALL be smaller than
  the tools it replaces.

## Notes

- The Purdue wrappers (`jobinfo` and friends) are site commands; the generic `job_info` must not
  depend on them. [`site-extras.md`](site-extras.md) decides whether the site extra enriches
  `job_info` or registers its own view.
- Related: [`spec/safe-execution/`](../spec/safe-execution/) (`args`, the executor),
  [`tool-surface.md`](tool-surface.md) (names, annotations, budgets, aliases),
  [`capability-extras.md`](capability-extras.md) (the distribution and its probe),
  [`lmod-tool-group.md`](lmod-tool-group.md) (`modules=[…]`), [`xdu-storage.md`](xdu-storage.md)
  (indexes large trees through `job_submit`).
- Found by: roadmap revision, 2026-10-16 (out of cycle).
