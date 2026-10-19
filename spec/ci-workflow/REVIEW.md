# REVIEW — CI runs the release gate on every push

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 980fb24e225d9c91fac9f4400b1f153138fce9e7 (the pass); verdict pinned to
  64bac1f885ef332dca23f89eb9a91f335c889bf6, which adds only the seed below · **Base:** develop (`de2b380`) · **Date:** 2026-10-18
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Contract drift:** `GOAL.md` moved after the shape commit (`28df6b3`) by the plan-stage
  rulings (`0bd7fa8`) and the 2026-10-17 moved-base clarification (`6de76de`), both the foreman's
  as judge for this effort; graded against them.

## Verification run

By the blind reviewer, at 980fb24:

- 16 planted sdists through `bin/sdist_hygiene.py`: every dirty one (`.security/a.md`, a bare
  `.security` entry and symlink, `R//.security/a.md`, `R/./.security/a.md`,
  `R/src/../.security/a.md`, `/R/.security/a.md`, `R/spec/`, a long PAX name,
  `tests/fixtures/sub/x`, `.agents/factory/x.md`, uncompressed and bz2 tars named `.tar.gz`, an
  empty and a missing `dist/`) → rc 1, the path named; clean inputs and lookalikes
  (`src/rcac_mcp/bin/x.py`, `tests/fixtures.py`, `docs/spec/x`, a bare root entry) and the real
  hatchling sdist → rc 0.
- 8 hand mutants of `bin/sdist_hygiene.py` in a scratch clone: all killed by
  `tests/test_sdist_hygiene.py`.
- The workflow's `run:` steps parsed against the profile's `setup` and `verify` rows and the
  recipe's `gate`: `setup + verify + (gate − verify)` equals them verbatim, in order.
- The mutants workflow replayed: "killed 68 of 68 cases", both controls, exit 0; a case forced to
  fail for the wrong reason, and `CASES = ()`, each exit 1.
- actionlint 1.7.12 (with shellcheck): 0 errors. zizmor 1.31.1: no findings (default persona); two
  low `concurrency-limits` hints in pedantic/auditor.
- GitHub's records, read only: run 39190417102 on 980fb24 (CPython 3.14.7, `665 passed` twice,
  `--help`, audit, build, twine, hygiene); run 39189454397 on the plant, failing at the hygiene
  step on `.agents/planted.md`; `GITHUB_TOKEN Permissions: Contents: read, Metadata: read`; the
  pinned action SHAs resolved to the claimed tags.
- A local replay of the nine gate steps: all green.

## Requirement → evidence matrix

| R-ID | Status | Verified how | By |
|------|--------|--------------|----|
| R1 | ✅ | `on: push` for every branch, `pull_request` to `develop`/`main`, no `pull_request_target`; runs observed on push | reviewer |
| R2 | ✅ | step parse against the recipe; run 39190417102's steps and versions; local replay | reviewer |
| R3 | ✅ | run 39189454397 failing at the hygiene step; 16 planted sdists; one shared `FURNITURE` definition | reviewer |
| R4 | ✅ | `permissions: contents: read`, no job override, the run's token scopes, no secrets, `persist-credentials: false`, no cache | reviewer |
| R5 | ✅ | full-SHA `uses:` resolved via `gh api` to the claimed tags; uv 0.12.17 matches the ruling and `required-version` | reviewer |
| R6 | ✅ | `workflow_dispatch` only; replay "killed 68 of 68"; negative controls | reviewer |
| R7 | ✅ | the `ci` row's command run against HEAD (success), the red SHA (failure) and develop's head (empty) | reviewer |
| R8 | ✅ | no "no CI" claim outside `spec/` except the seed and roadmap entry this cycle retires | reviewer |
| R9 | ✅ | `EVIDENCE.md`: `## Lint`, `## Local reds`, `## Green run` (39188588571 on X = ffb03b5, success), `## Red run` (39189454397 on the plant 5c977fa, failure at the hygiene step only), `## Landing hand-off`; the P5 gate's `evidence.py` modes `local`, `green`, `red`, `ledger` agree (foreman's re-run); the actual head 980fb24's run 39190417102 is `success` (the R9(b) clarification) | orchestrator |

Unmapped changes: none material (a stale-sentence removal in `bin/audit_lock.py`'s docstring, the
one-word seed-pointer repair a ruling allowed, the adopted-seed status, and the hygiene tests R3
needs).

## Findings

None CONFIRMED, none PLAUSIBLE.

### Out of scope, recorded and seeded

- **A truncated gzip sdist raises `EOFError`, not the formatted "cannot be read" line**
  (`bin/sdist_hygiene.py:63-66` catches `OSError` and `tarfile.TarError` only). The check still
  exits 1, so it fails closed in the release gate and in CI; no R-ID or invariant is broken, only
  the docstring's promise of one formatted line. Seeded as
  [`issues/sdist-hygiene-truncated-gzip.md`](../../issues/sdist-hygiene-truncated-gzip.md) with its
  `ROADMAP.md` entry (Part III), committed at `64bac1f` before this verdict, which is pinned there.
- The sdist now carries `.github/workflows/*.yml`: harmless, outside the furniture set, and the same
  set the recipe uses. No action.

## Human-gate triggers

- None. No path on § Invariants' high-blast-radius list changed, and no *[human gate]* section is
  touched.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run.
