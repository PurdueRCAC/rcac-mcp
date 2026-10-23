---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Run your own server on the cluster, Jupyter-style, behind a token

## Problem

The recommended deployment is `stdio` on the user's laptop, executing on the cluster over their own
SSH (`SECURITY.md`). It works, but every tool call is an SSH round trip, filesystem-heavy tools pay
that latency per call, and each agent session needs its own server.

The obvious alternative — the user starts the server on a login node, or inside a batch job, and
their agents connect to it over HTTP — has no supported shape here. The authenticated HTTP modes
(`-t http -a jwt|oidc`) resolve to `delegate` execution, built for a shared multi-user deployment,
and `local` execution over HTTP exists only without authentication, for development (`SECURITY.md`
§ *Mode 2*). A user can't run a server as themselves behind a token of their own.

Running an HTTP server on a shared login node has its own hazards, and they shape the design: every
other user on the node can reach `127.0.0.1`; a token on the command line is visible to everyone
through `ps`; a token in a URL leaks into logs and `Referer` headers (Jupyter's history shows both);
and a forwarded port is reachable from the user's own browser, which makes DNS rebinding a concern.

## Why it was deferred

Not deferred — the Part II cycle, after release 0.2.0. It is built on the upgraded HTTP transport
([`spec/stack-upgrade/`](../spec/stack-upgrade/)) and the safe executor
([`spec/safe-execution/`](../spec/safe-execution/)), and it names its command and paths `cluster-mcp`
([`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md)).

**Pre-existing:** n/a — new work.

## Outcome / vision

A **single-user Streamable HTTP server**, run by the user, as the user (`local` execution), on the
cluster.

**The token.** A JWT: `aud` bound to the server's canonical URI, `sub` the Unix user, `exp`
required, signed with a per-user secret kept `0600` under `~/.config/cluster-mcp/`.

- At startup the server prints one token and writes it to a `0600` runtime file, as Jupyter does.
- `cluster-mcp token new --lifetime 8h` mints one ahead of time; the client passes it as an
  `Authorization: Bearer` header, read from an environment variable or a file.
- The server checks `aud`, `exp` and that `sub` is its own user, comparing in constant time.
- The token never appears in argv or in a URL, and is redacted from logs.

**The listener.** By default a `0600` unix socket, which the laptop reaches with
`ssh -L 8765:/path/to.sock login-node`. A TCP port is opt-in and always requires the token; the
server never binds a wildcard address by default.

**Request checks.** `Origin` and `Host` are validated on every request, as the MCP transport spec
requires.

**Lifecycle.** An idle timeout ends the server; one server per user per host; the runtime file is
removed on exit. Long sessions are documented as running inside a batch job, since login-node
policy limits long-lived processes.

**What it buys.** Commands run on the cluster with no SSH round trip per call; filesystem and index
tools run next to the data; one SSH session (with its MFA) carries the tunnel; several agents can
share one server.

**Non-goal: a shared multi-user endpoint.** One server that many users authenticate to, with the
server acting as each of them, is out of scope for now. This cycle builds the per-user unit only.

TLS is a shaping question: over the SSH tunnel it is redundant, and a server reachable beyond the
node needs a real certificate and a site's network approval.

## Sketch of the acceptance criteria

- **R1** — WHEN started in token mode, the server SHALL serve Streamable HTTP on a `0600` unix socket
  by default, and SHALL print one token and write it to a `0600` runtime file.
- **R2** — `cluster-mcp token new --lifetime <duration>` SHALL mint a token with `aud`, `sub` and
  `exp` set, signed with the user's `0600` secret.
- **R3** — IF a request's token is missing, expired, for a different audience or for a different
  user, THEN the server SHALL reject it.
- **R4** — The server SHALL NOT accept a token from argv or a URL query string, and SHALL NOT write a
  token to its logs.
- **R5** — WHERE a TCP listener is requested, the server SHALL require the token on it, and SHALL NOT
  bind a wildcard address unless asked explicitly.
- **R6** — The server SHALL validate `Origin` and `Host` on every request.
- **R7** — WHEN no request has arrived within the idle timeout, the server SHALL exit and remove its
  runtime file.
- **R8** — The docs SHALL show the `ssh -L` socket-forwarding setup and the batch-job mode.
- **R9** — `AGENTS.md` § Invariants §4 and `SECURITY.md` SHALL describe token mode's place in the
  execution-mode ladder in the same commit as the code.

## Notes

- Hosted chat clients that accept only administrator-registered HTTPS endpoints can't use a
  per-user server behind a tunnel; serving them is part of the out-of-scope shared endpoint.
- **This collides with an invariant.** `AGENTS.md` § Invariants §4 forbids making `local` reachable
  when authentication is configured. Token mode is exactly `local` behind authentication, so the
  cycle has to add a distinct, explicit mode to the execution-mode ladder rather than widen an
  existing rung — a human-gate change, rewritten in the same commit as the code.
- The existing `delegate` mode and `-a jwt|oidc` are otherwise untouched by this cycle.
- Related: [`spec/stack-upgrade/`](../spec/stack-upgrade/), [`spec/safe-execution/`](../spec/safe-execution/),
  [`rename-to-cluster-mcp.md`](rename-to-cluster-mcp.md), [`xdu-storage.md`](xdu-storage.md)
  (index queries benefit most from running next to the data).
- Found by: roadmap revision, 2026-10-16 (out of cycle).
