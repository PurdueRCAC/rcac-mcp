---
slug: sdist-hygiene-truncated-gzip
title: An unreadable sdist is named, never a traceback
kind: fix
appetite: small
status: in_progress
branch: fix/sdist-hygiene-truncated-gzip
base: develop
current_phase: P1
last_updated: '2026-10-19'
phases:
- id: P1
  name: Read each sdist in full and name every unreadable one in one line
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/sdist-hygiene-truncated-gzip/verify/gate.sh P1
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — An unreadable sdist is named, never a traceback

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/{slug}/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Prototype:** [`research/prototype.diff`](research/prototype.diff), the tree the gate was
  dry-run on (against `develop` `5153e47`).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
  **Artifact-deliverable R-IDs.** Some requirements are met by a *committed document* — a research
  audit, a protocol doc, an assessment — not by CLI-observable behaviour. Attach such an R-ID to the
  phase that produces and commits the artifact, and give that phase a content `verify:`
  (`test -f …`, a `grep -q` for the sections that must exist) rather than forcing a CLI drive.
  **Say so in the phase body**, because it changes who can grade it: `code-review` blinds its reviewer
  to all of `spec/`, so an R-ID whose evidence lives there is graded by the *orchestrator*, not the
  blind reviewer. Naming it in `TECH.md` is what lets the review skill route it correctly instead of
  reporting an unverifiable requirement.
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in the profile's `throwaway environment` helper, `<helper> sh -c "…"`, so they hit
  a disposable instance, never the developer's real data). A gate carrying `$( )` or quoting the
  calling shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it. `--verdict approved`
  also clears `review.blocked_reason` unless one is passed explicitly.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

---

## Phase P1 — Read each sdist in full and name every unreadable one in one line
**Satisfies:** R1, R2, R3, R4, R5, R6 · **Depends on:** —
**Goal:** `uv run python bin/sdist_hygiene.py <dir>` reads each sdist in full, names every one it
cannot read in a single `<tarball>: cannot be read (…)` line, keeps checking the rest, never prints
a traceback and never calls an unreadable sdist clean; readable sdists are judged byte for byte as
on `5153e47`. This is the whole change and the last phase.

- [ ] Apply the prototype: `git apply spec/sdist-hygiene-truncated-gzip/research/prototype.diff`.
      It adds `member_names`, `check_streams` and the broad, one-line handler to `bin/sdist_hygiene.py` (PLAN §2),
      the new tests to `tests/test_sdist_hygiene.py`, and the § Testing entry's new sentences to
      `AGENTS.md`. Read each hunk against PLAN §2 before committing; reword freely, but a changed
      line that a `verify/mutants.py` anchor names moves that anchor in the same commit.
- [ ] Leave `FURNITURE`, `SDIST_SUFFIX`, `member_violations`, `main`, the release recipe's step and
      `.github/workflows/ci.yml` untouched (`static.py` checks each against `5153e47`).
- [ ] Every new test is `@mark.unit`, and every test id in the module is listed in
      `verify/ids.txt`; a test added or renamed changes that file in the same commit.
- [ ] Comments and docstrings are declarative and carry no spec ids (§ Conventions).
- [ ] Run the profile's full `verify` once before the commit:
      `uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && bin/sandbox.sh uv run rcac-mcp --help`.
- [ ] **Deferral ledger.** Walk this phase and PLAN §5 for "defer", "follow-up", "known limitation",
      "do not fix here" and "out of scope". Each one has an `issues/{slug}.md` (from the KB's
      `ISSUE.md` template, `status: unshaped`) and a `ROADMAP.md` entry, or `.security/` when it
      describes an unremediated weakness (AGENTS.md § Factory, Where deferred work goes). None is
      expected; say so in the phase report. The adopted seed `issues/sdist-hygiene-truncated-gzip.md`
      stays: `code rcac-mcp roadmap` retires it after landing.
- **Verify:** `sh spec/sdist-hygiene-truncated-gzip/verify/gate.sh P1` — static checks, the named
  test ids, the 120-plant drive of the real command with base parity, the drive's negative control
  against `5153e47`, and the twelve mutants. Offline, one child at a time, about 30 s. Each part runs
  alone as a mode (`static`, `tests`, `drive`, `control`, `mutants`), so build's fail-once mutation
  can target one part.
- **Touches:** `bin/sdist_hygiene.py`, `tests/test_sdist_hygiene.py`, `AGENTS.md` (§ Testing),
  `spec/sdist-hygiene-truncated-gzip/TECH.md` (and `verify/` only if an anchor or id moves).

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
