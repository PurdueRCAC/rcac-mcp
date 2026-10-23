---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `sbatch` job scripts written in the same second share a name

## Problem

When `sbatch` is given `script_content`, it writes the script to
`~/jobs/<job_name>_<YYYYmmdd_HHMMSS>.sh` (`src/rcac_mcp/tools/slurm.py`) and submits that file.
The timestamp has one-second resolution, so two submissions with the same job name in the same
second write the same file. Slurm keeps its own copy of a script at submission, so one after the
other the first job is unaffected, but its file on disk now holds the second script; two calls
that overlap (two clients of one server) can submit the second script's content twice. One agent
submitting a sweep in a loop hits the first case.

## Why it was deferred

`safe-execution` made the write byte-exact and kept the naming as it was: changing where the tool
writes is outside that cycle's contract. **Pre-existing:** yes, present on `develop` before
`safe-execution` (research 04 §7 of `spec/safe-execution/`, PLAN risk 7).

## Outcome / vision

Every job script written from content gets its own file, so a submission never runs another
submission's script.

## Sketch of the acceptance criteria

- **R1** — WHEN two `sbatch` calls with the same `job_name` and `script_content` arrive within one
  second, each SHALL write and submit its own file, and neither file SHALL be overwritten.
- **R2** — The file SHALL be created without replacing an existing one (an exclusive create, or a
  name with sub-second or unique parts), and the tool SHALL still return the job id.

## Notes

- Related: `spec/safe-execution/` (research 04 §7, PLAN risk 7). `ROADMAP.md` places it in Part 0,
  ahead of the `job_*` family in `issues/slurm-capability.md`, which inherits the fixed naming.
- Found by: `safe-execution` planning research, filed at P5 (2026-10-17).
