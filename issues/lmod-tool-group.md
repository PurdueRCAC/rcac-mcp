---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Add an LMOD module-system tool group

## Problem

Every tool in this server is about *jobs*. None is about *software*. On an LMOD cluster an agent
cannot discover what is installed, what versions exist, what a name resolves to, or what
`module load` would do to the environment — so it guesses, and `INSTRUCTIONS.md:7` has to compensate
with prose ("Load software using `module load <name>`… the recommended compiler stack is GCC 14.1.0
with OpenMPI"). Baked-in prose about a specific compiler version is exactly the kind of thing that
goes stale silently and that a tool would answer correctly.

This matters more than a missing convenience. Most real failures on an HPC cluster are environment
failures: the module was not loaded in the job script, the version differs from the login node, two
modules conflict. An agent writing an `sbatch` script today has `sbatch` but no way to check that the
`module load` lines it wrote are valid.

The four commands that carry nearly all the value:

| Command | Answers |
|---|---|
| `module avail` | what is installed |
| `module spider <name>` | what versions exist and what they require |
| `module list` | what is loaded now |
| `module show <name>` | what loading it would change |

## Why it was deferred

Not deferred — sequenced after [`capability-extras.md`](capability-extras.md), and deliberately so:
it is the **second** consumer of the extras mechanism, which is what proves that mechanism
generalizes rather than having been shaped around Slurm alone. Doing LMOD first would produce two
ad-hoc conditionals instead of one mechanism.

**Pre-existing:** n/a — new work.

## Outcome / vision

`cluster-mcp[lmod]` adds a module tool group. An agent can check `module spider python` before writing
a job script, and `INSTRUCTIONS.md` loses its hardcoded compiler-version claim because the tools
answer that question live.

Two things the plan has to get right:

1. **`module` is a shell function, not a binary.** It is defined by LMOD's init script, so
   `executor.run("module avail")` may find nothing in a non-interactive shell. The reliable path is
   `lmod` / `$LMOD_CMD` directly, or sourcing the init profile first — this is the rabbit hole of the
   cycle and should be researched before design, not discovered during build.
2. **`module avail` writes to stderr**, and on a large cluster returns thousands of lines. Both
   interact badly with a `CommandResult` that an agent reads whole: the output goes in the wrong
   field, and it blows a context window. Filtering and truncation are part of the tool, not polish.

## Sketch of the acceptance criteria

- **R1** — WHERE the `lmod` capability is enabled, the server SHALL register tools covering
  `module avail`, `module spider`, `module list`, and `module show`.
- **R2** — The module tools SHALL work in a non-interactive shell, where `module` is not defined as a
  shell function.
- **R3** — WHEN LMOD writes to stderr on success, the tool SHALL return that content as its result
  rather than as an error.
- **R4** — WHEN an unfiltered listing would exceed a documented size bound, the tool SHALL truncate it
  and say so in the returned text, rather than returning thousands of lines.
- **R5** — IF LMOD is not present on the target system, THEN each module tool SHALL return a clear
  message saying so rather than a raw shell error.
- **R6** — `INSTRUCTIONS.md` SHALL stop asserting a specific recommended compiler version, deferring to
  the tools.

## Notes

- The direction is explicitly "may not be LMOD" (`AGENTS.md` § *Direction*) — Tcl Environment Modules,
  Spack, and EasyBuild-generated hierarchies all exist. This cycle should not over-abstract for them,
  but its tool names should not be so LMOD-specific that a sibling `[tmod]` extra cannot reuse them.
- Related: [`capability-extras.md`](capability-extras.md) (strict prerequisite),
  [`site-extras.md`](site-extras.md) (a site extra pulls this one in).
- Found by: the harness port, 2026-08-26 (out of cycle).
