---
slug: ci-workflow
title: Run the gate on GitHub on every push
kind: ci
appetite: small
status: in_review
branch: ci/ci-workflow
base: develop
current_phase: done
last_updated: '2026-10-18'
phases:
- id: P1
  name: One sdist-hygiene definition, run by the release recipe and tested for holes
  status: done
  satisfies:
  - R3
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/ci-workflow/verify/gate.sh P1
- id: P2
  name: The gate and mutants workflows, with AGENTS.md naming them, in one commit
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 1
  verify: sh spec/ci-workflow/verify/gate.sh P2
- id: P3
  name: Local evidence - zero-finding lint, and the gate red at the right step for
    each plant
  status: done
  satisfies:
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/ci-workflow/verify/gate.sh P3
- id: P4
  name: 'Outward: a green gate run on the cycle branch head'
  status: done
  satisfies:
  - R9
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/ci-workflow/verify/gate.sh P4
- id: P5
  name: 'Outward: a red gate run on a throwaway branch, the branch removed, and the
    deferral ledger'
  status: done
  satisfies:
  - R9
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/ci-workflow/verify/gate.sh P5
review:
  last_reviewed_commit: 64bac1f885ef332dca23f89eb9a91f335c889bf6
  verdict: approved
  blocked_reason: ''
  cycle: 1
---

# TECH.md — Run the gate on GitHub on every push

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/ci-workflow/TECH.md`); the
per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state
commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/01-facts.md`](research/01-facts.md) and
  [`research/prototypes/`](research/prototypes/) (lean path).
- **Gates:** [`verify/`](verify/). `gate.sh` dispatches per phase; `static.py`, `replay.py`,
  `hygiene_mutants.py`, `junit_ids.py` + `ids.txt` are drafted; `evidence.py` is written in P3–P5.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`.
- `appetite`: `small`. It caps phase count and the build-iteration budget.
- `branch`, `base`: `ci/ci-workflow`, `develop`.
- phase `status`: `pending | in_progress | done | blocked`.
- `satisfies`: GOAL R-IDs this phase delivers. **R9 is an artifact-deliverable R-ID**: its
  evidence is `spec/ci-workflow/EVIDENCE.md`, which `code-review`'s blind reviewer cannot see, so
  the *orchestrator* grades R9, from P3–P5's `evidence.py` checks, which re-read GitHub and re-run
  the local reds. R7 and R8 are met by `AGENTS.md`, which is outside `spec/` and blind-gradable.
- `parallel`: `false` throughout. Every phase edits `AGENTS.md` or depends on the one before.
- `hammerable`: `false` throughout. Every phase is a gate or the evidence for one.
- `verify`: one command per phase, `sh spec/ci-workflow/verify/gate.sh P<n>` (plain, no quoting).
- `attempts`, `review.cycle`: as in the template.

## Conventions (apply to every phase)

- `AGENTS.md` is the constitution. This change touches § Conventions, § Packaging, § Factory
  (Profile, Release recipe), § Testing and the repository map. It touches no numbered § Invariants
  section and no high-blast-radius path. **`src/` and `bin/sandbox.sh` are never edited.**
- One phase per `code-build` invocation; one atomic commit with the change and the `TECH.md`
  state: `[ci] Build ci-workflow P<n>: …`, no `Co-Authored-By` trailer.
- Comments and docstrings in shipped files (`bin/`, `tests/`, `.github/`) are declarative and
  carry no `R#`/`P#` ids.
- **No push, PR, branch deletion on `origin`, or `gh` write happens in P1–P3.** P4 and P5 each name
  their pushes as separate checklist items. Each needs Geoffrey's explicit go, obtained by the
  foreman, per push. `gh` reads (`run list`, `run view`, `run watch`, `api` GETs) need none. If a
  go is not given, the phase stays `in_progress` and build stops and reports.
- Before every push: `sh spec/ci-workflow/verify/gate.sh prepush` (its own mode, PLAN §6).

---

## Phase P1 — One sdist-hygiene definition
**Satisfies:** R3 (the shared definition; CI wiring lands in P2) · **Depends on:** —
**Goal:** the recipe's inline hygiene check becomes `bin/sdist_hygiene.py`, which the recipe runs
and a unit-test module pins, shown non-hollow by planted mutants. Design: PLAN §2.1.

- [x] Add `bin/sdist_hygiene.py` from `research/prototypes/sdist_hygiene.py` (SPDX header, module
      docstring as drafted).
- [x] Add `tests/test_sdist_hygiene.py` from `research/prototypes/sdist_hygiene_tests.py`
      (`@mark.unit` on every test). It loads the script by path with
      `importlib.util.spec_from_file_location`, builds tarballs in `tmp_path` with `tarfile`, and
      calls the functions and `main` in-process (`capsys` for stderr). Keep exactly the ids in
      `verify/ids.txt`, and parametrise `test_each_furniture_entry_is_named` over the tuple
      written in the test, **never** the script's `FURNITURE` (PLAN §2.1: that shape is hollow).
- [x] `AGENTS.md` § Factory → Release recipe: replace the inline `python3 -c` step (and its
      folded block) with `- uv run python bin/sdist_hygiene.py dist/`, under the comment in PLAN
      §2.5. In caveat `security-in-sdist`, "The hygiene check lists what the sdist does contain."
      becomes "The hygiene check names each offending path."
- [x] `AGENTS.md` § Packaging, sdist bullet: "`[tool.hatch.build.targets.sdist]` excludes them,
      and `bin/sdist_hygiene.py`, which the release recipe's `gate` runs, re-checks the built
      tarball —". CI is added to this sentence in P2.
- [x] `AGENTS.md` § Testing: add the `tests/test_sdist_hygiene.py` entry (what it pins, in the
      style of its siblings). Repository map: add `bin/sdist_hygiene.py` beside `bin/sandbox.sh`.
- [x] Run the profile's `verify`, then the gate.
- **Verify:** `sh spec/ci-workflow/verify/gate.sh P1`. It runs `static.py hygiene`; the module
  under JUnit with all 19 `ids.txt` ids passed and nothing else red; and `hygiene_mutants.py`,
  all ten plants killed by their named ids with a `<failure>`.
- **Touches:** `bin/sdist_hygiene.py`, `tests/test_sdist_hygiene.py`, `AGENTS.md`.

## Phase P2 — The workflows and their documentation, one commit
**Satisfies:** R1, R2, R3 (CI runs the shared check), R4, R5, R6, R7, R8 · **Depends on:** P1
**Goal:** `.github/workflows/ci.yml` and `mutants.yml` land with every `AGENTS.md` change that
names them, in the same commit (§ Conventions; GOAL "same commit as the workflow"). Design:
PLAN §2.2–§2.5.

- [x] Add `.github/workflows/ci.yml` and `.github/workflows/mutants.yml` from
      `research/prototypes/` (pins after the review's cooldown ruling: checkout v7.0.1, setup-uv
      v10.2.0, uv 0.12.17), unchanged otherwise. If an action pin changes,
      `static.py`'s `PINS` and `research/01-facts.md` change with it, read again with `gh api` as
      recorded there.
- [x] `AGENTS.md`, exactly as PLAN §2.5: the profile `ci` row; the § Packaging CI bullet and the
      sdist bullet's "and CI"; the caveat renamed to `ci-mirror-no-index` with its new text; the
      § Conventions "CI mirrors the gate" bullet, with the old sentence removed from the version
      bullet; § Testing's `mutants.yml` sentence; and the repository map's `.github/workflows/`.
- [x] `issues/rename-to-cluster-mcp.md`: "`no-ci-no-index` caveat" → "`ci-mirror-no-index`
      caveat" (PLAN D2). No other seed is edited (GOAL non-goal).
- [x] `grep -rn 'no-ci-no-index' . --include='*.md'` outside `spec/` prints nothing.
- [x] Run the profile's `verify`, then the gate.
- **Verify:** `sh spec/ci-workflow/verify/gate.sh P2`. It runs P1's checks, then
  `static.py hygiene workflow agents controls` (31 plants rejected and the clean tree accepted),
  then actionlint + shellcheck and `zizmor` online (pin checks included) with zero findings. Then it replays the gate
  job on the snapshot (9/9 green, `rcac_mcp` imported from the snapshot; the audit step reaches
  the advisory service for real), replays the mutants job
  (green, every case killed), and replays the mutants job with `empty-cases` (red, "no mutation
  cases found").
- **Touches:** `.github/workflows/ci.yml`, `.github/workflows/mutants.yml`, `AGENTS.md`,
  `issues/rename-to-cluster-mcp.md`.
- **Amendment (2026-10-17, rebase onto `develop` `fb2d4dd`):** `develop` gained
  `uv run python bin/audit_lock.py` in the recipe's `gate` (`stack-upgrade`) and
  `CLUSTER_MCP_TEST_ERA=modern uv run pytest -q` in `verify` and the `gate` (`fb2d4dd`). Reopened:
  `ci.yml`'s gate job takes both in the order `static.py`'s expected-step list gives (nine steps);
  `AGENTS.md` § Packaging, § Testing, the repository map and the recipe's `audit` caveat say CI
  runs them; `bin/audit_lock.py`'s docstring drops its "No CI workflow exists yet". GOAL carries
  the moved-base clarification; PLAN §2.2 the new step list. Gate unchanged.
- **Blocked (2026-10-17):** the gate's replay is red at step 3/9, `uv run pytest -q`: under uv
  0.12.17, CI's pin, `tests/test_audit.py::test_audit_fails_closed_offline` fails in both eras
  (441 passed, 1 failed). uv 0.12.x refuses an offline audit with its generic "Network connectivity
  is disabled, but the requested data wasn't found in the cache" (still exit 2, fails closed), and
  `bin/audit_lock.py` and the test expect uv 0.13's "requires network access and cannot be
  performed in offline mode". 0.12.19, the newest 0.12, prints the same; 0.13.0 (2026-10-09) is
  inside the 14-day cooldown until 2026-10-23. Every other step passes under 0.12.17 (online audit:
  no advisories in 94 packages). Awaiting the foreman's ruling: the pin, or a fix on `develop`.
- **Amendment (2026-10-18, rebase onto `develop` `de2b380`):** the fix landed on `develop`
  (`audit-uv-portability`, `de2b380`, after release 0.1.1 at `c68adaa`): `bin/audit_lock.py`
  decides "offline" itself and runs the launching uv, `tests/test_audit.py` pins the wrapper's
  own messages under every supported uv, and `pyproject.toml` declares `[tool.uv]
  required-version = ">=0.12.17"`, exactly CI's pin. The rebase conflicted only in
  `bin/audit_lock.py`'s docstring, resolved by keeping `develop`'s launching-uv and `UV_OFFLINE`
  text and this cycle's sentence that CI runs the file; `AGENTS.md` merged cleanly (`develop`'s
  "Supported uv is 0.12.17+" and § Testing's `test_audit.py` entry beside this cycle's CI text).
  Pin, workflows and gate unchanged. The gate is green: the replay runs all nine steps under uv
  0.12.17.

## Phase P3 — Local evidence
**Satisfies:** R9 (a) and (d) · **Depends on:** P2
**Goal:** `spec/ci-workflow/EVIDENCE.md` records the zero-finding lint and the local reds, each
re-checked live by the gate. **R9 is graded by the orchestrator** (its evidence is under `spec/`).

- [x] Write `verify/evidence.py` with mode `local`. `EVIDENCE.md` must have `## Lint` naming
      `actionlint 1.7.12`, `shellcheck 0.11.0` and `zizmor 1.31.1` with their zero-finding output,
      and `## Local reds` holding the three red replay lines (`.security/planted.md`,
      `.agents/planted.md`, `planted red`) and the ten `killed` lines from `hygiene_mutants.py`.
      The prose is a record only: the gate re-runs every one of these checks itself.
- [x] Create `spec/ci-workflow/EVIDENCE.md`: paste the linters' versions and outputs, and the
      replay and hygiene-mutants output from this run, dated.
- **Verify:** `sh spec/ci-workflow/verify/gate.sh P3`. It runs P2's checks, then the three red
  replays (each red at its step and naming its text), then `evidence.py local`.
- **Touches:** `spec/ci-workflow/EVIDENCE.md`, `spec/ci-workflow/verify/evidence.py`.
- **Amendment (2026-10-17, rebase onto `develop` `fb2d4dd`):** reopened to re-record the lint and
  the local reds against the nine-step gate job; the hygiene reds now fail at step 9/9. Gate
  unchanged.
- **Amendment (2026-10-18, rebase onto `develop` `de2b380`):** `EVIDENCE.md`'s `## Lint` and
  `## Local reds` re-recorded on the rebased tree (`7d56cd1`): lint zero findings; the three red
  replays under uv 0.12.17, the hygiene reds at step 9/9 naming `dist/rcac_mcp-0.1.1.tar.gz`'s
  planted path and `test-red` at step 3/9; all ten hygiene mutants killed. Gate unchanged.

## Phase P4 — Outward: a green gate run on the cycle branch head
**Satisfies:** R9 (b); observes R1 and R2 on GitHub · **Depends on:** P3
**Goal:** the `ci.yml` run for a push of `ci/ci-workflow` concludes `success` with every step
green, and its id is recorded.

- [x] `sh spec/ci-workflow/verify/gate.sh prepush` prints `gate[prepush]: ok`.
- [x] **Push 1 (Geoffrey's go, obtained by the foreman):** `git push -u origin ci/ci-workflow`.
- [x] Read: `gh run list -R purduercac/rcac-mcp --workflow ci.yml --commit "$(git rev-parse HEAD)" -e push --json databaseId,attempt,url,status,conclusion,headSha`
      (re-read until listed), then `gh run watch <id> -R purduercac/rcac-mcp --exit-status`.
- [x] (Not needed: green on attempt 1.) If red: `gh run view <id> -R purduercac/rcac-mcp --log-failed`. A Linux-only failure in
      `tests/` is fixed there, committed as `[ci] Build ci-workflow P4: …`, re-gated with
      `prepush`, and pushed again. **Each re-push is a new go.** A cause in `src/` is a STOP and a
      GOAL clarification (non-goal).
- [x] Extend `verify/evidence.py` with mode `green`. It reads the run recorded under
      `## Green run` (`run:`, `commit:`) with `gh run view <id> --json
      attempt,status,conclusion,event,headBranch,headSha,path,jobs`. It requires
      `completed`/`success`, event `push`, branch `ci/ci-workflow`, path
      `.github/workflows/ci.yml`, `headSha` equal to the recorded commit, `attempt` 1 (or each
      re-run listed in `EVIDENCE.md` with its reason), and a job `gate` whose steps include `Run <cmd>` with `success` for
      each of the nine commands. It also requires the recorded commit to be an ancestor of HEAD,
      with `git diff --name-only <commit> HEAD` touching only `spec/ci-workflow/`.
- [x] Record `## Green run` in `EVIDENCE.md`: run id, attempt, URL, commit (X below), branch, date.
- [x] Write `verify/evidence.py`'s `landing` mode: the profile ci row's read for HEAD
      (`gh run list --workflow ci.yml --commit <HEAD> -e push --json databaseId,attempt,status,conclusion`),
      requiring at least one run, all `completed`/`success`. `gate.sh landing` runs it.
- **Verify:** `sh spec/ci-workflow/verify/gate.sh P4`, which runs `evidence.py local green` (reads
  only).
- **Touches:** `spec/ci-workflow/EVIDENCE.md`, `spec/ci-workflow/verify/evidence.py`, and
  `tests/` only if a Linux-only failure needs a fix.
- **Amendment (2026-10-18, run B):** X = `ffb03b5`, the state-only run-A commit pushed as the
  branch's first push (foreman's ruling: accepted). Run `39188588571`, attempt 1, event `push`,
  completed/success, job `gate` and all nine `Run <cmd>` steps `success`. `gh run view --json` has
  no `path` field, so `green` reads the workflow path from the same run's REST record (`gh api
  repos/purduercac/rcac-mcp/actions/runs/<id>`). A re-run is listed in `## Green run` as
  `- re-run N: <reason>`. **Known gap (foreman's ruling, routed by the foreman):** the profile
  defines no site-private scan command, so the pre-push scan over commit messages (code-build
  Step 6) has no stored command to run in this cycle; build checks messages by reading them.

## Phase P5 — Outward: a red gate run on a throwaway branch, its removal, and the deferral ledger
**Satisfies:** R9 (c) and its throwaway clause · **Depends on:** P4
**Goal:** a planted hygiene violation on `ci/ci-workflow-red` turns the gate red at the hygiene
step on GitHub, naming the path. The branch is then gone from `origin`, and the plant is in
neither `develop` nor `main`.

- [x] `sh spec/ci-workflow/verify/gate.sh prepush` prints `gate[prepush]: ok`.
- [x] Throwaway, outside the cycle worktree:
      `git worktree add -b ci/ci-workflow-red "$TMPDIR/ci-workflow-red" <X>`, where X is the green
      run's recorded commit, so the plant's one parent is X. In it, add
      `.agents/planted.md` ("Planted by the ci-workflow cycle to show the sdist hygiene step red;
      never merged.") and commit `[ci] Throwaway: plant .agents/ to show the gate red`. Record
      the plant SHA.
- [x] **Push 2 (Geoffrey's go):** `git -C "$TMPDIR/ci-workflow-red" push origin ci/ci-workflow-red`.
- [x] Read: `gh run list -R purduercac/rcac-mcp -w ci.yml -b ci/ci-workflow-red -c <plant sha> --json databaseId,url,status,conclusion`,
      `gh run watch <id> -R purduercac/rcac-mcp` (a non-zero exit is expected), and
      `gh run view <id> -R purduercac/rcac-mcp --log-failed`, which must show
      `.agents/planted.md` under the step `Run uv run python bin/sdist_hygiene.py dist/`. A red at
      any other step is a failed proof: investigate, never record it.
- [x] **Push 3 (Geoffrey's go):** `git push origin --delete ci/ci-workflow-red`. Then
      `gh api repos/purduercac/rcac-mcp/branches/ci/ci-workflow-red` answers 404 (not an empty
      `ls-remote`, which an SSH failure also produces). Then
      `git worktree remove "$TMPDIR/ci-workflow-red"` and `git branch -D ci/ci-workflow-red`.
- [x] Extend `verify/evidence.py` with modes `red` and `ledger`.
  - `red` reads `## Red run` (`run:`, `plant:`). It requires `completed`/`failure`, branch
    `ci/ci-workflow-red`, `headSha` equal to the plant, and the hygiene step as the first failed
    step, with every earlier `Run …` step `success`. `--log-failed` must contain
    `.agents/planted.md`. `gh api repos/purduercac/rcac-mcp/commits/<plant>` must show exactly one
    file, `.agents/planted.md` with status `added`, and exactly one parent, the green run's
    commit X. `gh api …/branches/ci/ci-workflow-red` must answer 404.
    `gh api repos/purduercac/rcac-mcp/compare/develop...<plant>` and `…/main...<plant>` must both
    show `ahead_by` ≥ 1, so the plant is in neither branch.
  - `ledger`: every `issues/*.md` path named in `PLAN.md` or `TECH.md` exists and has a
    `ROADMAP.md` entry.
- [x] **Deferral ledger.** Walk P1–P5 and PLAN §5 for "defer", "follow-up", "known limitation",
      "not this cycle" and "out of scope". Each such item needs an `issues/{slug}.md` (from the KB
      `ISSUE.md` template, `status: unshaped`) plus a `ROADMAP.md` entry, or a statement in PLAN
      that it is not deferred work. At planning time there is none: PLAN §5 records the
      workflow-lint gap and `uvx twine` as accepted risks (no seed, foreman's ruling 2026-10-17)
      and the mutants dispatch limit as self-resolving at the release.
- [x] Record `## Red run` in `EVIDENCE.md`: run id, URL, plant SHA, failing step, log line, and
      the removal (the 404 and the date).
- [x] **Landing hand-off** (in the phase report and `EVIDENCE.md`): the ci rule this cycle
      installs needs a green run on the *actual* head, which P4–P5's evidence commits moved past
      X. Publish makes that push (`code-publish` 1.4, `squash-local`: **push 4, its own go**) and
      then runs `sh spec/ci-workflow/verify/gate.sh landing`. Not green is a STOP.
- **Verify:** `sh spec/ci-workflow/verify/gate.sh P5`, which runs `evidence.py local green red
  ledger` (reads only).
- **Touches:** `spec/ci-workflow/EVIDENCE.md`, `spec/ci-workflow/verify/evidence.py`, and
  `issues/` + `ROADMAP.md` only if a deferral is recorded.
- **Amendment (2026-10-18, run A):** prepush green on `adda414` (P4's commit). Plant
  `5c977fad938f7e5cc010714f6d064a47e9716e55`, one parent X (`ffb03b5`), adding only
  `.agents/planted.md`; the throwaway worktree is under the session scratchpad rather than
  `$TMPDIR` (same role: outside the cycle worktree). Pushed as
  `git push origin <plant>:refs/heads/ci/ci-workflow-red` (the foreman's standing approval for
  this effort). Its `ci.yml` push run is `39189454397`. Run B reads it, records `## Red run`,
  deletes the branch (push 3) and removes the worktree.
- **Amendment (2026-10-18, run B):** run `39189454397`, attempt 1, event `push`, at the plant:
  completed/failure, job `gate` red first and only at `Run uv run python bin/sdist_hygiene.py
  dist/`, every earlier step `success`, its failed log naming `.agents/planted.md`. Branch deleted
  (push 3, the foreman's standing approval for a throwaway `ci/*-red` branch): `ls-remote --exit-code`
  exits 2 (no ref) and the branches API answers 404; the throwaway worktree removed, and the local
  plant branch deleted after checking it pointed at the plant. `red` takes the hygiene step and the
  planted path from `gate.sh`'s `agents-furniture` replay rather than restating them, and also
  requires the record's `- failing step:` and `- log:` lines to match GitHub; shown red on a wrong
  plant SHA, a wrong recorded failing step, a gate expecting another step, the green run's id, and
  a wrong log line, each plant restored and `cmp`-checked. `ledger` asserts it found at least one
  `issues/<name>.md` (shown red on an unseeded name). Deferral walk: P1–P5 and PLAN §5 hold no
  deferred work (the §3 "out of scope" is the untouched coupled core; §5's items are accepted risks
  or self-resolving, as recorded), so no seed is added.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail). In P4 and P5,
   stop at each push for its go.
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[ci]` commit; stop and report.
