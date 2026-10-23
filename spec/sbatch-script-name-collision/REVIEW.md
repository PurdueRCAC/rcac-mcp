# REVIEW — `sbatch` job scripts written in the same second share a name

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent). The correctness
> pass grades the branch diff against [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants
> **only** — it does not see `PLAN.md`/`TECH.md`. Every finding cites an **executed** command.

- **Reviewed commit:** 30801597662820ae588f51feb15c3f9515280558  ·  **Base:** develop `d34de62`  ·  **Date:** 2026-10-23
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — one blind reviewer (the diff touches no coupled-core path).

## Verification run

- `uv lock --check`; the suite under `bin/sandbox.sh` → `1733 passed` per era; `--help` exits 0;
  the cycle's three mutants killed.
- Real `rcac-mcp -e local` stdio servers under `bin/sandbox.sh`, both eras, an `sbatch` stand-in
  that copies the script at submission; negative controls: the old `{name}_{timestamp}.sh`, a
  per-process counter, a generator seeded from the clock at import — each fails, the head passes.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | 12 calls in one held second (same, none, `''`, `'...'`, names that sanitise alike); 25 on the real clock, up to 17 in one second: all distinct, each its own bytes | ✅ |
| R2 | 4 overlapping calls on one server; 4 over two servers sharing a HOME: every job its own bytes | ✅ |
| R3 | seeded `~/jobs` (old-form names in the held second, token-form files, `0444`, a directory, a symlink): byte- and mode-identical; only the submitted paths are new | ✅ (Q1(a)) |
| R4 | each call returns its job id; `<stem>_<YYYYmmdd_HHMMSS>_[0-9a-f]{16}.sh`, executable | ✅ |
| R5 | docstring ≡ fixture, 1960 characters, both eras; mutated docstring or fixture caught | ❌ F2 |

Unmapped changes: none.

## Findings

### [LOW/CONFIRMED] The served description does not say an empty name becomes `job`
- **Where:** `src/rcac_mcp/tools/slurm.py:105-106`, `tests/fixtures/served_surface.json:513`.
- **Failure scenario:** "`<name>`: the sanitised job_name" says nothing of `None`, `''` or a name
  sanitised to nothing, which the code names `job` (R4). Nothing stated is false; the old text had
  the same gap.
- **Touches:** R5 ("how it is named, matching the code").

### [Out of scope: ruled at the PLAN/TECH gate] The token costs 17 characters of name headroom
- A sanitised `job_name` of 220–236 characters, written on develop, now fails cleanly ("File name
  too long", nothing submitted). Accepted at the PLAN/TECH gate (2026-10-23) with no seed; no R-ID
  covers it.

## Human-gate triggers

- None: `tools/slurm.py` is a leaf; §9's surface change is the named one-line
  `sbatch.description` delta.

## Routing (each as its class) — cycle 1

1. **The served description states every name the code can give**, the `job` fallback included,
   within the 2000-character cap; the docstring test pins the fallback.
