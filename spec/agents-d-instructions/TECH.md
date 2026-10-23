---
slug: agents-d-instructions
title: Serve /etc/agents.d as the server's instructions
kind: feature
appetite: big
status: in_review
branch: feature/agents-d-instructions
base: develop
current_phase: done
last_updated: '2026-10-23'
phases:
- id: P1
  name: Site reader and composer
  status: done
  satisfies:
  - R3
  - R4
  - R5
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/agents-d-instructions/verify/p1.sh && uv run python spec/agents-d-instructions/remediation/F1-budget-units.py && uv run python spec/agents-d-instructions/remediation/F2-name-lines.py && uv run python spec/agents-d-instructions/remediation/F5-glob-state.py
- id: P2
  name: Startup reads once and serves the composed instructions
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/agents-d-instructions/verify/p2.sh && uv run python spec/agents-d-instructions/remediation/F1-budget-units.py && uv run python spec/agents-d-instructions/remediation/F3-delegate-others.py
- id: P3
  name: cluster_context and the startup-text resource
  status: done
  satisfies:
  - R1
  - R5
  - R6
  - R7
  - R8
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/agents-d-instructions/verify/p3.sh && uv run python spec/agents-d-instructions/remediation/F3-delegate-others.py && uv run python spec/agents-d-instructions/remediation/F4-per-server.py
- id: P4
  name: Documentation, mutants and the deferral ledger
  status: done
  satisfies:
  - R10
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/agents-d-instructions/verify/p4.sh && uv run python spec/agents-d-instructions/remediation/F3-delegate-others.py docs
review:
  last_reviewed_commit: ef8322d0b1ee8ad0247d3387389f275e21b190fc
  verdict: changes-requested
  blocked_reason: CRITICAL budget unit (§9) + 2 LOW + 1 PLAUSIBLE + C12
  cycle: 1
---

# TECH.md — Serve /etc/agents.d as the server's instructions

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/agents-d-instructions/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.
- **Gates:** [`verify/`](verify/), drafted with the plan and dry-run on a prototype, one tree per
  phase. Each `pN.sh` states its footprint; run from the worktree root.

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

**This cycle's own preconditions.**

- **Build starts only after `safe-execution` has landed on `develop`** (GOAL C9). Before P1, merge
  `origin/develop` into this branch (it carries `safe-execution`); its `resources.py` edits
  (`LIST_CONTEXT`, `cat --`) are what P3 deletes. If `safe-execution` changed any of the contracts
  PLAN § 5 lists as assumed, stop and re-plan the affected phase.
- The gates default their base to `git merge-base HEAD origin/develop`; fetch first.
- Comments and docstrings name no `R#`/`P#` (§ Conventions). Every new test is `unit` or
  `integration`. Every CLI drive goes through `bin/sandbox.sh`.
- The served-surface fixture changes only by `verify/fixture_deltas.py apply`, never by hand; the
  commit that changes it lists the deltas it applied in its body (C10).

**Amendment — review 1 remediation (2026-10-23).** Review cycle 1 returned changes-requested with
five classes (REVIEW.md → Routing, cycle 1) and GOAL C12. Remediation reopened P1–P4 rather than
adding phases, and bundles them into one commit: C12's code, its tests and its documentation
(§8, README) are one same-commit obligation (§ Invariants' opening rule; § Conventions), and
the class tests, their mutants and § Testing land together. `verify/` stays frozen at the plan
commit; each reopened phase's gate runs its frozen gate, then the class checkers in
[`remediation/`](remediation/) (`F1`–`F5`, each evaluating its class's property over planted
directories through both reads, then the real CLI, then the class's mutants). The frozen p1–p3
gates were run green on the final tree before reopening, so no gate was retuned to P4's. P1 now
also satisfies R4 (the composer's unit), P3 also R8 (C12's line served in `delegate` mode).
`__init__.py`'s `_read_site` (this cycle's own function, high blast radius) gains the
`delegate` read for others; `_resolve_exec_mode` and every other human-gated file are untouched.

---

## Phase P1 — Site reader and composer
**Satisfies:** R3, R5, R8 (and R1's and R4's reading and budget rules, wired in P2) · **Depends on:** —
**Goal:** `src/rcac_mcp/site.py` reads a directory the way the convention says, through either
path, and composes instructions within a budget; nothing calls it yet.

- [x] Create `src/rcac_mcp/site.py` exactly to PLAN § 2.1–2.2: the constants (`AGENTS_D`,
      `READ_TIMEOUT` read at call time), `SiteFile`, `SiteContext` and its constructors and `text`,
      `read_local`, `READ_SITE` (registered with `scripts.fixed`; the directory as `"$1"`; it
      unsets `base64 tr test` first), `parse_remote`, `read_remote`, `read_bounded` (daemon thread),
      `units`, `omission_note`, `compose`, `minimum_budget`, `BudgetError(ValueError)`, `register`,
      `site_of`. Names and signatures are the ones `verify/site_gate.py`'s docstring lists.
- [x] `tests/test_site.py` with the test functions `verify/p1.sh` names, built on `read_violations`
      and `compose_violations`. The layout includes `Z.md` and `10-first.md` (byte order versus
      case-folded order), a dotfile, a space, `AGENTS.md`, `Upper.MD`, a blank file, a `.txt`, a
      symlink, a directory and a FIFO named `*.md`, plus `.md`, `-n.md` and `end 0.md`.
      `test_remote_script_matches_local_read` runs `READ_SITE` under `bin/sandbox.sh`
      (`integration`). No test skips (`test_only_need_shell_skips`): the non-UTF-8 name row asserts
      the file system's refusal where it refuses one (macOS). A failed read composes within the
      smallest budget a good read accepts (`test_a_budget_that_serves_a_good_read_serves_a_failed_one`).
- [x] `tests/test_hygiene.py` stays green: the new launches start with `bin/sandbox.sh`.
- [x] *Review 1, F1:* every length the composer reserves, cuts or checks is in UTF-16 units (`_cut`
      by `units`; with no files the minimum also holds the site's own reason), pinned by
      `test_astral_names_and_reasons_keep_the_budget` (both reads) and
      `test_every_reason_fits_the_room_kept_for_it`; mutants `site-detail-code-points`,
      `site-reason-unchecked`.
- [x] *Review 1, F2:* names are shown through `site.shown` (C0, DEL, C1, U+2028, U+2029 as `\xNN`/
      `\uNNNN`) in headers, reasons and the note, pinned by `test_a_name_cannot_break_a_served_line`
      (both reads) and `test_a_name_is_shown_with_its_control_characters_escaped`; mutant
      `site-name-unescaped`.
- [x] *Review 1, C12 read:* `read_local(for_others=True)` is unreadable when the directory lacks
      others' read or search bit or a file others' read bit, pinned by
      `test_a_read_for_others_keeps_only_what_others_can_read`; mutant `site-others-unchecked`.
- [x] *Review 1, F5:* `READ_SITE` unsets `GLOBIGNORE`, `set +f`, and `shopt -u` the glob options
      itself (not `FIXED_RESET`), pinned by `test_remote_read_ignores_the_login_shells_glob_state`
      (any bash) and `test_remote_read_ignores_exported_shell_options` (`BASHOPTS`, bash ≥ 4.1,
      through `need_shell`: skips on macOS, runs on CI); mutant `site-glob-state-inherited`.
- **Verify:** `sh spec/agents-d-instructions/verify/p1.sh` (named tests, then `site_gate.py`'s
  independent oracle); since review 1, then `remediation/F1`, `F2` and `F5`.
- **Touches:** `src/rcac_mcp/site.py`, `tests/test_site.py`.

## Phase P2 — Startup reads once and serves the composed instructions
**Satisfies:** R1, R2, R4, R8 · **Depends on:** P1
**Goal:** the real CLI reads the site once at startup, where its mode says, within the bound, and
every client in both eras receives the base plus the site context within the budget; a budget too
small exits 2 naming the minimum.

- [x] `__init__.py` (high blast radius): `--instructions-budget` and `RCAC_INSTRUCTIONS_BUDGET` in
      the interface, `APP_USAGE` and `APP_HELP` (same commit); `_resolve_budget`; `_read_site`
      branching on `'ssh'` only; the three lines in `run()` per PLAN § 2.3. No change to
      `_resolve_exec_mode`, and no second `if exec_mode == 'delegate':` line.
- [x] `server.py` (high blast radius): `create_mcp_server(…, *, site, budget=DEFAULT_BUDGET)`,
      `instructions=compose(SERVER_INSTRUCTIONS, site, budget)`, `register(server, site)`.
      `SERVER_INSTRUCTIONS` itself is unchanged in this phase (it is rewritten in P3, with the tool
      it names).
- [x] Harness: `SITE`, the `site=` keyword on `delegate_server`, `shared_server`, `bare_server`,
      the `read_resource` verb, `Listed.instructions`; § Testing's verb list follows.
- [x] Rewrites: `test_instructions` (base check plus served equals `compose(…, SITE)`),
      `test_protocol` (composed text), `test_exec_mode` stub (`**kwargs`), `test_transports`
      (instructions per transport and era equal `compose(SERVER_INSTRUCTIONS, read_local())`),
      `site=SITE` at every direct `create_mcp_server('none')`; re-anchor the `instructions-dropped`
      mutant on the new line and re-run it.
- [x] `tests/test_site_startup.py` with the functions `verify/p2.sh` names; the hung-read test
      has `local`, `ssh` and `local-at-the-good-minimum` rows, each starting within 0.5 s of the
      bound with the timeout line.
- [x] `uv run python spec/agents-d-instructions/verify/fixture_deltas.py apply` (the `instructions`
      delta), listed in the commit body and in `test_surface.py`'s docstring.
- [x] `AGENTS.md` § Configuration: the variable, the flag, the unit, the default (same commit as
      the flag).
- [x] *Review 1, C12 wiring:* `_read_site` reads for others in `delegate` mode only (no new
      `if exec_mode == 'delegate':` line), pinned by `test_startup_reads_once_where_the_mode_says`;
      mutant `delegate-reads-for-itself`.
- **Verify:** `sh spec/agents-d-instructions/verify/p2.sh` (named tests in both eras, then
  `serve_gate.py instructions` driving the real CLI over stdio, then the fixture check); since
  review 1, then `remediation/F1` (the real CLI at the minimum budget) and `F3`.
- **Touches:** `src/rcac_mcp/__init__.py`, `src/rcac_mcp/server.py`, `tests/harness.py`,
  `tests/test_*.py` named above, `tests/mutants.py`, `tests/fixtures/served_surface.json`,
  `AGENTS.md`.

## Phase P3 — cluster_context and the startup-text resource
**Satisfies:** R1, R5, R6, R7, R9 · **Depends on:** P2
**Goal:** `cluster_context` and `rcac://context` return the startup text in every mode and era with
no executor; the cache is gone; the base instructions are the compact rewrite naming the tool.

- [x] `src/rcac_mcp/tools/context.py` with `cluster_context()` (PLAN § 2.5), appended to the import
      list in `tools/__init__.py`.
- [x] `resources.py`: the resource returns `site_of(get_server()).text`; the new description;
      delete `_cluster_context_cache`, `load_cluster_context`, `get_cached_context`,
      `clear_context_cache`, `LIST_CONTEXT`.
- [x] `SERVER_INSTRUCTIONS` rewritten compact per PLAN § 2.4, listing `cluster_context`; a test in
      `test_instructions.py` holds `units(SERVER_INSTRUCTIONS) <= 1500`; `NON_TOOL_TERMS` loses
      `findscratch` (§9: same commit as the tool, with the README inventory and the fixture).
- [x] `EXPECTED_TOOLS` gains `cluster_context`; `test_tools` and `test_no_executor` take
      `EXECUTORLESS`; the cache test goes.
- [x] `tests/test_context.py` with the functions `verify/p3.sh` names; R9's test stubs
      `middleware.DelegatingExecutor` with instances that all report one hostname, and asserts
      nothing ran and no `sudo` launched; the changed-directory test rewrites and grows a planted
      directory mid-session; the unmapped-user test pins the intended behaviour ruled in GOAL C11
      (2026-10-19): a mapped user is R7's and R9's "authenticated user" for the tool, `delegate`
      mode refuses the tool to an identity absent from the map, the instructions and
      `rcac://context` reach it anyway, and §5/`middleware.py` stay untouched.
- [x] `fixture_deltas.py apply` (adds `tools.cluster_context` and the resource description); list
      all three deltas in the commit body and `test_surface.py`'s docstring.
- [x] `README.md`: tool inventory, the site-context subsection, how to raise both limits.
- [x] `AGENTS.md` §8 and §9 per PLAN § 2.7 (the contract lands with its section, § Invariants'
      opening rule).
- [x] *Review 1, C12 served:* `test_delegate_serves_only_what_others_can_read` (a file and the
      directory others cannot read, both eras: the line, no site text; `local` keeps the files);
      §8 and README state the rule in the same commit, and the "world-readable" assumption is
      gone from the live text.
- [x] *Review 1, F4:* `test_each_server_keeps_its_own_site` (servers built before any is asked,
      both eras); mutant `site-holder-global`.
- **Verify:** `sh spec/agents-d-instructions/verify/p3.sh` (named tests in both eras, then
  `serve_gate.py context`: tool, resource and one read over stdio in both eras and over HTTP in
  `delegate` mode with two users; then the fixture check); since review 1, then `remediation/F3`
  and `F4`.
- **Touches:** `src/rcac_mcp/tools/context.py`, `tools/__init__.py`, `resources.py`, `server.py`,
  `tests/test_context.py`, `tests/test_{tools,no_executor,registry,instructions,surface}.py`,
  `tests/fixtures/served_surface.json`, `README.md`, `AGENTS.md`.

## Phase P4 — Documentation, mutants and the deferral ledger
**Satisfies:** R10 · **Depends on:** P3
**Goal:** the documentation R10 names is complete and true, the new contracts each have a killed
mutant, and nothing deferred is left unrecorded. R10's evidence is in the repo (`AGENTS.md`,
`README.md`, `server.py`), not under `spec/`, so the blind reviewer can grade it.

- [x] `AGENTS.md` § Testing: `test_site.py`, `test_context.py`, `test_site_startup.py`, the new
      harness verbs, and the three fixture deltas; the Footguns list's `resources.py` entry becomes
      `site.py` (the per-server holder); the Repository map gains `site.py` and `tools/context.py`;
      the Architecture section notes the startup read.
- [x] Mutants in `tests/mutants.py`, each run with `--self-check` and killed for its `why`:
      `site-agents-md-included`, `site-byte-order`, `site-budget-overflow`, `site-read-unbounded`
      (factor 2), `startup-ssh-unbounded`, `context-rereads`, `context-through-executor` (plus the
      re-anchored `instructions-dropped`); a parametrised target is named with its id.
      *Amended at build:* `site-read-unbounded`, `startup-ssh-unbounded` and `context-rereads` had
      only a bare `assert` to be killed for, so their targets' assertions gained messages naming the
      contract (`past its 0.3 s bound`, `a hung read held startup`, `what is served changed after
      startup`); `context-through-executor` plants a run through the request's executor in
      `tools/context.py` and is killed on the shared rows for `an executor ran`.
- [x] Re-read PLAN § 5 and every phase body above for "known limitation", "left as a follow-up",
      "do not fix here": each needs an `issues/{slug}.md` (from the KB's `ISSUE.md`,
      `status: unshaped`) and a `ROADMAP.md` entry, or `.security/` if sensitive. Known candidates:
      none deferred by design; the open question on file order is the maintainer's, not a deferral.
      *Amended at build:* the re-read found two items PLAN § 5 records and leaves, neither
      security-sensitive, filed as `issues/site-file-size-cap.md` ("No size cap on a file") and
      `issues/fastmcp-update-check.md` (FastMCP's startup update check, pre-existing), each with
      its `ROADMAP.md` entry in Part III.
- [x] Do **not** narrow `resource-reads-without-executor`'s ROADMAP entry or retire the seeds here;
      that is `code rcac-mcp roadmap` after landing (C10).
- [x] Standing last-phase items: the deferral ledger above; no other project-level obligation is
      carried by the previous cycle's last phase in this repo.
- [x] *Review 1:* § Testing, §8 and the Footguns `site.py` entry describe the five classes'
      tests and the holder's pin; the seven new mutants are in `tests/mutants.py`, each killed
      with `--self-check`.
- **Verify:** `sh spec/agents-d-instructions/verify/p4.sh` (`docs_gate.py all`, then the profile's
  full verify). `p4.sh docs` is the pre-push part. Since review 1, then
  `remediation/F3-delegate-others.py docs`.
- **Touches:** `AGENTS.md`, `tests/mutants.py`, `tests/test_mutations.py` (anchors), `ROADMAP.md`
  and `issues/` only if something was deferred. *Amended at build:* also the assertion messages in
  `tests/test_site.py`, `tests/test_site_startup.py` and `tests/test_context.py` (the mutants'
  `why`), and `tests/test_context.py`'s two citations of a GOAL clarification, restated as the rule
  itself (the foreman's ruling on P3: no spec ids in the suite, in spirit).

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
