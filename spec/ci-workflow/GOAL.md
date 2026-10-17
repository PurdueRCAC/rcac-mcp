# GOAL — Run the gate on GitHub on every push

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** ci-workflow
- **kind:** ci
- **appetite:** small

## Problem

Nothing runs on push. There is no `.github/` directory on `develop` (`3d010ec`): no test workflow,
no publish workflow, no container build. The suite, which since `restore-test-coverage` pins the
coupled core's contracts and runs in seconds, runs only when someone remembers to run it. The gap
is **pre-existing**, filed by the harness port on 2026-08-26 and sequenced after
`restore-test-coverage` so CI would not test a docs-only suite.

It costs more now that the factory lands cycles autonomously, in three ways:

1. **The release recipe's local `gate` is the only thing between a bad build and a permanent tag.**
   Its `no-ci-no-index` caveat says so (`AGENTS.md` § Factory → Release recipe). A gate that runs
   only on the machine of whoever cuts the release eventually does not run.
2. **The sdist-hygiene assertion has no automatic enforcement.** The sdist built from `main` once
   shipped `.agents/` wholesale before `pyproject.toml` was given exclude rules. Nothing but a human
   reading a tarball stands between a future top-level directory and a published artifact, and a
   `.security/` path there would publish an inventory of unremediated weaknesses.
3. **Publish has nothing to read.** `code-publish` gates a landing on the project's CI when the
   profile has a `ci` key; the profile says `none yet`, so a green local run is the only evidence.

`AGENTS.md` also contradicts itself today: § Packaging and the `no-ci-no-index` caveat say there is
no CI, while § Conventions already claims "The release recipe's `gate` mirrors CI."

## Outcome / vision

A minimal, honest workflow on GitHub Actions. Every push to the base, the release channel and a
cycle branch, and every pull request into the first two, runs the profile's gate plus the release
recipe's build checks on the supported Python, with a read-only token, no secrets and every action
pinned. The mutation runner is available on demand without gating anything. `AGENTS.md` names the
workflow and says how publish reads it, and the cycle proves the check can go both green and red
on GitHub itself, not only on paper.

## Acceptance criteria (the contract)

**The workflow** (`.github/workflows/`, file and job names are the plan's call):

- **R1 (triggers)** — WHEN a commit is pushed to `develop`, `main`, or any branch whose name a
  pattern in the profile's `branches` key produces (`feature/…`, `fix/…`, `refactor/…`, `docs/…`,
  `test/…`, `ci/…`), or a pull request is opened or updated against `develop` or `main`, the gate
  job SHALL run for it. The workflow SHALL NOT use the `pull_request_target` trigger.
- **R2 (gate job)** — The gate job SHALL run, in one job on the supported Python (`requires-python`,
  today 3.14), the profile's `setup` (`uv sync --locked`), all of its `verify` (`uv lock --check`,
  `uv run pytest -q`, `bin/sandbox.sh uv run rcac-mcp --help`), a build of the sdist and wheel, and
  `uvx twine check --strict` on the built artifacts; IF any of these exits non-zero, THEN the job,
  and so the check on that commit, SHALL fail.
- **R3 (sdist hygiene)** — IF the built sdist contains any path the release recipe's sdist-hygiene
  gate rejects, or no sdist was built, THEN the gate job SHALL fail and its log SHALL name each
  offending path. The workflow and the recipe SHALL reject the same set of paths, either by reading
  one shared definition or by an `AGENTS.md` same-commit rule that binds the two copies.
- **R4 (least privilege)** — The workflow SHALL run with a token no wider than
  `permissions: contents: read`, declared at the workflow level with no job widening it; SHALL
  reference no repository or organization secret; and SHALL write nothing outside the runner: no
  package upload, git push, tag, release, container build or push, signature, or attestation.
- **R5 (pinned actions)** — Every `uses:` reference in the workflow SHALL name a full 40-character
  commit SHA, never a tag or branch (a trailing comment may carry the readable version).
- **R6 (mutation runner, opt-in)** — WHEN started manually (`workflow_dispatch`), a separate job or
  workflow SHALL run `tests/mutants.py --self-check` over every case in `mutants.CASES` as the file
  defines them at that commit, and SHALL fail IF any case fails. It SHALL NOT run on `push` or
  `pull_request`, and it is not part of the check publish reads (R7).

**The documentation** (same commit as the workflow: § Conventions, a rule restated in N places):

- **R7 (profile `ci` key)** — `AGENTS.md` § Factory → Profile's `ci` row SHALL name the workflow
  file and the check publish reads, and SHALL state the landing rule for `squash-local`: the run of
  that check on the cycle branch head commit must be green before landing, and a red, missing or
  unfinished run is not green.
- **R8 (no stale "no CI" claims)** — `AGENTS.md` SHALL nowhere state that the project has no CI:
  § Packaging SHALL say a workflow runs the gate on push and pull request; the release recipe's
  `no-ci-no-index` caveat (renamed if the plan likes) SHALL say CI mirrors its `gate` and that the
  recipe keeps its local copy because a release can be cut before CI has run on that commit; and
  § Conventions' "the release recipe's `gate` mirrors CI" SHALL be true of the step lists as landed.

**Evidence** (behaviour on GitHub is only observable on GitHub):

- **R9 (shown, not asserted)** — Before landing, `spec/ci-workflow/` SHALL record: (a) a static lint
  of the workflow file with zero findings (the plan picks the linter); (b) the run id or URL of a
  green gate run on the cycle branch head commit; (c) the run id or URL of a red gate run on a
  throwaway branch, caused by a deliberately planted failure (a failing test or a hygiene
  violation), with the failing step named; and (d) R3's check failing, with the offending path
  named, against a sdist planted with a `.security/` path (on GitHub or locally). The throwaway
  commit SHALL NOT reach `develop` or `main`, and the throwaway branch SHALL be gone from `origin`
  by landing.

## Non-goals (no-gos)

- **A dependency or advisory audit step.** Owned by the `stack-upgrade` seed (its R5); it lands with
  the dependency upgrade, against this workflow.
- **PyPI publishing, container builds or pushes, artifact signing, attestations.** Separate decisions
  with credential and supply-chain implications, made explicitly by the maintainer, not inherited
  from a workflow that grew.
- **Coverage reporting** and coverage tooling.
- **A Python version matrix or an OS matrix.** One job on the supported Python is honest for now.
- **GitHub repository settings:** branch protection, required status checks, rulesets, Actions
  permission changes. Publish reading the run (R7) is the gate this cycle builds; anything on the
  GitHub side is Geoffrey's separate call.
- **Running the mutation runner on push or pull request**, or making it gate a landing.
- **Changing the release recipe's `gate` steps** beyond what R3 needs to share or bind the hygiene
  path set, and any change to `src/`.
- **Rewording other seeds** (`issues/stack-upgrade.md`, `issues/auth-modes-annotation-typo.md`) that
  describe the no-CI state as their own context; `code-roadmap` and those cycles own them.

## Clarifications

Shaping decisions taken on the foreman's guidance, 2026-10-16, for confirmation at the GOAL gate:

- **Q:** Which pushes trigger the gate? — **A:** `develop`, `main`, and the cycle branches, so that
  publish can read a cycle branch head's run green before a `squash-local` landing; pull requests
  into `develop` and `main`. Whether the trigger lists the profile's prefixes or matches every
  branch is the plan's call; if it lists prefixes, a new prefix in the profile's `branches` key is
  a same-commit edit to the trigger (2026-10-16).
- **Q:** Does the mutation runner gate anything? — **A:** No. Manual `workflow_dispatch` only, never
  on push or PR, not read by publish (seed note, 2026-10-16; decided 2026-10-16).
- **Q:** Which Python? — **A:** One job on 3.14, the floor `requires-python` names; the plan
  confirms the runner can provide it (2026-10-16).
- **Q:** SHA-pin only third-party actions, or GitHub-owned ones (`actions/*`, `astral-sh/*`) too?
  — **A:** Every `uses:`. A tag on a GitHub-owned action is as mutable as any other, and one rule
  is checkable by grep; the cost is a SHA bump when an action is upgraded (2026-10-16).
- **Q:** How is a workflow's behaviour verified, given it only runs on GitHub? — **A:** A static lint
  plus one green run on the cycle branch head and one deliberately red run on a throwaway branch,
  recorded in the spec (R9). Pushes that start runs, and the throwaway branch's removal, each need
  Geoffrey's go, which the foreman obtains (2026-10-16).
- **Q:** Is the seed's draft lock check (`R3a`) in scope? — **A:** Yes, as part of R2: `uv lock
  --check` is already in the profile's `verify` (2026-10-16).

Plan-stage rulings by the foreman, after the adversarial design review, 2026-10-17:

- **Q:** Does R3 allow a shared check stricter than the recipe's old inline one? — **A:** Yes.
  `bin/sdist_hygiene.py` also rejects members outside the sdist root, `..` and `./`-prefixed
  members, and unreadable tarballs. The recipe and CI run the same file, so they still reject the
  same set (2026-10-17).
- **Q:** R9(b) says "the cycle branch head commit", but recording the run is itself a commit. —
  **A:** The recorded green run may be on commit X, provided only `spec/ci-workflow/` changes after
  X. Landing still needs a green run on the actual head, read with the profile `ci` row's command
  after publish's push (2026-10-17).
- **Q:** What if GitHub runs nothing for a commit? — **A:** GitHub skips the push run when the
  pushed head commit's message carries `[skip ci]` (or a sibling), and skips pull-request runs on
  a conflicted PR. Both fail safe under R7: a missing run is not green (2026-10-17).
- **Q:** R6's dispatch before the next release? — **A:** GitHub offers `workflow_dispatch` only
  for a workflow on the default branch, so `mutants.yml` is dispatchable from the release that
  carries it to `main`. This is a recorded limitation, shown by local replay of the job until then
  (2026-10-17).
- **Q:** Which action and uv versions does R5 pin? — **A:** The newest release at least 14 days
  old (a supply-chain cooldown): `actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.2.0, uv
  0.12.17 (2026-10-17).
- **Q:** Renaming the `no-ci-no-index` caveat touches `issues/rename-to-cluster-mcp.md`. — **A:**
  Accepted: a one-word pointer repair in the same commit, not a rewording of that seed
  (2026-10-17). No workflow-lint seed this cycle (2026-10-17).

Moved-base clarification, 2026-10-17, confirmed by the foreman (judge for this effort) when the
cycle was rebased from `3d010ec` onto `develop` at `fb2d4dd`:

- **Q:** `stack-upgrade` landed first (`acbdfd7`, retired at `3dd963d`) and put a dependency audit
  in the release recipe's `gate`; `fb2d4dd` then made the profile's `verify` and the recipe's
  `gate` run the suite in both protocol eras. What does CI mirror now? — **A:** The lists as they
  stand at landing, both additions included. R2's `verify` is the profile's `verify` as landed, so
  the gate job runs `uv run pytest -q` and then `CLUSTER_MCP_TEST_ERA=modern uv run pytest -q`;
  and R8's "mirrors its `gate`" takes in `uv run python bin/audit_lock.py`, which needs the network
  the runner has and fails closed without it. The non-goal "A dependency or advisory audit step.
  Owned by the `stack-upgrade` seed (its R5); it lands with the dependency upgrade, against this
  workflow", and the Related materials line that `issues/stack-upgrade.md` "adds the
  dependency-audit step to this workflow", are no longer true as worded: `stack-upgrade` landed
  before any workflow existed, and its audit lives in the recipe's `gate` (`bin/audit_lock.py`), not
  in a workflow. Read the non-goal as: this cycle designs no audit of its own; CI runs the recipe's
  audit step, verbatim, only because CI mirrors the gate. R-IDs are unchanged (2026-10-17).

## Related materials

- Seed: [`issues/ci-workflow.md`](../../issues/ci-workflow.md) (promoted 2026-10-16, `status:
  unshaped`) and its `ROADMAP.md` entry "Nothing runs on push".
- `AGENTS.md`: § Factory → Profile (`ci`, `branches`, `setup`, `verify`), § Factory → Release recipe
  (`gate`, caveat `no-ci-no-index`), § Packaging, § Testing (the suite, `bin/sandbox.sh`, the
  mutation runner), § Conventions.
- `pyproject.toml` `[tool.hatch.build.targets.sdist]` (the exclude list R3's check backs up).
- Prerequisite: [`spec/restore-test-coverage/`](../restore-test-coverage/) (landed on `develop`
  2026-10-16).
- Downstream: `issues/stack-upgrade.md` (adds the dependency-audit step to this workflow).
- KB: `skill/code-publish.md` step 1.4 (how publish reads `ci` for `squash-local`).
