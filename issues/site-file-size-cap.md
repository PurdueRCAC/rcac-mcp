---
status: unshaped
kind: fix
appetite: small
lane: public
---

# One very large site file can cost every site rule

## Problem

The startup read (`src/rcac_mcp/site.py`) takes each eligible `/etc/agents.d/*.md` file whole, with
no size limit: `read_local` reads the file into memory, and in `ssh` mode `READ_SITE` sends each
file's content as one base64 word over the shared executor. The read as a whole is bounded by
`site.READ_TIMEOUT` (10 s, `read_bounded`). A file of tens of megabytes can push the `ssh` read past
that bound, and then the read is abandoned and the server starts with the timeout line: **no** site
rule is served, not only the large file left out. When the read does finish, `compose` leaves the
file out of the instructions (it cannot fit the budget) and names it, but `cluster_context` and
`rcac://context` return the full text, so a client gets the whole file as one tool result.

Administrative markdown is a few kilobytes, so nothing shows this today; it takes a mistake in the
directory (a log or a generated dump named `*.md`) to trigger it.

## Why it was deferred

`agents-d-instructions` recorded it and chose not to cap (its `PLAN.md` § 5): the convention's files
are small, and a cap is a new rule about which files a site serves, which the GOAL did not ask for.
**Pre-existing** in part: the old `rcac://context` read every file whole too; that one oversize file
now costs the whole startup read is new with that cycle's bounded read.

## Outcome / vision

One oversize file never costs the others: a file over a stated per-file limit is left out of the
read and named, the way a file that does not fit the budget is named, and every other eligible file
is still served.

## Sketch of the acceptance criteria

- **R1** — WHEN an eligible site file is larger than the per-file limit, the server SHALL leave
  that file out of the site context and name it, and SHALL serve every other eligible file.
- **R2** — The per-file limit SHALL be stated in `AGENTS.md` § Invariants §8 and the README.

## Notes

- Related: `spec/agents-d-instructions/` (PLAN § 5, "No size cap on a file"); `AGENTS.md`
  § Invariants §8 (the read, its bound and the budget).
- Found by: `agents-d-instructions` planning, filed at P4 (2026-10-23).
