# 01 — Facts pinned for ci-workflow (2026-10-16)

Lean path (`appetite: small`): no fan-out. Each fact below was read or run by the planner on
2026-10-16; the command is given so build and review can re-read it.

## Actions: version ↔ commit SHA

Both tags are lightweight tags that name a commit directly (no annotated tag object to peel).

| Action | Release (published) | Commit SHA | Read with |
|---|---|---|---|
| `actions/checkout` | `v7.0.1` (2026-07-20) | `3d3c42e5aac5ba805825da76410c181273ba90b1` | `gh api repos/actions/checkout/git/ref/tags/v7.0.1` → `object.type: commit`; `gh api repos/actions/checkout/commits/v7.0.1 --jq .sha` agrees |
| `astral-sh/setup-uv` | `v10.2.0` (2026-09-21) | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | `gh api repos/astral-sh/setup-uv/git/ref/tags/v10.2.0` → `object.type: commit` |

- **Cooldown rule (foreman's ruling, 2026-10-17): the newest release at least 14 days old.**
  `checkout` v7.0.1 is the latest release and qualifies. `setup-uv`'s latest, `v10.3.0`
  (2026-10-09, `1c37ad07…`), does not, so the pin is `v10.2.0`. Its `action.yml` at that SHA has
  `version`, `python-version` and `enable-cache`.
- Both run on `node24`. At those SHAs: `checkout`'s `persist-credentials` defaults to `true`
  (the plan sets `false`); `setup-uv` takes `version`, `python-version` (sets `UV_PYTHON`) and
  `enable-cache` (default `auto`, which saves to the GitHub Actions cache on hosted runners; the
  plan sets `false`, so nothing is written outside the runner).
- `uv`: by the same rule, `0.12.17` (2026-09-18; `0.13.1` of 2026-10-14 is too new), which is in
  `setup-uv` v10.2.0's embedded checksum table. The plan pins `version: '0.12.17'` so `uv lock
  --check` cannot drift with an unpinned `latest`. `uv lock --check` passes under 0.12.17 on the
  tree (executed by the reviewer, and by `replay.py`, which installs that uv for its steps).

## Python 3.14 on the runner

`uv python list 3.14 --only-downloads --all-platforms --all-arches` lists
`cpython-3.14.8-linux-x86_64-gnu <download available>`. With `python-version: '3.14'`, setup-uv
exports `UV_PYTHON=3.14`, and `uv sync --locked` fetches a managed 3.14 on `ubuntu-24.04` when the
image has none. Leaving `python-version` unset would let uv pick the newest interpreter that
satisfies `>=3.14`, which stops being 3.14 the day 3.15 ships: the pin is what makes R2's
"supported Python" exact. It is quoted on purpose: YAML reads a bare `3.14` as a float, and the
static check requires the string.

## The repository on GitHub

`gh api repos/purduercac/rcac-mcp` → public, default branch `main`.
`…/actions/permissions` → `enabled: true`, `allowed_actions: all`, `sha_pinning_required: false`.
`…/actions/permissions/workflow` → `default_workflow_permissions: write`: **the repository default
token is read-write**, so the workflow-level `permissions: contents: read` is load-bearing, not
decorative. `…/actions/workflows` → 0 workflows today.

`workflow_dispatch` only offers a manual run for a workflow file that exists on the default
branch. `main` moves only at a release, so `mutants.yml` is dispatchable from the release that
first carries it (PLAN §5).

## Linting the workflow (R9a)

Runnable here with no system install, both fetched by `uvx` at pinned versions:

- `uvx --with shellcheck-py==0.11.0.1 --from actionlint-py==1.7.12.25 actionlint FILE…`:
  actionlint 1.7.12 with shellcheck 0.11.0 on `PATH`, so `run:` scripts are shellchecked too.
  Exit 1 on any finding. Negative control: a planted `echo $foo` → SC2086, exit 1.
- `GH_TOKEN=$(gh auth token) uvx zizmor@1.31.1 FILE…`: the GitHub Actions security linter,
  online, so it also checks each pin against GitHub (`ref-version-mismatch`, `impostor-commit`);
  the token only reads the public API. Exit 14 on a high finding. Control: the v10.2.0 comment on
  the v10.3.0 SHA → `ref-version-mismatch`. Negative control: `actions/checkout@v7` → `unpinned-uses`, exit 14.
  On the prototypes, the default persona reports **no findings**; the `pedantic` persona adds only
  `concurrency-limits` (no `concurrency:` key). The plan declines it: `cancel-in-progress` would
  cancel the run for a superseded push, and R1 says every push runs.

Both linters report zero findings on `research/prototypes/ci.yml` and `mutants.yml`.

## One hygiene definition for the recipe and CI (R3)

The recipe's inline `python3 -c` is replaced by a script both run:
`uv run python bin/sdist_hygiene.py dist/` (prototype: `research/prototypes/sdist_hygiene.py`).
The recipe's `gate` list and the workflow's step then carry the same one-line command, and the
path set (`FURNITURE`) exists once. Rejected alternatives: the workflow parsing the YAML block out
of `AGENTS.md` at run time (needs PyYAML on the runner, which is not a dependency, and couples CI
to Markdown layout); two copies bound only by a § Conventions rule (allowed by R3, but a rule is
weaker than one file).

Run against real builds and planted tarballs, the prototype agrees with the recipe's inline check
on every in-root case (`.security/…`, `.agents/…`, `tests/fixtures/…` all named; `binary.txt`,
`specs/x`, `tests/fixturesX` all pass) and is stricter in two the inline check misses: a member
outside the sdist root (`.security/top.md` at the archive top; the inline check strips the first
component unconditionally and reads `top.md`), and a `./`-prefixed member
(`./rcac_mcp-0.1.0/spec/GOAL.md`, which the inline check reads as `rcac_mcp-0.1.0/spec/GOAL.md`).
The hatchling sdist of HEAD (`rcac_mcp-0.1.0.tar.gz`, 48 members) is all under
`rcac_mcp-0.1.0/` and passes.

## Local replay

`verify/replay.py` replays a job's run steps on a snapshot clone. On a scratch repo holding the
prototypes and the drafted `AGENTS.md` edits: the gate job, 7/7 green in 17 s; `test-red` red at
`uv run pytest -q` ("planted red"); `agents-furniture` and `security-furniture` red at the
hygiene step naming the planted path; a red at the wrong step and a green where red was expected
both fail the replay. The mutants job: green in 110 s (all cases killed); `empty-cases` red with
"no mutation cases found". `verify/static.py … controls`: the clean prototypes pass, all 31 plants
are rejected for their stated reason. After the review, replays run uv 0.12.17 from an
allowlisted environment on a depth-1 snapshot, and an inherited `PYTEST_ADDOPTS=--collect-only`
no longer reaches the steps.

Against the prototype test module (`research/prototypes/sdist_hygiene_tests.py`, 19 tests after
the review): `junit_ids.py` reports all 19 `ids.txt` ids passed, and `hygiene_mutants.py` kills
all ten plants (the review added three that left the first 15 tests green). Its first run found the module hollow against
`drop-security`, because the furniture cases were parametrised over the script's own tuple. They
now use a literal one.

## PyYAML footgun

PyYAML implements YAML 1.1 and reads a bare `on:` key as the boolean `True`. `static.py` reads
`doc.get('on', doc.get(True))`; a checker that looks only for `'on'` would see no triggers at all.
