---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `list_directory` misreads some entries

## Problem

`list_directory` (`src/rcac_mcp/tools/filesystem.py`) parses the text of `ls -l
--time-style=long-iso` by splitting each line on whitespace. Three kinds of entry come back wrong:

- a name containing a newline is split across two lines, so the tool returns a truncated name and
  skips or misparses the rest;
- a symbolic link's name is returned as `name -> target`, and its `path` carries the arrow;
- a device file's size column is `major, minor`, so `int()` raises and the whole call fails.

A name with leading spaces also loses them. The tool's argument handling is not involved: the
directory reaches `ls` as one literal argument, and the entries are what `ls` printed.

## Why it was deferred

`safe-execution` moved the tool onto an argument list and a single call, and kept the parsing as it
was: changing what the tool returns is outside that cycle's contract. **Pre-existing:** yes, present
on `develop` before `safe-execution` (research 04 §4 of `spec/safe-execution/`).

## Outcome / vision

Every entry of a directory is returned with its exact name and its own path, whatever bytes the
name holds and whatever kind of file it is, and one odd entry never fails the listing.

## Sketch of the acceptance criteria

- **R1** — WHEN a directory holds a name with a newline, leading spaces or ` -> `, a symbolic link,
  or a device file, `list_directory` SHALL return each entry with its exact name and path.
- **R2** — The listing SHALL be parsed from a machine-readable form (NUL-terminated fields, say),
  not from the column layout of `ls -l`.

## Notes

- Related: `spec/safe-execution/` (research 04 §4, PLAN risk 7). `ROADMAP.md` places it under
  `issues/tool-surface.md`, whose `file_*` family rewrites `list_directory`.
- Found by: `safe-execution` planning research, filed at P4 (2026-10-17).
