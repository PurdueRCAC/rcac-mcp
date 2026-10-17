# PLAN — Run the gate on GitHub on every push

> **Status:** Reviewed (adversarial design review folded in) · **Last updated:** 2026-10-17
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing facts are in
> [`research/01-facts.md`](research/01-facts.md) (lean path: one brief, no fan-out), with working
> prototypes in [`research/prototypes/`](research/prototypes/). Every design element traces to a
> GOAL R-ID.

## 1. Summary

Two workflow files and one shared script. `.github/workflows/ci.yml` has one job, `gate`, that runs
the profile's `setup` and `verify` and the release recipe's build checks, one step per command,
verbatim, on Python 3.14, with a read-only token, no secrets, no caches and two SHA-pinned actions.
`.github/workflows/mutants.yml` runs the mutation runner on `workflow_dispatch` only. The sdist path
set moves out of the recipe's inline `python3 -c` into `bin/sdist_hygiene.py`, which the recipe and
CI both run, so they cannot disagree. `AGENTS.md` names the workflow and the landing rule, drops
every "no CI" claim, and binds the three step lists in § Conventions. Everything local lands and is
proved in three phases with no push. Two last phases make the pushes that the GitHub evidence needs,
each push a separate, approved act. Small appetite: no source change, five phases.

## 2. Design

### 2.1 `bin/sdist_hygiene.py`: the one hygiene definition (R3)

A stdlib script, run as `uv run python bin/sdist_hygiene.py dist/` (prototype:
[`research/prototypes/sdist_hygiene.py`](research/prototypes/sdist_hygiene.py), to be copied
unchanged unless review says otherwise).

- `FURNITURE = ('.security', 'spec', 'issues', 'tests/fixtures', 'bin', '.agents')`, the recipe's
  set today, unchanged.
- `member_violations(root, names)`: every member must sit under one root directory, named after
  the tarball (`rcac_mcp-0.1.0/`). A member outside it, or one with `..`, is reported. Inside it,
  a member whose leading path components equal a `FURNITURE` entry is reported. Paths are compared
  by component (`PurePosixPath.parts`), so `./` prefixes are read and `binary.txt`, `specs/`, and
  `tests/fixturesX` are not matched.
- `sdist_violations(dist)`: every `*.tar.gz` in `dist`. Reported: no sdist at all, a tarball that
  cannot be read, and each member problem, as `<tarball>: <path>`.
- `main`: prints each problem on stderr and exits 1. A clean run prints `sdist hygiene: <names>
  clean` on stdout and exits 0.

The recipe's `gate` replaces its inline step with that one command, and the workflow's last step is
the same string. Both run the same file, so they reject the same set by construction (R3's "one
shared definition"). The checker in §2.4 fails if either side stops running it, or if a second
`tarfile` copy appears in `AGENTS.md` or a workflow. The `security-in-sdist` caveat's "lists what
the sdist does contain" becomes "names each offending path", which is what the check does.

**`tests/test_sdist_hygiene.py`** (`unit`) loads the script by path (`importlib.util`), builds
tarballs in `tmp_path` with `tarfile`, and calls the pure functions and `main` in-process. It needs
no subprocess, so the unit guard holds. The test ids, listed in `verify/ids.txt`, are fixed here
because the gate names them:
`test_clean_sdist_passes`; `test_each_furniture_entry_is_named[<entry>]` × 6;
`test_lookalikes_pass`; `test_member_outside_root_is_named`; `test_dot_slash_prefix_is_read`;
`test_no_sdist_fails`; `test_unreadable_tarball_fails`; `test_every_tarball_is_checked` (a clean
tarball beside a bad one); `test_main_names_each_path_and_fails` (stderr carries every path, exit
1); `test_main_fails_on_a_single_offender`; `test_main_passes_a_clean_sdist`;
`test_parent_reference_is_named` (`rcac_mcp-0.1.0/../x`); `test_root_is_the_tarball_name` (an
archive laid out under another directory has every member reported);
`test_furniture_matches_the_recorded_set`. The last one pins the tuple: a change to the set is a
deliberate edit to the test. The furniture cases are parametrised over a tuple written out in the
test, never over the script's `FURNITURE`: parametrised over the script's own tuple, a dropped
entry takes its test case with it and the module stays green (caught by `hygiene_mutants.py` on
the prototype). Prototype: [`research/prototypes/sdist_hygiene_tests.py`](research/prototypes/sdist_hygiene_tests.py),
19 tests, green in a scratch copy of the tree beside the other 296.

### 2.2 `.github/workflows/ci.yml`: the gate (R1, R2, R4, R5)

See [`research/prototypes/ci.yml`](research/prototypes/ci.yml). Its shape:

- `on.push.branches: ['**']`, so every branch push runs, tags excluded (a `branches` filter
  excludes tag pushes), and no `paths` filter can skip a commit. This is a superset of R1's list.
  Every `{prefix}/{slug}` the profile's open `branches` set produces is covered with no
  same-commit rule tying the trigger to the profile, and so is the throwaway branch R9 needs.
  `on.pull_request.branches: [develop, main]`, with default types (opened, synchronize, reopened).
  No `pull_request_target`, `workflow_run`, or `workflow_dispatch`.
- `permissions: contents: read` at workflow level. The repository's default token is **read-write**
  (research/01), so this line is what makes the token read-only. No job declares `permissions`.
- `defaults.run.shell: bash`, which GitHub runs as `bash --noprofile --norc -eo pipefail`, so a
  failing command fails its step.
- One job, `gate` (`name: gate`, the check publish reads), on `ubuntu-24.04` (pinned, not
  `-latest`), `timeout-minutes: 20`. Its steps, in order:
  1. `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1` with
     `persist-credentials: false` (no token left in `.git/config`).
  2. `astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0` with
     `version: '0.12.17'`, `python-version: '3.14'` (exports `UV_PYTHON`), `enable-cache: false`
     (nothing written to the Actions cache). Both pins follow the cooldown rule, the newest
     release at least 14 days old (research/01).
  3. Seven `run:` steps, one command each, verbatim: `uv sync --locked`, `uv lock --check`,
     `uv run pytest -q`, `bin/sandbox.sh uv run rcac-mcp --help`, `uv build -o dist/`,
     `uvx twine check --strict dist/*`, `uv run python bin/sdist_hygiene.py dist/`. That is
     `setup`, then `verify` split at `&&`, then the recipe's `gate` steps `verify` lacks, in that
     order. A non-zero exit fails its step and the job (R2). An empty `dist/` makes `twine` and the
     hygiene step fail (R3: "no sdist was built").
- No `${{ }}` expression anywhere, no step `name:`, and no `env`, `if`, `continue-on-error`,
  `concurrency`, artifact upload, or cache step. Concurrency
  with `cancel-in-progress` would cancel the run for a superseded push, against R1.

### 2.3 `.github/workflows/mutants.yml`: on demand only (R6)

See [`research/prototypes/mutants.yml`](research/prototypes/mutants.yml). `on: workflow_dispatch`
alone, the same permissions, defaults and pins, and one job, `mutants`, with `uv sync --locked`
and one block. The block reads every `mutants.CASES` id at that commit and fails with a message
when there are none: `--self-check` with no ids runs only the runner's own controls and exits 0,
which would be a hollow green. It then runs `tests/mutants.py --self-check` on all of them through
`tee` (with `pipefail`, the runner's exit status decides). Finally it requires one `killed` line
per case, so a lost report line cannot pass. A separate file keeps the "never on push or pull
request" property a one-line fact about its `on:` key. Publish never reads it (§2.5).

### 2.4 Static checks of the workflow and the docs (gates R1, R3–R8)

`spec/ci-workflow/verify/static.py` (drafted, runnable) runs under
`uv run --with pyyaml==6.0.3` against `--root` (default `.`), so the final gates can point it at a
fresh clone. It has three sections, plus `controls`.

- **`hygiene`**: `FURNITURE` equals the six entries. The recipe's `gate` runs `HYGIENE_CMD`
  exactly once. No recipe `gate` step and no workflow `run` step contains `tarfile` (scoped to code,
  so § Testing prose may name the module).
- **`workflow`**: checks both files.
  - `.github/workflows/` holds exactly `ci.yml` and `mutants.yml`.
  - Top-level keys come from an allowlist, `permissions` is exactly `{contents: read}`, and
    `defaults` is bash.
  - The triggers are exactly the ones in §2.2/§2.3. PyYAML reads `on` as `True` (research/01).
  - Each workflow has its one job, with allowlisted job keys and no `permissions`, `if` or `env`.
  - Steps carry only allowlisted keys (`uses` + `with`, or `run` alone), so a step-level `name`,
    `if`, `env` (for example `PYTEST_ADDOPTS=--collect-only`), `shell`, `working-directory` or
    `continue-on-error` is rejected.
  - The first two steps are checkout and setup-uv with exactly the `with:` values above.
  - Every `uses:` names one of the two allowed actions at its recorded 40-hex SHA. Raw-text and
    parsed `uses:` counts must agree.
  - A ban list (any `${{` expression, a bare `secrets`, `secrets.`, `github.token`, `GITHUB_TOKEN`, `pull_request_target`, `|| true`,
    `git push`/`tag`, `gh …`, `twine upload`, `uv publish`, docker, cosign, attestation,
    upload-artifact, `actions/cache`, curl, wget) is checked over non-comment lines and the
    re-dumped parse.
  - **The gate's run steps must equal the list computed from `AGENTS.md`**: the profile table's
    `setup` and `verify` cells, then the recipe YAML's `gate`. A step added to any of the three
    lists without the other side fails, which makes § Conventions' "CI mirrors the gate"
    mechanically true (R8). The mutants block must equal its recorded text.
  - `requires-python`'s floor is `3.14`, matching `python-version`.
- **`agents`**: no stale no-CI phrase anywhere in `AGENTS.md`, matched across line wraps. The
  profile `ci` row names the file, `gate`, `squash-local`, "head commit", "red", "missing",
  "unfinished", `gh run list --workflow ci.yml --commit`, `-e push` and "latest attempt". § Packaging names the workflow and "pull request". A
  `ci…` caveat says "mirrors" and "before CI has run". § Conventions names the workflow, "same
  commit", `python-version`, "commit SHA" and `bin/sdist_hygiene.py`.
- **`controls`**: requires the unplanted files to pass every section. It then applies 31 plants
  to in-memory copies, each an exact edit whose anchor must occur exactly once, and requires each
  plant to be rejected for its stated reason. The plants: a tag-pinned or wrong-SHA action,
  `contents: write`, job-level permissions, `pull_request_target`, a `paths` filter, a narrowed
  branch list, `push` on mutants, a dropped step, `|| true`, `continue-on-error`, a step `if`, a
  step `env`, a `secrets.` read, a `git push`, cache on, unquoted `3.14`, credentials persisted,
  the mutants empty-guard removed, a recipe gate step CI lacks, the recipe no longer running the
  script, `tests/fixtures` dropped from `FURNITURE`, a second inline `tarfile` check, a planted
  "no CI" sentence, the ci row losing "unfinished" or `-e push`, a step `name:` carrying
  `${{ format('{0}', secrets) }}` (which actionlint and zizmor both pass), `toJSON(secrets)` and
  `github.head_ref` in a run step, and a plain step `name:`. All 31 are rejected and the clean
  prototypes pass (research/01).

### 2.5 `AGENTS.md` and the one seed pointer (R7, R8)

Exact text, validated by `static.py agents` on a scratch copy (research/01):

- **Profile `ci` row** (it defines the exact read, because `code-publish`'s generic
  `gh run list --branch {branch} --limit 1` can accept an older commit's run, a pull-request run
  or a mutants dispatch): `` `.github/workflows/ci.yml`, job `gate`: the one check publish reads.
  Before a `squash-local` landing, list the push runs of the cycle branch's head commit with
  `gh run list --workflow ci.yml --commit "$(git rev-parse HEAD)" -e push --json
  databaseId,attempt,status,conclusion`: at least one run must be listed, and every listed run,
  in its latest attempt, must be `completed` with conclusion `success`. A red, cancelled,
  missing or unfinished run is not green. Pull-request runs and `.github/workflows/mutants.yml`
  are never read ``. `gate.sh landing` runs exactly this read.
- **§ Packaging:** the "no CI, no publish workflow…" bullet becomes "**CI runs the gate on every
  push and pull request.** `.github/workflows/ci.yml` runs the profile's `setup` and `verify` and
  the release recipe's build checks for each push to any branch and each pull request into
  `develop` or `main` (§ Conventions binds the step lists)". It keeps the true remainder: no
  publish workflow, no published container image, not on PyPI, install from `git+https://…`.
  "Wiring CI is a tracked seed" goes. The sdist bullet says `bin/sdist_hygiene.py`, which the
  recipe's `gate` and CI both run, re-checks the built tarball. P1 says only the recipe; P2 adds
  CI.
- **Release recipe:** the hygiene step becomes `- uv run python bin/sdist_hygiene.py dist/` under
  a comment naming the script as the one definition. Caveat `no-ci-no-index` is renamed
  `ci-mirror-no-index` with this text:

  > CI (`.github/workflows/ci.yml`) mirrors this gate on every push and on pull requests into
  > develop and main. The recipe keeps its own copy because a release can be cut before CI has
  > run on that commit: run the gate here, and never wait on CI in its place. Pushing develop and
  > main starts CI runs; the tag push starts none. There is no publish workflow, no published
  > container image, and no PyPI project. A release is a signed tag plus a GitHub release, and
  > users install from `git+https://github.com/purduercac/rcac-mcp@{v}`.

  The one pointer to the old key, in `issues/rename-to-cluster-mcp.md` ("the `no-ci-no-index`
  caveat"), follows the rename in the same commit (deviation D2).
- **§ Conventions:** "The release recipe's `gate` mirrors CI." leaves the version bullet. A new
  bullet reads:

  > **CI mirrors the gate.** `.github/workflows/ci.yml`'s `gate` job runs the profile's `setup`,
  > then each command of its `verify`, then each release-recipe `gate` step that `verify` lacks:
  > one step per command, verbatim, in that order. A change to any of the three lists changes the
  > workflow in the same commit, and the reverse. The sdist furniture set lives only in
  > `bin/sdist_hygiene.py`, which the recipe and CI both run. The workflow's `python-version` is
  > the floor of `requires-python` and moves with it. Every `uses:` in `.github/workflows/` names
  > a full commit SHA, the version in a trailing comment; the token stays `permissions: contents:
  > read`, and no workflow reads a secret or writes outside its runner.
- **§ Testing:** the module list gains `tests/test_sdist_hygiene.py` (P1). The mutation-runner
  paragraph gains `mutants.yml`, saying it runs every case on demand, gates nothing, and becomes
  dispatchable at the release that first carries it to `main` (P2).
- **Repository map:** the "repo-level paths outside the package" paragraph gains
  `bin/sdist_hygiene.py` (P1) and `.github/workflows/` (P2).

### 2.6 Evidence (R9)

`spec/ci-workflow/EVIDENCE.md` holds four sections, each backed by a live re-check in the gate,
never by the prose alone:

- **Lint** (R9a): both linters' commands and versions, and their zero-finding output.
- **Local reds** (R9d, plus R3 red locally): `replay.py` output for `security-furniture` (a real
  hatchling build of a snapshot whose pyproject exclude and `.gitignore` line were removed, red at
  the hygiene step naming `.security/planted.md`), `agents-furniture`, `test-red`, and the
  `hygiene_mutants.py` report.
- **Green run** (R9b): the run id and URL, the commit, the branch.
- **Red run** (R9c): the run id and URL, the plant commit, the failing step, the log line, and
  the throwaway branch's removal.

**Local replay.** `verify/replay.py` (drafted, runnable) fetches one commit at depth 1 into a fresh
repository, as `actions/checkout` does: HEAD, or a commit synthesized from what `git add -A` would
stage, built through a private index so the real index is untouched. It runs the snapshot's own
workflow steps as `bash -eo pipefail -c`. The step environment is an allowlist (PATH, HOME,
TMPDIR, locale, user, SHELL) plus `CI=true` and `UV_PYTHON=3.14`, with the pinned uv 0.12.17
installed in the scratch directory, first on PATH, and its version asserted. Nothing else leaks
in: no `VIRTUAL_ENV`, `UV_PROJECT` (strip-docs-subsystem's trap), other `UV_*`,
`PYTEST_ADDOPTS` or `BASH_ENV`. It does not reproduce the OS or bash (macOS bash 3.2 against
the runner's 5.x). The replay is local evidence; P4's run is the only Linux evidence. After `uv sync` it asserts that `rcac_mcp` imports from the
snapshot. With `--expect-red STEP --expect-log TEXT` it passes only on a red at that step, with
every earlier step green and the text in that step's output.
`verify/hygiene_mutants.py` (drafted, run against the prototype test module) applies ten plants to
`bin/sdist_hygiene.py` in a snapshot and requires the named test ids to fail with a JUnit
`<failure>`, so the unit tests cannot be hollow.

**GitHub.** P4 pushes the cycle branch and reads the `ci.yml` run on its head (commit X). P5
pushes `ci/ci-workflow-red`: X plus one commit adding `.agents/planted.md`, which no exclude
rule covers, so the build ships it and the hygiene step goes red naming it. A real build failing
on GitHub for the planted reason shows R3 and R9c together. The plant is `.agents/`, not
`.security/`, because a `.security/` plant on a public branch would need its exclude and ignore
lines removed on a pushed commit. R9d allows "or locally", and the local replay builds that case
for real. P5 then deletes the remote branch. `verify/evidence.py` (written at P3–P5) reads
`gh run view --json` for each recorded run:
- **Green:** `completed`/`success`, event `push`, branch `ci/ci-workflow`, the recorded `headSha`,
  `attempt == 1` (or every re-run recorded in EVIDENCE.md with its reason), and every one of the
  seven `Run <cmd>` steps `success`. `git diff --name-only X HEAD` is limited to
  `spec/ci-workflow/`.
- **Red:** `failure`; the first failed step is the hygiene step and all earlier ones succeeded;
  `--log-failed` contains `.agents/planted.md`. `gh api repos/purduercac/rcac-mcp/commits/<plant>`
  shows exactly one file, `.agents/planted.md`, `added`, and one parent, X.
  `gh api …/branches/ci/ci-workflow-red` answers 404 (an empty `ls-remote` would also come from an
  SSH failure). GitHub's compare API shows the plant in neither `develop` nor `main`.
- **Landing:** the ci rule this cycle installs needs a green run on the *actual* head, which
  the evidence commits move past X. That is one more push, made at publish (`code-publish` step
  1.4 in `squash-local`: push first, its own go) and read with `gate.sh landing`, the ci row's
  command. Not green is a STOP. TECH P5 hands this over explicitly.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | §2.2 `push: ['**']` + `pull_request: [develop, main]`, no `pull_request_target`; static `workflow` triggers check + plants; P4 green push run |
| R2   | §2.2 one job, setup-uv `python-version: '3.14'`, seven verbatim steps under `bash -eo pipefail`; static step-list = AGENTS.md lists; replay green and red-at-pytest; P4 run with every step `success` |
| R3   | §2.1 `bin/sdist_hygiene.py` run by recipe and CI (same command); static `hygiene`; unit tests + hygiene_mutants; replay red naming `.security/planted.md` / `.agents/planted.md`; P5 red on GitHub |
| R4   | §2.2 workflow-level `contents: read`, no job permissions, no secrets, `persist-credentials: false`, `enable-cache: false`, allowlisted actions, ban list; static checks + plants |
| R5   | §2.2 both `uses:` at 40-hex SHAs with version comments (research/01); static pin check + plants; zizmor `unpinned-uses` |
| R6   | §2.3 `mutants.yml`, `workflow_dispatch` only, every `mutants.CASES` id, empty and lost-line guards; replay green and `empty-cases` red; not named by the `ci` row |
| R7   | §2.5 profile `ci` row text (exact read: `--workflow ci.yml --commit <head> -e push`, every listed run's latest attempt `success`); static `agents`; `gate.sh landing` |
| R8   | §2.5 Packaging, `ci-mirror-no-index` caveat, Conventions bullet; static `agents` stale-claim scan + the computed step-list equality |
| R9   | §2.6 EVIDENCE.md: (a) actionlint + zizmor zero findings (P3); (b) green run (P4); (c) red run + branch removal (P5); (d) local `security-furniture` replay (P3) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research and again on the drafted design. The change touches no numbered § Invariants
section (§1–§9) and no path on the high-blast-radius list. `bin/sandbox.sh` is *run* by CI, as the
profile's `verify` already runs it, and is not edited. `src/` is untouched (GOAL non-goal), so the
coupled core is out of scope. The sections touched:

- **§ Conventions (same-commit rules)**: the workflow, its documentation and the seed pointer land
  in one commit (P2), as the GOAL requires. The new bullet makes "CI mirrors the gate" true and
  checkable. The new comments (workflow headers, script docstrings) are declarative and carry no
  R#/P# ids. New tests carry `@mark.unit`, and their gate names test ids instead of selecting by
  marker. No `Co-Authored-By` trailer.
- **§ Conventions, CLI drives in the sandbox**: CI's only CLI drive is the profile's
  `bin/sandbox.sh uv run rcac-mcp --help`. The integration tests sandbox their own drives (statically
  checked). `RCAC_SSH_HOST` is unset on the runner either way. The new unit test launches nothing.
- **§ Packaging**: the sdist exclude list is unchanged. The hygiene check backs it up from one file.
  `uv.lock` is untouched, and `pyproject.toml` is untouched.
- **§ Factory (Profile, Release recipe)**: the harness footprint. The `ci` key is filled. The
  recipe's `gate` changes only in its hygiene step, which now calls the shared script; the GOAL
  allows exactly that. A caveat is renamed and rewritten. Release `modes`, `bump`, `tag` and `push`
  are unchanged.
- **§ Testing**: one module added and documented. The mutation runner is unchanged and gains an
  on-demand runner.
- **Security lane**: nothing sensitive is deferred. The workflow reads no secret, and the token is
  read-only on a public repository whose default token is read-write.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| D1 (ruled within R3 by the foreman, 2026-10-17; GOAL clarification). The shared hygiene check rejects slightly more than today's inline one: members outside the sdist root, `..` and `./`-prefixed members, and unreadable tarballs. The recipe step's interpreter becomes `uv run python` instead of `python3` | One file must define the set for both (R3). Porting the one-liner verbatim keeps its blind spot: a `.security/` member at the archive top reads as `top.md` and passes (research/01, executed). `uv run python` runs the project's 3.14 everywhere, whereas `python3` is 3.12 on the runner image and whatever the release machine has | A verbatim port is simpler, but it leaves a `.security/` miss in the check whose whole job is `.security/`. The recipe and CI still reject the same set, because they run one file. The foreman ruled it within R3 (2026-10-17) |
| D2 (accepted by the foreman, 2026-10-17). Rename caveat `no-ci-no-index` → `ci-mirror-no-index`, and update its one pointer in `issues/rename-to-cluster-mcp.md` in the same commit | A key named "no CI" restates the claim R8 removes. The seed's pointer (it cites the caveat for its hard-coded repo URL) would dangle otherwise | Keeping the old key avoids touching the seed but leaves "no-ci" in `AGENTS.md`. Leaving the pointer dangling breaks § Conventions' N-places rule. The seed edit is a reference repair, not the rewording of no-CI context that the GOAL's non-goal excludes |
| D3. A new unit-test module and spec-local gate scripts (`static.py` with 26 plants, `replay.py`, `hygiene_mutants.py`) | The deliverable is a gate (trigger 3). Both precedent reviews' top findings were green-but-hollow gates. Each check here is shown red on plants every time it runs | Inline `verify:` greps cannot parse YAML, compute the mirror list, or show their own red; they would be the hollow per-file checks REVIEW.md records |
| D4. `uv` pinned in the workflow (`version: '0.12.17'`), a fourth version to bump | An unpinned `latest` lets a uv release change `uv lock --check` or `uv build` behaviour under an unchanged commit | Unpinned is fewer moving parts, but a red that no commit caused makes the green/red evidence meaningless |

## 4. Rabbit holes (resolved)

- Which SHA does each action tag name, and are the tags annotated? → Both are lightweight tags
  naming commits. The SHAs and the commands that read them are recorded
  ([`research/01-facts.md`](research/01-facts.md)).
- Can the runner provide 3.14? → Yes: a uv-managed `cpython-3.14.8-linux-x86_64-gnu`. Without the
  pin, uv would drift to 3.15 when it ships ([01](research/01-facts.md)).
- Is the lint runnable without system installs? → `actionlint` (with shellcheck) and `zizmor`
  (online, for its pin checks) via `uvx`, at pinned versions, both shown red on plants
  ([01](research/01-facts.md)).
- One hygiene definition → one script that both sides run. The alternatives were parsing
  `AGENTS.md` on the runner, or two copies bound by a rule ([01](research/01-facts.md)).
- Can a workflow be proved before any push? → Local replay of its exact steps on a snapshot
  clone, green and red ([01](research/01-facts.md)). It runs on macOS, though, so Linux-only
  failures surface only at P4 (§5).
- `--self-check` with no case ids passes → the mutants block refuses an empty id list and counts
  `killed` lines (shown red with `empty-cases`).
- Are the hygiene unit tests hollow? → `hygiene_mutants.py` on the prototypes found one hollow
  case: parametrising over the script's own `FURNITURE` let a dropped `.security` pass. The
  tests now use a literal tuple.
- **Adversarial design review (trigger 3), ran 2026-10-17.** The foreman ran a fresh reviewer on a
  scratch clone with the prototypes and drafted gates, and executed every attack there. **Held
  up:** the branches-only filter excludes tags; workflow-level `contents: read` overrides the
  read-write default, and job-level widening is rejected; actionlint catches duplicate keys;
  `|| true`, `continue-on-error`, `if`, `env` and `shell` are rejected; the exact step-list
  equality holds; the red-at-step replay pins both step and log text. What each finding changed:
  1. **HIGH: a secret could reach a step `name:`** (`${{ format('{0}', secrets) }}`: static,
     actionlint and zizmor all passed). → `${{` is banned outright in both files, as is a bare
     `secrets`, and step `name:` is no longer an allowed key. Four plants added (§2.4).
  2. **HIGH: publish's CI read could accept the wrong run** (`--branch … --limit 1` takes an
     older commit's run, a PR run or a mutants dispatch; the draft row lacked `-e push`). → The
     ci row defines the exact command, with `--workflow ci.yml --commit <head> -e push` and every
     listed run's latest attempt `success`. `gate.sh landing` runs it, and static requires its
     parts (§2.5). The KB skill diff is the foreman's to propose.
  3. **MEDIUM: three hollow spots in the hygiene tests** (single offender exits 0; `..` clause
     removed; root read from the first member: all 15 tests green). → Four tests added (19), and
     three plants added to `hygiene_mutants.py` (ten, all killed).
  4. **MEDIUM: the replay did not reproduce the runner** (uv on PATH, inherited
     `PYTEST_ADDOPTS`/`UV_*`/`BASH_ENV`, full-history clone, bash 3.2). → Env allowlist plus
     `CI=true`, pinned uv installed and asserted, depth-1 fetch. The "the way the runner would"
     claim is withdrawn, and the docstring says P4 is the only Linux evidence.
  5. **MEDIUM: pin freshness.** → Foreman's ruling, the cooldown rule (newest release at least
     14 days old): `setup-uv` v10.2.0 `c18668ad…`, uv 0.12.17; checkout v7.0.1 stays
     ([01](research/01-facts.md)).
  6. **MEDIUM: R9 evidence gaps.** → The plant commit is checked by API (one added file, parent
     X); deletion by a 404 from the branches API; the green run is `attempt == 1` or its re-runs
     are recorded; and the landing push of the actual head is planned (§2.6).
  - **LOW, folded:** zizmor runs online in the gates (`ref-version-mismatch`, `impostor-commit`;
    control: a mismatched comment is flagged). The `tarfile` ban is scoped to code. `uvx twine`
    unpinned is an accepted risk (§5). `[skip ci]` and conflicted PRs are recorded in GOAL.

## 5. Risks & open questions

- **R9(b) reading (foreman's ruling, 2026-10-17; GOAL clarification).** The recorded green run
  is on commit X, with only `spec/ci-workflow/` changed after it. Landing still needs a green run
  on the actual head: the publish-time push and `gate.sh landing` (§2.6).
- **Linux-only test failures.** The suite has run only on macOS. The first run on Ubuntu is
  P4's. A failure there is fixed in `tests/` within P4, re-gated with `prepush`, and pushed again
  (a new approval). A cause in `src/` is a STOP, because `src/` is a non-goal.
- **`mutants.yml` cannot be dispatched before the next release** (a recorded limitation, the
  foreman's ruling of 2026-10-17; GOAL clarification). GitHub offers `workflow_dispatch` only for
  a workflow on the default branch, `main`. R6 is met as worded ("WHEN started manually"), and
  the job's exact block is shown by local replay until the release. `AGENTS.md` § Testing says
  when it becomes dispatchable. This is not deferred work: it resolves itself at the release.
- **Workflow drift after the cycle is guarded by a rule, not a machine.** `static.py` is
  spec-local. Nothing in CI lints the workflow or re-checks pins later, and adding that would add
  scope. The foreman ruled no workflow-lint seed (2026-10-17): this is an accepted risk, not a
  deferral.
- **`uvx twine` is unpinned** (accepted risk), in the recipe and therefore in CI (mirrored
  verbatim). Pinning it changes the recipe's `gate`, a non-goal. The run holds no secret and a
  read-only token, so a bad twine release can redden a run but not publish.
- **GitHub skips some runs.** A pushed head commit whose message carries `[skip ci]` (or a
  sibling) gets no push run, and a conflicted PR gets no PR run. Both fail safe: a missing run is
  not green (GOAL clarification).
- **Harness note, not this repo's:** `code-publish` step 1.4 reads `gh run list --branch {branch}
  --limit 1`. Inside this repo the profile's ci-row command binds; the KB skill diff that makes
  publish run it is the foreman's to propose to Geoffrey.

## 6. Verification strategy

Every phase runs one command, `sh spec/ci-workflow/verify/gate.sh P<n>`, which is cumulative.
Local phases read a snapshot of what `git add -A` would commit.

- **P1**: `static.py hygiene`; `tests/test_sdist_hygiene.py` with JUnit, and every id in
  `verify/ids.txt` passed (`junit_ids.py`, reading the report, never the terminal);
  `hygiene_mutants.py`, all ten plants killed.
- **P2**: P1, plus `static.py hygiene workflow agents controls`; actionlint and zizmor with zero
  findings; `replay.py` gate job green; mutants job green; mutants `empty-cases` red.
- **P3**: P2, plus replays red at the right step for `test-red`, `agents-furniture` and
  `security-furniture`; `evidence.py local`.
- **`prepush`**: the check that must pass before any push, and its own mode. It requires a clean
  tree on `ci/ci-workflow`, then runs `static.py` and both linters on a fresh clone of HEAD, then
  P3's checks with `--from HEAD`.
- **`landing`**: the ci row's read for HEAD, for publish to run after its push.
- **P4 / P5**: `evidence.py` reads GitHub (green; then red, branch removal and the ledger). These
  gates push nothing. Each push is a checklist item with its own approval.

The profile's full `verify` runs in every local phase as replay step 2–4 of the gate job, on the
snapshot.

---

*Backing research: [`research/01-facts.md`](research/01-facts.md) (lean path, no digest).*
