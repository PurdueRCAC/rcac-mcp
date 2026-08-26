---
name: cm-release
description: >-
  Human-gated cutter of cluster-mcp versions — the operational sibling of cm-harness that fills the gap
  cm-publish leaves ("never bumps a version or cuts a tag"). Two modes: `release` (a final version on
  main) and `pre-release` (an alpha/beta/rc on main, GitHub release flagged --prerelease). Shared core:
  bump the single version source (pyproject.toml) + `uv lock`, run the gate (pytest → uv build
  --no-sources → twine check --strict → sdist hygiene), sign an annotated tag, then — only after an
  explicit human OK before the first irreversible step — push + `gh release create` and verify.
  Rehearses the whole thing in an isolated git worktree first. Operational, NOT a lifecycle step.
disable-model-invocation: true
argument-hint: "<release|pre-release> <version> [--skip-dry-run] [--publish-pypi] | status"
allowed-tools: Read, Edit, Grep, Glob, AskUserQuestion, Bash(uv run *), Bash(uv sync *), Bash(uv lock *), Bash(uv build *), Bash(uvx twine *), Bash(git status *), Bash(git branch *), Bash(git rev-parse *), Bash(git log *), Bash(git show *), Bash(git diff *), Bash(git fetch *), Bash(git pull *), Bash(git switch *), Bash(git add *), Bash(git commit *), Bash(git push *), Bash(git tag *), Bash(git worktree *), Bash(gh release *), Bash(gh run *), Bash(gh repo *), Bash(tar *), Bash(curl *), Bash(mktemp *), Bash(head *), Bash(tail *), Bash(ls *)
---

# cm-release — cut a version (release / pre-release), human-gated

## When to Use

Invoke `/cm-release` to bump the version and cut a release — the concern `/cm-publish` explicitly
leaves out. It is an **operational sibling of `/cm-harness`, NOT a lifecycle step**: it touches no
`spec/`, no FSM, no `GOAL/PLAN/TECH/REVIEW`; it moves the version and the tag and publishes artifacts.
This is where **irreversible, permanent** outward publishes happen — a git tag can be force-moved but
a published version string on a package index can **never** be reused — so it always confirms before
the first push and rehearses everything in an isolated git worktree first.

Reference: [`factory/invariants.md`](../../factory/invariants.md) §10 (version single-sourced), and
the *Packaging* section of [`AGENTS.md`](../../../AGENTS.md).

**What this project does NOT have yet — say so, don't pretend.** There is no CI workflow, no PyPI
publish workflow, no container build, and no man pages. A cut release is therefore **a tag plus a
GitHub release**, and users install from `git+https://github.com/purduercac/rcac-mcp@X.Y.Z`. The gate
below still runs `twine check --strict` because it validates the metadata that a future publish will
depend on, and catches a broken long-description before it is baked into a tag. Wiring CI and a
publish workflow is a `ROADMAP.md` seed, not something to improvise here.

**Harness portability.** Runs on any harness — see [`factory/portability.md`](../../factory/portability.md).
Fallbacks: run the *Current state* commands yourself if not auto-injected; ask in plain text and STOP
if `AskUserQuestion` is unavailable. `git` / `gh` / `uv` are portable shell, and the worktree dry-run
is plain `git worktree`.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree (must be clean): !`git status --porcelain | head -n 20`
- Version (pyproject.toml — the only source): !`head -n 5 pyproject.toml`
- Recent tags: !`git tag -l --sort=-v:refname | head -n 8`
- main tip: !`git log --oneline -1 main 2>/dev/null`
- Signing key: !`git config user.signingkey || echo "(none configured)"`
- Default remote branch: !`gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo "(gh unavailable)"`

## Argument Parsing

Parse `$ARGUMENTS` case-insensitively for the mode/flags (the version is case-sensitive). If
self-contradictory or ambiguous, STOP and ask.

- **Mode** (first positional, required): `release` | `pre-release`. Missing → STOP and offer both via
  `AskUserQuestion`. There is no `patch` mode: with a single `main` there is no released line to
  hotfix separately from the working line, so a patch *is* a `release`.
- **`status`** as the sole token → Step 0 fast-path (no work).
- **Version** (second positional, required — **never inferred or auto-bumped**; a permanent string is
  too dangerous to guess): validate PEP 440, no `v` prefix, **no hyphen in prereleases** (`0.2.0a1`,
  not `0.2.0-a1`), strictly greater than the latest tag (PEP 440 ordering, e.g. via
  `uv run python -c "from packaging.version import Version; ..."`), and not already a tag.
  **Mode/suffix consistency:** `pre-release` REQUIRES a prerelease suffix (`aN`/`bN`/`rcN`); `release`
  REQUIRES a final version (no suffix). Any mismatch → STOP.
- **Flags:** `--skip-dry-run` opts out of Step 2 (must be explicit; discouraged).
  `--publish-pypi` additionally uploads to PyPI with `uvx twine upload` — **off by default**, requires
  the human to have credentials configured, and is the single most irreversible thing this skill can
  do. Any unrecognized token → STOP and ask.

## Safety Principles

- **Confirm before every irreversible/outward step.** Push and any package-index publish are permanent
  — a version string can NEVER be reused on PyPI. Steps 1–5 are all reversible in-tree; the single
  Step 6 `AskUserQuestion` gate precedes the first push. **No push without an explicit human OK.**
- **Dry-run first (default on).** The bump and the full gate are rehearsed in an isolated
  `git worktree` before a single real ref moves. `--skip-dry-run` requires an explicit flag.
- **`main` only, never force.** The bump commit and the tag land on `main`. Never force-push, never
  rewrite a published tag.
- **The gate is non-negotiable.** `uv run pytest -q`, `uv build --no-sources`,
  `uvx twine check --strict dist/*`, and the sdist-hygiene check must ALL pass. A red gate is a STOP,
  never an override-to-ship.
- **Sdist hygiene is a real check here, not a formality.** The factory (`.agents/`), the design records
  (`spec/`), the backlog (`issues/`), the hidden lane (`.security/`), and the test fixtures must not
  ship in the tarball. `pyproject.toml` excludes them; the gate is what notices when a new top-level
  directory slips past that list. **A `.security/` path in a published tarball would publish an
  inventory of unremediated weaknesses** — treat that specific failure as a hard stop and tell the
  human plainly.
- **Version is single-sourced.** Bump `pyproject.toml` only; `rcac_mcp.__version__` reads it via
  `importlib.metadata`. Never hardcode a version elsewhere ([`invariants.md`](../../factory/invariants.md) §10).
- **Signed tags.** `git tag -s` using the repo's configured `user.signingkey`, verified with
  `git tag -v` **before** any push. If signing is not configured or fails, STOP — an unsigned tag is a
  deliberate human choice (`git tag -a`), not a fallback you take on your own.
- **Release notes are drafted, then confirmed.** Auto-draft from `git log <lasttag>..HEAD` grouped by
  `[category]`; present for human edit at Step 6; never publish unreviewed notes.
- **Never `rm`.** `dist/` is gitignored (leave it); `git worktree remove` cleans the rehearsal.
- **Operational, not meta.** `cm-release` never writes `META.md` findings and never recurses; harness
  friction here goes to `/cm-harness`.
- **`[release]` subjects, and no co-author trailer** on any commit.

## Procedure

### Step 0 — status fast-path (when requested)
`status` (or no meaningful args): report the current version, the latest tags, the `main` tip, whether
the intended target is already a tag, and whether a release looks in-flight (an unpushed local tag, a
dirty bump). Stop.

### Step 1 — Parse + pre-flight
Parse `$ARGUMENTS` (see Argument Parsing) → resolve mode and version. Require a **clean tree**
(`git status --porcelain` empty → else STOP: commit/stash first) and that you are on `main`.
`git fetch origin`; confirm `main` matches `origin/main` (diverged → STOP). STOP if the target version
is already a tag, is not strictly greater than the latest tag, or violates the mode/suffix rule.

### Step 2 — Worktree dry-run (default ON; `--skip-dry-run` opt-out)
Rehearse the whole thing in isolation before any real ref moves:
1. `dir=$(mktemp -d)` (outside the repo, so it never dirties the working tree);
   `git worktree add --detach "$dir/rel" main` — **`--detach`**, because `main` is already checked out
   in the main tree and git refuses to check out the same branch twice.
2. In that worktree, replay the shared core (Step 3: bump + `uv lock`) and the **full gate** (Step 4).
3. `git worktree remove "$dir/rel"`. Any red → STOP and report; **nothing in the real tree moved.**

(A few permission prompts may appear for commands run against the `/tmp` worktree path; expected and
harmless.)

### Step 3 — Bump the single version source
1. Edit the `version = "…"` line in `pyproject.toml` → X.Y.Z (the ONLY source).
2. `uv lock` (updates the `rcac-mcp` entry in `uv.lock`).
3. Commit `[release] Bump version to X.Y.Z`, staging **exactly** `pyproject.toml` — **and `uv.lock`
   only if it is tracked.** It is currently in `.gitignore`, so `git add uv.lock` fails and the
   version in the lock is not part of the released record. Check with `git ls-files --error-unmatch
   uv.lock` rather than assuming either way; if the repo later starts tracking it, this step picks it
   up with no edit. If anything *else* wants staging, something is wrong — STOP and look.

### Step 4 — Gate (non-negotiable)
Run all of:
- `uv run pytest -q`
- `uv build --no-sources`
- `uvx twine check --strict dist/*`
- sdist hygiene — `tar tzf dist/*.tar.gz` must NOT contain `.agents/`, `.security/`, `spec/`,
  `issues/`, or `tests/fixtures/`. List what it *does* contain in the report.

Any failure → STOP (never override-to-ship). `dist/` is gitignored; leave it.

### Step 5 — Signed tag
```
git tag -s X.Y.Z -m "cluster-mcp X.Y.Z"     # uses the repo's configured user.signingkey
git tag -v X.Y.Z                            # verify BEFORE anything is pushed
```
Signing or verification failure → STOP. Everything to this point is reversible in-tree
(`git tag -d`, `git reset`).

### Step 6 — PAUSE: confirm before anything irreversible
Draft the release notes from `git log <lasttag>..HEAD` grouped by `[category]` (for the first release,
from the beginning of history; link `#NN` issues if present). Then present via `AskUserQuestion`: the
mode, the version, what the tag points at, `--prerelease` yes/no, whether `--publish-pypi` was asked
for and what that permanently consumes, the drafted notes (human-editable), and the exact
push/publish commands. **Nothing is pushed until an explicit OK.** `AskUserQuestion` unavailable → ask
in plain text and STOP.

### Step 7 — Push + publish
Push the branch, then the tag (the tag must be on the remote before `--verify-tag`):
```
git push origin main
git push origin X.Y.Z
gh release create X.Y.Z --verify-tag [--prerelease] --title "cluster-mcp X.Y.Z" --notes-file <file>
```
`--prerelease` for `pre-release` mode only. There is no publish workflow wired, so this fires no CI —
say so in the report rather than waiting for a run that will never appear.

Only with `--publish-pypi`, and only after the Step 6 confirmation named it explicitly:
`uvx twine upload dist/*`.

### Step 8 — Verify after publish
- Confirm the tag exists on the remote and points where intended (`git ls-remote --tags origin`).
- Confirm the GitHub release renders and is flagged correctly (`gh release view X.Y.Z`).
- Confirm a clean install resolves the new tag:
  `.agents/factory/bin/sandbox.sh uvx --from git+https://github.com/purduercac/rcac-mcp@X.Y.Z rcac-mcp --version`
  — run it in the sandbox so the probe cannot pick up your working tree or write into your real cache.
- With `--publish-pypi`: `curl -s https://pypi.org/pypi/rcac-mcp/json` — the new version appears under
  `releases`, and `info.version` is the intended "latest" (for a pre-release, PyPI keeps the last
  stable there and `pip` needs `--pre`).

### Step 9 — Report
Mode, version, tag SHA + signature status, the GitHub release URL, what did and did not publish, the
sdist contents summary, the install-probe result, and any caveat — especially that **no CI ran and
nothing reached a package index** unless `--publish-pypi` was used.

## Examples

- `/cm-release release 0.2.0` — bump, gate, sign `0.2.0` on `main`, GitHub release.
- `/cm-release pre-release 0.2.0rc1` — same, flagged `--prerelease`.
- `/cm-release status` — current version, tags, main tip, in-flight check; no changes.
- `/cm-release release 0.2.1 --publish-pypi` — also upload to PyPI (permanent; confirmed at Step 6).

## Notes

- **Reference `invariants.md` §10, don't duplicate it** (single-source version). This skill introduces
  no new numbered invariant.
- The absence of CI is the biggest gap between this skill and a mature release process. Until a
  workflow exists, **this skill's local gate is the only thing standing between a bad build and a
  permanent tag** — which is exactly why `--skip-dry-run` is discouraged and a red gate is a STOP.
- Never `rm`; `dist/` is gitignored and `git worktree remove` cleans the rehearsal.
