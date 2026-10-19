# PLAN — An unreadable sdist is named, never a traceback

> **Status:** Draft for review · **Last updated:** 2026-10-19
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). The prototype the gate was dry-run on is
> [`research/prototype.diff`](research/prototype.diff) (against `develop` `5153e47`). Every design
> element traces to a GOAL R-ID.

## 1. Summary

`bin/sdist_hygiene.py` gains one helper, `member_names(tarball)`, that reads a tarball *in full*:
it lists the members, then reads on from where the listing stopped to the end of the stream,
requiring an end-of-archive block and zero bytes from it on, and letting the decompressor verify its
trailer; bz2 and xz streams are also decoded one by one, strictly. `sdist_violations` calls it
inside a handler that catches every `Exception` and prints the error as one collapsed line. The
furniture set, the root rule, `main` and both callers stay as they are. One phase: the fix, its
tests and the § Testing entry are one vertical slice, and the gate is offline.

## 2. Design

### Root cause (reproduced on `5153e47`, Python 3.14.6)

Three defects share one cause, `TarFile.getnames()` being taken for a full read:

1. **The traceback (R1, R2).** `TarFile.next()` turns only errors raised while *parsing a header
   block* into `tarfile.ReadError`. While it *seeks over member data* to reach the next header, the
   decompressor's own errors escape raw: `EOFError` for a truncated gzip or bz2 stream, `zlib.error`
   for damaged deflate data, `lzma.LZMAError` for damaged xz. The handler catches only `OSError`
   and `tarfile.TarError`, so the run stops with a traceback and the later tarballs and the summary
   are lost (R3).
2. **Silent passes (R4), found while planning.** `TarFile.next()` treats an invalid or truncated
   header after offset 0 as the end of the archive and returns no error, and listing stops at the
   end-of-archive marker, so the compression trailer (gzip CRC32 and length) is never read. On
   `5153e47` each of these exits 0 with `clean`: damaged deflate data mid-stream, a damaged gzip
   checksum or length, a truncated xz stream, a member placed after the end-of-archive marker, and a
   damaged header in front of `.security/hidden.md`, which hides that member and every one after it.
   The review (§4) added three more: an archive cut exactly at a header boundary (no end-of-archive
   block, so the members after the cut vanish), and a damaged later bz2 or xz stream, which
   `BZ2File` and `LZMAFile` skip without error.
3. **Not one line (R2).** Since Python 3.13 the `tarfile.open` failure message spans six lines (one
   per compression method), so a non-tarball or empty `.tar.gz` prints the `cannot be read (` line
   followed by five more.

### The change: `bin/sdist_hygiene.py` only

- **`member_names(tarball) -> list[str]`** (new). Opens with `tarfile.open(tarball)` (auto-detected
  compression, as today), takes `archive.getnames()`, then seeks the decompressed stream
  (`archive.fileobj`) back to `archive.offset`, the block where listing stopped, and reads to the
  end in `tarfile.RECORDSIZE` blocks, with no cap on how far. Any non-zero byte raises
  `tarfile.ReadError` (*data after the end of the archive*), and fewer than `tarfile.BLOCKSIZE`
  bytes there raises *no end-of-archive block*. Reaching the end makes `GzipFile`, `BZ2File` and
  `LZMAFile` check their own end-of-stream marker, checksum and length, raising on any mismatch. Seeking to `archive.offset`
  rather than reading from the current position matters: when the unparseable header is the last
  non-zero block, the current position is already past it.
- **`check_streams(tarball, decoder)`** (new), run when `archive.fileobj` is a `BZ2File` or an
  `LZMAFile` (`STRICT_STREAMS`). It re-reads the raw file and decodes stream after stream with a
  fresh `BZ2Decompressor` or `LZMADecompressor`, output bounded to a record per call and dropped.
  Each stream must reach its end marker, and whatever follows must decode as another stream, so a
  damaged later stream or trailing garbage raises instead of being skipped. Gzip needs no such pass:
  `GzipFile` already rejects a damaged later member and garbage. zstd's reader is strict too.
- **`sdist_violations`**: the `try` body becomes `names = member_names(tarball)`, and the handler
  becomes `except Exception as error`, with a comment saying why the catch is broad (the tar layer
  and each decompressor raise their own types; one missed type is a traceback). The reason is
  `' '.join(f'{type(error).__name__}: {error}'.split())`: the exception type keeps an empty or terse
  message meaningful (`EOFError: Compressed file ended…`), and collapsing whitespace makes it one
  line. `continue` goes on to the next tarball, as today. A tarball that fails partway reports only
  its `cannot be read` line, never the furniture found before the failure, so N counts it once.
- **Docstring**: one paragraph saying a tarball is read in full before it is judged, and an
  unreadable one is named, the next is checked, and it is never called clean.
- **Unchanged**: `FURNITURE`, `SDIST_SUFFIX`, `member_violations`, `main`, the recipe step and
  `ci.yml`. `static.py` pins each against `5153e47` as parsed code.

### Tests: `tests/test_sdist_hygiene.py` (`unit`, in-process)

New builders write the planted bytes in `tmp_path` from the standard library with a fixed seed.
New tests (ids are the gate's contract, listed in [`verify/ids.txt`](verify/ids.txt)):

- `test_unreadable_sdist_is_named_and_fails[<shape>]` through `main()`: exit 1, empty stdout,
  exactly the one `cannot be read (…)` line and `1 problem(s)`. Shapes: `truncated-gzip` (the GOAL's
  200 kB half-cut repro), `not-a-tarball`, `empty`, `deflate-damaged`, `gzip-checksum-damaged`,
  `gzip-length-damaged`, `header-damaged-mid-archive`, `member-data-cut-short`,
  `member-after-end-of-archive`, `last-header-damaged`, `garbage-after-gzip-stream`,
  `no-end-of-archive`, `uncompressed-cut-at-a-header`, `second-gzip-member-missing`,
  `member-after-long-zero-tail`, `checksum-damaged-after-long-zero-tail`,
  `second-bz2-stream-damaged`, `second-xz-stream-damaged`, `garbage-after-xz-stream`,
  `truncated-xz`, `truncated-bz2`. An escaping exception fails the test, which is how R6 holds.
- `test_every_truncation_of_a_gzip_stream_is_named`: every cut in the last 64 bytes and sixteen cuts
  through the stream.
- `test_a_directory_named_like_an_sdist_is_named`; `test_any_error_while_reading_is_named` (a
  monkeypatched `tarfile.open` raising `RuntimeError`, pinning the broad catch).
- `test_an_unreadable_sdist_does_not_stop_the_others`: `aaa` clean, `mmm` truncated, `zzz` with
  `.security/x`, so stderr is exactly the `mmm` line, the `zzz` line and `2 problem(s)`.
- `test_readable_variants_are_judged_as_before[<shape>]` (GNU format, uncompressed, two gzip
  members, trailing gzip zero padding, two xz streams, two bz2 streams) and
  `test_furniture_in_another_compression_is_named` (bz2).
- `test_every_kind_of_member_is_judged[<shape>]`: a furniture directory entry, a symlink, a
  hardlink, duplicated and unsorted furniture, and a non-ASCII GNU name, each reported exactly as
  `5153e47` reports it (R5 pinned inside `member_names`).

**AGENTS.md § Testing**: the `tests/test_sdist_hygiene.py` entry gains what it now pins (truncated
and damaged streams, data after the end, any read error, the others still checked, readable
variants unchanged). The prototype's wording is in `research/prototype.diff`.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `member_names` + `except Exception` in `sdist_violations`; tests `…[truncated-gzip]`, `test_every_truncation_of_a_gzip_stream_is_named`; drive plants `truncated-half`, `cut-*` |
| R2   | the broad catch and the one-line `reason`; tests over every unreadable shape, the directory, `test_any_error_while_reading_is_named`; drive plants of the same shapes |
| R3   | `continue` after the line, the loop and summary unchanged; `test_an_unreadable_sdist_does_not_stop_the_others`; drive plant `mixed` |
| R4   | the full read (end-of-archive block, zero tail with no cap, decompressor trailer, strict bz2/xz streams) and the handler always appending a line; every unreadable test asserts exit 1 and no stdout; mutants `marker-only`, `nonzero-tail-allowed`, `tail-read-from-current`, `tail-capped`, `end-marker-optional`, `streams-trusted`, `unreadable-dropped` |
| R5   | `FURNITURE`, `member_violations`, `main` untouched (`static.py`); `member_names` returns `getnames()` as is (`test_every_kind_of_member_is_judged`, mutants `files-only`, `dedupe-sort`); readable-variant tests; drive's byte-for-byte base parity on 18 readable plants; CI's gate on a real hatchling sdist at publish |
| R6   | the new tests sit in `tests/test_sdist_hygiene.py`, which the profile's `verify` runs (`uv run pytest -q`); `mutants.py` shows each regression turns named tests red |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before design (from the GOAL) and again against this design. No numbered section (§1–§9) is
touched: `bin/sdist_hygiene.py` and its test are leaves, outside the coupled core and the
high-blast-radius list, so neither research trigger 1 nor 2 fires.

- **§ Conventions, "CI mirrors the gate"** — the check stays one file; `ci.yml` and the recipe's
  step are unchanged (`static.py` diffs `ci.yml` against `5153e47` and counts the recipe step).
- **§ Conventions, test markers** — every new test is `@mark.unit`; the gate names its test ids
  rather than selecting by marker.
- **§ Conventions, comments** — declarative, no spec ids in code or tests.
- **§ Conventions, "point to a rule"** — the § Testing entry describes the module; nothing restates
  the furniture set.
- **§ Testing, the unit guard** — the tests run the script in-process and launch nothing; the CLI
  drive lives in the gate, outside pytest.
- **§ Packaging** — `bin/` is excluded from the sdist; unchanged.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `except Exception` instead of a list of types | The GOAL's class is "any error raised while names are listed"; `tarfile` delegates to gzip, bz2, lzma and zstd, each with its own errors, and the set moves between Python releases | Listing `EOFError`, `zlib.error`, `lzma.LZMAError` fixes today's instances and leaves the next type a traceback. The broad catch still fails closed, and the `try` body is the one helper, so a programming error there turns the clean-path tests red |
| A second read of the stream after listing | Listing alone passes damaged and truncated streams as clean (R4) | `getnames()` alone is the defect; `extractall` to a temporary directory reads everything but writes files and costs more |
| A third, strict pass over bz2 and xz files (`check_streams`) | `BZ2File` and `LZMAFile` skip a damaged later stream, so the tail read cannot see it (R4) | Narrowing the claim to gzip leaves an sdist the check could not read called clean; decompressing the whole file into memory to hand `tarfile` a `BytesIO` re-implements its format detection and unbounds memory |
| An end-of-archive block is required | An archive cut exactly at a header boundary lists only the members before the cut and is otherwise indistinguishable from a whole one (R4) | Accepting it keeps a marker-less tar `clean`, but that is the same bytes as a cut one; hatchling and GNU tar always write the block |

## 4. Rabbit holes (resolved)

Lean path (`kind: fix`, `appetite: small`); no research fan-out, since triggers 1 and 2 do not fire.
Trigger 3 fires (the check is a gate), so the adversarial design review ran.

- **Which errors escape, and does fixing the traceback suffice?** → No. Read `TarFile.next()` in
  CPython 3.14.6 and planted every shape (`verify/plants.py`): the traceback is one of three defects,
  and the other two pass a broken sdist as clean (§2, Root cause).
- **Does the full read reject a genuine sdist?** → No. A real `uv build --sdist --offline` of the
  prototype (hatchling, `rcac_mcp-0.1.1.tar.gz`) is `clean` under both the base and the prototype;
  so are GNU-format, uncompressed, two-member gzip, zero-padded gzip and marker-less archives. The
  real build stays out of the gate (it needs a warm uv cache); CI's gate runs it on every push.
- **Cost of the second pass** → seeking a `GzipFile` backwards rewinds and decompresses again;
  sdists are under a megabyte, and the whole module runs in 0.2 s.
- **Adversarial design review** → ran (trigger 3), a fresh child given the GOAL, this design, the
  drafted `verify/` and the prototype worktree, tasked with breaking both. Every crafted input gave
  one formatted line or `clean`, never a traceback; no memory blow-up; the gate ran green under
  `UV_OFFLINE=1`. Its findings and what each changed:
  1. HIGH: a damaged later bz2/xz stream hid members and passed as clean → `check_streams`, test
     shapes `second-bz2-stream-damaged`, `second-xz-stream-damaged`, `garbage-after-xz-stream`, plants
     and the `streams-trusted` mutant.
  2. HIGH: nothing pinned R5 inside the helper; a files-only or de-duplicated listing passed the whole
     gate while dropping furniture → `test_every_kind_of_member_is_judged`, five base-parity furniture
     plants, mutants `files-only` and `dedupe-sort`.
  3. HIGH: a cap on the tail read passed the gate → shapes `member-after-long-zero-tail` and
     `checksum-damaged-after-long-zero-tail` (a mebibyte of zeros first), and the `tail-capped`
     mutant; the design states the read has no cap.
  4. MEDIUM: an archive cut at a header boundary (plain tar, or gzip with its later member missing)
     passed → the end-of-archive block is required; `no-end-of-archive` moved from readable to
     unreadable; shapes, plants and the `end-marker-optional` mutant.
  5. LOW: the full read is slower on long zero tails (0.7 s for 1 GiB of zeros) → §5.
  6. LOW: an all-zero file is a valid empty tar and passes, in base too → §5 (a new check is a
     non-goal).
  7. LOW: gate hardening → `gate.sh` exports `UV_OFFLINE=1`; `static.py` also scans `bin/` for a
     second `FURNITURE`. A mutant whose anchor goes missing is red, never skipped.
  After the folds, the drafted gate is green on the revised prototype (120 plants, 13 red on base,
  12 of 12 mutants killed), and the full suite passes (702 tests).

## 5. Risks & open questions

- **Newly rejected shapes.** Garbage after a gzip or xz stream, non-zero data after the
  end-of-archive block, an archive with no end-of-archive block, and a damaged later bz2 or xz
  stream were `clean` on `5153e47` and are now `cannot be read`. All are outside what hatchling and
  GNU tar write (a real offline `uv build --sdist` stays clean); each is where a hidden or lost
  member sits. R5 covers readable sdists, and these are not read in full, so no R-ID bends; the
  sign-off may rule otherwise on the end-of-archive block alone.
- **Cost on a long zero tail.** The tail is read to the end, uncapped by design (a cap is a
  hole): 1 GiB of zeros inside the stream takes 0.7 s, and raw zero padding after a gzip stream is
  skipped by `gzip` a byte at a time (50 MB: 2.8 s). Memory stays bounded (about 21 MB).
- **An all-zero file** is a valid empty tar with no members and passes, as on `5153e47`. Rejecting
  it would be a new check on a readable sdist, a non-goal; a missing `PKG-INFO` is the same class.
- **The broad catch masks a bug as "cannot be read".** It still fails closed (exit 1, named), and
  the clean-path tests and the base-parity drive go red on such a bug.
- **Mutant anchors are exact strings** from the prototype. Build copies the prototype; if it words
  a line differently, `mutants.py` reports the anchor and build moves the table with the code.

## 6. Verification strategy

One gate, [`verify/gate.sh`](verify/gate.sh) `P1`, offline and deterministic, about 30 s, one child
at a time. Its parts run alone as modes (`static`, `tests`, `drive`, `control`, `mutants`):

- **static** — what must not change, against `5153e47` (see §2).
- **tests** — `uv lock --check`, then pytest with every id in `verify/ids.txt` named on the command
  line, so a renamed or dropped test is an error.
- **drive** — `verify/drive.py` writes 120 plants (`verify/plants.py`) and runs the recipe's command,
  `uv run python bin/sdist_hygiene.py <dir>`, over each: exact stderr lines, exit status, empty
  stdout on failure, no `Traceback`, and byte-for-byte parity with `5153e47` on every readable plant.
- **control** — the same drive against `5153e47`'s script must see thirteen named plants red, so
  the drive is not blind.
- **mutants** — `verify/mutants.py` applies twelve breaks of the fix to a snapshot; each must turn
  its named tests to `<failure>`.

`gate.sh` exports `UV_OFFLINE=1`, so a step that would reach the network fails instead.

The profile's full `verify` (both eras, the sandboxed `--help`) runs at publish; the prototype passed
the full suite (702 tests).
