---
slug: absence-check-leftover-classification
title: Explain an importable leftover by git status, not file suffix
kind: test
appetite: small
status: in_review
branch: test/absence-check-leftover-classification
base: develop
current_phase: done
last_updated: '2026-10-22'
phases:
- id: P1
  name: Classify each leftover by git status, with the suffix rule as the fallback
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
  - R9
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/absence-check-leftover-classification/verify/gate.sh P1
review:
  last_reviewed_commit: e550c200fe0c18c646d0984813c3201558932d92
  verdict: changes-requested
  blocked_reason: '2 MEDIUM: symlinked/nested leaves fall back; global excludes unread'
  cycle: 1
---

# TECH.md — Explain an importable leftover by git status, not file suffix

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/{slug}/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/prototype.diff`](research/prototype.diff), the working prototype.

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

## Phase P1 — Classify each leftover by git status, with the suffix rule as the fallback
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R8, R9 · **Depends on:** —
**Goal:** when a removed documentation module still imports, `absence_violations` explains each
location by what git knows of its files (tracked, untracked, ignored), one line and one remedy per
class, and by today's suffix text wherever git cannot answer; `test_docs_modules_absent` fails and
passes exactly as before. This is the whole change and the last phase.

- [x] Apply the prototype: `git apply spec/absence-check-leftover-classification/research/prototype.diff`.
      It adds `git_env`, `ask_git`, `git_classes`, `leaf_entries`, `covering`, `suffix_explanation`
      and `git_explanation` to `tests/test_registry.py`, moves `test_docs_modules_absent` to
      `integration`, adds the controls (PLAN §2's table), adds `need_git` to `tests/conftest.py`,
      and rewrites the `tests/test_registry.py` entry in `AGENTS.md` § Testing. Read each hunk
      against PLAN §2 before committing; reword freely, but a changed line that a
      `verify/check.py` `MUTANTS` anchor names moves that anchor in the same commit.
- [x] Leave `module_absent`, the body and parametrize of `test_docs_modules_absent`, and every path
      outside `tests/test_registry.py`, `tests/conftest.py` and `AGENTS.md` untouched (`static`
      checks each against `55fc5b7`).
- [x] Every git launch is a list literal starting with `str(SANDBOX)` and runs with `git_env()`,
      setup included; no skip in the module, and none in conftest outside `need_git`.
- [x] Every test id in the module is listed in `verify/ids.txt` with its kind (`git` for the
      controls behind `need_git`, `plain` otherwise); a test added or renamed changes that file in
      the same commit.
- [x] Comments and docstrings are declarative and carry no spec ids (§ Conventions).
- [x] Run the profile's full `verify` once before the commit:
      `uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q && bin/sandbox.sh uv run rcac-mcp --help`.
- [x] **Deferral ledger.** Walk this phase and PLAN §5 for "defer", "follow-up", "known limitation",
      "do not fix here", "not handled" and "out of scope". Each one has an `issues/{slug}.md` (from
      the KB's `ISSUE.md` template, `status: unshaped`) and a `ROADMAP.md` entry, or `.security/`
      when it describes an unremediated weakness (AGENTS.md § Factory, Where deferred work goes).
      PLAN §5 records two accepted limits (a global excludes file, an in-tree non-editable install)
      as risks, not deferred work; confirm that reading or record them. One deferral is expected:
      the `integration` marker's description in `pyproject.toml` (PLAN §5) gets
      `issues/integration-marker-wording.md` and its `ROADMAP.md` entry. The adopted seed
      `issues/absence-check-leftover-classification.md` stays: `code rcac-mcp roadmap` retires it
      after landing.
      *Built 2026-10-22:* the two accepted limits read as risks (each still gets a working `del`),
      not deferred work; `issues/integration-marker-wording.md` and its Part III entry filed.
- **Verify:** `sh spec/absence-check-leftover-classification/verify/gate.sh P1` — static checks
  against `55fc5b7`, every named id passing with git and `CI=true` in both eras, the slice without
  git off CI (git ids skip) and on CI (they fail), and the twelve mutants. Offline, one child at a
  time, about 110 s. Each part runs alone as a mode (`static`, `tests`, `nogit`, `mutants`), so
  build's fail-once mutation can target one part. R9 is met by `AGENTS.md`, which is in the tree:
  the blind reviewer can grade it.
- **Touches:** `tests/test_registry.py`, `tests/conftest.py`, `AGENTS.md` (§ Testing),
  `spec/absence-check-leftover-classification/verify/` (when an id or anchor moves).

**Moved-base amendment (foreman, 2026-10-22).** The branch was rebased onto develop `bfa0595`, which changed only `uv.lock`; `verify/gate.sh`'s `BASE` moves from `55fc5b7` to `bfa0595` so its "nothing outside the change differs" check compares against the real base. No gate check is loosened.

**Review cycle 1 remediation (2026-10-22).** REVIEW.md's two MEDIUM findings and its coverage
observations, each closed as a class. P1 is reopened; `gate.sh` is unchanged, and `verify/` only
grows: `ids.txt` lists the new controls (88 ids), and `check.py` adds a mutant per new branch and
per surviving mutant (23 on macOS, 22 elsewhere; the mutants part now takes about 4 min).

1. *Every leaf git lists gets git's class.* Git is asked from the location's parent about the
   location by name, and leaves are keyed by their path relative to it, as git prints it: no
   `resolve()` of a leaf, so a symlinked location is the link (tracked: "re-added", no `del`;
   untracked: its own `del`). On macOS both sides are compared composed (NFC), which matches
   whether `core.precomposeUnicode` is on or off. A repository nested inside falls back with the
   reason added to the message. The top-containment guard stays, on the parent. § Testing lists
   every fallback case, a missing `bin/sandbox.sh` included. Controls: `…_for_odd_entries`
   `tracked-symlink-location`, `untracked-symlink-location`, `decomposed-name`,
   `decomposed-name-unprecomposed`, `tracked-decomposed-name`, `nested-repository` (now with its
   reason), `above-ceiling`.
2. *The classes are the developer's `git status`* (foreman's ruling): `developer_excludes` reads
   `core.excludesFile` from the developer's global config files, else the XDG `ignore` default,
   from the test process's HOME, and passes it with `-c`, unless the repository sets its own. The
   hook and `GIT_*` scrub and `GIT_CONFIG_NOSYSTEM` stay. `scratch_repo` gives every control a
   scratch HOME. Control: `test_absence_message_follows_developer_excludes` (8 rows, each checked
   against a plain `git status --ignored` in the same scratch HOME).
3. *Every surviving mutant dies:* sum check dropped (`listed-twice-accepted`, killed by
   `…_fails_midway[mixed-lists-twice]`), `GIT_CONFIG_NOSYSTEM` dropped (`system-config-read`, by
   `…_developer_excludes[system-config]`), the `GIT_CEILING_DIRECTORIES` keep dropped
   (`ceiling-dropped`, by `…_odd_entries[above-ceiling]`), a top-level symlink followed
   (`location-symlink-followed`). `GIT_CLASSES` is gone. The completeness check is now one count
   per leaf, so `unlisted-file-accepted` moved its anchor.
