# EVIDENCE — Run the gate on GitHub on every push

The record behind GOAL R9. Each section is the dated trace of one run; the phase gates re-run
every check named here and `verify/evidence.py` holds this file to what the gates run (the lint
commands and pins in `verify/gate.sh`, the steps of `.github/workflows/ci.yml`, the plants in
`verify/hygiene_mutants.py`), so the prose is never the proof on its own.

## Lint

R9(a): a static lint of both workflow files with zero findings.

Recorded: 2026-10-18

Tools, each fetched by `uvx` at the version `verify/gate.sh` pins: actionlint 1.7.12
(`actionlint-py==1.7.12.25`), with shellcheck 0.11.0 (`shellcheck-py==0.11.0.1`) on its `PATH`
so every `run:` script is shellchecked too; and zizmor 1.31.1, online, so it also checks each
action pin against GitHub (`ref-version-mismatch`, `impostor-commit`). Run from the worktree root
on the tree of `7d56cd1`, the cycle rebased onto `develop` `de2b380` (P3 changes nothing outside
`spec/ci-workflow/`).

```
$ uvx -q --with shellcheck-py==0.11.0.1 --from actionlint-py==1.7.12.25 actionlint -version
1.7.12
installed by downloading from release page
built with go1.26.1 compiler for darwin/arm64
$ uvx -q --with shellcheck-py==0.11.0.1 --from actionlint-py==1.7.12.25 sh -c 'shellcheck --version'
ShellCheck - shell script analysis tool
version: 0.11.0
license: GNU General Public License, version 3
website: https://www.shellcheck.net
$ uvx -q zizmor@1.31.1 --version
zizmor 1.31.1
$ uvx -q --with shellcheck-py==0.11.0.1 --from actionlint-py==1.7.12.25 actionlint .github/workflows/ci.yml .github/workflows/mutants.yml
exit 0
$ GH_TOKEN=$(gh auth token) uvx -q zizmor@1.31.1 --no-progress .github/workflows/ci.yml .github/workflows/mutants.yml
 INFO zizmor: 🌈 zizmor v1.31.1
 INFO audit: zizmor: 🌈 completed .github/workflows/ci.yml
 INFO audit: zizmor: 🌈 completed .github/workflows/mutants.yml
No findings to report. Good job! (2 suppressed)
exit 0
```

actionlint prints nothing when it finds nothing. zizmor's two suppressed findings are below its
default persona; `--persona pedantic` shows them, one per workflow, both
`help[concurrency-limits]: insufficient job-level concurrency limits` (2 low). The plan declines
a `concurrency:` key on purpose: `cancel-in-progress` would cancel the run for a superseded push,
and R1 says every push runs (`research/01-facts.md`).

## Local reds

R9(d), and R3 red locally: the nine-step gate job replayed (`verify/replay.py`) on a snapshot of
the tree, each with one plant, under CI's uv 0.12.17 (the replay installs it and asserts its
version), red at the intended step with every earlier step green and the intended text in that
step's output (blank lines dropped). `exit 0` is the replay's own verdict: its expectation held.
Each snapshot is a commit synthesized from what `git add -A` would stage, with the tree of
`7d56cd1`; the `security-furniture` plant removes the `/.security` sdist exclude and the
`.gitignore` line in the snapshot only, so a real hatchling build ships `.security/planted.md`.

Recorded: 2026-10-18

```
$ uv run -q --with pyyaml==6.0.3 python spec/ci-workflow/verify/replay.py --plant security-furniture --expect-red sdist_hygiene --expect-log '.security/planted.md'
replay[gate @ e1284c7816 from worktree + security-furniture] step 1/9 ok: uv sync --locked
replay[gate @ e1284c7816 from worktree + security-furniture] step 2/9 ok: uv lock --check
replay[gate @ e1284c7816 from worktree + security-furniture] step 3/9 ok: uv run pytest -q
replay[gate @ e1284c7816 from worktree + security-furniture] step 4/9 ok: CLUSTER_MCP_TEST_ERA=modern uv run pytest -q
replay[gate @ e1284c7816 from worktree + security-furniture] step 5/9 ok: bin/sandbox.sh uv run rcac-mcp --help
replay[gate @ e1284c7816 from worktree + security-furniture] step 6/9 ok: uv run python bin/audit_lock.py
replay[gate @ e1284c7816 from worktree + security-furniture] step 7/9 ok: uv build -o dist/
replay[gate @ e1284c7816 from worktree + security-furniture] step 8/9 ok: uvx twine check --strict dist/*
replay[gate @ e1284c7816 from worktree + security-furniture] step 9/9 FAILED (exit 1): uv run python bin/sdist_hygiene.py dist/
dist/rcac_mcp-0.1.1.tar.gz: .security/planted.md
sdist hygiene: 1 problem(s); see above
replay[gate @ e1284c7816 from worktree + security-furniture]: red as expected at 'uv run python bin/sdist_hygiene.py dist/', naming '.security/planted.md'
exit 0
$ uv run -q --with pyyaml==6.0.3 python spec/ci-workflow/verify/replay.py --plant agents-furniture --expect-red sdist_hygiene --expect-log '.agents/planted.md'
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 1/9 ok: uv sync --locked
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 2/9 ok: uv lock --check
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 3/9 ok: uv run pytest -q
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 4/9 ok: CLUSTER_MCP_TEST_ERA=modern uv run pytest -q
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 5/9 ok: bin/sandbox.sh uv run rcac-mcp --help
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 6/9 ok: uv run python bin/audit_lock.py
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 7/9 ok: uv build -o dist/
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 8/9 ok: uvx twine check --strict dist/*
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture] step 9/9 FAILED (exit 1): uv run python bin/sdist_hygiene.py dist/
dist/rcac_mcp-0.1.1.tar.gz: .agents/planted.md
sdist hygiene: 1 problem(s); see above
replay[gate @ 7fe2d9a9d2 from worktree + agents-furniture]: red as expected at 'uv run python bin/sdist_hygiene.py dist/', naming '.agents/planted.md'
exit 0
$ uv run -q --with pyyaml==6.0.3 python spec/ci-workflow/verify/replay.py --plant test-red --expect-red 'uv run pytest -q' --expect-log 'planted red'
replay[gate @ e95a60aaef from worktree + test-red] step 1/9 ok: uv sync --locked
replay[gate @ e95a60aaef from worktree + test-red] step 2/9 ok: uv lock --check
replay[gate @ e95a60aaef from worktree + test-red] step 3/9 FAILED (exit 1): uv run pytest -q
........................................................................ [ 10%]
........................................................................ [ 21%]
........................................................................ [ 32%]
........................................................................ [ 43%]
........................................................................ [ 54%]
........................................................................ [ 64%]
........................................................................ [ 75%]
...F.................................................................... [ 86%]
........................................................................ [ 97%]
..................                                                       [100%]
=================================== FAILURES ===================================
_______________________________ test_planted_red _______________________________
    @mark.unit
    def test_planted_red() -> None:
>       assert False, 'planted red'
E       AssertionError: planted red
E       assert False
tests/test_planted_red.py:6: AssertionError
=============================== warnings summary ===============================
tests/test_tokens.py::test_mismatched_token_is_rejected[algorithm]
  /private/var/folders/42/998kw6wj2ts8nlls3qyqmqs00000gn/T/ci-replay.7btcfxhc/repo/.venv/lib/python3.14/site-packages/jwt/api_jwt.py:149: InsecureKeyLengthWarning: The HMAC key is 32 bytes long, which is below the minimum recommended length of 64 bytes for SHA512. See RFC 7518 Section 3.2.
    return self._jws.encode(
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/test_planted_red.py::test_planted_red - AssertionError: planted red
assert False
1 failed, 665 passed, 1 warning in 20.15s
replay[gate @ e95a60aaef from worktree + test-red]: red as expected at 'uv run pytest -q', naming 'planted red'
exit 0
```

The hygiene unit tests are not hollow: `verify/hygiene_mutants.py` breaks `bin/sdist_hygiene.py`
ten ways in a snapshot, and each plant is killed only when its named test ids fail with a JUnit
`<failure>`.

```
$ uv run -q --with pyyaml==6.0.3 python spec/ci-workflow/verify/hygiene_mutants.py
hygiene-mutants: killed prefix-not-component (test_lookalikes_pass)
hygiene-mutants: killed no-root-check (test_member_outside_root_is_named)
hygiene-mutants: killed no-sdist-passes (test_no_sdist_fails)
hygiene-mutants: killed drop-security (test_each_furniture_entry_is_named[.security], test_furniture_matches_the_recorded_set)
hygiene-mutants: killed first-tarball-only (test_every_tarball_is_checked)
hygiene-mutants: killed exit-zero (test_main_names_each_path_and_fails)
hygiene-mutants: killed silent (test_main_names_each_path_and_fails)
hygiene-mutants: killed single-offender-passes (test_main_fails_on_a_single_offender)
hygiene-mutants: killed dotdot-allowed (test_parent_reference_is_named)
hygiene-mutants: killed root-from-first-member (test_root_is_the_tarball_name)
exit 0
```

## Green run

R9(b), and R1 and R2 observed on GitHub: the first push of `ci/ci-workflow` ran `ci.yml`'s `gate`
job green, every step `success`, on the first attempt. Commit X below is the pushed head; every
later commit on the branch touches only `spec/ci-workflow/` (`verify/evidence.py green` checks
both, and re-reads the run from GitHub).

Recorded: 2026-10-18

- run: 39188588571
- attempt: 1
- url: https://github.com/PurdueRCAC/rcac-mcp/actions/runs/39188588571
- commit: ffb03b51e6b549f7143267d2c202914b6d2eed4a
- branch: ci/ci-workflow
- date: 2026-10-18 (completed 2026-10-19T02:10:27Z)

```
$ gh run list -R purduercac/rcac-mcp --workflow ci.yml --commit ffb03b51e6b549f7143267d2c202914b6d2eed4a -e push --json databaseId,attempt,url,status,conclusion,headSha
[{"attempt":1,"conclusion":"success","databaseId":39188588571,"headSha":"ffb03b51e6b549f7143267d2c202914b6d2eed4a","status":"completed","url":"https://github.com/PurdueRCAC/rcac-mcp/actions/runs/39188588571"}]
```

Job `gate`, conclusion `success`; its run steps, each `success`: `uv sync --locked`, `uv lock
--check`, `uv run pytest -q`, `CLUSTER_MCP_TEST_ERA=modern uv run pytest -q`, `bin/sandbox.sh uv
run rcac-mcp --help`, `uv run python bin/audit_lock.py`, `uv build -o dist/`, `uvx twine check
--strict dist/*`, `uv run python bin/sdist_hygiene.py dist/`.
