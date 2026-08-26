---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The cluster-context cache is process-global and keyed by a non-unique hostname

## Problem

`resources.py:23`:

```python
# Cache for cluster context (loaded once per connection)
_cluster_context_cache: dict[str, str] = {}
```

`load_cluster_context()` keys it on `executor.hostname` (`:40`) and returns the cached value if
present (`:43`). It is populated by running `find /etc/agents.d …` and `cat` on each file **through
the calling user's executor**.

Two problems compound:

1. **The key does not distinguish users.** `DelegatingExecutor.hostname` returns
   `socket.gethostname()` (`executor/delegate.py:84`) — identical for every authenticated user on the
   server. So in `delegate` mode the first user to read `rcac://context` populates the cache from
   *their* `sudo -u <them> cat`, and every subsequent user is served that value.
   `AGENTS.md` § *Executors* now records the general form: `hostname` is not a user-distinguishing
   key.
2. **The cache is never invalidated.** The comment says "loaded once per connection", but the dict is
   module-global and lives for the process. `clear_context_cache()` exists (`:88`) and nothing calls
   it. An administrator editing `/etc/agents.d/*.md` on a long-running server never sees the change.

The delegate mode is the one mode that exists specifically to keep users apart, and this is a
process-global cache inside it.

## Why it was deferred

Not deferred — found while writing `invariants.md` §8 and filed rather than fixed, because the harness
port was not a cycle.

**Pre-existing:** yes — on `main` as of `2def4a4`.

## Outcome / vision

Context is cached per *reader*, not per hostname, and does not outlive its usefulness. The natural key
is the delegated identity where one exists; the natural bound is a TTL or an explicit invalidation on
a signal an admin can send. The plan should pick deliberately rather than reflexively deleting the
cache — the uncached path is a `find` plus one `cat` per file over SSH on every read.

## Sketch of the acceptance criteria

- **R1** — WHILE running in `delegate` mode, two tool calls from distinct authenticated identities
  SHALL NOT share a cached cluster-context value.
- **R2** — WHEN `/etc/agents.d/*.md` changes on the cluster, a running server SHALL reflect the change
  without a restart, within a documented bound.
- **R3** — The `/etc/agents.d` contract SHALL be unchanged: `maxdepth 1` glob, `sort` order,
  `<!-- Source: {filename} -->` headers, and empty-string-not-error on a missing directory
  (`invariants.md` §8).
- **R4** — A test SHALL exercise two distinct identities against the cache and assert isolation —
  without requiring `sudo` (assert against a stub executor whose `hostname` is deliberately identical
  for both).
- **R5** — IF the cache is retained, THEN `clear_context_cache()` SHALL either be reachable or be
  removed; a dead invalidation function is worse than none.

## Notes

- **Lane judgement, recorded deliberately.** This is in the public lane, not `.security/`. The cached
  content is `/etc/agents.d/*.md` — world-readable administrative markdown that every user could read
  directly anyway — so there is no exploitable disclosure to conceal, only a caching bug whose *shape*
  is cross-user. Had it cached anything user-private, it would have gone hidden. If the plan discovers
  the cache can hold user-private content on some path, **stop and move the seed to `.security/`.**
- Related: `AGENTS.md` § *Resources* documents this as a known defect and forbids building new
  per-user state on the cache.
- Found by: the harness port, 2026-08-26 (out of cycle).
