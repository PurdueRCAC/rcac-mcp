---
slug: sbatch-script-name-collision
title: Give every sbatch job script written from content its own file
kind: fix
appetite: small
status: in_review
branch: fix/sbatch-script-name-collision
base: develop
current_phase: done
last_updated: '2026-10-23'
phases:
- id: P1
  name: A per-call token in the job script's name, proven sequential, overlapping and across processes
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/sbatch-script-name-collision/verify/gate.sh P1
- id: P2
  name: The sbatch docstring states the new name, as one named served-surface delta
  status: done
  satisfies:
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/sbatch-script-name-collision/verify/gate.sh P2
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — Give every sbatch job script written from content its own file

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/{slug}/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** none (lean path; PLAN §4).

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

## Phase P1 — A per-call token in the job script's name
**Satisfies:** R1, R2, R3, R4 · **Depends on:** —
**Goal:** every `sbatch` call with `script_content` writes and submits
`~/jobs/<name>_<YYYYmmdd_HHMMSS>_<16 hex>.sh`, a name no other call draws, whether the calls come
one after the other, overlap through one server or two, or come from separate processes; proven by
tests that go red on the old name and on a per-process counter.

- [x] `src/rcac_mcp/tools/slurm.py`: `import secrets`; in `_job_script_path`, keep `name` and
      `timestamp` exactly as they are (the clock still read as `datetime.now()` through the module's
      `datetime`), add `token = secrets.token_hex(8)` and return
      `UserPath(f'{DEFAULT_JOBS_DIR}/{name}_{timestamp}_{token}.sh')`. Its docstring states the form
      and, declaratively, why the token comes from the entropy source on every call (PLAN §2). No
      other line of `sbatch` changes; the `sbatch` docstring waits for P2.
- [x] `tests/test_sbatch.py`: a frozen-clock helper (a `datetime` subclass whose `now()` returns one
      fixed instant, patched over `slurm.datetime`); the path regex in
      `test_script_content_is_written_then_submitted` gains `_[0-9a-f]{16}`; add
      `test_same_second_scripts_get_their_own_paths` (`unit`; rows `same-name`, `no-name`,
      `sanitised-alike`), `test_overlapping_submissions_each_submit_their_own_script` (`integration`;
      the `mode` fixture × `servers` rows `one-server`, `two-servers`; `harness.Rendezvous(2)` plus a
      test-local wrapper executor that waits on a `threading.Barrier(2)` with a timeout after each
      `write` and records the submitted script's bytes at `run`), and
      `test_separate_server_processes_name_scripts_apart` (`integration`; two
      `[str(SANDBOX), sys.executable, '-c', …]` children with clock, `os.getpid`,
      `socket.gethostname` and `platform.node` pinned alike, each printing its path and the origin
      of `rcac_mcp`, which must be the parent's). Each violation for two calls on one path carries
      "calls share a script path"; a run that sees another call's bytes carries "ran another call's
      script". PLAN §2 lists each test's assertions.
- [x] `tests/test_writes.py`: `test_same_second_scripts_keep_their_own_bytes` (`integration`,
      `_modes()`: `local`, `delegate`, `ssh`), seeding `~/jobs` with old-form files at the frozen
      second, a new-form file and an unrelated file, then six same-second calls (`sweep`×2, no
      name ×2, `a/b` and `a_b`): job id returned, distinct submitted paths, each an executable
      `~/jobs` file beginning with its sanitised stem and holding its own bytes, every seeded file
      byte-identical ("changed a file already in ~/jobs").
- [x] `tests/mutants.py`: Cases `script-name-per-second` and `script-name-process-counter`, with the
      targets and `why` in PLAN §2's table, beside the other `tools/slurm.py` cases.
- [x] `AGENTS.md` § Testing: the `tests/test_sbatch.py` entry states the new form
      (`~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh`, the token new for each call) and the three new
      tests; the `tests/test_writes.py` entry names the same-second row.
- [x] Confirm with `--collect-only` that the collected ids match `verify/ids-P1.txt`; if
      parametrization yields a different spelling for the same rows, fix the list in this commit and
      say so in the body.
- **Verify:** `sh spec/sbatch-script-name-collision/verify/gate.sh P1`: `static` (only
  `tools/slurm.py` under `src/`; executor, context, middleware, server and sandbox untouched; one
  `secrets.token_hex(8)` and no counter, seeded generator, clock, pid or host-name source in
  `slurm.py`; frozen-clock names distinct and full-form; no old form in `AGENTS.md`), `tests1`
  (every P1 id passes once in each era, inside `bin/sandbox.sh`), `mutants1` (both planted naming
  defects killed for the stated reason). Phase-only parts for build's fail-once mutation:
  `gate.sh tests1`, `gate.sh mutants1`.
- **Touches:** `src/rcac_mcp/tools/slurm.py`, `tests/test_sbatch.py`, `tests/test_writes.py`,
  `tests/mutants.py`, `AGENTS.md` (§ Testing).

## Phase P2 — The `sbatch` docstring states the new name
**Satisfies:** R5 · **Depends on:** P1
**Goal:** the served `sbatch` description says where a script written from content goes and how it
is named, exactly as P1 names it, landed as the one named delta `sbatch.description`; and the
cycle's deferral ledger is closed.

- [x] `src/rcac_mcp/tools/slurm.py`: replace the `sbatch` docstring's `script_content` sentence with
      PLAN §2's text: the path form, `<name>` as the sanitised job name (the rule not restated),
      `<token>` as 16 random hex digits new per call. `Args:`, `Returns:` and `Examples:` stay as
      they are. The served description goes from 1,872 to 1,960 characters; it must stay at most
      2,000, under Claude Code's 2,048-character cut (gate ruling, 2026-10-23).
- [x] `tests/fixtures/served_surface.json`: §9's scripted hand-edit of the one delta: load it, set
      `tools.sbatch.description` to `inspect.getdoc(sbatch.fn)`, dump with `indent=1,
      ensure_ascii=False` and the trailing newline. The diff is the one description line.
- [x] `tests/test_sbatch.py`: `test_the_docstring_states_the_script_path_the_tool_writes` (`unit`;
      PLAN §2), whose mismatch carries "does not match the docstring". `tests/mutants.py`: Case
      `script-token-short` (`secrets.token_hex(4)`), targeting it.
- [x] `AGENTS.md`: §9's sentence on surface deltas and § Testing's `tests/test_surface.py` delta list
      each add that `` `sbatch-script-name-collision` `` changed `` `sbatch.description` ``; § Testing's
      `tests/test_sbatch.py` entry names the docstring test.
- [x] Commit body lists the served-surface delta: `sbatch.description`.
- [x] **Deferral ledger.** Walk P1, this phase and PLAN §5 for "defer", "follow-up", "known
      limitation", "not fixed here", "out of scope" and "left as". Each deferral has an
      `issues/{slug}.md` (`status: unshaped`, from the KB's `ISSUE.md` template) and a `ROADMAP.md`
      entry, or `.security/` when the security lane says so. PLAN §5's name-length headroom was
      accepted at the PLAN/TECH gate with no seed (2026-10-23). No deferral is expected.
- **Verify:** `sh spec/sbatch-script-name-collision/verify/gate.sh P2`: `static`; `surface` (the
  fixture differs from the base only at `tools.sbatch.description`, equal to the served docstring
  stating the new form, and at most 2,000 characters, a check on this cycle's delta only;
  `AGENTS.md` records the delta twice); `tests1` and `tests2` in both
  eras; `mutants2`; `full`, the profile's verify, with each pytest inside the sandbox. Phase-only
  parts for build's fail-once mutation: `gate.sh tests2`, `gate.sh mutants2`.
- **Touches:** `src/rcac_mcp/tools/slurm.py`, `tests/fixtures/served_surface.json`,
  `tests/test_sbatch.py`, `tests/mutants.py`, `AGENTS.md` (§9, § Testing).

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
