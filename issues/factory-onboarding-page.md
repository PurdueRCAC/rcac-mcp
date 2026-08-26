---
status: unshaped
kind: docs
appetite: small
lane: public
---

# The factory has no front door

## Problem

This repository now carries eight `cm-*` skills, five factory reference documents, six templates and
four scripts, and no on-ramp. A newcomer — a colleague, a contributor, an RSE at another center
evaluating the approach — has `AGENTS.md` (a dense operating manual for agents, not people) and
`.agents/factory/methodology.md` (the *why*, written for an agent that already knows what a harness
is). Neither introduces the idea.

The upstream HyperShell harness ships `factory/getting-started.html`: a 60KB self-contained page
covering agents-not-chatbots, what a harness is, skills as runnable procedures, Shape Up adapted to
machine tempo, the artifact spine, TECH.md as an FSM, the self-improvement loop, and a section
addressed to institutional leadership on governance, cost, reproducibility and workforce. It was
**deliberately not copied** during the port (`.agents/factory/harness-log.md`, bootstrap entry,
divergence 4): duplicating near-identical prose across two repositories invites exactly the silent
drift the upstream ledger's F13 finding was filed about.

That decision left one loose end in the machinery. `/cm-harness` Step 6 carries a staleness check on
the onboarding page that is **conditional on a file that does not exist** — correct today, but a
conditional that can never fire is a check nobody maintains.

## Why it was deferred

Deliberately, at port time, with the reasoning recorded in the harness ledger. Copying 60KB of
general-audience prose is cheap to do and expensive to keep true, and at the moment of the port there
was no cluster-mcp-specific experience to write it from.

**Pre-existing:** n/a — created by the port's own scoping decision.

## Outcome / vision

An on-ramp that does not duplicate what already exists elsewhere. Three shapes, in ascending cost, and
the promotion should pick one rather than defaulting to the largest:

1. **A short local README** in `.agents/` — what the eight skills are, the order to run them in, one
   worked example — linking out to the upstream page for the method. Cheapest; probably enough.
2. **A ported page** adapted with cluster-mcp examples. Faithful, and the thing that drifts.
3. **Extract the shared method** into something both repositories reference. Correct in principle,
   real coordination cost, and premature with two consumers.

The honest input this project does not have yet is *experience*: nobody has run a full cycle here.
Waiting until a few have landed means the worked example is real rather than invented — which is why
this sits last in Part III rather than first.

## Sketch of the acceptance criteria

- **R1** — A reader who has never seen this harness SHALL be able to identify, from one document, what
  the eight skills are and in what order a cycle runs them.
- **R2** — The document SHALL include at least one worked example drawn from a cycle that actually ran
  in this repository — not an invented one.
- **R3** — WHERE the method is covered upstream, the document SHALL link rather than restate, so the
  two cannot silently disagree.
- **R4** — `/cm-harness` Step 6's staleness check SHALL be made unconditional if a page lands here,
  and the harness ledger's divergence-4 note SHALL be updated to record the reversal.

## Notes

- Whatever lands must be excluded from the sdist along with the rest of `.agents/` — see
  [`ci-workflow.md`](ci-workflow.md) R2.
- Related: `.agents/factory/harness-log.md` bootstrap entry (the divergence this seed closes),
  `.agents/factory/methodology.md` (which currently points at this seed by name).
- Found by: the harness port, 2026-08-26 (out of cycle).
