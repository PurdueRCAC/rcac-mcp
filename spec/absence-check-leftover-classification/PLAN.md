# PLAN — Explain an importable leftover by git status, not file suffix

> **Status:** Draft for review · **Last updated:** 2026-10-22
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). The working prototype is
> [`research/prototype.diff`](research/prototype.diff) (applies to `develop` `55fc5b7`); the phase
> gate is [`verify/`](verify/). Every design element traces to a GOAL R-ID.

## 1. Summary

`absence_violations` keeps its verdict-free role and its signature, and gains a classifier: for each
location a removed module imports from, it asks git (through `bin/sandbox.sh`, as every launch in the
suite does) which files there are tracked, untracked or ignored, and writes one line per class with
that class's own remedy. When git cannot answer, the classifier returns nothing and today's suffix
text is produced verbatim. A process is forbidden in a `unit` test, so `test_docs_modules_absent`
moves to `integration` (its ids, body and verdict unchanged), the git-backed controls are
`integration` and go through a new `conftest.need_git`, and the existing suffix controls stay `unit`,
where they now pin the fallback. One phase; the appetite is small, the prototype is green, and the
gate is offline.

## 2. Design

All code is in `tests/test_registry.py` and `tests/conftest.py`; `AGENTS.md` § Testing changes with
it. Nothing under `src/`, `bin/`, `.github/`, `.gitignore` or `pyproject.toml` moves.

**The verdict stays where it is.** `module_absent` and the body of `test_docs_modules_absent`
(`assert module_absent(name), '\n'.join(absence_violations(name))`, then the `import_module` check)
are untouched. `absence_violations(name) -> list[str]` still returns one string per location, empty
when the module is absent. Only how each string is written changes.

**Classifier: `git_classes(path) -> dict[str, list[Path]] | None`.**

1. *Can git be asked here?* None when the unit guard is active (`subprocess.Popen` carries the
   guard's `unit_guard` flag: a `unit` test may start no process, and a refused launch would fail
   the test at teardown), when `bin/sandbox.sh` is missing (an sdist without `bin/`), or when
   `shutil.which(GIT)` finds nothing. `GIT = 'git'` is a module global, the seam the fallback
   controls use to take git away.
2. *Leaf entries.* Every file or symlink at or under the location, and every directory there that
   holds nothing (the location itself included), since an empty `__pycache__/` or an empty
   directory is what makes a namespace package import. A symlink is a leaf and never followed: git
   records the link. A bare-module location (`sub.pyc`, `sub.py`) is its one file. The walk is
   inside the `OSError` guard: an unreadable directory falls back, it never raises.
3. *Git's positive lists*, each call `[str(SANDBOX), program, '-C', where, …]` through `ask_git`,
   which catches `OSError`, `SubprocessError` and `ValueError` (a decode error) and returns None,
   under a 60 s timeout:
   - `rev-parse --show-toplevel` from the location (or its parent, for a file). A non-zero exit
     (not a work tree), empty output, or a top the location does not resolve under: None.
   - from the top, `ls-files -z` with `--cached` (tracked; staged counts), `--others
     --exclude-standard` (untracked) and `--others --ignored --exclude-standard` (ignored), each on
     the pathspec `:(literal)<location>`. Any non-zero exit: None. (`:(literal)` is spelled per
     pathspec because `GIT_LITERAL_PATHSPECS` makes `check-ignore` die.)
   - git lists files only, so the empty-directory leaves go to `check-ignore -z --stdin`: exit 0
     (some ignored) and 1 (none ignored) are both answers, anything else is None. It reports an
     empty ignored directory itself (probed on git 2.x, 2026-10-19).
   - **Completeness.** Every leaf must be in exactly one class, else None. A leaf in no list is a
     case git does not answer: a repository nested inside (listed as one directory), a name stored
     differently on disk and in the index (decomposed Unicode on macOS, a case-only rename), or a
     call that failed silently. "Not listed" is never read as "untracked", so a tracked file is
     never offered a `del` by default.
4. *The environment git runs in* (`git_env`, also used by the controls' setup): `os.environ`
   minus every `GIT_*` variable but `GIT_CEILING_DIRECTORIES`, plus `GIT_CONFIG_NOSYSTEM=1`. A git
   hook exports `GIT_DIR`, `GIT_INDEX_FILE` and the like; left in, they would make git answer for
   another repository, and make a control's `git add` write the developer's index. The sandbox's
   HOME drops the global config, so the repository's own ignore rules (`.gitignore`,
   `.git/info/exclude`) decide what is ignored, not a personal `core.excludesFile`.

**Message: `git_explanation(name, path, classes)`**, one string per location, a header line then
one line per class present, in the fixed order tracked, untracked, ignored:

- `- tracked [files]: the module has been re-added, and it must stay removed. Take these files out
  of the change that re-adds them; they are not leftovers.` No `del` (R3).
- `- untracked [files]: git neither tracks nor ignores them. Move out anything you mean to keep,
  delete the rest with \`del …\`, and re-run.` (R2; the remedy wording the GOAL leaves to the
  plan. It never says "re-added", nor "no commit holds them", which a `git rm --cached` file
  would belie.)
- `- ignored [files]: leftovers of an older checkout, … Delete them with \`del …\` and re-run.` (R1.)
- When nothing is tracked and both other classes are present, a closing line gives `del <location>`:
  deleting each class's files alone would leave the directory, which still imports.

Files are shown relative to the location's parent (as today), a directory with a trailing `/`.
A `del` names the **covering** paths of its class (`covering`): a directory stands for everything
under it only when every leaf there is in that class, so a removal never reaches a file of another
class, and never a tracked one (R3, R4). A class that fills the location gets `del <location>`,
which is today's remedy for a stale checkout. Paths are `shlex.quote`d.

**Fallback: `suffix_explanation(name, path)`** is today's two messages, moved out of
`absence_violations` byte for byte (R5). `absence_violations` picks it whenever `git_classes`
returns None.

**`conftest.need_git()`** returns when `shutil.which('git')` finds git, fails the test when
`CI=true`, and skips it otherwise, naming git in both. It is the `need_shell` shape from the
in-flight `safe-execution` cycle, whose hygiene test admits it at that cycle's rebase (GOAL
Clarifications). The `scratch_repo` fixture calls it, then `git init`s `tmp_path` (through the
sandbox, with `git_env`) and writes a `.gitignore` of `__pycache__/`, `*.py[cod]` and `*.swp`.

**Markers.** `test_docs_modules_absent` moves from `unit` to `integration`: with the guard active
the classifier never runs, and the message would always be the fallback. The git-backed controls
and the fallback controls that drive a fake or missing git are `integration`; the existing
`test_absence_message_for_leftover_bytecode` and `…_for_readded_source` stay `unit`, unchanged, and
now pin the fallback under the guard (and, through the guard's teardown, that no launch was tried).

**Controls** (new, in `tests/test_registry.py`; ids in [`verify/ids.txt`](verify/ids.txt)):

| Test | Rows | Pins |
|---|---|---|
| `test_absence_message_for_ignored_files` | `empty-pycache`, `pycache-pyc`, `nested-pyc`, `bare-pyc`, `ignored-other` | only an `ignored` line, its files, `del <location>`, no "re-added" (R1) |
| `test_absence_message_for_untracked_files` | `ds-store`, `pyc-temp`, `uncommitted-source`, `empty-directory`, `uncommitted-module-file` | only an `untracked` line, its files, `del <location>`, no "re-added" (R2) |
| `test_absence_message_for_tracked_files` | `init`, `module`, `data`, `nested-module`, `module-file` | only a `tracked` line, its files, "re-added", no `` `del `` anywhere (R3) |
| `test_absence_message_for_mixed_files` | `all-three`, `tracked-and-ignored`, `untracked-and-ignored`, `nested` | one line per class, each `del` exactly its covering paths, none reaching a tracked file, "re-added" iff tracked, the whole-location line iff nothing tracked (R3, R4) |
| `test_absence_message_falls_back_without_git` | `{mixed,bytecode,empty-pycache,bare-pyc,module-file}-{no-git,git-fails,git-answers-elsewhere,outside-work-tree}` | the message equals a literal copy of today's text, word for word, for directory and bare-file locations; nothing raises (R5, R8) |
| `test_absence_message_falls_back_when_git_fails_midway` | each shape `-ls-files`, `empty-pycache-check-ignore`, `mixed-omits-untracked` | git finds the work tree, then a later call fails or leaves a file out: the literal fallback, never a partial answer (R3, R5) |
| `test_absence_message_ignores_git_hook_environment` | `git-dir`, `work-tree`, `index-file`, `all` | each hook variable alone, and all together, with a *tracked* file: still only `tracked` (R3, R7: the scratch repository, never another) |
| `test_absence_message_for_odd_entries` | `tracked-symlink`, `nested-repository`, `unreadable-directory` | a tracked symlink to a directory is tracked; a nested repository and an unreadable directory fall back without raising (R3, R5) |
| `test_a_missing_git_fails_on_ci_and_skips_elsewhere` (`unit`) | `ci`, `local`, `ci-false` | `need_git` fails on CI, skips elsewhere, returns when git is on PATH (R8) |

`outside-work-tree` sets `GIT_CEILING_DIRECTORIES` to `tmp_path`, so the answer cannot depend on
where the host's temporary directory sits. The fallback rows never call `need_git`: they pass with
or without git (R8).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `git_classes` (`check-ignore`), the `ignored` line, `covering`; `test_absence_message_for_ignored_files` |
| R2 | untracked = neither listed nor ignored; the `untracked` line and remedy; `test_absence_message_for_untracked_files` |
| R3 | `ls-files --cached`; completeness (an unlisted file is never untracked); symlinks as leaves; `git_env`; the `tracked` line with no `del`; `covering` never reaches a tracked leaf; `…_for_tracked_files`, `…_mixed_files`, `…_ignores_git_hook_environment`, `…_for_odd_entries` |
| R4 | one line per class present, each with its own files and `del`; the whole-location line; `test_absence_message_for_mixed_files` |
| R5 | every None path in `git_classes`/`ask_git`, completeness, the `OSError` guard; `suffix_explanation` verbatim (gate `static` compares its two messages with the base's as parsed code); `…_falls_back_without_git`, `…_when_git_fails_midway`, `…_for_odd_entries`, the unchanged `unit` suffix controls |
| R6 | `module_absent` and the test body untouched (gate `static` compares them as parsed code against `55fc5b7`); `absence_violations` is called only for the message and never raises |
| R7 | names and base ids kept (gate `static`, `verify/ids.txt`); a control per class, mixed and fallback; `scratch_repo` under `tmp_path`, `git_env`, the hook-environment control |
| R8 | `need_git` (fail on CI, skip elsewhere); fallback rows never skip; gate `nogit` runs the slice with no git on PATH, off CI and on |
| R9 | the `tests/test_registry.py` entry in `AGENTS.md` § Testing (gate `static` reads it) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before design and again against it. No numbered § Invariants section (§1–§9) is touched:
the change is confined to `tests/` and `AGENTS.md`, and no path on the high-blast-radius list is
edited (`bin/sandbox.sh` is called, not changed). The sections it does touch:

- § Conventions, markers — every new test carries `unit` or `integration`; no gate is a bare marker
  selection: the gate names every id (`verify/ids.txt`).
- § Conventions, sandboxed drives — every git launch is a list literal starting with
  `str(SANDBOX)`, so `test_cli_drives_are_sandboxed` passes unchanged, and the gate runs it.
- § Conventions, comments — declarative, no spec ids; the prototype follows it.
- § Conventions, point to a rule — the stale-checkout bullet later in § Testing is still true (an
  ignored-only `src/rcac_mcp/docs` gets `del src/rcac_mcp/docs`) and is left alone.
- § Testing — check-function names and parametrised ids are a contract (kept); the unit guard's
  promise (no process in a `unit` test) is honoured by reading its flag rather than tripping it.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `test_docs_modules_absent` moves `unit` → `integration`, so `pytest -m unit` no longer selects it | git is a process, and the unit guard refuses every process in a `unit` test | Keeping `unit`: the classifier could never run there, so the message developers see would always be the fallback and the Outcome would go unmet. Reading git's index and ignore rules in-process: a new parser, or a dependency (a non-goal). |
| The classifier reads the unit guard's `unit_guard` flag | a refused launch is recorded and fails the test at teardown, even when caught | Re-marking the `unit` suffix controls `integration`: more churn, and the guard case would go unpinned. |

## 4. Rabbit holes (resolved)

No fan-out: appetite small, root cause known (trigger 1 off), no high-blast-radius path edited
(trigger 2 off). A prototype on a detached worktree of `55fc5b7` settled the unknowns; it is
[`research/prototype.diff`](research/prototype.diff).

- *Does `check-ignore` see an empty `__pycache__/` and files inside an ignored directory?* Yes, both
  (probed 2026-10-19).
- *Literal pathspecs.* `GIT_LITERAL_PATHSPECS=1` makes `check-ignore` die ("pathspec magic not
  supported"), which silently sent every location to the fallback in the first prototype run. The
  `ls-files` pathspec carries `:(literal)` instead.
- *The unit guard.* A git call from a `unit` test is refused and fails it at teardown; this is why
  the real test moves to `integration` and the classifier reads the guard's flag.
- *The sandbox rule.* `test_cli_drives_are_sandboxed` flags any launch outside a `unit` function not
  shaped `[str(SANDBOX), …]`; every git call is shaped so, and the prototype passes it.
- *A hook's environment.* An exported `GIT_DIR`/`GIT_INDEX_FILE` would redirect both the classifier
  and the controls' `git add` into another repository; `git_env` drops them (control and mutant).
- *A stale directory after partial removal.* Mixed untracked and ignored files with nothing
  tracked: removing each class's files leaves an empty directory that still imports, so the message
  adds `del <location>`.
- *A skip escaping `pytest.raises`.* A `need_git` that skipped on CI would have made its own
  self-test skip, not fail; caught by the gate's `need-git-skips-on-ci` mutant on the prototype.
  The self-test catches `BaseException` and checks the type.
- *A tracked file reported untracked.* The first draft read "not tracked, not ignored" as
  untracked, so any leaf git did not list (a symlinked directory dropped by the walk, a nested
  repository, a name normalised differently) came out untracked with a `del`. Positive lists and
  the completeness rule replace it (review findings 3, 6, 7).
- **Adversarial design review** (trigger 3, which applies: the deliverable is a test's diagnostic
  text, not a gate's verdict, but it ships a skip helper and a fallback that can each fail green).
  A fresh reviewer had the GOAL, this design, the gate and the prototype, and ran mutants against a
  copy. Ten findings, all folded:
  1. HIGH: `GIT_DIR` alone leaking was not caught, and with it a tracked file came out untracked
     with a `del`. → The hook control runs each variable alone and together, with a tracked file;
     mutant `git-dir-kept`.
  2. HIGH: a failed `ls-files` or a fatal `check-ignore` after a working `rev-parse` was taken as an
     answer. → `…_when_git_fails_midway`, with a fake git that runs the real one except for the
     failing call; mutant `check-ignore-fatal-accepted`. A failed `ls-files` is now also caught by
     completeness, so its own mutant is equivalent and left out (noted in `check.py`).
  3. HIGH: a tracked symlink to a directory produced a header with no class at all. → Symlinks
     are leaves; completeness returns None for an empty classification; mutant `symlink-followed`.
  4. MEDIUM: the bare-file fallback text was not pinned, and `static` did not compare the moved
     messages with the base. → `bare-pyc` and `module-file` literal rows; `static` compares
     `suffix_explanation`'s two returns and its `held`/`sources` lines with the base's.
  5. MEDIUM: an unreadable empty directory raised `PermissionError`. → The walk is inside the
     `OSError` guard; control `unreadable-directory`.
  6. LOW: a name stored differently on disk and in the index came out untracked with a `del`. →
     Completeness; mutant `unlisted-file-accepted` (killed by `mixed-omits-untracked` and
     `nested-repository`).
  7. LOW: a nested repository was offered a `del`. → Completeness; control `nested-repository`.
  8. LOW: "no commit holds them" can be false. → Dropped from the untracked line.
  9. LOW: `pyc-temp` is not where Python writes the temp file. → A comment on the row says so.
  10. LOW: the § Testing text checks were weak (one exact line break; `tracked` matched inside
      `untracked`). → Whitespace folded, whole-word matches.

  The reviewer judged the `unit` → `integration` move sound (CI runs the suite unfiltered in both
  eras), and asked that the lost `-m unit` selection and the marker's CLI-only description be
  noted. The § Testing entry now says both.

## 5. Risks & open questions

- **GOAL clarification for the sign-off (R6, R7).** `test_docs_modules_absent` keeps its ids, body
  and verdict, but becomes `integration`. In every unfiltered run (the profile's `verify`, CI, the
  release gate) it fails and passes exactly as today; a `pytest -m unit` run no longer selects it.
  The plan reads R6 as the unfiltered suite. If the foreman reads it as every marker selection, the
  alternative is the Outcome unmet in the real test (see § 3).
- **`safe-execution` rebase.** Both cycles add a helper to `tests/conftest.py`; that cycle's
  `test_only_need_shell_skips` must admit `need_git` (GOAL Clarifications). Whichever lands second
  resolves the conflict.
- **Classification follows the repository's rules only.** A `.DS_Store` that a developer's global
  excludes file ignores is reported untracked: both classes get a `del`, so only the label differs.
- **An in-tree non-editable install** (a `site-packages` under the ignored `.venv/`) is reported as
  ignored leftovers with a `del`; reinstalling is the better remedy. The default `uv sync` installs
  editable, so this is unlikely; not handled.
- **The `integration` marker's description** in `pyproject.toml` ("drives the installed CLI")
  does not fit `test_docs_modules_absent` or the git controls. Rewording it is outside this GOAL's
  paths, so the § Testing entry notes the mismatch instead. Rewording it later is deferred work:
  P1's ledger item records it as `issues/integration-marker-wording.md` plus a `ROADMAP.md` entry.
- **A host whose `git` is a stub** (macOS without the Command Line Tools) passes `need_git` and then
  fails the controls' `git init`. Acceptable: the developer's git is broken, and CI has git.

## 6. Verification strategy

One phase, gated by `sh spec/absence-check-leftover-classification/verify/gate.sh P1`, offline and
one child at a time, about 110 s on mac-m2. Its parts run alone as modes:

- `static` (`verify/check.py static --base 55fc5b7`): `module_absent` and the body and parametrize
  of `test_docs_modules_absent` equal the base as parsed code, and the test carries `integration`
  and not `unit`; `absence_violations(name: str) -> list[str]`; every base test id is still listed;
  nothing outside `tests/test_registry.py`, `tests/conftest.py`, `AGENTS.md` and the cycle's records
  differs from the base; no skip, `skipif`, `xfail` or `importorskip` in the module, nor in conftest
  outside `need_git`/`need_shell`; the § Testing entry names the classes, the fallback, `need_git`,
  the sandbox and `integration`, and no longer states the suffix rule as the explanation.
- `tests`: `uv lock --check`, then the module and `test_cli_drives_are_sandboxed` with `CI=true` in
  both protocol eras; every id in `verify/ids.txt` passes (a skip is red), and no collected id is
  unlisted.
- `nogit`: PATH is a farm of `uv sh dirname mktemp mkdir rm` with no git. Off CI the 33 git ids skip,
  naming git, and the 40 others pass; with `CI=true` the git ids fail or error, naming git, and the
  others still pass.
- `mutants` (`verify/check.py mutants`): twelve one-line breaks in a snapshot, each killed by named
  ids: always the fallback, `check-ignore`'s exit 1 not read as an answer, a removal naming the
  whole location, the untracked list dropped, the guard flag not read, the hook environment kept,
  `GIT_DIR` alone kept, a missing git not caught, a fatal `check-ignore` accepted, symlinks
  followed, an unlisted file accepted, `need_git` skipping on CI.

Dry runs: green on the prototype (all four parts, after the review's folds, 2026-10-19; the full
suite passes there in both eras, 758 tests); red on the unbuilt cycle tree in
every part (`static` on the marker, `tests` and `nogit` on missing ids, `mutants` on a missing
anchor). The profile's full `verify` runs once before the phase commit.

---

*Backing research: [`research/prototype.diff`](research/prototype.diff).*
