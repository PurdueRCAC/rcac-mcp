# REVIEW — An unreadable sdist is named in one line, never a traceback

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 89d06ba274cfcede668ed82cd77aa12500a4e671  ·  **Base:** develop (`5153e47`)  ·  **Date:** 2026-10-19
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

## Verification run

- R1: a 200 kB member gzipped and cut in half → one line `…: cannot be read (EOFError: Compressed
  file ended…)`, then `sdist hygiene: 1 problem(s); see above`, exit 1, no traceback (develop's
  check: the `EOFError` traceback).
- R2: 21 unreadable shapes, a directory named like an sdist, and a planted `RuntimeError`: each
  named in one line; a multi-line `ReadError` collapsed to one.
- R3: xz-padded, truncated-gz, furniture and outside-root sdists in one directory → 4 lines, the
  `4 problem(s)` summary, exit 1, no traceback.
- R4: about 100,000 truncations and random bit flips per format: no gz, bz2 or xz variant called
  clean with content that differs from the original; damaged later zstd frames and trailing
  garbage refused.
- R5: a real `uv build --sdist` clean locally and in CI (run 39224880838 printed `sdist hygiene:
  rcac_mcp-0.1.1.tar.gz clean`); the furniture and outside-root tests unchanged and green; time
  linear and memory bounded at 27–30 MB RSS (1 GiB zero tail in gz 0.74 s; 256 MiB in xz 1.47 s
  and bz2 1.52 s; a bit flip mid-300 MB gz now caught).
- R6: the `old-except-types` mutant killed; the full `verify`: `uv lock --check`, `702 passed` in
  both eras, sandboxed `--help`.
- 15 reviewer mutants: 12 killed; the three survivors are an unreachable raise, an unspecified
  one-versus-two zero-block case, and an equivalent rewrite (coverage observations, no defect).
- The recipe line and `ci.yml:49` still run the one shared file unchanged.

## Requirement → evidence matrix

| R-ID | Status | Verified how |
|------|--------|--------------|
| R1 | ✅ | truncated gzip drive; named test |
| R2 | ✅ | 21 shapes + planted errors |
| R3 | ✅ | mixed directory, summary count |
| R4 | ✅ | ~100k truncations and bit flips per format |
| R5 | ✅ | real sdist clean locally and in CI; furniture tests; bounded time and memory |
| R6 | ✅ | mutant killed; full `verify` |

Unmapped changes: the strict bz2/xz/zstd stream handling and the link and duplicate-member tests
serve R4 and R5. Two readable but non-standard shapes change verdict (xz stream padding, bz2 zero
padding): now refused, since Python's own readers cannot read them to the end and PEP 625 sdists
are gzip. Accepted by the foreman's plan-gate ruling (an sdist the check cannot read and verify
in full is refused).

## Findings

None CONFIRMED, none PLAUSIBLE.

One out-of-scope observation, pre-existing on `develop` and outside this cycle's non-goals (a
readable but malformed archive), was routed to its home before this verdict.

## Human-gate triggers

- None (`bin/sdist_hygiene.py` and its tests are not on § Invariants' high-blast-radius list).

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run.
