# 03 — What `/etc/agents.d/` holds on the clusters today

> Probe of 2026-10-19, read-only, within the repo card's dogfood class (Gautschi, Gilbreth, Negishi,
> Scholar; never Anvil). One `find /etc/agents.d -maxdepth 1 -mindepth 1 -printf '%f %y %s'` per
> cluster, plus `wc -c -m` over the eligible files concatenated. Names, types, sizes and counts
> only: no file content was copied, and no host other than the cluster alias was recorded.

## Inventory

| Cluster  | Entries | Types | `AGENTS.md` (bytes) | Topic files (bytes each)                                   | Topic total bytes / chars |
|----------|---------|-------|---------------------|-------------------------------------------------------------|---------------------------|
| Gautschi | 6       | all regular files | 10,299 | filesystems 2,169 · lmod 1,620 · policies 1,933 · slurm 2,783 · unix 1,930 | 10,435 / 10,373 |
| Negishi  | 6       | all regular files | 10,285 | filesystems 2,225 · lmod 1,617 · policies 1,929 · slurm 2,723 · unix 1,926 | 10,420 / 10,363 |
| Scholar  | 6       | all regular files | 11,696 | filesystems 2,434 · lmod 1,983 · policies 1,884 · slurm 3,331 · unix 2,199 | 11,831 / 11,779 |
| Gilbreth | —       | —     | —                   | not probed: SSH host-key verification failed; the probe stops there (fixing `known_hosts` is outside a read-only probe) | — |

(File names are `<topic>.md`; the table drops the extension.)

## Facts the design leans on

- **Same layout on every cluster probed:** one assembled `AGENTS.md` plus the same five topic files,
  all regular files (no symlinks, no subdirectories, no dotfiles), all names lowercase ASCII.
- **`AGENTS.md` is about the size of the five topic files together** (10,299 vs 10,435 bytes on
  Gautschi), which confirms the GOAL's "every rule arrives twice": skipping it halves the text.
- **Topic files are 1.6K–3.3K bytes each; a site's eligible text is 10.4K–11.8K bytes.** Characters
  run about 0.5% under bytes: the files carry a little non-ASCII text, so the budget's unit matters
  (a byte count would over-count; see `02-client-truncation.md` for what the client counts).
- **With framing** (a `<!-- Source: x.md -->` header and a blank line per file, about 30 characters
  each) a whole site is about 10.6K–12.0K characters. A base of about 1.3K plus the section heading
  brings the uncut instructions to about 12K–13.5K, inside `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH=16384`
  for every cluster probed.
- **At the 4,096 default, at most one or two topic files fit** next to a base of about 1.3K–2.1K
  characters. The order rule decides which: in `sort` order the first file is `filesystems.md`;
  `policies.md` comes third.
- **Anvil** (outside the dogfood class, not probed) was measured at about 12.4K characters of topic
  text when the seed was written (`issues/agents-d-instructions.md`); nothing here contradicts it.

## Not probed (would need more than names and sizes)

- Section structure within files (heading counts), which a by-section cut would need. The plan cuts
  by whole file, so it does not.
- File modes and ownership. The current layout reads as world-readable (the probing user is not an
  administrator and `wc` read every file); an unreadable file is handled by R8's design, not assumed
  away.
