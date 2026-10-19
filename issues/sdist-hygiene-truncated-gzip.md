---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A truncated sdist crashes the hygiene check instead of naming it

## Problem

`bin/sdist_hygiene.py` promises one formatted line per problem, including "a tarball cannot be
read" (its docstring, lines 17–21; the handler at lines 63–66). A gzip stream cut short raises
`EOFError` while the members are read, outside the `tarfile` errors the handler catches, so the
check exits 1 with a Python traceback instead of the `<tarball>: cannot be read (…)` line. The
verdict is right — it still fails closed, and both the release recipe's `gate` and CI's `gate` job
stay red — only the message breaks its contract.

**Pre-existing:** no; introduced with the shared check in the `ci-workflow` cycle.

## Why it was deferred

Found by the `ci-workflow` blind review (2026-10-18) as an observation outside every R-ID: the
check fails closed, so no requirement or invariant is broken, and the fix is a message.

## Outcome / vision

Every unreadable tarball — truncated, corrupt, not a tarball — produces the formatted "cannot be
read" line and exit 1, never a traceback.

## Sketch of the acceptance criteria

- **R1** — WHEN an sdist's gzip stream is truncated, the check SHALL exit 1 and print
  `<tarball>: cannot be read (…)`, with no traceback.

## Notes

- Found by: the `ci-workflow` review, cycle 1, 2026-10-18 (a planted truncated `.tar.gz`).
