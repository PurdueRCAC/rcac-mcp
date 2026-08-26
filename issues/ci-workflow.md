---
status: unshaped
kind: ci
appetite: small
lane: public
---

# Nothing runs on push

## Problem

There is no `.github/` directory at all — no test workflow, no publish workflow, no container build,
no dependency review. The test suite runs only when someone remembers to run it locally, and until
[`restore-test-coverage.md`](restore-test-coverage.md) lands it would test nothing that matters
anyway.

This costs more now than it did before the factory arrived, in two specific ways:

1. **`/cm-release`'s local gate is the only thing between a bad build and a permanent tag.** The skill
   says so out loud and discourages `--skip-dry-run` for exactly that reason. A gate that runs only on
   the machine of whoever cuts the release is a gate that eventually does not run.
2. **The sdist-hygiene assertion has no automatic enforcement.** Verified during the harness port: the
   sdist built from `main` shipped `.agents/` wholesale before `pyproject.toml` was given exclude
   rules. Nothing but a human reading a `tar tzf` stands between a future top-level directory and a
   published artifact. `.security/` is the case that matters — publishing that lane would publish an
   inventory of unremediated weaknesses.

## Why it was deferred

Not deferred — filed as the gap it is. Sequenced after `restore-test-coverage` because CI over a
docs-only suite tests the wrong thing and would have to be rewritten immediately.

**Pre-existing:** yes.

## Outcome / vision

A minimal, honest workflow: run the tests on the supported Python on every push and PR, and assert the
sdist contains nothing it should not. That is the cheap half and it is most of the value.

Deliberately **not** in scope: publishing to PyPI, building containers, or signing artifacts in CI.
Those are separate decisions with credential and supply-chain implications the maintainer should make
explicitly, not inherit from a workflow that grew.

## Sketch of the acceptance criteria

- **R1** — WHEN a commit is pushed or a PR is opened against `main`, a workflow SHALL run
  `uv run pytest` on the supported Python version and SHALL fail the check on any test failure.
- **R2** — The same workflow SHALL build the sdist and FAIL if the tarball contains any of `.agents/`,
  `.security/`, `spec/`, `issues/`, or `tests/fixtures/`.
- **R3** — The workflow SHALL run `uvx twine check --strict` on the built artifacts.
- **R4** — The workflow SHALL NOT publish to any package index, push a container, or require any
  repository secret.
- **R5** — `AGENTS.md` § *Packaging* SHALL be updated — it currently states there is no CI — and
  `/cm-release` Step 4 SHALL note that CI now mirrors its gate.

## Notes

- The supported Python is `>=3.14`, which is aggressive; confirm runner availability before designing
  a matrix. A single-version job is fine and honest for now.
- R2 duplicates `/cm-release` Step 4's hygiene check by design: the release skill keeps its local copy
  because a release can be cut from a machine before CI has run on that commit.
- Related: [`restore-test-coverage.md`](restore-test-coverage.md) (strict prerequisite).
- Found by: the harness port, 2026-08-26 (out of cycle).
