# Harness change log (`cm-harness`)

The cross-job ledger of every harness self-improvement **decision** — the *act* side of the factory's
self-improvement loop. `/cm-harness` appends one entry per **applied** / **rejected** (and notable
**deferred**) finding, and **reads this file before applying**: a proposed fix that reverts a recent
change, or repeats a previously-rejected one, is flagged to the human rather than silently re-applied.
This is the loop's **anti-thrash memory**. (Findings themselves live in each feature's
`spec/{slug}/META.md`; this file is the durable record of what was *done* about them.)

Entry format — one section per decision, newest at the bottom:

```markdown
## {YYYY-MM-DD} — {slug} {F#}: {one-line title}
`decision=applied|rejected|deferred commit={sha|—} target={file}`
- **Rationale:** what was changed (and why it generalizes) / why rejected (overfit, stale, would-weaken-a-gate) / why deferred.
```

Read `origin`/`severity`/`category` from the finding in `META.md`; this ledger records the *outcome*.

---

<!-- Decisions are appended below this line by /cm-harness. -->

## 2026-08-26 — (bootstrap): Port the HyperShell software factory to cluster-mcp
`decision=applied commit=— target=.agents/`
- **Rationale:** Not a `META.md` finding — the founding entry, recorded so the first real `/cm-harness`
  run has a baseline to reason against. The eight `cm-*` skills, the factory reference docs, the
  templates and the FSM scripts were ported from
  [hypershell](https://github.com/hypershell/hypershell)'s `.agents/` harness, which carries ~20
  applied refinements in its own ledger; this port inherits them rather than rediscovering them.
  **Four deliberate divergences from the upstream harness**, each a decision a future run should not
  silently revert:
  1. **`main`, not git-flow.** Upstream is `develop`/`master`. This repo has a single `main`;
     `cm-publish` targets it, `cm-roadmap`'s "landed" test is `git ls-tree main -- spec/{slug}`, and
     `cm-release` tags on `main` with no back-merge step.
  2. **`sandbox.sh` replaces `temp_site.sh`.** Upstream isolates a HyperShell *site* (database + logs).
     The hazard here is different and larger: a CLI drive in `local` mode writes to the developer's
     real home as the developer, and a drive in the default `ssh` mode would open a session on a
     production cluster. `sandbox.sh` gives the run a throwaway `HOME`/XDG, blanks the auth
     environment, and **unsets `RCAC_SSH_HOST` so the default stdio→`ssh` path fails closed**
     (verified: exits `bad_argument` with "SSH host required").
  3. **`cm-release` is trimmed to what exists.** No man pages, no ghcr, no back-merge, no PyPI
     publish until the project opts in — the worktree dry-run, the gate, and the signed tag are kept
     because those are the parts that catch real mistakes.
  4. **`getting-started.html` was not ported.** 60KB of general-audience onboarding prose whose value
     is method-level and largely project-independent; duplicating it invites exactly the silent drift
     the upstream ledger's F13 entry was filed about. Seeded as `factory-onboarding-page` in
     `ROADMAP.md` instead. `cm-harness` Step 6's staleness check on it is therefore **conditional on
     the file existing**.

## 2026-08-26 — (bootstrap): Correct seven defects found by an adversarial audit of the port
`decision=applied commit=— target=.agents/ AGENTS.md ROADMAP.md issues/`
- **Rationale:** Not `META.md` findings either — the port was audited before landing on `main` by five
  independent reviewers (cross-references, skill consistency, invariant truth, roadmap accuracy,
  runnability), each finding then refuted by a separate agent. 15 candidates, 7 survived. Recorded so a
  later run does not "helpfully" restore any of it. Every fix was re-verified by execution.
  1. **`sandbox.sh` broke pytest** (the worst of them). `cd "$sandbox"` is correct for write
     containment but pointed pytest's rootdir at the throwaway dir, so `pyproject.toml` never loaded and
     the documented `sandbox.sh uv run pytest -m integration` example collected **zero** tests while
     exiting 0 — a verify gate that is green over nothing. Added a `CLUSTER_MCP_REPO` export and
     changed every example to `sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest …'`, which keeps the
     HOME/auth/fail-closed-SSH guarantees and gives up only cwd containment (pytest's `tmp_path`
     replaces it). Propagated to AGENTS.md, invariants.md §10, templates/TECH.md, templates/REVIEW.md,
     cm-build Step 4 and cm-harness Step 6.
  2. **`-m unit` / `-m integration` select nothing.** The markers are registered but no test carries
     one, so every marker-only gate reports "no tests ran" and exits 0. Caveated at each point of use
     rather than silently tagging 81 docs tests that are about to be deleted.
  3. **invariants.md §4 forbade what the code does.** "Never make `local` the default for a network
     transport" contradicted rung 4 — a bare `-t http` *does* resolve to `local`, as `APP_HELP` and
     `SECURITY.md` both say. Left as a prohibition it would have made `cm-review` auto-CRITICAL correct,
     pre-existing behavior. Restated descriptively: this is the posture; never *widen* it.
  4. **The `auth-modes-annotation-typo` seed told the wrong story**, and its acceptance criteria were
     vacuous because of it. `typing.get_type_hints` does **not** raise on the malformed annotation, and
     PEP 563 is **not** what saves it: evaluated eagerly on 3.14 it still silently yields
     `Final[slice(...)]`. R2 as originally drafted ("assert `get_type_hints` succeeds") passes on the
     unfixed code. Rewrote the mechanism and re-drafted R1/R2 to assert the resolved annotation's
     *shape*, with a requirement to see the test red first. Same correction applied to the ROADMAP entry.
  5. **`cm-release` ran four commands its `allowed-tools` did not cover** — `git config` (on every
     load, feeding the signed-tag STOP), `git ls-files`, `git ls-remote`, `sandbox.sh`. Added all four;
     added only the *read* forms of `git config` to `settings.json`, not `git config *`, so the
     project-wide auto-allow cannot write config.
  6. **AGENTS.md claimed "no container build".** `Dockerfile` + `compose.yml` + `nginx-dev.conf` are
     tracked and documented in the README, and `compose.yml` pins the server's argv — so a CLI flag
     change is a change to those files. Corrected to "no *published* container image" and folded them
     into the same-commit surface.
  7. **`cm-harness` Step 6's sandbox check was incomplete** — three checks for what are now five
     guarantees, so an edit dropping auth-blanking or the new `CLUSTER_MCP_REPO` export would pass.
     Expanded to five, including a fail-closed check with `RCAC_SSH_HOST` deliberately set in the
     parent environment.
