---
status: adopted:absence-check-leftover-classification
kind: test
appetite: small
lane: public
---

# Explain an importable leftover by git status, not file suffix

## Problem

`tests/test_registry.py` keeps the removed documentation modules unimportable. When one still
imports, `absence_violations` says where from and classifies what it finds there by **file suffix**:
bytecode alone is reported as a stale checkout, with its remedy (`del <path>`); anything else is
reported as a re-added module, with no remedy.

So an untracked leftover that is neither bytecode nor source — a `.DS_Store`, or an interrupted
`*.pyc.<n>` temp file next to the bytecode — is reported as a re-added module, and the developer gets
no remedy for what is really a stale checkout. The verdict (the test fails) is right; only the
explanation is wrong.

## Why it was deferred

Observed by the `strip-docs-subsystem` review (cycle 3), which judged the verdict correct and the
message a nicety. `restore-test-coverage` was the first candidate home, but its scope is the coupled
core's contracts, and this is a message fix to an existing check.

**Pre-existing:** yes — on `develop` since the strip landed.

## Outcome / vision

`absence_violations` classifies a leftover by **git status**: files git ignores or does not track
are leftovers of an old checkout and get the `del` remedy; a tracked file means the module was
re-added, and the message says so.

## Sketch of the acceptance criteria

- **R1** — WHEN the leftover directory holds only untracked or ignored files (bytecode, `.DS_Store`,
  a `*.pyc.<n>` temp), `absence_violations` SHALL report a stale checkout and name the `del` remedy.
- **R2** — WHEN the leftover holds a tracked file, it SHALL report a re-added module and offer no
  remedy.
- **R3** — The existing check-function names and test ids SHALL keep working (`AGENTS.md`
  § Testing), with negative controls for both cases.

## Notes

- Tests must build their own scratch git repository under `tmp_path`, never classify against the
  developer's checkout.
- Found by: `strip-docs-subsystem` review, cycle 3, 2026-10-16.
