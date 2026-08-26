---
slug: example-slug
title: "One-line human title for this feature"
kind: feature
appetite: big
status: in_progress
branch: feature/example-slug
base: main
current_phase: P1
last_updated: "2026-01-01"
phases:
  - id: P1
    name: "First vertical slice (core + small + novel)"
    status: pending
    satisfies: [R1]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v -m unit -k example"
  - id: P2
    name: "Second slice"
    status: pending
    satisfies: [R2, R3]
    depends_on: [P1]
    parallel: false
    hammerable: true
    hill: uphill
    verify: ".agents/factory/bin/sandbox.sh sh -c 'uv run rcac-mcp -e local --help && cd \"$CLUSTER_MCP_REPO\" && uv run pytest -q -k cli'"
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
---

# TECH.md — {title}

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/{slug}/TECH.md`); the per-phase
checklists below are the work. `cm-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs (if `appetite: big`).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `cm-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `cm-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (a leaf tool module, docs,
  tests). The coupled core (`context.py`, `middleware.py`, `auth.py`, `token.py`, `executor/*`,
  `server.py`) is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
  Anything touching who a command runs as, on which host, under whose identity is `false` by default.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in `.agents/factory/bin/sandbox.sh sh -c '…'` so they get a throwaway `HOME`, a
  blank auth environment, and no reachable cluster). **Two traps.** A `pytest` inside the sandbox
  must `cd "$CLUSTER_MCP_REPO"` first, or it roots itself in the throwaway dir and never loads
  `pyproject.toml`. And **no test carries `@mark.unit`/`@mark.integration` yet**, so a gate whose
  only selector is `-m …` reports "no tests ran" and exits 0 — green over nothing.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md) — it is the constitution. Consult
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) for the curated footgun
  checklist relevant to this change.
- One phase per `cm-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. Branch commit subjects follow the house style `[{category}] Build {slug}
  P<n>: …` (no `WIP:` prefix) — squashed into the single PR-title commit at `cm-publish`.
- **No `Co-Authored-By` trailer** (repo convention).
- Adding, renaming, or removing a tool updates `SERVER_INSTRUCTIONS` in `server.py` **and** the tool
  inventory in `README.md` **in the same commit** — both are agent- and user-facing contracts.

---

## Phase P1 — First vertical slice
**Satisfies:** R1 · **Depends on:** —
**Goal:** <what this slice delivers, end to end and independently verifiable>.

- [ ] <concrete step>
- [ ] <concrete step>
- **Verify:** `uv run pytest -v -m unit -k example` (and/or drive the CLI in the sandbox).
- **Touches:** `src/rcac_mcp/…`, `tests/…`, `README.md`.

## Phase P2 — Second slice
**Satisfies:** R2, R3 · **Depends on:** P1
**Goal:** <…>.

- [ ] <concrete step>
- **Verify:** `.agents/factory/bin/sandbox.sh sh -c 'uv run rcac-mcp -e local --help && cd "$CLUSTER_MCP_REPO" && uv run pytest -q -k cli'`.
- **Touches:** `src/rcac_mcp/…`.

---

## How `cm-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone, and never on exit 0 alone;
   assert the post-condition you actually care about.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
