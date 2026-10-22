# REVIEW — Explain an importable leftover by git status, not file suffix

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent). The correctness
> pass grades the branch diff against [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants
> **only**. Every finding cites an **executed** command, not an assertion.

- **Reviewed commit:** e550c200fe0c18c646d0984813c3201558932d92 · **Base:** develop (`bfa0595`) · **Date:** 2026-10-22
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3

## Verification run

- The profile's `verify` in a clean clone: `758 passed` in each era, sandboxed `--help` exit 0; CI
  run 39867576240 green (758 per era, no skips).
- End-to-end planted trees (pycache, bare `.pyc`, `.DS_Store`, `*.pyc.<n>`, untracked and tracked
  `__init__.py`, mixed): the right class and remedy, `del` reaching only ignored and untracked files.
- A fuzz of 150 random trees against `git status`: 0 wrong, 0 fallbacks. 300 shapes against the
  base's suffix rule with git absent: 0 differ. No git off CI: 33 skipped, 725 passed; no git with
  `CI=true`: 33 errors as intended.
- All 17 base test ids still collected (73 at head); a real pre-commit hook classified correctly.

## Requirement → evidence matrix

| R-ID | Status | Note |
|------|--------|------|
| R1 | ❌ finding 2 | ignored rows right except files ignored by the developer's global excludes |
| R2 | ❌ finding 1 | untracked symlinked locations and nested repositories fall to the suffix rule |
| R3 | ❌ finding 1 | a tracked symlinked location gets a `del` remedy |
| R4 | ✅ | mixed trees: one line per class |
| R5 | ✅ | fallback text identical to the base's |
| R6 | ✅ | verdict unchanged with and without git |
| R7 | ✅ | ids kept; controls in `tmp_path` |
| R8 | ✅ | `need_git` fails on CI, skips elsewhere |
| R9 | ❌ finding 1 | § Testing's fallback list omits the cases finding 1 hits |

## Findings

### [MEDIUM/CONFIRMED] Leaves git classifies fall back to the suffix rule

- **Where:** `tests/test_registry.py:101` (a symlinked location is its own only leaf), `:148`
  (`base = path.resolve()` keys it by its target), `:169` (no key matches, so the suffix rule answers).
- **Failure scenario:** with git present inside a work tree (outside R5's fallback conditions), a
  tracked symlink `src/rcac_mcp/docs` to a directory holding `__pycache__/` (`git status`: `A
  src/rcac_mcp/docs`) is reported "only bytecode … Delete the leftover with `del …`", a `del` for a
  tracked file; an untracked symlinked location, an untracked nested repository (`?? sub/vendor/`)
  and an NFD-named untracked file on macOS all get the suffix rule's "re-added" text.
- **Evidence:** `uv run pytest -q 'tests/test_registry.py::test_docs_modules_absent'` with the
  planted symlink; the reviewer's probes `p3_symlink_loc.py` and `p4_empty.py`.
- **Touches:** R2, R3, R9.

### [MEDIUM/CONFIRMED] The developer's global git excludes are never read

- **Where:** `tests/test_registry.py:114-123`, with `bin/sandbox.sh` replacing `HOME` and
  `XDG_CONFIG_HOME`.
- **Failure scenario:** a `.DS_Store` ignored by `~/.config/git/ignore`, or a `*.orig` ignored by
  `core.excludesFile` in `~/.gitconfig`, shows `!!` in the developer's `git status` but is reported
  "untracked: git neither tracks nor ignores them". The remedy is still `del`, but the class is
  wrong. `.git/info/exclude` is honoured.
- **Foreman's ruling:** R1's "git ignores" means what the developer's own `git status` shows, global
  excludes included; the hook and environment scrub stays.

### Coverage observations (pinned in the remediation)

- Surviving mutants: the sum check dropped, `GIT_CONFIG_NOSYSTEM` dropped, the
  `GIT_CEILING_DIRECTORIES` keep dropped, a top-level symlink followed. `GIT_CLASSES`
  (`test_registry.py:93`) is defined and never used.

## Routing (each as its class)

1. **Every leaf git lists gets git's class.** Key leaves by their path as git reports it (no
   `resolve()` through a symlink; unicode normalised as git prints it), and classify a symlinked
   location as the link it is. What git cannot classify (a nested repository) falls back with the
   reason named, and § Testing lists every fallback case.
2. **The classes match the developer's `git status`**, global excludes and `core.excludesFile`
   included, while hooks and the `GIT_*` scrub stay.
3. **Every surviving mutant above dies,** and dead code goes.

## Human-gate triggers

- None (`tests/test_registry.py` and `tests/conftest.py` are not on the high-blast-radius list).
