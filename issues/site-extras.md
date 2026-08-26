---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Introduce site extras, starting with cluster-mcp[rcac]

## Problem

A user at Purdue should be able to install one thing — `cluster-mcp[rcac]` — and get the right
capability extras plus Purdue's own tools and context, without first knowing that Gautschi runs Slurm
and LMOD. That is the second half of the packaging direction in `AGENTS.md`, and it is what makes
`cluster-mcp[alcf]`, `[ncsa]` and `[tacc]` a matter of contribution rather than redesign.

A site extra is two things at once, and conflating them is the trap:

1. **A dependency alias** — `rcac = ["cluster-mcp[slurm]", "cluster-mcp[lmod]"]`. Cheap, mechanical.
2. **A point of view** — the site's own tools and the site's own agent-facing context. Today that is
   `tools/rcac.py` (`myquota`, `storage_paths`, `jobinfo`, `jobcmd`, `jobenv`, `jobscript`,
   `showpartitions`, `average_wait`), the RCAC-specific `slist`/`sfeatures` that currently sit *inside*
   `tools/slurm.py`, and the Purdue half of `SERVER_INSTRUCTIONS` — the 25GB home quota,
   `/depot/<group>`, `$CLUSTER_SCRATCH`, `findscratch`.

Two boundary problems are already visible in the tree. `slist` and `sfeatures` are RCAC commands
living in the generic Slurm module, so the Slurm/site line is drawn in the wrong place today. And
`SERVER_INSTRUCTIONS` is one `Final[str]` mixing both halves — [`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md)
starts that split; this cycle has to finish it into something a *contributor* can extend without
editing a core constant.

## Why it was deferred

Not deferred — sequenced after [`capability-extras.md`](capability-extras.md), whose mechanism it
consumes. Filed separately because the interesting work here is the **contribution boundary**, not the
Purdue code: what exactly does a new center have to write, and where does it put it?

**Pre-existing:** n/a — new work.

## Outcome / vision

`cluster-mcp[rcac]` installs cleanly and behaves exactly as the server does today for a Purdue user.
More importantly, the repository documents a site-extra contract concrete enough that someone at TACC
can add `cluster-mcp[tacc]` by writing one module and one context file, without touching the core —
and the review for that contribution has something to grade against.

The `/etc/agents.d/*.md` convention is the other half of this story and is deliberately *not* being
replaced. Static, packaged site context (what ships in the extra) and dynamic, admin-supplied cluster
context (what the resource loads at runtime) answer different questions and should stay distinct; the
plan should say plainly how they compose, because a contributor will ask.

## Sketch of the acceptance criteria

- **R1** — `pyproject.toml` SHALL define an `rcac` extra that pulls the capability extras Purdue needs;
  `cluster-mcp[rcac]` SHALL register the Purdue tool group in addition to those capabilities.
- **R2** — WHERE the `rcac` extra is not installed, no Purdue-specific tool SHALL appear in
  `TOOL_REGISTRY` and no Purdue-specific path, quota figure, or command name SHALL appear in
  `SERVER_INSTRUCTIONS`.
- **R3** — `slist` and `sfeatures` SHALL move out of the generic Slurm group into the site group.
- **R4** — Site context SHALL be contributed as data alongside the site's tools, not by editing a core
  constant.
- **R5** — The repository SHALL document what a new site extra must provide, in enough detail that a
  contributor adding one touches no core module.
- **R6** — The composition of packaged site context with runtime `/etc/agents.d/*.md` context SHALL be
  documented and SHALL be deterministic.

## Notes

- Naming: an extra called `rcac` inside a distribution called `cluster-mcp` is the intended shape.
  Whether the *module* keeps `rcac` or becomes `sites/rcac.py` is a plan-level call.
- Related: [`capability-extras.md`](capability-extras.md) (strict prerequisite),
  [`lmod-tool-group.md`](lmod-tool-group.md) (pulled in by the site extra),
  [`generalize-storage-discovery.md`](generalize-storage-discovery.md) (the site hook this establishes
  is where a discovery implementation lands).
- Found by: the harness port, 2026-08-26 (out of cycle).
