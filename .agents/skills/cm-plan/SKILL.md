---
name: cm-plan
description: >-
  Turn a shaped spec/{slug}/GOAL.md into a design and a phased roadmap. Runs an invariant gate against
  AGENTS.md, fans out read-only research subagents (scaled to appetite; codebase-first), synthesizes
  spec/{slug}/PLAN.md, re-checks invariants, and generates spec/{slug}/TECH.md — the phased YAML-FSM
  driven by /cm-build. Second step of the software-factory lifecycle (see .agents/factory/methodology.md).
disable-model-invocation: true
argument-hint: "[appetite small|big] [skip research] [status]"
allowed-tools: Read, Write, Edit, Grep, Glob, Agent, AskUserQuestion, WebSearch, WebFetch, Bash(git status *), Bash(git branch *), Bash(git rev-parse *), Bash(git log *), Bash(git add *), Bash(git commit *), Bash(uv run *), Bash(uv sync *), Bash(ls *), Bash(head *)
---

# cm-plan — research → PLAN → TECH

## When to Use

Invoke `/cm-plan` after `/cm-feature` has landed a quality `GOAL.md` on a feature/fix branch. It
produces the design (`PLAN.md`), optional backing `research/`, and the phased FSM (`TECH.md`), then
stops for your sign-off before `/cm-build` touches code. Depth scales to the GOAL's `appetite`.

Reference: [`.agents/factory/methodology.md`](../../factory/methodology.md),
[`.agents/factory/invariants.md`](../../factory/invariants.md) (the gate),
templates [`PLAN.md`](../../factory/templates/PLAN.md) / [`TECH.md`](../../factory/templates/TECH.md).

**Harness portability.** Runs on any harness — see [`factory/portability.md`](../../factory/portability.md).
Fallbacks: if the *Current state* block isn't auto-injected, run those commands yourself in Step 1; ask
in plain text and STOP if `AskUserQuestion` is unavailable; and **if subagents are unavailable, do the
research fan-out sequentially yourself** (Step 3 gives the fallback). Everything else is portable shell.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree: !`git status --porcelain | head -n 20`
- Spec artifacts: !`ls -1 spec/*/ 2>/dev/null | head -n 40`

## Argument Parsing

- `skip research` / `no research` → collapse to a lean plan (no fan-out) regardless of appetite.
- `appetite small|big` → override the GOAL's appetite for this planning run.
- `status` / `report` → summarize what artifacts already exist for this slug and what's missing; no work.

## Safety Principles

- **On a feature/fix branch, never `main`.** Derive `{slug}` = branch minus its `feature/`|`fix/`
  prefix. STOP if on the base branch or the tree is dirty.
- **`GOAL.md` must exist, be committed, and carry no unresolved `[NEEDS CLARIFICATION]` markers.** If
  markers remain, STOP and send the human back to `/cm-feature`.
- **Research is strictly read-only** and biased to the codebase (this is a small, self-contained
  repo); use the web for genuinely external unknowns — the MCP specification, FastMCP's API surface,
  Slurm/LMOD/site conventions, another center's tooling. Keep briefs minimal — only what informs
  `PLAN.md`.
- **Research never touches a real cluster.** A research subagent may read code, docs, and the web; it
  may not SSH anywhere, and any CLI it drives goes through `.agents/factory/bin/sandbox.sh`. If an
  unknown can only be resolved by observing a production system, that is a question for the human,
  not a task for a subagent.
- **Never build.** No source edits, no implementation. That's `/cm-build`.
- **The invariant gate is mandatory** (both checkpoints). Any bend gets recorded in PLAN's deviation
  table, never applied silently.

## Procedure

### Step 1 — Pre-flight & load
1. Confirm feature/fix branch + clean tree; resolve `{slug}`.
2. Read `spec/{slug}/GOAL.md` (appetite, R-IDs, non-goals). Read
   [`invariants.md`](../../factory/invariants.md).

### Step 2 — Invariant gate #1 (pre-research sanity)
Given the GOAL's intent, list the invariant sections (`invariants.md` §1–§10) this change will touch
and confirm the intent is even sane against them (e.g. anything adding per-request state must respect
the ContextVar isolation contract in §1, not a module global). If the GOAL is fundamentally at odds
with an invariant, STOP and escalate.

Answer one question explicitly and carry it into `PLAN.md` §3: **does this change touch the execution
path — who a command runs as, on which host, under whose identity?** If yes, §1/§4/§5 are in play,
the Step 3 high-blast-radius exception fires, and every phase that touches it is `hammerable: false`.

### Step 3 — Research fan-out (appetite-scaled)
- **`appetite: small` / `kind: fix` / `skip research`:** skip the fan-out. Do at most a couple of
  targeted reads yourself. Proceed to Step 4 with a lean plan; `research/` may be omitted.
  **Exception — diagnostic fixes:** when the GOAL's root cause is *unknown* or it explicitly requests
  diagnosis (an unreproducible failure, an `appetite: … — diagnosis-gated` qualifier), run the full
  fan-out regardless of `kind`/`appetite` — for such a fix the investigation *is* the deliverable, and
  skipping it yields a test-only guess. `kind`/`appetite` are proxies for "is the root cause known?";
  when they disagree with the GOAL, the GOAL wins.
  **Exception — high blast radius:** run the full fan-out whenever the change is expected to touch the
  coupled core — any file on `invariants.md`'s **High-blast-radius files** list (`context.py`,
  `middleware.py`, `auth.py`, `token.py`, `executor/*`, `server.py`) — regardless of `appetite`/`kind`.
  Step 2 already surfaced which invariants the change touches; a "small" edit to those files still
  needs the exact contracts pinned before design, or a lean plan gets them subtly wrong.
  (An explicit `skip research` argument stays a human override and still skips.)
- **`appetite: big`:** identify the *rabbit holes* — the scary unknowns that could blow the appetite
  (unfamiliar code paths, an upstream API you have assumed rather than read, packaging/extras
  mechanics, another site's conventions, protocol-level behavior). Launch **read-only research
  subagents in parallel** (Agent tool), one per topic, breadth-first:
  - Each gets: the topic + GOAL context + explicit scope boundaries + "produce a ≈1–2k-token brief
    and **write it to `spec/{slug}/research/NN-topic.md`**" (use `general-purpose` so it can write;
    number `01`, `02`, … with distinct paths so there are no write conflicts). Prefer
    codebase-exploration; reserve `WebSearch`/`WebFetch` for external unknowns.
  - Scale count to appetite (a few for medium features; more for large). Log what you fan out.
  - **Consume each agent's returned summary; never read its `.output` transcript sidecar** — it can
    flood your context. **No subagents in your harness?** Do the research yourself, sequentially,
    writing the same `spec/{slug}/research/NN-topic.md` files — identical output, only parallelism lost.
- Read the returned briefs and synthesize **`spec/{slug}/research/00-digest.md`** — the consolidated
  decisions that resolve any cross-brief contradictions with a single recommendation each.

### Step 4 — Write `PLAN.md`
From the template: **Summary**, **Design** at architecture altitude (reference concrete
`src/rcac_mcp/…` files), the **requirement → design map** (every R-ID covered), **rabbit holes
resolved** (link briefs), **risks/open questions**, **verification strategy** (seeds the per-phase
`verify:` commands — prefer driving the real CLI in the sandbox,
`.agents/factory/bin/sandbox.sh sh -c "…"`, over unit tests alone, and name anything only a human
can verify).

### Step 5 — Invariant gate #2 (post-design)
Re-walk the touched invariant sections against the *drafted design*. Fill PLAN's **deviation
justification table** for anything that bends an invariant or adds complexity (with the simpler
alternative and why it was rejected). Empty is the goal. STOP and escalate on an unavoidable
CRITICAL-invariant conflict.

### Step 6 — Generate `TECH.md` (the FSM)
Copy the template; author phases as **vertical slices, not horizontal layers** — each independently
verifiable end-to-end, ordered **core + small + novel first**. **Size circuit-breaker (soft):** if the
roadmap needs **>~8 phases**, the scope is probably too big for one unit of work — pause and reconsider
with the human (split into a pilot + follow-ups, or trim to the appetite) before committing a mega-plan.
For each phase set: `id`, `name`, `satisfies` (R-IDs), `depends_on`, `parallel` (**false** for the
coupled core — `context.py`, `middleware.py`, `auth.py`, `token.py`, `executor/*`, `server.py`; `true`
only for independent leaf-tool/docs/test work), `hammerable` (**false** for any correctness/security
phase, and always for anything on the execution path), `hill: uphill`, and a real `verify:` command.
Foundational protocol/executor changes must land and pass **before** dependent tool or middleware
phases. Set top `status: in_progress`, `current_phase` to the first phase, `last_updated` today.
Then **validate**: `uv run python .agents/factory/bin/next_phase.py spec/{slug}/TECH.md` must exit 0
and report the first phase.

### Step 7 — Meta-note (self-improvement loop · silence by default)
Before committing, reflect on the **skillset itself** — not the task, not the code. Write nothing
unless the bar is met.

**The bar (one test):** *was this the skill's fault — not mine, not the task's?* **Qualifies:** you
hand-fixed a command this skill gave (wrong flag/path, unquoted `verify:` YAML); a genuinely ambiguous
instruction; a `[NEEDS CLARIFICATION]` that better guidance could have pre-empted; an
allowed-tools/step mismatch; a gate that passed or failed misleadingly. **Stay silent for:** a merely
hard task; your own error against clear guidance; a one-off content/code issue (→ `PLAN.md`/`GOAL.md`,
not here); a vague preference.

If (and only if) the bar is met, record it in `spec/{slug}/META.md` (create from
[`templates/META.md`](../../factory/templates/META.md) if absent, else append). You may also add a
one-line **What worked well** note when a part of this skill materially helped. Caps: **≤3 findings**,
terse; if an equivalent finding already exists, append "· seen again" rather than duplicating; a fix
that would weaken a non-negotiable gate (the invariant gate, the `verify:` design, an `invariants.md`
item) is `severity=high` and must say so. **Records only** — `/cm-harness` applies fixes later,
human-reviewed. Use the next unused `F#`; always write `status=open`; append the finding as a section
**outside** any code fence:
```markdown
## F<n> — <one-line title>
`origin=cm-plan:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why it's the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the change to the skill/template/script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```
Likely sources here: the research fan-out mechanics (Step 3), the invariant-gate steps, or
`TECH.md`/YAML authoring.

### Step 8 — Commit
```
git add -A spec/{slug}      # PLAN.md + TECH.md, plus research/ and META.md when present (research/ is omitted on the lean path)
git commit -m "[{category}] Plan {slug}: design + phased roadmap"
```
`{category}` = the same category as the shape commit (`cm-feature` Step 7) — normally `{kind}`
(`fix`|`feature`|`refactor`), or a more specific AGENTS.md category (e.g. `docs`) when it fits.
**No `Co-Authored-By` trailer.** Do not push.

### Step 9 — Report & hand off
Report the design summary, the phase list (id · name · satisfies · verify), any deviations recorded,
whether the change touches the execution path, and open risks. Sign-off gate: the human reviews
`PLAN.md`/`TECH.md`; then `/cm-build` executes phase one. Stop.

## Examples

- `/cm-plan` — full appetite-scaled run for the current branch's slug.
- `/cm-plan skip research` — lean plan for a small change (no fan-out).
- `/cm-plan status` — list existing GOAL/research/PLAN/TECH for this slug and what's missing.

## Notes

- For a very large fan-out you may ask the human whether to use the `Workflow` tool instead of ad-hoc
  `Agent` calls — but default to `Agent` fan-out here.
- Keep `research/` lean: reviewers and future readers pay a tax for sprawl. Only keep what informs the
  design.
- `TECH.md` is the resume ground-truth for `/cm-build`; if `next_phase.py` reports it invalid, fix it
  before committing.
