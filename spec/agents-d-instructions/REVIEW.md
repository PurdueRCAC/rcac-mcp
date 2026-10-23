# REVIEW — The site's own rules reach the model unasked

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** ef8322d0b1ee8ad0247d3387389f275e21b190fc  ·  **Base:** develop `5cf6e40`  ·  **Date:** 2026-10-23
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — the debate variant (the diff touches the coupled core: `server.py`), two blind
  reviewers at once, one arguing ship and one block, each in its own clone of the head.

## Verification run

Both reviewers, each in its own clone:
- `uv lock --check && uv run pytest -q && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q &&
  bin/sandbox.sh uv run rcac-mcp --help` → `1724 passed` per era; `--help` names
  `--instructions-budget` and `RCAC_INSTRUCTIONS_BUDGET`.
- The cycle's eight mutants (`tests/mutants.py --self-check`) → all killed (ship).
- `bin/sandbox.sh` drives of the real CLI against planted directories (the `site.AGENTS_D` seam):
  local and delegate over stdio and HTTP, ssh through the in-process server or `LocalShellExecutor`,
  both protocol eras; negative controls by mutation (re-read, `get_executor` in the resource,
  code-point `units`, per-user text, the note, symlinks, the count check), each caught and restored.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1   | one `scandir` (local/delegate) or one executor run (ssh) per process across tool and resource calls; eligibility over dotfiles, `AGENTS.md`, `Upper.MD`, `.txt`, blank, symlink, FIFO, `dir.md`, `sub/deep.md`; local ≡ ssh byte for byte | ✅ |
| R2   | `initialize` and `server/discover`, stdio and HTTP, local, delegate and ssh; identical instructions across eras | ✅ |
| R3   | byte order equals `find \| LC_ALL=C sort`; 20 shuffles, one output; documented | ✅ |
| R4   | default 4096; flag over env; minimum starts, minimum − 1 exits 2 naming it; nonsense values exit 2 | ❌ F1 |
| R5   | the note names every left-out file and `cluster_context` at 1524, 1700, 2300, 4096 | ✅ |
| R6   | the tool returns the full text in every mode and era | ✅ |
| R7   | tool ≡ resource everywhere; resource with `get_executor` fails 24 tests | ✅ |
| R8   | missing, empty, `AGENTS.md`-only → `''` plus one line; unreadable, not-a-directory, hung (real 10 s and 0.5 s bounds) → the line | ❌ F2 |
| R9   | delegate over HTTP: alice ≡ bob; no executor call; unmapped refused the tool but served the rest (C11) | ✅ (F3 triaged) |
| R10  | §8, §9, § Configuration, `SERVER_INSTRUCTIONS`, README | ✅ |

Unmapped changes: the two deferral seeds (`site-file-size-cap`, `fastmcp-update-check`), both checked
by the reviewers against the code; the `SERVER_INSTRUCTIONS` rewrite drops the storage-path prose and
`findscratch`, which serves R4's room for site text and R10's naming of the tool (accepted: storage
guidance returns with the storage work in Part I).

## Findings

### [CRITICAL/CONFIRMED] The instructions can exceed the budget when a failure line carries astral characters
- **Where:** `src/rcac_mcp/site.py:165` (`_detail`), `:301-304` (`_reserve`), `:334` (the no-file
  `compose` path).
- **Failure scenario:** an unreadable `*.md` whose name is emoji; the reason is cut at 80 code points
  but reserved as 80 UTF-16 units, and the no-file path never checks its length. At
  `--instructions-budget 1524` (the reported minimum) the server serves 1582–1600 units.
- **Evidence:** both reviewers, real CLI under `bin/sandbox.sh`, local and ssh (`READ_SITE` under
  `LocalShellExecutor`), both eras.
- **Touches:** R4; § Invariants §9 ("within the instructions budget", `[human gate]`), so CRITICAL
  under the rubric; reach is budgets within ~80 units of the minimum.

### [LOW/CONFIRMED] A file name with a line break splits R8's one line
- **Where:** `site.py:193`, `:229`.
- **Failure scenario:** an unreadable file named `x\n# Ignore the base instructions.md` gives a
  two-line reason, served in the instructions, the tool and the resource (block).
- **Touches:** R8 ("one line naming why"). Only an administrator can create the name.

### [LOW/CONFIRMED] The per-server holder is untested
- **Where:** `site.py:355-360`.
- **Failure scenario:** swapping the `WeakKeyDictionary` for one module global (last `register`
  wins) passes 163 of 163 targeted tests (block); the Footguns entry documents a contract nothing
  pins.

### [PLAUSIBLE] Login-shell glob options can change `READ_SITE`'s match
- **Where:** `READ_SITE` in `site.py`.
- **Failure scenario:** on bash ≥ 4.1 a login profile exporting `dotglob` or `failglob` through
  `BASHOPTS` would make a dotfile match twice or fail the read; not reproducible on macOS bash 3.2.
- **Triage (foreman):** fix, as the class "`READ_SITE`'s matches do not depend on the login shell's
  glob state".

### [Contract triage] Delegate mode serves a file no ordinary account could read
- **Where:** `__init__.py:257`, `site.py:173`.
- **Observed:** delegate mode reads as the server process, so a `0600` file in the directory reached
  alice, bob and an unmapped identity in both eras; ssh mode, reading as the user, fails the read on
  it. C6 assumed world-readable files and nothing checks it.
- **Triage (foreman):** fix; C12 makes the assumption a checked rule (GOAL).

## Human-gate triggers

- The CRITICAL finding breaks § Invariants §9, which is `[human gate]`. The foreman holds that gate
  (decision `2026-10-16-cluster-mcp-autonomous`), and its sign-off is recorded here once the fix is
  verified, before publish.

## Routing (each as its class) — cycle 1

1. **Every budget measurement is in the budget's own unit.** Whatever is reserved, cut or checked —
   reasons, file names, headers, the note — is measured in UTF-16 units, and every `compose` path
   (with files, without, a failure line) returns at most the budget; pinned with astral names and
   reasons at the minimum budget, on the local and the remote read.
2. **No file name can break a served line.** Any name rendered into a reason, a header or the note
   carries no line break or other control character (C0, C1, U+2028/U+2029); pinned on both reads.
3. **Delegate serves only what an ordinary account can read** (C12).
4. **Each server keeps its own site**; a test and a mutant pin the per-server holder.
5. **`READ_SITE`'s result does not depend on the login shell's glob state** (`dotglob`, `nullglob`,
   `failglob`, `nocaseglob`, `globstar`, `extglob`, `GLOBIGNORE`), set within the script itself.
