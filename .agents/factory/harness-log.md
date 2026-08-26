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
