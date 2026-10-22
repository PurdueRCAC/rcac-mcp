---
status: unshaped
kind: test
appetite: small
lane: public
---

# The `integration` marker says it drives the CLI, and not every integration test does

## Problem

`pyproject.toml` describes the two pytest markers under `[tool.pytest.ini_options]`. `unit` is
"fast and isolated; no subprocess, no network, no real cluster"; `integration` is "drives the
installed CLI; run it under bin/sandbox.sh". The suite now has `integration` tests that drive no
CLI at all: `test_docs_modules_absent` and the git-backed absence-message controls in
`tests/test_registry.py` are `integration` only because they start git (through `bin/sandbox.sh`),
which the unit guard forbids. Their marker's description tells a reader they run the CLI.

In practice `integration` already means "anything that is not `unit`": every test carries exactly
one of the two (`AGENTS.md` § Testing), and the unit guard is what draws the line. The
description states a narrower rule than the one the suite enforces, so a reader choosing a marker
for a new test that starts a process but drives no CLI is left to guess. `AGENTS.md` § Testing
notes the mismatch in the `tests/test_registry.py` entry instead of fixing it.

## Why it was deferred

The cycle that moved those tests (`absence-check-leftover-classification`) was limited to
`tests/test_registry.py`, `tests/conftest.py` and `AGENTS.md`; `pyproject.toml` was outside its
paths, and rewording a marker is a suite-wide definition, not a detail of one check.

**Pre-existing:** partly. The description predates the cycle, which added the first `integration`
tests it plainly misdescribes.

## Outcome / vision

The marker descriptions in `pyproject.toml` state the line the unit guard enforces: `unit` starts
no process and opens no connection, and `integration` is everything else, a CLI drive under
`bin/sandbox.sh` included. `AGENTS.md` § Testing then drops its note about the mismatch.

## Sketch of the acceptance criteria

- **R1** — The `integration` marker's description in `pyproject.toml` SHALL cover every test that
  carries it, CLI drives and other process launches alike, and SHALL keep the rule that a launch
  goes through `bin/sandbox.sh`.
- **R2** — `AGENTS.md` § Testing SHALL describe the markers in the same terms as `pyproject.toml`,
  and SHALL NOT keep a note that the two disagree.

## Notes

- Related: `spec/absence-check-leftover-classification/` (PLAN §5, P1); `AGENTS.md` § Testing.
- Found by: `absence-check-leftover-classification` plan, 2026-10-19; filed at P1, 2026-10-22.
