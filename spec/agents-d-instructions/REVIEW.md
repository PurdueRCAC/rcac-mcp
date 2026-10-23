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

## Review cycle 2 — changes-requested (2026-10-23)

- **Reviewed commit:** 1e1b9705345bc898095cc2d6acebd71a5becc030 · a fresh blind debate pass over the
  full spec-excluded diff (not scoped to cycle 1's findings), each reviewer in its own clone.
- **Verification:** `1777 passed, 14 skipped` per era in both clones (the 14 are the exported
  `BASHOPTS` rows, which need bash ≥ 4.1; this host has 3.2); CI 40010802253 on `1e1b970`:
  `1791 passed` per era, none skipped. Ship swept every budget from the minimum − 40 to + 600 over
  five non-BMP layouts on both reads, block fuzzed 241,331 budgets: no output over budget, no cut
  base, no unnamed file. Names with C0, DEL, C1, U+2028/9, tab and ESC broke or forged no line. The
  remote read under bash, zsh and tcsh login shells with hostile profiles (glob options, `set -f`,
  `GLOBIGNORE`, exported functions, aliases, `CDPATH`) equalled `read_local`. Cycle 1's five classes
  hold.

### [HIGH/CONFIRMED] Delegate mode still serves text an ordinary account cannot read
- **Where:** `src/rcac_mcp/site.py:218-248` (`_others_may`), reached through `__init__.py`
  (`_read_site`).
- **Failure scenario:** the check reads only the "other" mode bits of the directory and each file.
  Served in delegate mode, to every token holder including an unmapped one, in both eras: a `0604`
  file whose group holds every account (the group class has no read bit); a `0705` directory; an
  `agents.d` at `0755` under a `0700` parent, directly or through a symlink; a `0644` file with an
  ACL entry denying read.
- **Touches:** C12; README ("serves only what every account could read") and a test docstring
  overclaim it.

### [LOW/CONFIRMED] The reset of hijackable functions in `READ_SITE` is unpinned
- **Where:** `site.py:73` (`unset -f base64 shopt tr test`).
- **Failure scenario:** deleting the line passes the site, context and startup suites; with it gone,
  a login profile exporting a `base64`, `tr` or `shopt` function makes the read malformed. Both
  reviewers.

### [LOW/CONFIRMED] Two names that decode alike are one file to the composer and tie in order
- **Where:** `site.py:400` (`g not in trial`, dataclass equality), `site.py:131` (sort key).
- **Failure scenario:** on Linux, two names with different invalid UTF-8 bytes both decode to
  `a�.md`; with identical text, one is neither served nor named (R5), and their order follows
  directory enumeration (R3). Driven through `parse_remote`; APFS cannot hold the names. And
  `shown()` is not injective: a real newline and the literal text `\x0a` display alike (both).

### [Contract triage] The heading over R8's line
- Ship asked whether `## Site context (/etc/agents.d)` plus the reason is two lines against R8's
  "one line". **Foreman:** the heading is R2's section frame; R8's "one line" is the reason. No change.

### Scope note
- The `SERVER_INSTRUCTIONS` rewrite drops RCAC's storage guidance (home quota, scratch purge, depot,
  `findscratch`): both reviewers. **Foreman:** accepted, as in cycle 1. A site's own guidance is now
  delivered by its `/etc/agents.d`, which this cycle exists to serve, and the generic base keeps the
  room R4's default needs.

## Human-gate triggers — cycle 2

- The HIGH is a disclosure in `delegate` mode through `__init__.py` (high-blast-radius) and the
  §9 surface (`[human gate]`). The foreman holds the gate and routes it to a fix; the contract is
  tightened to its stated purpose, not narrowed (GOAL C12, amended 2026-10-23).

## Routing (each as its class) — cycle 2

1. **Delegate serves only what every account can read.** In `delegate` mode a path is readable
   only if each class (owner, group, other) has the bit it needs on the resolved file, the
   directory and every ancestor up to `/` (search on ancestors, read and search on the directory,
   read on the file), and none of them carries extended ACL entries; anything else is unreadable
   (R8's line). Pin the group class, an ancestor (direct and through a symlink) and an ACL, and
   correct the README and docstring.
2. **Every function `READ_SITE` relies on is reset by the script and pinned**: a test with a login
   profile that defines and exports each such function, and a mutant deleting the reset.
3. **A file is identified and ordered by its raw name bytes**, never by its decoded or shown form;
   `shown()` is injective (the backslash escaped too). Pin with two names that decode alike, on the
   remote parse.
