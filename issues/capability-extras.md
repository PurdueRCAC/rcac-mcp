---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Make tool groups optional packaging extras

## Problem

Every tool registers unconditionally. `tools/__init__.py` defines `TOOL_REGISTRY` and `mcp_tool`, then
imports each tool module at the bottom of the file; `@mcp_tool` appends at decoration time. There is
no condition anywhere — a site with no Slurm still advertises `sbatch`, `squeue`, `scancel`, `sacct`,
`sinfo`, `scontrol_show_job`, `scontrol_show_node`, `slist` and `sfeatures` to every connecting model,
and every one of those is an invitation to compose a workflow that cannot run. The same will be true
of LMOD tools the moment they exist.

`AGENTS.md` § *Direction* commits to `cluster-mcp[slurm,lmod]` as the shape of the answer: you install
the capabilities you have. That needs two things this repository does not have — extras in
`pyproject.toml`, and a registration mechanism that can **omit a group cleanly** rather than importing
it and hoping the import fails in a useful way.

This collides with a documented invariant rather than sitting beside it. `AGENTS.md` § *Tools* and
`invariants.md` §7 both currently state that registration is an import-time side effect driven by that
hardcoded list, and that a module missing from it silently registers nothing. This cycle **replaces**
that contract, so both documents move in the same commit as the code.

## Why it was deferred

Not deferred — it is the load-bearing cycle of Part I, filed so the mechanism gets designed once
rather than improvised twice. [`lmod-tool-group.md`](lmod-tool-group.md) and
[`site-extras.md`](site-extras.md) are both downstream consumers, and the point of doing this first is
that the second consumer is what proves the mechanism generalizes past its first instance.

**Pre-existing:** n/a — new work.

## Outcome / vision

`pip install cluster-mcp` gives the generic core: shell, filesystem, transfer. `cluster-mcp[slurm]`
adds the scheduler group. A group that is not installed is not imported, not registered, and not named
in `SERVER_INSTRUCTIONS` — the model sees exactly the tools that exist.

Two design questions the plan has to answer rather than assume:

1. **What is an extra actually gating?** These tools shell out; they have no Python dependencies to
   install. So `[slurm]` is a *declaration of intent*, not a dependency pull — which means the
   mechanism needs a real switch (entry points, an env var, a CLI flag, a probe for `sinfo` on the
   remote host) and the extra is what sets it. Getting this wrong produces extras that do nothing.
2. **Does `SERVER_INSTRUCTIONS` stay a constant?** It is currently one `Final[str]`. If tool groups
   are conditional, the instructions describing them cannot be — this becomes composed-at-startup
   text, which is a change to the one thing every model reads.

A capability *probe* (ask the cluster whether `sinfo` exists) is attractive and should be considered
explicitly, including its cost: it puts a remote round-trip on the startup path of a server whose SSH
connection is established eagerly, and it fails differently in `local` mode.

## Sketch of the acceptance criteria

- **R1** — `pyproject.toml` SHALL define a `slurm` extra, and the base install SHALL register only the
  generic tool groups.
- **R2** — WHERE the `slurm` capability is not enabled, `TOOL_REGISTRY` SHALL contain no Slurm tool and
  the server SHALL start with no import error.
- **R3** — `SERVER_INSTRUCTIONS` as served SHALL name exactly the tools present in `TOOL_REGISTRY`,
  verified by a test that cross-checks the two under at least two different capability sets.
- **R4** — IF a capability is requested that is not installed, THEN the server SHALL fail at startup
  with a message naming the missing extra — never start silently degraded.
- **R5** — Adding a new capability group SHALL require no edit to a hardcoded import list; the
  mechanism SHALL be documented in `AGENTS.md` in the same commit that replaces the current § *Tools*
  contract.

## Notes

- `invariants.md` §7 must be rewritten in lockstep — it is the checklist `cm-review` grades against,
  and leaving it describing the old mechanism makes every subsequent review wrong.
- The `@mcp_tool`-returns-a-`FunctionTool` gotcha (see
  [`storage-paths-resource-typeerror.md`](storage-paths-resource-typeerror.md)) is a property of the
  decorator and survives any registration redesign — do not assume a new mechanism fixes it.
- Related: [`lmod-tool-group.md`](lmod-tool-group.md), [`site-extras.md`](site-extras.md).
- Found by: the harness port, 2026-08-26 (out of cycle).
