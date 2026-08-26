---
status: unshaped
kind: test
appetite: big
lane: public
---

# Rebuild test coverage over the coupled core

## Problem

Every test in this repository is a documentation-index test:

| File | Tests | Subject |
|---|---|---|
| `tests/test_database.py` | 22 | FTS5 schema, upsert, BM25 search |
| `tests/test_indexer.py` | 35 | frontmatter, snippets, Jinja2, chunking |
| `tests/test_tools.py` | 19 | `doc_search` / `doc_load` |
| `tests/test_cli.py` | 5 | `--index-docs` flows |

81 of 81. `tests/conftest.py` provides only docs fixtures. So
[`strip-docs-subsystem.md`](strip-docs-subsystem.md) removes the whole suite, and what it leaves
behind has **never had a test at all** — including every path
`.agents/factory/invariants.md` marks highest-blast-radius:

- the `ContextVar` isolation contract (`context.py`, `middleware.py`) — the thing that keeps one
  user's tool call from running as another;
- identity-claim precedence `sub` → `email` → `preferred_username` and the hard failure when none is
  present (`AuthExecutorMiddleware._extract_identity`);
- user-map enforcement — `PermissionError` for an unmapped identity, never a default user;
- the `sudo -n -H -u <user> <shell> -c` argv, and `shlex.quote` on `cwd` only;
- `_resolve_exec_mode()`, the five-rung security posture ladder;
- the `JWT_SECRET` ≥ 32 check in its two separate locations;
- the `Executor` protocol being satisfied by all three backends.

A `verify:` gate is the factory's central quality mechanism, and over an empty or docs-only suite it
is theatre. Every cycle after the docs strip inherits that.

## Why it was deferred

Split out of `strip-docs-subsystem` deliberately: that cycle's job is subtraction, and bundling the
construction of a new test suite into it would blur what the review is grading. Filed before the
strip so the obligation exists in writing before the deletion makes it urgent.

**Pre-existing:** yes — the coverage gap is on `main` today; the strip only makes it total.

## Outcome / vision

A suite whose failures mean something: the isolation contract has a test that would actually fail if
someone replaced the `ContextVar` with a module global, and the auth path has tests that do not need
root, a sudoers rule, or two real identities to run.

The hard part is the second clause. `DelegatingExecutor` is testable without `sudo` by asserting the
**argv it builds** rather than running it; middleware is testable by driving `on_call_tool` with a
stub token; isolation is testable with two concurrent `asyncio` tasks and a stub executor each.
`.agents/factory/review-rubric.md` § *Refutation protocol* already anticipates exactly these
stand-ins — this cycle is where they become real tests instead of a reviewer's improvisation.

## Sketch of the acceptance criteria

- **R1** — A test SHALL run two concurrent `asyncio` tasks that each `set_executor()` a distinct
  instance, and assert each task's `get_executor()` returns its own — failing if `executor_var` is
  replaced by a module global.
- **R2** — Tests SHALL cover `_extract_identity` for each claim in precedence order, for the
  first-non-empty rule, and for the no-claim case raising rather than defaulting.
- **R3** — A test SHALL assert an identity absent from the user map produces `PermissionError` and no
  command execution.
- **R4** — A test SHALL assert the exact argv `DelegatingExecutor.run` constructs, including `-n`,
  `-H`, `-u <local_user>`, and `shlex.quote` applied to `cwd` and not to `command` — without invoking
  `sudo`.
- **R5** — Tests SHALL cover all five rungs of `_resolve_exec_mode()`.
- **R6** — A test SHALL assert `isinstance(x, Executor)` for all three backends.
- **R7** — Every test SHALL carry `@mark.unit` or `@mark.integration`; the suite SHALL pass under
  `--strict-markers`; integration tests SHALL run under `.agents/factory/bin/sandbox.sh`.

## Notes

- `pytest-asyncio` is already a dev dependency; no config exists, so it runs in strict mode. R1 needs
  an async test — confirm the mode before designing around it.
- `tests/conftest.py` is entirely docs fixtures and will be empty after the strip; this cycle owns
  the replacement.
- Related: [`ci-workflow.md`](ci-workflow.md) depends on this — CI over a docs-only suite tests the
  wrong thing.
- Found by: the harness port, 2026-08-26 (out of cycle).
