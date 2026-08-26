# PLAN — {Title}

> **Status:** Draft for review · **Last updated:** {YYYY-MM-DD}
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (when `appetite: big`). Every design element traces to a GOAL R-ID.

## 1. Summary

<2–4 sentences: the approach in a nutshell and why it fits the appetite.>

## 2. Design

<The real technical design at architecture altitude: module boundaries, the executor/tool/middleware
surfaces touched, control and data flow through a tool call, CLI surface, environment variables,
error and exit-status handling, what `SERVER_INSTRUCTIONS` has to say afterwards. Reference concrete
files under `src/rcac_mcp/…`. Be specific enough to build from, not so specific it duplicates the
code.>

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | <module/function/behavior> |
| R2   | <…> |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) **before**
research and **again** after this design was drafted. List every load-bearing invariant this change
touches and confirm compliance.

State plainly whether the change touches the **execution path** — who a command runs as, on which
host, under whose identity (`invariants.md` §1, §4, §5). That answer sets the review posture for the
whole cycle.

- <invariant> — <how this design honors it>.

### Deviation justifications

Any place this design bends an invariant or adds complexity — with the simpler alternative and why
it was rejected. Empty is the goal.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

Scary unknowns that could have blown the appetite, and how research settled them (link the relevant
`research/NN-*.md`). This is where risk was bought down before committing to phases.

- <unknown> → <resolution> ([`research/NN-topic.md`](research/NN-topic.md)).

## 5. Risks & open questions

- <residual risk, mitigation, or a question that needs a human before/at build>.

## 6. Verification strategy

How we will *prove* the feature works — the CLI flows to drive and the tests to add/run (this seeds
each phase's `verify:` command in `TECH.md`).

Prefer driving the real CLI over unit tests alone, and **always** through the sandbox:
`.agents/factory/bin/sandbox.sh sh -c "…"`. It gives the run a throwaway `HOME`, blanks the auth
environment, and unsets `RCAC_SSH_HOST` so the default stdio→`ssh` path fails closed rather than
reaching a production cluster.

Name here anything that is **not** verifiable that way — a delegate-mode behavior needing root and
two real identities, a live-cluster tool — and say what stands in for it (a unit test over the argv
builder, an `asyncio` two-task isolation test) plus what the human must run by hand.

---

*Backing research (if present): [`research/00-digest.md`](research/00-digest.md).*
