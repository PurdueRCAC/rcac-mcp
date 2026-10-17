---
status: adopted:ci-workflow
kind: ci
appetite: small
lane: public
---

# Nothing runs on push

## Problem

There is no `.github/` directory at all — no test workflow, no publish workflow, no container build,
no dependency review. The test suite runs only when someone remembers to run it locally. Until
`restore-test-coverage` landed ([`spec/restore-test-coverage/`](../spec/restore-test-coverage/),
2026-10-16) it would have tested nothing that matters.

This costs more now than it did before the factory arrived, in two specific ways:

1. **The release recipe's local gate is the only thing between a bad build and a permanent tag.** The
   recipe's caveats say so out loud, and its `final: true` forbids skipping the rehearsal for exactly
   that reason (`AGENTS.md` § Factory → Release recipe). A gate that runs only on
   the machine of whoever cuts the release is a gate that eventually does not run.
2. **The sdist-hygiene assertion has no automatic enforcement.** Verified during the harness port: the
   sdist built from `main` shipped `.agents/` wholesale before `pyproject.toml` was given exclude
   rules. Nothing but a human reading a `tar tzf` stands between a future top-level directory and a
   published artifact. `.security/` is the case that matters — publishing that lane would publish an
   inventory of unremediated weaknesses.

## Why it was deferred

Not deferred — filed as the gap it is. Sequenced after `restore-test-coverage` because CI over a
docs-only suite tests the wrong thing and would have to be rewritten immediately (cleared: landed on
`develop` 2026-10-16).

**Pre-existing:** yes.

## Outcome / vision

A minimal, honest workflow: run the tests on the supported Python on every push and PR, and assert the
sdist contains nothing it should not. That is the cheap half and it is most of the value.

Deliberately **not** in scope: publishing to PyPI, building containers, or signing artifacts in CI.
Those are separate decisions with credential and supply-chain implications the maintainer should make
explicitly, not inherit from a workflow that grew.

## Sketch of the acceptance criteria

- **R1** — WHEN a commit is pushed to `develop` or `main`, or a PR is opened against either, a workflow SHALL run
  `uv run pytest` on the supported Python version and SHALL fail the check on any test failure.
- **R2** — The same workflow SHALL build the sdist and FAIL if the tarball contains any path the
  release recipe's sdist-hygiene gate rejects (`AGENTS.md` § Factory → Release recipe).
- **R3** — The workflow SHALL run `uvx twine check --strict` on the built artifacts.
- **R3a** — The workflow SHALL run `uv lock --check` and fail if `uv.lock` has drifted from
  `pyproject.toml`. The lock is tracked and is what pins the transitive tree for anyone installing
  from `git+https://…`, so a stale one is a silently wrong install, not a formatting nit.
- **R4** — The workflow SHALL NOT publish to any package index, push a container, or require any
  repository secret.
- **R5** — `AGENTS.md` § *Packaging* SHALL be updated — it currently states there is no CI — the
  profile's `ci` key SHALL name the workflow, and the release recipe's caveats SHALL note that CI now
  mirrors its `gate`.

## Notes

- The supported Python is `>=3.14`, which is aggressive; confirm runner availability before designing
  a matrix. A single-version job is fine and honest for now.
- R2 duplicates the release recipe's sdist-hygiene gate by design: the recipe keeps its local copy
  because a release can be cut from a machine before CI has run on that commit.
- Related: [`spec/restore-test-coverage/`](../spec/restore-test-coverage/) (strict prerequisite;
  cleared: landed on `develop` 2026-10-16).
- Found by: the harness port, 2026-08-26 (out of cycle).
- 2026-10-16: the suite is no longer docs-only. The docs strip left `tests/test_registry.py`,
  `tests/test_instructions.py` and `tests/test_cli.py` (tool registry, served instructions, removed
  CLI flags); the coupled core is still untested, so the sequencing after `restore-test-coverage`
  stands.
- 2026-10-16: `restore-test-coverage` landed tests for the coupled core, so the suite is the real
  gate this seed waits for. It needs `uv` and `bin/sandbox.sh` (integration tests drive the CLI
  under it) and still runs in seconds. The mutation runner, `tests/mutants.py`, is not collected:
  `uv run python tests/mutants.py --self-check <every case id>` (`AGENTS.md` § Testing has the
  one-liner) runs every planted violation of the core's contracts in about 60–90 s and exits non-zero
  if any survives or is killed for the wrong reason. It is offered as an opt-in CI job, separate
  from R1's test job; whether it gates, and on which paths, is a shaping call.
