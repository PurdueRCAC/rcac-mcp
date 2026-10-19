# GOAL — Explain an importable leftover by git status, not file suffix

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** absence-check-leftover-classification
- **kind:** test
- **appetite:** small

## Problem

`tests/test_registry.py` keeps the removed documentation modules (`rcac_mcp.docs`,
`rcac_mcp.tools.docs`) unimportable: `test_docs_modules_absent` fails when one still imports, and
its failure message is `absence_violations(name)`. That function says where the module resolves and
explains what it finds there by **file suffix** (`non_bytecode_files`, `tests/test_registry.py:76`,
keeps every file whose suffix is not `.pyc`; `absence_violations`, `:82`). Nothing but bytecode is
reported as a stale checkout, with the remedy `del <path>` (`:105–109`); anything else is reported
as a re-added module that must be taken out of the change that re-adds it, with no `del` remedy
(`:99–102`).

So an untracked leftover that is neither bytecode nor source, such as a `.DS_Store` or an interrupted
`*.pyc.<n>` temp file next to the bytecode, is reported as a re-added module. A developer whose
checkout is merely stale is told they re-added a module, and is given no remedy that applies to
them. The verdict (the test fails) is right; only the explanation and the remedy are wrong. This is
**pre-existing**: on `develop` since `strip-docs-subsystem` landed, found by that cycle's review
(cycle 3, 2026-10-16).

## Outcome / vision

When a removed documentation module still imports, the failure message tells the developer what git
knows about each file behind it: whether it is tracked, untracked or ignored. Each class gets its
own explanation and the remedy that fits it. A stale checkout is never blamed as a re-added module,
and a re-added module is never offered a `del`. Where git cannot answer, the message is exactly
today's. Whether the test fails never changes.

## Acceptance criteria (the contract)

- **R1** — WHEN a removed module imports from files that git ignores (bytecode, `__pycache__/`, or
  any other ignored file), the failure message SHALL list them as ignored leftovers of an older
  checkout and SHALL give the `del <path>` remedy, and SHALL NOT say the module was re-added.
- **R2** — WHEN a removed module imports from files that git does not track and does not ignore (a
  `.DS_Store`, a `*.pyc.<n>` temp file, an uncommitted source file), the failure message SHALL list
  them as untracked and SHALL give a remedy line for untracked files, and SHALL NOT say the module was
  re-added.
- **R3** — WHEN a removed module imports from a file git tracks, the failure message SHALL say the
  module was re-added, SHALL list the tracked files, and SHALL give the remedy of taking them out of
  the change that re-adds them, never a `del` remedy for those files.
- **R4** — WHEN the files behind one import location fall into more than one class, the failure
  message SHALL name each class present, with its own files and its own remedy line.
- **R5** — IF git is not available, or the location is not inside a git work tree (an sdist or
  installed-package test run), THEN the failure message SHALL be today's suffix-based explanation,
  and the classification SHALL NOT raise.
- **R6** — `test_docs_modules_absent` SHALL fail in exactly the cases it fails today, and pass in
  exactly the cases it passes today, with or without git: the change alters the explanation only.
- **R7** — The existing check-function names (`module_absent`, `absence_violations`) and
  parametrised test ids SHALL keep working (`AGENTS.md` § Testing). Each class in R1–R3, the mixed
  case in R4 and the fallback in R5 SHALL have a negative control, and every git-backed control SHALL
  classify against a scratch git repository it builds under `tmp_path`, never the developer's
  checkout.
- **R8** — WHEN the suite runs where git is not on `PATH`, the git-backed controls SHALL NOT fail or
  error for that reason (skipping with a stated reason is acceptable), and the R5 fallback control
  SHALL still run.
- **R9** — `AGENTS.md` § Testing SHALL describe the absence check's explanation by git status, with
  the fallback, in place of the suffix rule it states today.

## Non-goals (no-gos)

- Changing the verdict: which modules are checked, `module_absent`, or what makes
  `test_docs_modules_absent` fail.
- Deleting, cleaning or ignoring leftovers automatically; the test explains, the developer acts.
- Classifying anything finer than tracked / untracked / ignored (e.g. staged versus committed,
  submodules, or files outside the work tree beyond R5's fallback).
- A new runtime or test dependency.
- Any other check, gate, `.gitignore` rule or CI change.

## Clarifications

- **Q:** May the change alter whether the test fails? — **A:** No. The verdict never changes; only
  the explanation and remedy do, classified by git status (tracked versus untracked versus ignored),
  with a remedy line for each class. (Foreman ruling, resolved 2026-10-19.) → R1–R4, R6.
- **Q:** What happens without git, or outside a git checkout, as in an sdist test run? — **A:** The
  test behaves the same as today: it falls back to today's explanation, and never crashes, nor passes
  where it failed. (Foreman ruling, resolved 2026-10-19.) → R5, R6. Read during shaping as also
  binding the new controls, so the suite stays green without git (R8); confirm at the gate.
- **Q:** How are the R-IDs phrased? — **A:** As observable behaviour, what a developer sees in the
  failure message, not the mechanism. (Foreman ruling, resolved 2026-10-19.)
- **Q:** Is an untracked file a stale-checkout leftover (the seed's draft R1) or its own class? —
  **A:** Its own class with its own remedy line (ruling 1); the seed's draft grouped untracked with
  ignored. The plan picks the untracked remedy's wording within R2. (Resolved 2026-10-19.)
- **Q:** How do the git-backed controls behave without git (R8)? — **A:** Through one helper that
  fails the test on CI (`CI=true`) and skips it elsewhere, never a bare `skip`: the
  `safe-execution` cycle (in flight) adds a hygiene test allowing exactly one such helper,
  `need_shell`, and it will admit this one, `need_git`, at its rebase. The fallback control
  (today's message, no git) never skips. (Foreman, GOAL gate, 2026-10-19.)

## Related materials

- Seed: [`issues/absence-check-leftover-classification.md`](../../issues/absence-check-leftover-classification.md)
- `ROADMAP.md` → Part III, "The absence check blames a stale checkout on a re-added module"
- `tests/test_registry.py`: `non_bytecode_files` (`:76`), `absence_violations` (`:82`),
  `test_docs_modules_absent` (`:150`), `test_absence_message_for_leftover_bytecode` (`:207`),
  `test_absence_message_for_readded_source` (`:228`); anchors confirmed on `develop` `55fc5b7`
- `AGENTS.md` § Testing (`:205`), the `tests/test_registry.py` bullet
- Prior cycle: `spec/strip-docs-subsystem/` (the review that found it)
