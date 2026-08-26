# EARS — Easy Approach to Requirements Syntax

A lightweight controlled-natural-language convention for writing acceptance criteria that are
**testable and low-ambiguity**. Used by `cm-feature` to shape `GOAL.md` acceptance criteria (R-IDs).

**Nudge, don't hard-enforce.** EARS reduces ambiguity; it does not eliminate it, and forcing it onto
genuinely exploratory or ubiquitous requirements stilts them. Prefer EARS where it clarifies; fall
back to plain, unambiguous prose where EARS would be contrived. Every criterion still gets a stable
R-ID.

## Generic template

> **While** \<optional precondition/state>, **when** \<optional trigger>, the \<component> **shall**
> \<observable response>.

Keep the `<component>` a real part of this server (`rcac-mcp`, the `run_command` tool, the
`AuthExecutorMiddleware`, `SSHExecutor`, the `rcac://context` resource, `SERVER_INSTRUCTIONS`) and
the `<response>` **observable** — an exit status, a `CommandResult` field, an MCP tool listing, a
raised exception type, a printed message — so `cm-review` can check it by driving the CLI in a
sandbox.

## The six patterns

| Pattern | Keyword | Form |
|---|---|---|
| **Ubiquitous** | *(none)* | The \<component> shall \<response>. |
| **State-driven** | `While` | While \<state>, the \<component> shall \<response>. |
| **Event-driven** | `When` | When \<trigger>, the \<component> shall \<response>. |
| **Optional-feature** | `Where` | Where \<feature is included>, the \<component> shall \<response>. |
| **Unwanted-behavior** | `If … Then` | If \<unwanted condition>, then the \<component> shall \<response>. |
| **Complex** | combo | While \<state>, when \<trigger>, the \<component> shall \<response>. |

## cluster-mcp-flavored examples

- **R1 (event):** *When* a tool call arrives whose token carries no `sub`, `email`, or
  `preferred_username` claim, the `AuthExecutorMiddleware` *shall* raise and the call *shall* fail
  without executing a command.
- **R2 (unwanted):** *If* `rcac-mcp` is started with `-e ssh` and neither `--ssh-host` nor
  `RCAC_SSH_HOST` is set, *then* the CLI *shall* exit with `exit_status.bad_argument` and a message
  naming both.
- **R3 (state):** *While* running in `delegate` mode, concurrent tool calls from two distinct
  identities *shall* execute under their own mapped local users, with no executor shared between
  them.
- **R4 (optional-feature):** *Where* the `slurm` extra is installed, `rcac-mcp` *shall* register the
  Slurm tool group; where it is absent, the server *shall* start with those tools omitted and no
  import error.
- **R5 (ubiquitous):** The `Executor` protocol *shall* be satisfied by all three backends, verified
  by an `isinstance` check in a unit test.

## Anti-patterns

- Untestable adjectives ("fast", "robust", "user-friendly", "secure") — replace with an observable
  threshold or a concrete mechanism.
- Multiple requirements in one line — split so each has its own R-ID and pass/fail.
- Specifying the *how* (implementation) in a criterion — that belongs in `PLAN.md`.
- Encoding a **suspected cause/mechanism** in a *fix's* criterion (e.g. "the fix must not use the
  broken code path") — the root cause is unverified until `/cm-plan`; state the observable broken→fixed
  behavior instead.
- Writing a criterion only a live cluster can verify. If the only way to check R\<n> is to SSH into
  production, it is not a criterion this factory can grade — restate it against what
  `.agents/factory/bin/sandbox.sh` can reach, or mark it explicitly as human-verified in the GOAL.
