# REVIEW — {Title}

> Adversarial QA by `cm-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** {sha}  ·  **Base:** {base}  ·  **Date:** {YYYY-MM-DD}
- **Verdict:** approved | changes-requested
- **Cycle:** {n} of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

## Verification run

Commands actually executed and their outcomes (the spine of the review). Every CLI drive goes
through the sandbox — never a real cluster, never the developer's home:

- `uv run pytest -v` → <result — note that `-m unit`/`-m integration` select nothing until the
  restore-test-coverage cycle tags the suite, so a marker-only selector proves nothing>
- `.agents/factory/bin/sandbox.sh sh -c "uv run rcac-mcp -e local --help"` → <observed behavior>
- `.agents/factory/bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest -q'` → <result, if
  a test drive needed the sandbox's HOME/auth/SSH isolation>
- <other CLI drives, import checks, or targeted tests>

## Requirement → evidence matrix

Bidirectional traceability. Flag requirements with no implementing change **and** changes that map
to no requirement (scope creep).

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1   | <…>                          | <command>    | ✅ / ❌ |

Unmapped changes (possible scope creep): <list or "none">.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `cm-build`.

### [CRITICAL/CONFIRMED] <one-line defect>
- **Where:** `file:line`
- **Failure scenario:** <concrete inputs/state → wrong output/crash/wrong user>
- **Evidence:** <the command run and what it showed>
- **Touches invariant / requirement:** <R-ID or invariant name>

## Human-gate triggers

Set if any CONFIRMED finding touches the high-blast-radius core (`context.py`, `middleware.py`,
`auth.py`, `token.py`, `executor/base.py`, `executor/delegate.py`, `executor/ssh.py`, `server.py`)
or a security invariant (executor isolation, identity-claim precedence, user-map enforcement, the
sudo argv, the `JWT_SECRET` minimum, the issuer/audience/algorithm triple, the exec-mode resolution
ladder) — these **always** require human sign-off before `cm-publish`, regardless of auto-loop.

- <triggered? which finding?>

## Not verifiable in the sandbox

Anything a criterion needed that root, a sudoers rule, two real identities, or a live cluster would
have been required to exercise — with the stand-in evidence used instead and what the human must run
by hand. An empty section is the good case; an *omitted* section hides a gap.

- <criterion> → <stand-in evidence> · <what a human must still run>

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Was every planned phase actually shipped? Did scope balloon beyond the appetite? <notes>
