---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Generalize storage discovery beyond myquota and findscratch

## Problem

Storage is the one piece of cluster context an agent needs before it can do almost anything — where to
write job output, what is purged, what has quota left. This server answers that question with two
Purdue commands and no fallback:

- `storage_paths()` (`tools/rcac.py:337`) runs `myquota` and hands the output to
  `_parse_myquota_output()` (`:269`), a parser written against Purdue's exact column layout.
- Scratch resolution shells out to `findscratch`, treated as the authoritative source (commit
  `d73f972`).
- The `rcac://storage` resource (`resources.py:137`) formats `paths.home`, `paths.scratch` and
  `paths.depots` — a data model with `depot` in it, which is a Purdue concept, not an HPC one.

On a cluster with neither command, `myquota` returns nonzero, the parser sees nothing it recognizes,
and the failure surfaces as a parse error rather than "this site does not expose quotas that way."
`SERVER_INSTRUCTIONS` compounds it by asserting Purdue's numbers — 25GB home, `/depot/<group>`,
`$CLUSTER_SCRATCH` — as though they were facts about clusters.

## Why it was deferred

Not deferred — sequenced last in Part I, because it needs the site hook that
[`site-extras.md`](site-extras.md) establishes. A discovery protocol with no place to put a site's
implementation is just an abstraction with one caller.

**Pre-existing:** yes — every one of these assumptions is on `main` today.

## Outcome / vision

A storage-discovery protocol with a generic default and a site-provided implementation. The generic
default does what any POSIX cluster supports — `$HOME`, `$SCRATCH`/`$TMPDIR` if set, `df` for
capacity — and says "unknown" honestly where it cannot answer. Purdue's `myquota`/`findscratch`
implementation moves behind the site extra, where it belongs.

Two design questions worth naming now:

1. **The data model.** `StoragePaths` has `home`, `scratch`, and `depots`. "Depot" is Purdue's word
   for group storage; TACC has `$WORK`, ALCF has project directories. A generalized model probably
   names *roles* (home / scratch / project / archive) with site labels attached, rather than
   hardcoding one site's vocabulary into the schema every client sees.
2. **Unknown is a value.** The most common honest answer on an unfamiliar cluster is "this path
   exists, quota unknown." If the model cannot express that, every site implementation will fabricate
   a number or fail — and a fabricated quota is worse than no quota, because an agent will plan
   against it.

## Sketch of the acceptance criteria

- **R1** — WHERE no site storage implementation is installed, `storage_paths()` SHALL return the
  paths it can determine generically and SHALL mark unavailable fields as unknown rather than raising
  or guessing.
- **R2** — IF a site's discovery command is absent or fails, THEN the tool SHALL report that the site
  does not expose the information, naming the command it tried — never a raw parse error.
- **R3** — The Purdue `myquota`/`findscratch` implementation SHALL move behind the site extra and
  SHALL produce output equivalent to today's for a Purdue user.
- **R4** — The storage data model SHALL express storage *roles* rather than Purdue's vocabulary, and
  SHALL be able to represent an unknown quota distinctly from a zero one.
- **R5** — The generic `SERVER_INSTRUCTIONS` SHALL contain no site-specific quota figure or path.

## Notes

- The `rcac://storage` resource is **also** broken for an unrelated reason — it calls a `@mcp_tool`-
  decorated function that is no longer callable. See
  [`storage-paths-resource-typeerror.md`](storage-paths-resource-typeerror.md); that fix factors
  `storage_paths` into an undecorated helper, which is exactly the seam this cycle needs. Doing the
  small fix first makes this one cheaper.
- Related: [`site-extras.md`](site-extras.md) (strict prerequisite),
  [`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md) (starts the `SERVER_INSTRUCTIONS` split).
- Found by: the harness port, 2026-08-26 (out of cycle).
