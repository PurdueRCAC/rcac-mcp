---
status: unshaped
kind: test
appetite: small
lane: public
---

# The served-surface fixture has no way to follow a deliberate surface change

## Problem

`tests/fixtures/served_surface.json` pins what the server serves: tool names, descriptions, input
and output schemas, resources and instructions (`tests/test_surface.py`, `AGENTS.md` § Invariants
§9). It is the surface `3d010ec` served, written by `spec/stack-upgrade/verify/snapshot.py` in an
archive of that commit in its own locked FastMCP 2 environment. `test_served_surface_matches_baseline`
compares the live surface with it through `surface_violations`, which accepts four named kinds of
FastMCP 4 metadata (`TOLERATED`), and `test_surface_fixture_is_the_baseline_shape` rejects a fixture
that carries that metadata.

So the fixture can only be re-taken on FastMCP 2. After a deliberate surface change on the current
stack (a new argument, a reworded docstring, changed instructions) there is no FastMCP 2 build of
that surface to snapshot, and a fixture re-taken on FastMCP 4 fails the shape test. The only path
left is to hand-edit the 2-shaped JSON, which nothing checks against what the server actually
serves. The snapshot script also lives under `spec/`, a retained record rather than a maintained
tool.

## Why it was deferred

`stack-upgrade` needed the fixture to be the old stack's surface: proving nothing changed in the
move was the point (its R3). Settling how the pin evolves afterwards is a different question, with
more than one reasonable answer, and no change in that cycle needed it. `AGENTS.md` §9 says the
question is open and asks for it to be raised before a surface change.

**Pre-existing:** no, introduced by `stack-upgrade` P3.

## Outcome / vision

A surface change updates the fixture in the same commit as the change, `SERVER_INSTRUCTIONS` and the
README (§9), with a maintained command that takes the snapshot on the current stack, and the test
still tells a deliberate change from an accidental one. One likely shape: re-baseline once to a
FastMCP 4 fixture with the tolerated metadata folded in, retire `TOLERATED` and the shape test, and
move the snapshot script into `tests/` or `bin/`.

## Sketch of the acceptance criteria

- **R1** — A documented command SHALL write the fixture from the current tree, under
  `bin/sandbox.sh`, on the locked stack.
- **R2** — WHEN the served surface differs from the fixture in any field, the suite SHALL fail
  naming the tool and field; WHEN the fixture is re-taken after a deliberate change, it SHALL pass.
- **R3** — The re-baseline SHALL show no difference in tool names, argument names, required lists
  or types from the `3d010ec` surface beyond those `TOLERATED` names today.

## Notes

- Related: `spec/stack-upgrade/` (P3 and its amendments); the first cycles that change the served
  surface: `safe-execution` (`args` on `sbatch`), `tool-surface`, `agents-d-instructions`.
- Found by: `stack-upgrade` P3 (2026-10-17), filed at P7.
- 2026-10-23: `safe-execution` landed the first deliberate surface change by hand-editing its
  deltas into the 2.x-shaped fixture (listed in `AGENTS.md` § Testing), so R3's comparison with
  the `3d010ec` surface has to allow those deltas as well as `TOLERATED`.
