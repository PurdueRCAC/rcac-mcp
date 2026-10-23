# Security Architecture

This document describes the security model for the RCAC MCP Server, including how
command execution is isolated across different deployment modes.

## Execution Modes Overview

The server supports three execution modes, each with different security characteristics:

| Mode | Transport | Auth | Use Case |
|------|-----------|------|----------|
| `ssh` | stdio | N/A | Local MCP client → remote HPC cluster |
| `local` | http/sse | none | Development/testing only |
| `delegate` | http/sse | jwt/oidc | Multi-user production deployment |

## Mode 1: SSH Execution (stdio)

**Intended use:** Desktop MCP clients (Claude Desktop, Cursor, etc.) connecting to
remote HPC clusters.

```
┌─────────────────┐      stdio       ┌─────────────────┐      SSH       ┌─────────────┐
│   MCP Client    │ ◄──────────────► │  rcac-mcp       │ ◄────────────► │ HPC Cluster │
│ (Claude, etc.)  │                  │  (local)        │                │             │
└─────────────────┘                  └─────────────────┘                └─────────────┘
```

**Security model:**
- The MCP server runs locally on the user's machine
- SSH connection uses the user's existing `~/.ssh/config` and keys
- No authentication at the MCP layer (stdio is inherently single-user)
- All commands execute as the SSH user on the remote system
- Security boundary is the SSH connection itself

**Configuration:**
```bash
rcac-mcp --ssh-host cluster.example.edu
# or
export RCAC_SSH_HOST=cluster.example.edu
rcac-mcp
```

**Trust assumptions:**
- The local user is trusted (they control the MCP client)
- The SSH connection is authenticated via standard SSH mechanisms
- The remote system enforces permissions via standard Unix file/process ownership

## Mode 2: Local Execution (http with auth=none)

**Intended use:** Development and testing only. **NOT FOR PRODUCTION.**

```
┌─────────────────┐      HTTP        ┌─────────────────┐
│   MCP Client    │ ◄──────────────► │  rcac-mcp       │ ──► local bash
│                 │                  │  (http server)  │
└─────────────────┘                  └─────────────────┘
```

**Security model:**
- Commands execute directly on the server as the process owner
- No authentication - anyone who can reach the HTTP endpoint can execute commands
- Single shared executor for all requests

**Configuration:**
```bash
rcac-mcp -t http -e local
```

**⚠️ WARNING:** This mode should only be used for local development. Never expose
to a network without authentication.

## Mode 3: Delegated Execution (http with auth)

**Intended use:** Multi-user production deployment where the server runs as root
and delegates commands to authenticated users.

```
┌─────────────────┐                  ┌─────────────────┐
│  User A         │ ──► JWT/OIDC ──► │                 │ ──► sudo -u alice ...
│  (sub: alice)   │                  │                 │
└─────────────────┘                  │  rcac-mcp       │
                                     │  (runs as root) │
┌─────────────────┐                  │                 │
│  User B         │ ──► JWT/OIDC ──► │                 │ ──► sudo -u bob ...
│  (sub: bob)     │                  │                 │
└─────────────────┘                  └─────────────────┘
```

**Security model:**
- Server runs as root (or a privileged user with sudo rights)
- Each request is authenticated via JWT or OIDC
- User identity is extracted from token claims (`sub`, `email`, or `preferred_username`)
- Identity is mapped to a local Unix user via the user map file
- Commands are wrapped with `sudo -n -H -u <local_user> ...`, and start in that user's home
- File transfers (`upload_file`, `download_file`) also run as the mapped user, through the same
  `sudo` launch; the server process reads and writes no file on a user's behalf
- OS-level controls (sudoers, PAM, SELinux, etc.) enforce what each user can do

**Configuration:**
```bash
export JWT_SECRET="your-secret-key-at-least-32-characters"
export RCAC_USER_MAP=/etc/rcac-mcp/users.map
rcac-mcp -t http -a jwt -e delegate
```

**User map file format** (`/etc/rcac-mcp/users.map`):
```
# Identity claim → local Unix user
# Lines starting with # are comments

alice@purdue.edu    alice
bob@purdue.edu      bob
service-account-1   svc_batch
```

### OIDC Configuration

`-a oidc` puts FastMCP's OIDC proxy in front of the server. It needs all four of
`OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `MCP_BASE_URL`; with any one unset or
empty the server refuses to start, naming all four, before a provider is built or a request is
made. FastMCP 4 treats the client secret as optional; this server requires it.

The proxy is held at the posture it had on FastMCP 2.x, pending a deliberate decision: it does
**not** fetch client-supplied metadata documents (CIMD) and does **not** forward the `resource`
parameter to the upstream provider. FastMCP 4 turns both on by default; `rcac-mcp` passes both off.

Upgrading from a release on FastMCP 2 drops the proxy's client registrations. FastMCP 2 kept them in
an encrypted `DiskStore` at `<home>/oauth-proxy/cache.db`; FastMCP 4 keeps them in an encrypted
`FileTreeStore` under `<home>/oauth-proxy/<key-fingerprint>/` (`<home>` is FastMCP's home
directory) and does not read the old store. Every client registered before the upgrade must register
again.

### Request Isolation in Delegate Mode

A critical security property of delegate mode is that concurrent requests from
different users are properly isolated. This is achieved using Python's
`contextvars.ContextVar` mechanism.

**How it works:**

```python
from contextvars import ContextVar

# This is NOT a global variable - it's a context-local variable
executor_var: ContextVar[Executor] = ContextVar('executor', default=None)
```

Context variables are *task-local* in async Python. Each async task (HTTP request)
gets its own isolated copy of the context:

```
Request 1 (alice)                    Request 2 (bob)
─────────────────────────            ─────────────────────────
on_call_tool middleware              on_call_tool middleware
  │                                    │
  ▼                                    ▼
set_executor(                        set_executor(
  DelegatingExecutor(alice)            DelegatingExecutor(bob)
)                                    )
  │                                    │
  ▼                                    ▼
get_executor() → alice's executor    get_executor() → bob's executor
  │                                    │
  ▼                                    ▼
sudo -u alice <command>              sudo -u bob <command>
```

**Why this is safe:**

1. **No shared state:** Each request's executor is stored in its own context, not
   in a module-level global variable.

2. **Automatic isolation:** FastMCP/Starlette creates a new async task per request,
   which automatically gets a fresh context copy.

3. **No cross-request leakage:** When request 1 calls `set_executor()`, it only
   affects request 1's context. Request 2 sees its own value.

4. **Clean teardown:** When a request completes, its context is discarded.

**Contrast with an unsafe approach:**

```python
# ❌ WRONG - This would be a security vulnerability!
_current_executor = None  # Shared across ALL requests

def set_executor(executor):
    global _current_executor
    _current_executor = executor  # Race condition! User A could get User B's executor
```

The `ContextVar` approach is the same pattern used by:
- FastMCP's `get_access_token()` for per-request auth context
- Starlette's request state
- Python's `decimal` module for per-context precision settings

### Sudoers Configuration

For delegate mode to work, the server process must be able to run commands as
mapped users without a password prompt. Example sudoers configuration:

```sudoers
# /etc/sudoers.d/rcac-mcp

# Allow rcac-mcp (running as root or rcac-mcp user) to run commands as any user
# NOPASSWD is required since there's no TTY for password prompts
rcac-mcp ALL=(ALL) NOPASSWD: ALL

# Or more restrictively, only allow specific users:
rcac-mcp ALL=(alice,bob,svc_batch) NOPASSWD: ALL
```

**Recommended restrictions:**
- Limit which users can be delegated to
- Use `NOEXEC` to prevent execution of binaries from writable directories
- Consider using SELinux/AppArmor for additional confinement

## Identity Claim Extraction

In delegate mode, the middleware extracts user identity from JWT/OIDC token claims
in the following order of preference:

1. `sub` - Standard JWT subject claim (most common)
2. `email` - Common in OIDC providers
3. `preferred_username` - Used by some OIDC providers (Keycloak, etc.)

The first non-empty claim found is used as the key to look up the local user in
the user map file.

**Example JWT payload:**
```json
{
  "sub": "alice@purdue.edu",
  "iss": "rcac-mcp",
  "aud": "rcac-mcp",
  "exp": 1706650000
}
```

This would be mapped using the `alice@purdue.edu` key from the user map.

## Recommendations

### For Development
- Use `local` mode with `auth=none`
- Never expose to network

### For Single-User Production
- Use `ssh` mode via stdio
- Leverage existing SSH key infrastructure

### For Multi-User Production
- Use `delegate` mode with `jwt` or `oidc` auth
- Run server as dedicated service account with minimal privileges
- Configure restrictive sudoers rules
- Enable TLS (use reverse proxy like nginx)
- Audit log all commands (future enhancement)
- Regularly rotate JWT secrets / OIDC credentials

## Reporting Security Issues

If you discover a security vulnerability, please report it privately to
rcac-help@purdue.edu rather than opening a public issue.
