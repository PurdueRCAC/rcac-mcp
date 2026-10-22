# GOAL — An unreadable sdist is named, never a traceback

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** sdist-hygiene-truncated-gzip
- **kind:** fix
- **appetite:** small

## Problem

`bin/sdist_hygiene.py` is the one check, shared by the release recipe's `gate` and CI's `gate`
job, that a built sdist ships no repository furniture. Its docstring (lines 20–22 on `develop`
`5153e47`) promises exit 1 when "a tarball cannot be read", with each problem as one line on
stderr naming the tarball, and its handler (lines 62–67) prints that line as
`<tarball>: cannot be read (…)`.

A gzip stream cut short breaks that promise. Reproduced on `5153e47` with Python 3.14.6: a
`rcac_mcp-0.1.0.tar.gz` holding a 200 kB member, truncated to half its bytes, makes
`python bin/sdist_hygiene.py <dir>` exit 1 with a Python traceback ending in
`EOFError: Compressed file ended before the end-of-stream marker was reached`, raised from the
member listing. No `cannot be read` line and no `sdist hygiene: N problem(s)` summary are printed.
A non-tarball named `.tar.gz` and an empty `.tar.gz` already produce the formatted line, so the
gap is in the class of errors the check turns into its message, not in one file shape.

The verdict is right: the check still fails closed, so the gate stays red. What breaks is the
contract a reader relies on. A release operator or a CI log reader gets a stack trace where the
check promised a line naming the bad tarball, and any other sdist in the same directory goes
unchecked because the run stops at the first.

**Pre-existing:** no. The check and its handler landed in `852b716` ("[ci] Run the release gate on
every push"), the `ci-workflow` cycle, and its blind review found this on 2026-10-18.

## Outcome / vision

Whatever makes an sdist unreadable, the check names it in the one formatted line and exits 1. It
never shows a traceback, and it never passes an sdist it could not read. Readable sdists are
judged exactly as today.

## Acceptance criteria (the contract)

Broken today → fixed. "The check" is `bin/sdist_hygiene.py`, run as
`uv run python bin/sdist_hygiene.py <dir>` (the recipe's and CI's invocation).

- **R1** — WHEN `<dir>` holds an sdist whose gzip stream is truncated, the check SHALL print
  `<tarball>: cannot be read (…)` on stderr and exit 1, with no traceback. (Today: an `EOFError`
  traceback and exit 1.)
- **R2** — IF an sdist in `<dir>` cannot be read for any other reason (a corrupt tar header, a file
  that is not a tarball but is named `.tar.gz`, a member read failing partway through the archive,
  or any other error raised while its member names are listed), THEN the check SHALL print the same
  `<tarball>: cannot be read (…)` line on stderr and exit 1, with no traceback.
- **R3** — WHEN `<dir>` holds an unreadable sdist alongside other sdists, the check SHALL still
  report every other sdist's problems, and SHALL end with the `sdist hygiene: N problem(s); see
  above` summary, where N counts the unreadable tarball. (Today: the run stops at the traceback, and
  the summary and the later tarballs are lost.)
- **R4** — The check SHALL NOT exit 0 for any sdist it could not read in full: an unreadable
  tarball never yields the `clean` line, whatever the error.
- **R5** — For a readable sdist the check's output and exit status SHALL be unchanged: a clean
  sdist exits 0 with `sdist hygiene: <name> clean`, and a furniture member or a member outside the
  root is reported and exits 1 exactly as on `5153e47`.
- **R6** — The profile's `verify` SHALL fail if R1 or R2 regresses to a traceback.

## Non-goals (no-gos)

- No change to what counts as furniture (`FURNITURE`), to the root rule, or to the clean-sdist
  path.
- No change to the message format beyond filling the `(…)`; the wording of the error inside the
  parentheses is the plan's choice.
- No second copy of the check: it stays the one file both the release recipe's `gate` and CI's
  `gate` job run, and neither invocation changes.
- No new checks on wheels or on sdists that are readable but malformed in other ways (missing
  `PKG-INFO`, wrong root name beyond today's outside-the-root rule).
- No change to the CI workflow or the release recipe.

## Clarifications

Rulings from the foreman, the judge at every gate for this effort, 2026-10-19:

- **Q:** Fix the truncated-gzip instance only, or the whole class of unreadable sdists? — **A:**
  The class. Every unreadable sdist ends in the formatted `<tarball>: cannot be read (…)` line and
  exit 1, never a traceback: a truncated gzip stream, a corrupt header, a non-tarball named
  `.tar.gz`, a member read failing mid-archive, and any other exception the tar layer raises while
  names are listed. The check still fails closed whatever goes wrong (R1, R2, R4; resolved
  2026-10-19).
- **Q:** May the fix touch what counts as furniture, or the clean-sdist path? — **A:** No. The
  check stays one file shared by the recipe and CI (R5, Non-goals; resolved 2026-10-19).
- **Q:** How are the R-IDs phrased? — **A:** As observable broken→fixed behaviour, never the
  suspected cause; which exception escapes, and why, is for the plan step to root-cause (resolved
  2026-10-19).
- **Q:** Where did the defect come from? — **A:** Escaped from `ci-workflow`: blame on
  `origin/develop` puts the handler (lines 54–69) in `852b716`, which carries `spec/ci-workflow/`.
  Recorded with `metrics.py escaped … --from ci-workflow` (resolved 2026-10-19).

## Related materials

- Seed: [`issues/sdist-hygiene-truncated-gzip.md`](../../issues/sdist-hygiene-truncated-gzip.md)
- Origin cycle: [`spec/ci-workflow/`](../ci-workflow/) (its `REVIEW.md` records the finding)
- Source: `bin/sdist_hygiene.py` (docstring lines 5–23, `sdist_violations` lines 54–69, `main`
  lines 72–82); tests: `tests/test_sdist_hygiene.py`
- Callers: `AGENTS.md` § Factory release recipe `gate` (`uv run python bin/sdist_hygiene.py
  dist/`), `.github/workflows/ci.yml` `gate` job
