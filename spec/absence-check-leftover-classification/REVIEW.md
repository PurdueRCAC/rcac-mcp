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

## Review cycle 2 — changes-requested (2026-10-22)

- **Reviewed commit:** b5b25dcea9b20e299b53ca4d850f4106b925f088 · **Mode:** a fresh blind pass. CI run
  39872447715 green (773 per era, git 2.55); `verify` and `CI=true` in both eras 773 each.
- **Cycle 1's routing held** for symlinked locations, NFD names, nested-repository detection and
  the four mutants; the global-excludes class did not: rebuilding git's configuration by hand
  misses what git itself resolves.

### [MEDIUM/CONFIRMED] Rebuilding git's excludes by hand diverges from the developer's `git status`

- **Where:** `tests/test_registry.py:120` (`ask_git` under the sandbox `HOME`), `:126-151`
  (`developer_excludes`); AGENTS.md:223 ("the classes are the developer's own `git status`").
- **Failure scenario:** git expands `~` against the sandbox's `HOME`, so `[include] path =
  ~/.gitconfig.local`, `[includeIf "gitdir:~/src/"]` and a repository's own `core.excludesFile =
  ~/…` all go unread: a globally ignored `.DS_Store` (`!!` in `git status --ignored`) is reported
  "untracked". LOW siblings with the same cause: an empty `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_COUNT`
  pairs, and the system config.
- **Evidence:** a scratch `HOME` with `[include] path=~/.gitconfig.local` → `git status --ignored`
  `!! src/rcac_mcp/docs/`; the test → `untracked ['docs/.DS_Store']`; `--no-includes` and dropping
  global excludes from `check-ignore` survive as mutants.

### [LOW/CONFIRMED] A repository nested inside a location still says "re-added"

- **Where:** `tests/test_registry.py:202`, `:217`.
- **Failure scenario:** `!! pkg/sub/vendor/` (or `??` holding only `x.pyc`): the suffix rule
  counts `vendor/.git/HEAD` as source and the message says "the module has been re-added",
  against R1 and R2.

### Coverage observation

- `need_git` made to always skip survives under `CI=true` (`37 passed, 51 skipped`): the unit test
  of the helper calls `need_git()` itself and so skips.

## Routing (each as its class) — cycle 2

1. **Ask the developer's own git, don't rebuild its configuration.** Run the read-only queries
   (`status --ignored`, `check-ignore`, `ls-files`) with the developer's real environment and
   configuration (HOME, XDG, includes, `GIT_CONFIG_*`, system config), removing only what
   redirects the repository (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, …) and disabling what
   could execute (`core.fsmonitor`, `core.hooksPath`), so every class equals what `git status
   --ignored` shows the developer. Tests use scratch HOMEs; the real one is never written.
2. **No fallback message says "re-added" for something git does not track,** a nested repository
   included.
3. **`need_git` never skips on CI, pinned** by a test that cannot skip itself.

## Human-gate triggers — cycle 2

- None.
